package com.qri.tracker.controller;

import com.qri.tracker.common.R;
import com.qri.tracker.common.UserContext;
import com.qri.tracker.entity.SysModule;
import com.qri.tracker.service.ModuleAuthService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 功能模块授权配置接口
 *
 * GET  /api/modules/tree                  — 当前用户可用模块树
 * GET  /api/modules/org/{childOrgId}/auth — 查询下级组织已开放模块
 * POST /api/modules/org/{childOrgId}/auth — 保存对下级组织的模块授权
 */
@RestController
@RequestMapping("/api/modules")
@CrossOrigin(origins = "*")
@RequiredArgsConstructor
public class ModuleAuthController {

    private final ModuleAuthService moduleAuthService;

    /** 获取当前用户可用模块树（带 enabled 标记，用于配置页展示） */
    @GetMapping("/tree")
    public R<List<SysModule>> moduleTree() {
        return R.ok(moduleAuthService.getModuleTreeForCurrentUser());
    }

    /** 查询某下级组织已开放的模块编码集合 */
    @GetMapping("/org/{childOrgId}/auth")
    public R<?> getOrgAuth(@PathVariable Long childOrgId) {
        Set<String> codes = moduleAuthService.getEnabledCodesForOrg(childOrgId);
        return R.ok(Map.of(
            "orgId",       childOrgId,
            "enabledCodes", codes
        ));
    }

    /** 保存对某下级组织的模块授权（全量覆盖） */
    @PostMapping("/org/{childOrgId}/auth")
    public R<?> saveOrgAuth(
            @PathVariable Long childOrgId,
            @RequestBody  SaveAuthReq req) {
        // 仅 L1-L3 级管理员可配置模块授权
        Integer level = UserContext.getOrgLevel();
        if (!UserContext.isSuperAdmin() && (level == null || level > 3)) {
            return R.fail(403, "仅 L1-L3 级管理员可配置模块授权");
        }
        try {
            moduleAuthService.saveOrgModuleAuth(childOrgId, req.moduleCodes());
            return R.ok("授权配置已保存");
        } catch (IllegalArgumentException e) {
            String msg = e.getMessage();
            if (msg != null && msg.startsWith("MODULE_OVER_GRANT:")) {
                return R.fail(403, "超出自身权限范围，无法授予以下模块：" + msg.substring(18));
            }
            return R.fail(msg);
        }
    }

    record SaveAuthReq(Set<String> moduleCodes) {}
}
