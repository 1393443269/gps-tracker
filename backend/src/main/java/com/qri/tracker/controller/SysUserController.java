package com.qri.tracker.controller;

import com.qri.tracker.common.R;
import com.qri.tracker.common.UserContext;
import com.qri.tracker.entity.SysUser;
import com.qri.tracker.service.SysOrgService;
import com.qri.tracker.service.SysUserService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

/**
 * 系统用户管理接口（仅管理员可访问）
 *
 * GET  /api/sys/users?orgId={id}      — 查询指定组织的用户列表
 * POST /api/sys/users                  — 新建用户
 * PUT  /api/sys/users/{id}             — 修改用户基本信息
 * PUT  /api/sys/users/{id}/password    — 重置用户密码
 */
@RestController
@RequestMapping("/api/sys/users")
@CrossOrigin(origins = "*")
@RequiredArgsConstructor
public class SysUserController {

    private final SysUserService userService;
    private final SysOrgService  orgService;

    /** 按组织查用户列表 */
    @GetMapping
    public R<List<SysUser>> list(@RequestParam Long orgId) {
        // 权限校验：只能查自己管辖范围内的组织
        if (!UserContext.isSuperAdmin()) {
            List<Long> scope = orgService.findSubtreeIds(UserContext.getOrgId());
            if (!scope.contains(orgId)) return R.fail(403, "无权查看该组织用户");
        }
        return R.ok(userService.listByOrgId(orgId));
    }

    /** 新建用户 */
    @PostMapping
    public R<SysUser> create(@RequestBody Map<String, Object> body) {
        String username = (String) body.get("username");
        String password = (String) body.get("password");
        if (username == null || password == null) return R.fail("用户名和密码不能为空");

        Long orgId    = body.get("orgId")    != null ? Long.parseLong(body.get("orgId").toString())    : null;
        Integer orgLevel = body.get("orgLevel") != null ? Integer.parseInt(body.get("orgLevel").toString()) : null;
        Integer userType = body.get("userType") != null ? Integer.parseInt(body.get("userType").toString()) : 1;

        // 非超管只能在自己管辖范围内创建用户
        if (!UserContext.isSuperAdmin() && orgId != null) {
            List<Long> scope = orgService.findSubtreeIds(UserContext.getOrgId());
            if (!scope.contains(orgId)) return R.fail(403, "无权在该组织下创建用户");
        }

        SysUser user = new SysUser();
        user.setUsername(username);
        user.setRealName((String) body.get("realName"));
        user.setPhone((String) body.get("phone"));
        user.setOrgId(orgId);
        user.setOrgLevel(orgLevel);
        user.setUserType(userType);
        user.setIsActive(true);

        try {
            return R.ok(userService.createUser(user, password));
        } catch (IllegalArgumentException e) {
            return R.fail(e.getMessage());
        }
    }

    /** 修改用户基本信息 */
    @PutMapping("/{id}")
    public R<?> update(@PathVariable Long id, @RequestBody Map<String, Object> body) {
        SysUser user = userService.getById(id);
        if (user == null) return R.fail(404, "用户不存在");

        if (body.containsKey("realName")) user.setRealName((String) body.get("realName"));
        if (body.containsKey("phone"))    user.setPhone((String) body.get("phone"));
        if (body.containsKey("isActive")) user.setIsActive((Boolean) body.get("isActive"));
        userService.updateById(user);
        return R.ok();
    }

    /** 管理员重置用户密码 */
    @PutMapping("/{id}/password")
    public R<?> resetPassword(@PathVariable Long id, @RequestBody Map<String, String> body) {
        String newPwd = body.get("newPassword");
        if (newPwd == null || newPwd.length() < 6) return R.fail("新密码不能少于6位");
        try {
            userService.resetPassword(id, newPwd);
            return R.ok();
        } catch (IllegalArgumentException e) {
            return R.fail(e.getMessage());
        }
    }
}
