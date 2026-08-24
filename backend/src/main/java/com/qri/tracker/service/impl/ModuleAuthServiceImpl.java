package com.qri.tracker.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.qri.tracker.common.UserContext;
import com.qri.tracker.entity.SysModule;
import com.qri.tracker.entity.SysOrg;
import com.qri.tracker.entity.SysOrgModuleAuth;
import com.qri.tracker.mapper.SysModuleMapper;
import com.qri.tracker.mapper.SysOrgModuleAuthMapper;
import com.qri.tracker.service.ModuleAuthService;
import com.qri.tracker.service.SysOrgService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.*;
import java.util.concurrent.TimeUnit;
import java.util.stream.Collectors;

@Slf4j
@Service
@RequiredArgsConstructor
public class ModuleAuthServiceImpl implements ModuleAuthService {

    private final SysModuleMapper          moduleMapper;
    private final SysOrgModuleAuthMapper   authMapper;
    private final SysOrgService            orgService;
    private final StringRedisTemplate      redis;

    /** Redis key 格式：org:module:{orgId} → SET of enabled module codes */
    private static final String CACHE_KEY  = "org:module:";
    private static final long   CACHE_TTL  = 300L;   // 5 分钟
    private static final String EMPTY_MARK = "__EMPTY__";

    // ── 模块树 ──────────────────────────────────────────────────────────────

    @Override
    public List<SysModule> getModuleTreeForCurrentUser() {
        Long orgId = UserContext.getOrgId();
        Set<String> enabledCodes = UserContext.isSuperAdmin()
                ? getAllNonSystemCodes()
                : getEnabledCodesForOrg(orgId);

        // 查全量模块（非系统级），标记 enabled
        List<SysModule> all = moduleMapper.selectList(
                new LambdaQueryWrapper<SysModule>()
                        .eq(SysModule::getIsSystem, false)
                        .orderByAsc(SysModule::getSortOrder));

        all.forEach(m -> m.setEnabled(enabledCodes.contains(m.getModuleCode())));

        return buildTree(all, null);
    }

    private Set<String> getAllNonSystemCodes() {
        return moduleMapper.selectList(
                new LambdaQueryWrapper<SysModule>().eq(SysModule::getIsSystem, false))
                .stream().map(SysModule::getModuleCode).collect(Collectors.toSet());
    }

    /** 将平铺列表组装为树形结构 */
    private List<SysModule> buildTree(List<SysModule> all, String parentCode) {
        List<SysModule> result = new ArrayList<>();
        for (SysModule m : all) {
            boolean isRoot = parentCode == null && m.getParentCode() == null;
            boolean isChild = parentCode != null && parentCode.equals(m.getParentCode());
            if (isRoot || isChild) {
                m.setChildren(buildTree(all, m.getModuleCode()));
                result.add(m);
            }
        }
        result.sort(Comparator.comparingInt(SysModule::getSortOrder));
        return result;
    }

    // ── 查询某组织已开放模块 ─────────────────────────────────────────────────

    @Override
    public Set<String> getEnabledCodesForOrg(Long orgId) {
        return authMapper.findEnabledCodes(orgId);
    }

