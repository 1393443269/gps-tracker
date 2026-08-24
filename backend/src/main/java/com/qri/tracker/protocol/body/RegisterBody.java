package com.qri.tracker.protocol.body;

import lombok.Data;

/**
 * 0x0100 终端注册 报文体
 * <pre>
 * | 省域 2B | 市域 2B | 制造商 5B(ASCII) | 终端型号 8B(ASCII) | 终端ID 7B(ASCII) | 车牌颜色 1B | 车牌 nB(GBK) |
 * </pre>
 */
@Data
public class RegisterBody {
    private int provinceId;
    private int cityId;
    private String manufacturer;   // 制造商 ID (5 bytes ASCII)
    private String terminalModel;  // 终端型号 (8 bytes ASCII, 右补0x00)
    private String terminalId;     // 终端 ID (7 bytes ASCII, 右补0x00)
    private int plateColor;        // 车牌颜色: 0=无,1=蓝,2=黄,3=黑,4=白
    private String plateNo;        // 车牌号 (GBK)
}
