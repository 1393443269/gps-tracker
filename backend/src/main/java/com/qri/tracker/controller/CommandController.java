package com.qri.tracker.controller;

import com.qri.tracker.common.R;
import com.qri.tracker.protocol.Message;
import com.qri.tracker.protocol.MessageHeader;
import com.qri.tracker.protocol.MessageId;
import com.qri.tracker.session.SessionManager;
import io.netty.channel.Channel;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;

import java.nio.charset.StandardCharsets;
import java.util.Map;

/**
 * 平台下行指令 API
 *
 * 通过 Netty Channel 向终端设备发送控制指令
 */
@Slf4j
@RestController
@RequestMapping("/api/commands")
@CrossOrigin(origins = "*")
public class CommandController {

    @Autowired private SessionManager sessionManager;

    /**
     * 发送文本信息（0x8300）
     * body: { "phone": "13812345678", "text": "请注意行车安全" }
     */
    @PostMapping("/text")
    public R<?> sendText(@RequestBody Map<String, String> req) {
        String phone = req.get("phone");
        String text  = req.get("text");
        if (phone == null || text == null) return R.fail("phone 和 text 不能为空");

        Channel ch = sessionManager.getChannel(phone);
        if (ch == null || !ch.isActive()) return R.fail(404, "设备不在线: " + phone);

        // 0x8300: [标志 1B][内容 nB(GBK)]
        byte[] textBytes = text.getBytes(StandardCharsets.UTF_8);
        byte[] body = new byte[1 + textBytes.length];
        body[0] = 0x01; // 标志: 显示终端 LCD
        System.arraycopy(textBytes, 0, body, 1, textBytes.length);

        ch.writeAndFlush(buildMessage(phone, MessageId.TEXT_MESSAGE, body));
        log.info("[CMD] 文本下发: phone={} text={}", phone, text);
        return R.ok();
    }

    /**
     * 终端控制（0x8105）
     * body: { "phone": "13812345678", "cmd": 1 }
     * cmd: 1=重启终端, 2=恢复出厂, 3=关机
     */
    @PostMapping("/control")
    public R<?> control(@RequestBody Map<String, Object> req) {
        String phone = (String) req.get("phone");
        int    cmd   = req.get("cmd") != null ? ((Number) req.get("cmd")).intValue() : 0;

        if (phone == null) return R.fail("phone 不能为空");
        Channel ch = sessionManager.getChannel(phone);
        if (ch == null || !ch.isActive()) return R.fail(404, "设备不在线: " + phone);

        // 0x8105 body: [指令字 4B]
        byte[] body = new byte[4];
        body[3] = (byte) cmd;

        ch.writeAndFlush(buildMessage(phone, MessageId.TERMINAL_CONTROL, body));
        log.info("[CMD] 终端控制: phone={} cmd={}", phone, cmd);
        return R.ok();
    }

    /**
     * 临时位置跟踪控制（0x8202）
     * body: { "phone": "...", "interval": 30, "duration": 3600 }
     * interval: 汇报间隔（秒）, duration: 跟踪时长（秒，0=取消跟踪）
     */
    @PostMapping("/track")
    public R<?> track(@RequestBody Map<String, Object> req) {
        String phone    = (String) req.get("phone");
        int    interval = req.get("interval") != null ? ((Number) req.get("interval")).intValue() : 30;
        int    duration = req.get("duration") != null ? ((Number) req.get("duration")).intValue() : 0;

        if (phone == null) return R.fail("phone 不能为空");
        Channel ch = sessionManager.getChannel(phone);
        if (ch == null || !ch.isActive()) return R.fail(404, "设备不在线: " + phone);

        // 0x8202 body: [间隔 2B, 时长 4B]
        byte[] body = {
            (byte) ((interval >> 8) & 0xFF), (byte) (interval & 0xFF),
            (byte) ((duration >> 24) & 0xFF), (byte) ((duration >> 16) & 0xFF),
            (byte) ((duration >> 8) & 0xFF),  (byte) (duration & 0xFF)
        };
        ch.writeAndFlush(buildMessage(phone, MessageId.LOCATION_TRACK, body));
        log.info("[CMD] 位置跟踪: phone={} interval={}s duration={}s", phone, interval, duration);
        return R.ok();
    }

    private Message buildMessage(String phone, int msgId, byte[] body) {
        MessageHeader header = new MessageHeader();
        header.setMsgId(msgId);
        header.setPhone(phone);
        header.setSerialNum(sessionManager.nextSerial());
        header.setBodyLength(body == null ? 0 : body.length);
        return new Message(header, body);
    }
}
