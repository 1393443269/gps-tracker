package com.qri.tracker.netty;

import com.qri.tracker.netty.codec.Jt808Decoder;
import com.qri.tracker.netty.codec.Jt808Encoder;
import com.qri.tracker.netty.codec.Jt808FrameDecoder;
import com.qri.tracker.netty.handler.Jt808ServerHandler;
import io.netty.channel.ChannelInitializer;
import io.netty.channel.ChannelPipeline;
import io.netty.channel.socket.SocketChannel;
import io.netty.handler.timeout.IdleStateHandler;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.util.concurrent.TimeUnit;

/**
 * Pipeline 初始化器
 * 每个新连接触发一次，向 pipeline 添加处理器
 */
@Component
public class NettyServerInitializer extends ChannelInitializer<SocketChannel> {

    @Value("${tracker.tcp.idle-timeout:60}")
    private int idleTimeout;

    /** 编码器无状态，可共享 */
    private final Jt808Encoder encoder = new Jt808Encoder();

    @Autowired
    private Jt808ServerHandler serverHandler;

    @Override
    protected void initChannel(SocketChannel ch) {
        ChannelPipeline p = ch.pipeline();

        // 1. 空闲检测：超过 idleTimeout 秒无读操作则触发 IdleStateEvent
        p.addLast(new IdleStateHandler(idleTimeout, 0, 0, TimeUnit.SECONDS));

        // 2. 帧解码（去除 7E 边界 + 反转义 + 校验）
        p.addLast(new Jt808FrameDecoder());

        // 3. 消息解码（ByteBuf → Message）
        p.addLast(new Jt808Decoder());

        // 4. 消息编码（Message → ByteBuf，写出时触发）
        p.addLast(encoder);

        // 5. 业务处理器（@Sharable，通过 Channel Attribute 存储会话状态）
        p.addLast(serverHandler);
    }
}
