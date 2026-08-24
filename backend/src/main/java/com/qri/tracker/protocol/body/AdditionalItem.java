package com.qri.tracker.protocol.body;

import lombok.Data;

/**
 * 位置附加信息项 (0x0200 追加部分)
 * 格式: [类型 1B][长度 1B][内容 nB]
 */
@Data
public class AdditionalItem {
    /** 附加信息 ID */
    private int id;
    /** 附加信息内容（原始字节） */
    private byte[] data;

    /**
     * 常见附加信息 ID
     */
    public static final int ID_MILEAGE          = 0x01; // 里程, 4B, 单位 1/10 km
    public static final int ID_FUEL             = 0x02; // 油量, 2B, 单位 1/10 L
    public static final int ID_SPEED_RECORD     = 0x03; // 行驶记录速度, 2B
    public static final int ID_MANUAL_ALARM     = 0x04; // 手动确认报警事件 ID, 2B
    public static final int ID_SIGNAL_STRENGTH  = 0x30; // 无线通信信号强度, 1B
    public static final int ID_GNSS_COUNT       = 0x31; // GNSS 卫星数, 1B

    /** 解析里程（单位：0.1 km） */
    public long getMileage() {
        if (id != ID_MILEAGE || data == null || data.length < 4) return 0;
        return ((data[0] & 0xFFL) << 24) | ((data[1] & 0xFFL) << 16)
             | ((data[2] & 0xFFL) << 8)  |  (data[3] & 0xFFL);
    }

    /** 解析 GNSS 卫星数 */
    public int getGnssCount() {
        if (id != ID_GNSS_COUNT || data == null || data.length < 1) return 0;
        return data[0] & 0xFF;
    }
}
