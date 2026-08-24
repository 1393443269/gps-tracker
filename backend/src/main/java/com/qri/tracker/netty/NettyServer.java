package com.qri.tracker.netty;

import io.netty.bootstrap.ServerBootstrap;
import io.netty.channel.Channel;
import io.netty.channel.ChannelFuture;
import io.netty.channel.ChannelOption;
import io.netty.channel.EventLoopGroup;
import io.netty.channel.nio.NioEventLoopGroup;
import io.netty.channel.socket.nio.NioServerSocketChannel;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.DisposableBean;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.stereotype.Component;

/**
 * JT/T 808 TCP 服务器
 * 随 Spring Boot 启动，监听 ${tracker.tcp.port}
 */
@Slf4j
@Component
public class NettyServer implements ApplicationRunner, DisposableBean {

    @Value("${tracker.tcp.port:9090}")
    private int tcpPort;

    @Autowired
    private NettyServerInitializer initializer;

    private EventLoopGroup bossGroup;
    private EventLoopGroup workerGroup;
    private Channel serverChannel;

    @Override
    public void run(ApplicationArguments args) throws Exception {
        bossGroup   = new NioEventLoopGroup(1);
        workerGroup = new NioEventLoopGroup();

        ServerBootstrap b = new ServerBootstrap();
        b.group(bossGroup, workerGroup)
         .channel(NioServerSocketChannel.class)
         .option(ChannelOption.SO_BACKLOG, 256)
         .childOption(ChannelOption.SO_KEEPALIVE, true)
         .childOption(ChannelOption.TCP_NODELAY, true)
         .childHandler(initializer);

        ChannelFuture f = b.bind(tcpPort).sync();
        serverChannel = f.channel();
        log.info("╔══════════════════════════════════════════╗");
        log.info("║  JT/T 808 TCP 服务启动，端口: {}          ║", tcpPort);
        log.info("╚══════════════════════════════════════════╝");
    }

    @Override
    public void destroy() {
        log.info("[NettyServer] 正在关闭 JT/T 808 TCP 服务...");
        if (serverChannel != null) serverChannel.close();
        if (bossGroup   != null) bossGroup.shutdownGracefully();
        if (workerGroup != null) workerGroup.shutdownGracefully();
        log.info("[NettyServer] 服务已停止");
    }
}
