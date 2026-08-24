package com.qri.tracker.entity;

import com.baomidou.mybatisplus.annotation.*;
import lombok.Data;

import java.time.LocalDateTime;

/**
 * 报警记录
 */
@Data
@TableName("alarm_record")
public class AlarmRecord {

    @TableId(type = IdType.AUTO)
    private Long id;

    private Long deviceId;
    private String phone;

    /** 报警类型（报警标志位号，0=SOS，1=超速，8=主电断...） */
    private Integer alarmType;

    /** 报警描述 */
    private String alarmDesc;

    private Double lat;
    private Double lng;
    private Integer speed;

    /** 报警时间 */
    private LocalDateTime alarmTime;

    /** 处理状态: 0=未处理, 1=已处理 */
    private Byte status;

    private String handler;
    private LocalDateTime handleTime;
    private String handleNote;

    @TableField(fill = FieldFill.INSERT)
    private LocalDateTime createdAt;
}
