package com.qri.tracker.netty.codec;

import com.qri.tracker.protocol.Message;
import io.netty.buffer.ByteBuf;
import io.netty.buffer.Unpooled;
import io.netty.channel.ChannelHandler;
import io.netty.channel.ChannelHandlerContext;
import io.netty.handler.codec.MessageToByteEncoder;
import lombok.extern.slf4j.Slf4j;

/**
 * JT/T 808 消息编码器（平台下行）
 *
 * 将 {@link Message} 对象编码为 808 帧：
 *   0x7E [转义后内容] 0x7E
 */
@Slf4j
@ChannelHandler.Sharable
public class Jt808Encoder extends MessageToByteEncoder<Message> {

    @Override
    protected void encode(ChannelHandlerContext ctx, Message msg, ByteBuf out) {
        // 1. 构建原始帧（含校验字节，不含 0x7E 帧标志）
        ByteBuf raw = buildRawFrame(msg);
        // 2. 添加起始标志，转义，添加结束标志
        out.writeByte(0x7E);
        escape(raw, out);
        out.writeByte(0x7E);
        raw.release();

        log.debug("[JT808] 下发消息 ID=0x{} phone={} serial={}",
                String.format("%04X", msg.getMsgId()),
                msg.getPhone(),
                msg.getSerialNum());
    }

    private ByteBuf buildRawFrame(Message msg) {
        byte[] body      = msg.getBody() == null ? new byte[0] : msg.getBody();
        int    bodyLen   = body.length;
        String phone     = msg.getPhone();

        // 消息头: 2(ID) + 2(属性) + 6(手机) + 2(流水号) = 12，再加消息体和校验字节
        ByteBuf buf = Unpooled.buffer(12 + bodyLen + 1);

        // 消息 ID
        buf.writeShort(msg.getMsgId());

        // 消息体属性: bit[0~9]=体长度，不加密，不分包
        buf.writeShort(bodyLen & 0x3FF);

        // 终端手机号 (6 bytes BCD)
        buf.writeBytes(stringToBcd(phone));

        // 流水号
        buf.writeShort(msg.getSerialNum());

        // 消息体
        buf.writeBytes(body);

        // XOR 校验字节
        byte checksum = 0;
        for (int i = 0; i < buf.readableBytes(); i++) {
            checksum ^= buf.getByte(i);
        }
        buf.writeByte(checksum);

        return buf;
    }

    /**
     * 对 src 中每个字节进行转义后写入 dst：
     *   0x7E → 0x7D 0x02
     *   0x7D → 0x7D 0x01
     */
    private void escape(ByteBuf src, ByteBuf dst) {
        while (src.isReadable()) {
            byte b = src.readByte();
            if (b == 0x7E) {
                dst.writeByte(0x7D);
                dst.writeByte(0x02);
            } else if (b == 0x7D) {
                dst.writeByte(0x7D);
                dst.writeByte(0x01);
            } else {
                dst.writeByte(b);
            }
        }
    }

    /**
     * 手机号字符串 → 6 字节 BCD
     * 不足 12 位则左补 '0'，超过 12 位则右截断
     */
    private byte[] stringToBcd(String phone) {
        if (phone == null) phone = "";
        // 左补 0 至 12 位
        String padded = String.format("%12s", phone).replace(' ', '0');
        if (padded.length() > 12) padded = padded.substring(padded.length() - 12);
        byte[] bcd = new byte[6];
        for (int i = 0; i < 6; i++) {
            int hi = Character.digit(padded.charAt(i * 2), 16);
            int lo = Character.digit(padded.charAt(i * 2 + 1), 16);
            bcd[i] = (byte) ((hi << 4) | lo);
        }
        return bcd;
    }
}
