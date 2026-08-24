package com.qri.tracker.netty.codec;

import io.netty.buffer.ByteBuf;
import io.netty.channel.ChannelHandlerContext;
import io.netty.handler.codec.ByteToMessageDecoder;
import lombok.extern.slf4j.Slf4j;

import java.io.ByteArrayOutputStream;
import java.util.List;

/**
 * JT/T 808 帧解码器（第一层）
 *
 * 职责：
 *   1. 从字节流中识别 0x7E...0x7E 帧
 *   2. 对帧内容进行反转义（0x7D01→0x7D, 0x7D02→0x7E）
 *   3. 校验 XOR 校验和
 *   4. 将有效帧（不含首尾 0x7E 和校验字节）传给下一个处理器
 */
@Slf4j
public class Jt808FrameDecoder extends ByteToMessageDecoder {

    private static final byte FLAG   = 0x7E;
    private static final byte ESCAPE = 0x7D;

    @Override
    protected void decode(ChannelHandlerContext ctx, ByteBuf in, List<Object> out) {
        while (in.isReadable()) {

            // 1. 跳过帧起始 0x7E 之前的杂乱字节
            while (in.isReadable() && in.getByte(in.readerIndex()) != FLAG) {
                in.skipBytes(1);
            }
            if (!in.isReadable()) return;

            // 2. 在当前 0x7E 处打标记（若后续数据不完整则回滚到这里）
            in.markReaderIndex();
            in.skipBytes(1); // 跳过起始 0x7E

            // 3. 跳过连续的 0x7E（两帧之间可能出现多个）
            while (in.isReadable() && in.getByte(in.readerIndex()) == FLAG) {
                in.markReaderIndex();
                in.skipBytes(1);
            }
            if (!in.isReadable()) {
                in.resetReaderIndex();
                return;
            }

            // 4. 读取帧内容，直到遇到下一个 0x7E（结束标志）
            ByteArrayOutputStream baos = new ByteArrayOutputStream(256);
            boolean foundEnd   = false;
            boolean incomplete = false;

            while (in.isReadable()) {
                byte b = in.readByte();
                if (b == FLAG) {
                    foundEnd = true;
                    break;
                } else if (b == ESCAPE) {
                    if (!in.isReadable()) {
                        incomplete = true;
                        break;
                    }
                    byte next = in.readByte();
                    if (next == (byte) 0x01) {
                        baos.write(0x7D);
                    } else if (next == (byte) 0x02) {
                        baos.write(0x7E);
                    } else {
                        // 非法转义序列，原样保留
                        baos.write(ESCAPE & 0xFF);
                        baos.write(next & 0xFF);
                    }
                } else {
                    baos.write(b & 0xFF);
                }
            }

            // 数据不完整，等待更多字节
            if (!foundEnd || incomplete) {
                in.resetReaderIndex();
                return;
            }

            byte[] raw = baos.toByteArray();

            // 最小帧长: 消息ID(2) + 属性(2) + 手机号(6) + 流水号(2) + 校验(1) = 13
            if (raw.length < 13) {
                log.debug("[JT808] 帧太短({}B)，丢弃", raw.length);
                continue;
            }

            // 5. 校验 XOR 校验和（所有字节 XOR 应 == 最后一字节）
            byte checksum = 0;
            for (int i = 0; i < raw.length - 1; i++) {
                checksum ^= raw[i];
            }
            if (checksum != raw[raw.length - 1]) {
                log.warn("[JT808] 校验失败，丢弃帧（计算={} 实际={}）",
                        String.format("%02X", checksum & 0xFF),
                        String.format("%02X", raw[raw.length - 1] & 0xFF));
                continue;
            }

            // 6. 输出有效帧（不含校验字节）
            ByteBuf frameBuf = ctx.alloc().buffer(raw.length - 1);
            frameBuf.writeBytes(raw, 0, raw.length - 1);
            out.add(frameBuf);
        }
    }
}
