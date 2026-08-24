package com.qri.tracker.service.impl;

import cn.hutool.crypto.digest.BCrypt;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.qri.tracker.entity.SysUser;
import com.qri.tracker.mapper.SysUserMapper;
import com.qri.tracker.service.SysUserService;
import org.springframework.stereotype.Service;

import java.util.List;

@Service
public class SysUserServiceImpl extends ServiceImpl<SysUserMapper, SysUser> implements SysUserService {

    @Override
    public SysUser findByUsername(String username) {
        return lambdaQuery()
                .eq(SysUser::getUsername, username)
                .eq(SysUser::getIsActive, true)
                .one();
    }

    @Override
    public SysUser createUser(SysUser user, String rawPassword) {
        if (findByUsername(user.getUsername()) != null) {
            throw new IllegalArgumentException("用户名已存在：" + user.getUsername());
        }
        user.setPassword(BCrypt.hashpw(rawPassword, BCrypt.gensalt()));
        save(user);
        return user;
    }

    @Override
    public void changePassword(Long userId, String oldRaw, String newRaw) {
        SysUser user = getById(userId);
        if (user == null) throw new IllegalArgumentException("用户不存在");
        if (!BCrypt.checkpw(oldRaw, user.getPassword())) {
            throw new IllegalArgumentException("原密码不正确");
        }
        user.setPassword(BCrypt.hashpw(newRaw, BCrypt.gensalt()));
        updateById(user);
    }

    @Override
    public List<SysUser> listByOrgId(Long orgId) {
        return lambdaQuery()
                .eq(SysUser::getOrgId, orgId)
                .orderByAsc(SysUser::getId)
                .list();
    }

    @Override
    public void resetPassword(Long userId, String newRaw) {
        SysUser user = getById(userId);
        if (user == null) throw new IllegalArgumentException("用户不存在");
        user.setPassword(BCrypt.hashpw(newRaw, BCrypt.gensalt()));
        updateById(user);
    }
}
