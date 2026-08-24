package com.qri.tracker.common;

/**
 * 当前登录用户的 ThreadLocal 上下文
 * 在 JwtAuthInterceptor.preHandle 中写入，afterCompletion 中清除
 */
public class UserContext {

    private static final ThreadLocal<LoginUser> HOLDER = new ThreadLocal<>();

    public static void set(LoginUser user) {
        HOLDER.set(user);
    }

    public static LoginUser get() {
        return HOLDER.get();
    }

    public static void clear() {
        HOLDER.remove();
    }

    /** 当前用户ID */
    public static Long getUserId() {
        LoginUser u = get();
        return u != null ? u.userId() : null;
    }

    /** 当前用户所属组织ID */
    public static Long getOrgId() {
        LoginUser u = get();
        return u != null ? u.orgId() : null;
    }

    /** 当前用户所属组织层级（1-5） */
    public static Integer getOrgLevel() {
        LoginUser u = get();
        return u != null ? u.orgLevel() : null;
    }

    /**
     * 是否为超级管理员（userType=9）
     * 超管跳过模块权限校验，直接放行所有接口
     */
    public static boolean isSuperAdmin() {
        LoginUser u = get();
        return u != null && u.userType() == 9;
    }

    /**
     * 登录用户信息（不可变记录）
     *
     * @param userId   用户ID
     * @param orgId    所属组织ID
     * @param orgLevel 组织层级
     * @param userType 用户类型 1=普通 9=超管
     * @param username 用户名
     */
    public record LoginUser(
        Long    userId,
        Long    orgId,
        Integer orgLevel,
        Integer userType,
        String  username
    ) {}
}