    // ── 保存模块授权 ─────────────────────────────────────────────────────────

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void saveOrgModuleAuth(Long childOrgId, Set<String> requestedCodes) {
        Long operatorOrgId = UserContext.getOrgId();
        Long operatorUserId = UserContext.getUserId();

        // Step 1：校验是否为直属下级
        SysOrg child = orgService.getById(childOrgId);
        if (child == null || !operatorOrgId.equals(child.getParentId())) {
            throw new IllegalArgumentException("只能配置直属下级的模块权限");
        }

        // Step 2：不超授校验
        Set<String> operatorCodes = UserContext.isSuperAdmin()
                ? getAllNonSystemCodes()
                : getEnabledCodesForOrg(operatorOrgId);
        Set<String> overGranted = new HashSet<>(requestedCodes);
        overGranted.removeAll(operatorCodes);
        if (!overGranted.isEmpty()) {
            throw new IllegalArgumentException("MODULE_OVER_GRANT:" + String.join(",", overGranted));
        }

        // Step 3：计算增量
        Set<String> current  = getEnabledCodesForOrg(childOrgId);
        Set<String> toAdd    = new HashSet<>(requestedCodes);  toAdd.removeAll(current);
        Set<String> toRevoke = new HashSet<>(current);         toRevoke.removeAll(requestedCodes);

        // Step 4：新增（UPSERT）
        LocalDateTime now = LocalDateTime.now();
        for (String code : toAdd) {
            SysOrgModuleAuth existing = authMapper.selectOne(
                    new LambdaQueryWrapper<SysOrgModuleAuth>()
                            .eq(SysOrgModuleAuth::getOrgId, childOrgId)
                            .eq(SysOrgModuleAuth::getModuleCode, code));
            if (existing != null) {
                existing.setIsEnabled(true);
                existing.setGrantedByOrg(operatorOrgId);
                existing.setGrantedByUser(operatorUserId);
                existing.setGrantedAt(now);
                authMapper.updateById(existing);
            } else {
                SysOrgModuleAuth auth = new SysOrgModuleAuth();
                auth.setOrgId(childOrgId);
                auth.setModuleCode(code);
                auth.setIsEnabled(true);
                auth.setGrantedByOrg(operatorOrgId);
                auth.setGrantedByUser(operatorUserId);
                auth.setGrantedAt(now);
                authMapper.insert(auth);
            }
        }

        // Step 5：撤销 + 级联禁用子孙
        if (!toRevoke.isEmpty()) {
            // 禁用 childOrgId 自身
            authMapper.cascadeDisable(List.of(childOrgId), new ArrayList<>(toRevoke));

            // 找所有子孙并级联禁用
            List<Long> descendants = orgService.findDescendantIds(childOrgId);
            if (!descendants.isEmpty()) {
                authMapper.cascadeDisable(descendants, new ArrayList<>(toRevoke));
            }

            // 清除所有受影响组织的 Redis 缓存
            List<Long> allAffected = new ArrayList<>();
            allAffected.add(childOrgId);
            allAffected.addAll(descendants);
            allAffected.forEach(this::invalidateCache);
        } else {
            // 仅清除 childOrgId 的缓存
            invalidateCache(childOrgId);
        }
    }

    // ── 模块权限校验（C6引擎） ────────────────────────────────────────────────

    @Override
    public boolean checkModulePermission(Long orgId, String moduleCode) {
        // 超管直通
        if (UserContext.isSuperAdmin()) return true;

        String cacheKey = CACHE_KEY + orgId;
        try {
            // 直接用 SMEMBERS 取全量，避免 hasKey + isMember 的 TOCTOU 竞态
            Set<String> members = redis.opsForSet().members(cacheKey);
            if (members != null && !members.isEmpty()) {
                // 有效缓存命中（包含空标记时说明该组织无任何授权）
                if (members.contains(EMPTY_MARK)) return false;
                return members.contains(moduleCode);
            }
        } catch (Exception e) {
            // Redis 不可用，直接降级到 DB 查询
            log.warn("Redis unavailable, fallback to DB for orgId={}, module={}", orgId, moduleCode);
            return getEnabledCodesForOrg(orgId).contains(moduleCode);
        }

        // 缓存 miss → 查库重建后再判断
        rebuildCache(orgId, cacheKey);
        try {
            Set<String> rebuilt = redis.opsForSet().members(cacheKey);
            if (rebuilt == null || rebuilt.isEmpty() || rebuilt.contains(EMPTY_MARK)) return false;
            return rebuilt.contains(moduleCode);
        } catch (Exception e) {
            log.warn("Redis unavailable after rebuild, fallback to DB for orgId={}", orgId);
            return getEnabledCodesForOrg(orgId).contains(moduleCode);
        }
    }

    /** 查库重建 Redis 缓存 */
    private void rebuildCache(Long orgId, String cacheKey) {
        try {
            Set<String> codes = getEnabledCodesForOrg(orgId);
            if (codes.isEmpty()) {
                // 防穿透：写空标记占位
                redis.opsForSet().add(cacheKey, EMPTY_MARK);
            } else {
                redis.opsForSet().add(cacheKey, codes.toArray(new String[0]));
            }
            redis.expire(cacheKey, CACHE_TTL, TimeUnit.SECONDS);
        } catch (Exception e) {
            log.warn("Redis rebuild failed for orgId={}", orgId, e);
        }
    }

    @Override
    public void invalidateCache(Long orgId) {
        try {
            redis.delete(CACHE_KEY + orgId);
        } catch (Exception e) {
            log.warn("Redis cache invalidate failed for orgId={}", orgId, e);
        }
    }
}
