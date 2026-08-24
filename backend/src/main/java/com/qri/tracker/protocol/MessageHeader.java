package com.qri.tracker.protocol;

import lombok.Data;

/**
 * JT/T 808 消息头（2013 版）
 * <pre>
 * | 消息ID 2B | 消息体属性 2B | 终端手机号 6B(BCD) | 流水号 2B | [分包信息 4B] |
 * </pre>
 * 消息体属性：
 *   bit[0~9]  = 消息体长度
 *   bit[10~11]= 数据加密方式（0=不加密）
 *   bit[13]   = 分包标志
 */
@Data
public class MessageHeader {
    /** 消息 ID */
    private int msgId;
    /** 消息体长度 */
    private int bodyLength;
    /** 数据加密方式 (0=无) */
    private int encryptType;
    /** 是否分包 */
    private boolean subPackage;
    /** 终端手机号（12位字符串，前补0） */
    private String phone;
    /** 消息流水号 */
    private int serialNum;
    /** 分包: 总包数（分包时有效） */
    private int totalPackets;
    /** 分包: 包序号（分包时有效） */
    private int packetSeq;
}
