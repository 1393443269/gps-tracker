package com.qri.tracker.netty.codec;

import com.qri.tracker.protocol.Message;
import com.qri.tracker.protocol.MessageHeader;
import io.netty.buffer.ByteBuf;
import io.netty.channel.ChannelHandlerContext;
import io.netty.handler.codec.MessageToMessageDecoder;
import lombok.extern.slf4j.Slf4j;

import java.util.List;

/**
 * JT/T 808 消息解码器（第二层）
 *
 * 输入：已去除 0x7E 帧和校验字节的 ByteBuf
 * 输出：{@link Message} 对象
 *
 * 帧结构（2013 版）：
 * | 消息ID 2B | 消息属性 2B | 终端手机 6B(BCD) | 流水号 2B | [分包信息 4B] | 消息体 nB |
 */
@Slf4j
public class Jt808Decoder extends MessageToMessageDecoder<ByteBuf> {

    @Override
    protected void decode(ChannelHandlerContext ctx, ByteBuf buf, List<Object> out) {
        try {
            MessageHeader header = new MessageHeader();

            // 消息 ID
            header.setMsgId(buf.readUnsignedShort());

            // 消息体属性
            int props = buf.readUnsignedShort();
            header.setBodyLength(props & 0x3FF);        // bit[0~9]
            header.setEncryptType((props >> 10) & 0x3); // bit[10~11]
            header.setSubPackage(((props >> 13) & 0x1) == 1); // bit13

            // 终端手机号 (6 bytes BCD → 12位字符串)
            byte[] phoneBytes = new byte[6];
            buf.readBytes(phoneBytes);
            header.setPhone(bcdToString(phoneBytes));

            // 消息流水号
            header.setSerialNum(buf.readUnsignedShort());

            // 分包信息（如有）
            if (header.isSubPackage()) {
                header.setTotalPackets(buf.readUnsignedShort());
                header.setPacketSeq(buf.readUnsignedShort());
            }

            // 消息体
            int bodyLen = Math.min(header.getBodyLength(), buf.readableBytes());
            byte[] body = new byte[bodyLen];
            if (bodyLen > 0) {
                buf.readBytes(body);
            }

            Message message = new Message(header, body);
            log.debug("[JT808] 收到消息 ID=0x{} phone={} serial={}",
                    String.format("%04X", header.getMsgId()),
                    header.getPhone(),
                    header.getSerialNum());
            out.add(message);

        } catch (Exception e) {
            log.error("[JT808] 消息解析异常", e);
        } finally {
            buf.release();
        }
    }

    /**
     * BCD 字节数组 → 号码字符串
     * 例：[0x01, 0x38, 0x12, 0x34, 0x56, 0x78] → "013812345678"
     */
    private String bcdToString(byte[] bcd) {
        StringBuilder sb = new StringBuilder(bcd.length * 2);
        for (byte b : bcd) {
            sb.append(String.format("%02x", b & 0xFF));
        }
        // 去掉右侧填充的 'f'
        String s = sb.toString().replaceAll("f+$", "");
        // 去掉左侧填充的 '0'（但保留全是0的情况下的最后一个0）
        return s.replaceAll("^0+(?!$)", "");
    }
}
