package com.qri.tracker.entity;

import com.baomidou.mybatisplus.annotation.*;
import lombok.Data;

import java.time.LocalDateTime;

/**
 * 组织-模块授权表实体
 */
@Data
@TableName("sys_org_module_auth")
public class SysOrgModuleAuth {

    @TableId(type = IdType.AUTO)
    private Long id;

    /** 被授权的组织ID */
    private Long orgId;

    /** 被授权的模块编码 */
    private String moduleCode;

    /** 是否开放 */
    private Boolean isEnabled;

    /** 授权操作方组织ID（0=系统初始化） */
    private Long grantedByOrg;

    /** 授权操作人用户ID */
    private Long grantedByUser;

    /** 授权时间，由 MetaObjectHandler 自动填充（INSERT 时），若未填充则依赖 DB DEFAULT CURRENT_TIMESTAMP */
    @TableField(fill = FieldFill.INSERT)
    private LocalDateTime grantedAt;

    private String remark;
}
