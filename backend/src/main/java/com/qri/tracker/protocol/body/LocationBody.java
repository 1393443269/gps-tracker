package com.qri.tracker.protocol.body;

import lombok.Data;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;

/**
 * 0x0200 位置信息汇报 解析结果
 * <pre>
 * | 报警标志 4B | 状态 4B | 纬度 4B | 经度 4B | 海拔 2B | 速度 2B | 方向 2B | 时间 6B(BCD) | [附加信息...] |
 * </pre>
 *
 * 状态位定义（部分）：
 *   bit0=ACC 开/关  bit1=定位/未定位  bit2=纬度S/N  bit3=经度W/E
 *
 * 报警标志位（部分）：
 *   bit0=紧急报警(SOS)  bit1=超速  bit8=主电源断  bit25=碰撞  bit26=侧翻
 */
@Data
public class LocationBody {

    // ── 基本字段 ──────────────────────────────────────────────────────────────
    private long alarmFlag;        // 报警标志（32位，用 long 避免符号扩展）
    private long statusFlag;       // 状态位
    private double lat;            // 纬度（已处理南北符号，WGS-84）
    private double lng;            // 经度（已处理东西符号，WGS-84）
    private int altitude;          // 海拔（米）
    private int speed;             // 速度（单位 0.1 km/h）
    private int direction;         // 方向（0-359°，正北为 0）
    private LocalDateTime gpsTime; // GPS 定位时间

    // ── 附加信息 ──────────────────────────────────────────────────────────────
    private List<AdditionalItem> additionalItems = new ArrayList<>();

    // ── 报警标志常用位掩码 ────────────────────────────────────────────────────
    public static final long ALARM_SOS          = 1L;        // bit0
    public static final long ALARM_OVERSPEED    = 1L << 1;   // bit1
    public static final long ALARM_FATIGUE      = 1L << 2;   // bit2
    public static final long ALARM_POWER_CUT    = 1L << 8;   // bit8
    public static final long ALARM_COLLISION    = 1L << 25;  // bit25
    public static final long ALARM_ROLLOVER     = 1L << 26;  // bit26

    /** 是否有报警 */
    public boolean hasAlarm() {
        return alarmFlag != 0;
    }

    /** 是否已定位 */
    public boolean isLocated() {
        return (statusFlag & 0x02) != 0;
    }

    /** 速度（km/h，保留1位小数） */
    public double getSpeedKmh() {
        return speed / 10.0;
    }

    /** 从附加信息中提取里程（0.1km） */
    public long getMileage() {
        return additionalItems.stream()
                .filter(i -> i.getId() == AdditionalItem.ID_MILEAGE)
                .mapToLong(AdditionalItem::getMileage)
                .findFirst()
                .orElse(0L);
    }
}
