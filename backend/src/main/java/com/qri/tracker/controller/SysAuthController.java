package com.qri.tracker.controller;

import cn.hutool.crypto.digest.BCrypt;
import com.qri.tracker.common.R;
import com.qri.tracker.common.UserContext;
import com.qri.tracker.entity.SysUser;
import com.qri.tracker.service.SysUserService;
import com.qri.tracker.util.JwtUtil;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.time.LocalDateTime;
import java.util.HashMap;
import java.util.Map;

/**
 * 认证接口
 * POST /api/auth/login       — 登录（不需要鉴权）
 * POST /api/auth/logout      — 注销（前端清 token 即可，接口仅作记录）
 * POST /api/auth/change_pwd  — 修改密码
 */
@RestController
@RequestMapping("/api/auth")
@CrossOrigin(origins = "*")
@RequiredArgsConstructor
public class SysAuthController {

    private final SysUserService userService;
    private final JwtUtil        jwtUtil;

    /** 登录 */
    @PostMapping("/login")
    public R<?> login(@RequestBody LoginReq req) {
        SysUser user = userService.findByUsername(req.username());
        if (user == null) {
            return R.fail(401, "用户名或密码错误");
        }
        if (!BCrypt.checkpw(req.password(), user.getPassword())) {
            return R.fail(401, "用户名或密码错误");
        }

        // 更新最后登录时间
        user.setLastLogin(LocalDateTime.now());
        userService.updateById(user);

        String token = jwtUtil.generate(
                user.getId(), user.getOrgId(), user.getOrgLevel(),
                user.getUserType(), user.getUsername());

        Map<String, Object> result = new HashMap<>();
        result.put("token",    token);
        result.put("userId",   user.getId());
        result.put("username", user.getUsername());
        result.put("realName", user.getRealName());
        result.put("orgId",    user.getOrgId());
        result.put("orgLevel", user.getOrgLevel());
        result.put("userType", user.getUserType());

        return R.ok(result);
    }

    /** 注销（前端清 localStorage 即可，服务端无状态） */
    @PostMapping("/logout")
    public R<?> logout() {
        return R.ok("已注销");
    }

    /** 修改密码 */
    @PostMapping("/change_pwd")
    public R<?> changePwd(@RequestBody ChangePwdReq req) {
        Long userId = UserContext.getUserId();
        if (userId == null) return R.fail(401, "未登录");
        try {
            userService.changePassword(userId, req.oldPassword(), req.newPassword());
            return R.ok("密码修改成功");
        } catch (IllegalArgumentException e) {
            return R.fail(e.getMessage());
        }
    }

    // ── 内部请求体记录 ───────────────────────────────────────────────────────
    record LoginReq(String username, String password) {}
    record ChangePwdReq(String oldPassword, String newPassword) {}
}
