package com.qri.tracker.entity;

import com.baomidou.mybatisplus.annotation.*;
import lombok.Data;

import java.time.LocalDateTime;

/**
 * 设备信息
 */
@Data
@TableName("device")
public class Device {

    @TableId(type = IdType.AUTO)
    private Long id;

    /** 终端手机号（唯一标识） */
    private String phone;

    /** 设备别名 */
    private String name;

    /** 车牌号 */
    private String plateNo;

    /** 车牌颜色 0=无,1=蓝,2=黄,3=黑,4=白 */
    private Byte plateColor;

    /** 制造商 */
    private String manufacturer;

    /** 终端型号 */
    private String terminalModel;

    /** 终端 ID */
    private String terminalId;

    /** 鉴权码（注册时平台生成） */
    private String authCode;

    /** 状态: 0=离线, 1=在线, 2=报警 */
    private Byte status;

    private LocalDateTime onlineTime;
    private LocalDateTime offlineTime;

    /** 最新纬度 */
    private Double lastLat;

    /** 最新经度 */
    private Double lastLng;

    /** 最新速度（0.1 km/h） */
    private Integer lastSpeed;

    private LocalDateTime lastLocationTime;

    @TableField(fill = FieldFill.INSERT)
    private LocalDateTime createdAt;

    @TableField(fill = FieldFill.INSERT_UPDATE)
    private LocalDateTime updatedAt;
}
