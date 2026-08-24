package com.qri.tracker.session;

import io.netty.channel.Channel;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

import java.util.Collections;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * 设备会话管理器
 * 维护 终端手机号 <-> Channel 的映射关系
 */
@Slf4j
@Component
public class SessionManager {

    /** phone -> Channel */
    private final ConcurrentHashMap<String, Channel> phoneToChannel = new ConcurrentHashMap<>();

    /** channelId -> phone */
    private final ConcurrentHashMap<String, String> channelToPhone = new ConcurrentHashMap<>();

    /** 平台下行消息流水号 */
    private final AtomicInteger serialCounter = new AtomicInteger(1);

    // ── 会话维护 ──────────────────────────────────────────────────────────────

    public void register(String phone, Channel channel) {
        phoneToChannel.put(phone, channel);
        channelToPhone.put(channel.id().asShortText(), phone);
        log.info("[SessionManager] 设备上线: phone={} channel={}", phone, channel.id().asShortText());
    }

    public void remove(Channel channel) {
        String channelKey = channel.id().asShortText();
        String phone = channelToPhone.remove(channelKey);
        if (phone != null) {
            phoneToChannel.remove(phone);
            log.info("[SessionManager] 设备下线: phone={} channel={}", phone, channelKey);
        }
    }

    public Channel getChannel(String phone) {
        return phoneToChannel.get(phone);
    }

    public String getPhone(Channel channel) {
        return channelToPhone.get(channel.id().asShortText());
    }

    public boolean isOnline(String phone) {
        Channel ch = phoneToChannel.get(phone);
        return ch != null && ch.isActive();
    }

    public Set<String> onlinePhones() {
        return Collections.unmodifiableSet(phoneToChannel.keySet());
    }

    public int onlineCount() {
        return phoneToChannel.size();
    }

    // ── 工具 ─────────────────────────────────────────────────────────────────

    /** 生成平台下行消息流水号 */
    public int nextSerial() {
        return serialCounter.getAndIncrement() & 0xFFFF;
    }
}
