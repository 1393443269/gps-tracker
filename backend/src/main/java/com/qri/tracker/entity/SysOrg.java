package com.qri.tracker.entity;

import com.baomidou.mybatisplus.annotation.*;
import lombok.Data;

import java.time.LocalDateTime;
import java.util.List;

/**
 * 五级组织架构实体
 */
@Data
@TableName("sys_org")
public class SysOrg {

    @TableId(type = IdType.AUTO)
    private Long id;

    /** 组织名称 */
    private String orgName;

    /** 父组织ID，根节点（L1总部）为 null */
    private Long parentId;

    /** 层级：1=总部 2=分公司 3=厂区/项目部 4=班组/部门 5=资产/设备/库位 */
    private Integer orgLevel;

    /** 业务编码，如 HQ / SC-01 */
    private String orgCode;

    /** 物化路径，如 /1/3/12/，加速子树查询 */
    private String orgPath;

    /** 同级排序 */
    private Integer sortOrder;

    /** 是否启用 */
    private Boolean isActive;

    /** 子组织列表（非 DB 字段，前端树形结构用） */
    @TableField(exist = false)
    private List<SysOrg> children;

    @TableLogic
    private Integer deleted;

    @TableField(fill = FieldFill.INSERT)
    private LocalDateTime createdAt;

    @TableField(fill = FieldFill.INSERT_UPDATE)
    private LocalDateTime updatedAt;
}
