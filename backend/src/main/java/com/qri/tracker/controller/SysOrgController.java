package com.qri.tracker.controller;

import com.qri.tracker.common.R;
import com.qri.tracker.common.UserContext;
import com.qri.tracker.entity.SysOrg;
import com.qri.tracker.service.SysOrgService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/**
 * 五级组织管理接口
 * GET  /api/org/children          — 当前用户直属下级列表
 * GET  /api/org/{id}/children     — 指定组织的直属下级列表
 * GET  /api/org/{id}              — 组织详情
 * POST /api/org                   — 新建组织（需 L1-L3 级管理员）
 * PUT  /api/org/{id}              — 修改组织信息
 */
@RestController
@RequestMapping("/api/org")
@CrossOrigin(origins = "*")
@RequiredArgsConstructor
public class SysOrgController {

    private final SysOrgService orgService;

    /**
     * 完整组织树（从当前用户所在组织为根节点向下展开）
     * 超管从 id=1 的总部开始，普通管理员从自己的 orgId 开始
     */
    @GetMapping("/tree")
    public R<List<SysOrg>> tree() {
        Long rootId = UserContext.isSuperAdmin() ? 1L : UserContext.getOrgId();
        return R.ok(orgService.buildTree(rootId));
    }

    /** 当前用户的直属下级列表（模块授权配置页初始化用） */
    @GetMapping("/children")
    public R<List<SysOrg>> myChildren() {
        Long orgId = UserContext.getOrgId();
        return R.ok(orgService.listDirectChildren(orgId));
    }

    /** 指定组织的直属下级列表 */
    @GetMapping("/{id}/children")
    public R<List<SysOrg>> children(@PathVariable Long id) {
        return R.ok(orgService.listDirectChildren(id));
    }

    /** 组织详情 */
    @GetMapping("/{id}")
    public R<SysOrg> get(@PathVariable Long id) {
        SysOrg org = orgService.getById(id);
        if (org == null) return R.fail(404, "组织不存在");
        return R.ok(org);
    }

    /** 新建组织（上级管理员操作，只能在自己管辖层级下新建） */
    @PostMapping
    public R<SysOrg> create(@RequestBody SysOrg req) {
        // 非超管：只能在自己所属组织下新建
        if (!UserContext.isSuperAdmin()) {
            Long myOrgId = UserContext.getOrgId();
            // parentId 必须在当前用户管辖范围内
            List<Long> scope = orgService.findSubtreeIds(myOrgId);
            if (req.getParentId() == null || !scope.contains(req.getParentId())) {
                return R.fail(403, "只能在自己管辖范围内新建组织");
            }
        }
        try {
            return R.ok(orgService.createOrg(req));
        } catch (IllegalArgumentException e) {
            return R.fail(e.getMessage());
        }
    }

    /** 修改组织基本信息（名称、编码、排序） */
    @PutMapping("/{id}")
    public R<?> update(@PathVariable Long id, @RequestBody SysOrg req) {
        SysOrg org = orgService.getById(id);
        if (org == null) return R.fail(404, "组织不存在");
        if (req.getOrgName()   != null) org.setOrgName(req.getOrgName());
        if (req.getSortOrder() != null) org.setSortOrder(req.getSortOrder());
        if (req.getIsActive()  != null) org.setIsActive(req.getIsActive());
        orgService.updateById(org);
        return R.ok();
    }
}
