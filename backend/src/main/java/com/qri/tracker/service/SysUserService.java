package com.qri.tracker.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.qri.tracker.entity.SysUser;

import java.util.List;

public interface SysUserService extends IService<SysUser> {

    /** 根据用户名查找用户（用于登录） */
    SysUser findByUsername(String username);

    /** 新建用户（自动 BCrypt 哈希密码） */
    SysUser createUser(SysUser user, String rawPassword);

    /** 修改密码（需验证旧密码） */
    void changePassword(Long userId, String oldRaw, String newRaw);

    /** 查询指定组织的用户列表 */
    List<SysUser> listByOrgId(Long orgId);

    /** 重置密码（管理员操作，无需验证旧密码） */
    void resetPassword(Long userId, String newRaw);
}
