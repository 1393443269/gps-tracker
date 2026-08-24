package com.qri.tracker.entity;

import com.baomidou.mybatisplus.annotation.*;
import lombok.Data;

import java.time.LocalDateTime;
import java.util.List;

/**
 * 功能模块注册表实体
 */
@Data
@TableName("sys_module")
public class SysModule {

    @TableId(type = IdType.AUTO)
    private Long id;

    /** 模块唯一编码，如 WH_IN */
    private String moduleCode;

    /** 模块名称 */
    private String moduleName;

    /** 父模块编码，一级模块为 null */
    private String parentCode;

    /** 同级排序 */
    private Integer sortOrder;

    private String description;

    /**
     * 是否为系统级模块（超管直通，不参与授权配置）
     * 0=否（正常授权）1=是（超管直通）
     */
    private Boolean isSystem;

    @TableLogic
    private Integer deleted;

    @TableField(fill = FieldFill.INSERT)
    private LocalDateTime createdAt;

    /** 子模块列表（非数据库字段，树形结构组装时使用） */
    @TableField(exist = false)
    private List<SysModule> children;

    /** 当前组织是否开放此模块（配置页回显时使用） */
    @TableField(exist = false)
    private Boolean enabled;
}
