package com.qri.tracker.protocol;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

/**
 * JT/T 808 报文（解码后）
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class Message {
    private MessageHeader header;
    /** 报文体原始字节 */
    private byte[] body;

    public int getMsgId() {
        return header == null ? -1 : header.getMsgId();
    }

    public String getPhone() {
        return header == null ? null : header.getPhone();
    }

    public int getSerialNum() {
        return header == null ? 0 : header.getSerialNum();
    }
}
