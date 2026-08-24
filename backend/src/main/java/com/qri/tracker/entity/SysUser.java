package com.qri.tracker.entity;

import com.baomidou.mybatisplus.annotation.*;
import com.fasterxml.jackson.annotation.JsonIgnore;
import lombok.Data;

import java.time.LocalDateTime;

/**
 * 系统用户实体
 */
@Data
@TableName("sys_user")
public class SysUser {

    @TableId(type = IdType.AUTO)
    private Long id;

    /** 登录用户名 */
    private String username;

    /** BCrypt 哈希密码（序列化时隐藏） */
    @JsonIgnore
    private String password;

    /** 真实姓名 */
    private String realName;

    /** 所属组织ID */
    private Long orgId;

    /** 继承自组织的层级 1-5 */
    private Integer orgLevel;

    /**
     * 用户类型：
     * 1 = 普通管理员（受模块权限约束）
     * 9 = 超级管理员（L1总部，跳过模块校验）
     */
    private Integer userType;

    private String phone;
    private String email;

    /** 是否启用 */
    private Boolean isActive;

    /** 最后登录时间 */
    private LocalDateTime lastLogin;

    @TableLogic
    private Integer deleted;

    @TableField(fill = FieldFill.INSERT)
    private LocalDateTime createdAt;

    @TableField(fill = FieldFill.INSERT_UPDATE)
    private LocalDateTime updatedAt;
}
