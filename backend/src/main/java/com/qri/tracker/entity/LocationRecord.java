package com.qri.tracker.entity;

import com.baomidou.mybatisplus.annotation.*;
import lombok.Data;

import java.time.LocalDateTime;

/**
 * 位置记录
 */
@Data
@TableName("location_record")
public class LocationRecord {

    @TableId(type = IdType.AUTO)
    private Long id;

    private Long deviceId;
    private String phone;

    /** 纬度（WGS-84） */
    private Double lat;

    /** 经度（WGS-84） */
    private Double lng;

    /** 海拔（米） */
    private Integer altitude;

    /** 速度（0.1 km/h） */
    private Integer speed;

    /** 方向（0-359°） */
    private Integer direction;

    /** 报警标志位 */
    private Long alarmFlag;

    /** 状态标志位 */
    private Long statusFlag;

    /** 里程（0.1 km） */
    private Long mileage;

    /** GPS 定位时间 */
    private LocalDateTime gpsTime;

    @TableField(fill = FieldFill.INSERT)
    private LocalDateTime createdAt;
}
