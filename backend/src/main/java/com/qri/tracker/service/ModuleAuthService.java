package com.qri.tracker.service;

import com.qri.tracker.entity.SysModule;

import java.util.List;
import java.util.Set;

public interface ModuleAuthService {

    /**
     * 获取当前用户可用的模块树
     * （用于配置页展示可勾选范围）
     */
    List<SysModule> getModuleTreeForCurrentUser();

    /**
     * 查询某下级组织已开放的模块编码集合
     *
     * @param childOrgId 下级组织ID
     */
    Set<String> getEnabledCodesForOrg(Long childOrgId);

    /**
     * 保存对某下级组织的模块授权（全量覆盖）
     *
     * @param childOrgId     被授权组织ID（必须是当前用户的直属下级）
     * @param requestedCodes 希望开放的模块编码集合
     */
    void saveOrgModuleAuth(Long childOrgId, Set<String> requestedCodes);

    /**
     * 校验当前用户所在组织对某模块是否有访问权限
     * 结果从 Redis 热缓存读取，miss 时查库重建
     *
     * @param orgId      组织ID
     * @param moduleCode 模块编码
     */
    boolean checkModulePermission(Long orgId, String moduleCode);

    /** 使某组织的模块权限缓存失效（授权变更后调用） */
    void invalidateCache(Long orgId);
}
