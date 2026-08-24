package com.qri.tracker.netty.handler;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.qri.tracker.entity.AlarmRecord;
import com.qri.tracker.entity.Device;
import com.qri.tracker.entity.LocationRecord;
import com.qri.tracker.protocol.Message;
import com.qri.tracker.protocol.MessageHeader;
import com.qri.tracker.protocol.MessageId;
import com.qri.tracker.protocol.body.AdditionalItem;
import com.qri.tracker.protocol.body.LocationBody;
import com.qri.tracker.protocol.body.RegisterBody;
import com.qri.tracker.service.AlarmService;
import com.qri.tracker.service.DeviceService;
import com.qri.tracker.service.LocationService;
import com.qri.tracker.session.SessionManager;
import io.netty.channel.Channel;
import io.netty.channel.ChannelHandler;
import io.netty.channel.ChannelHandlerContext;
import io.netty.channel.SimpleChannelInboundHandler;
import io.netty.handler.timeout.IdleStateEvent;
import io.netty.util.AttributeKey;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.messaging.simp.SimpMessagingTemplate;
import org.springframework.stereotype.Component;

import java.io.ByteArrayOutputStream;
import java.nio.charset.Charset;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * JT/T 808 业务处理器（@Sharable，通过 Channel Attribute 保存会话状态）
 *
 * 处理消息：
 *   0x0100 终端注册    → 应答 0x8100（含鉴权码）
 *   0x0102 终端鉴权    → 应答 0x8001
 *   0x0002 心跳        → 应答 0x8001
 *   0x0200 位置上报    → 存库 + WS 推送 + 应答 0x8001
 *   0x0001 终端通用应答 → 记录日志
 */
@Slf4j
@Component
@ChannelHandler.Sharable
public class Jt808ServerHandler extends SimpleChannelInboundHandler<Message> {

    private static final AttributeKey<String>  ATTR_PHONE  = AttributeKey.valueOf("808_phone");
    private static final AttributeKey<Boolean> ATTR_AUTHED = AttributeKey.valueOf("808_authed");

    @Autowired private SessionManager sessionManager;
    @Autowired private DeviceService  deviceService;
    @Autowired private LocationService locationService;
    @Autowired private AlarmService   alarmService;
    @Autowired private SimpMessagingTemplate messagingTemplate;

    private final ObjectMapper om = new ObjectMapper().findAndRegisterModules();

    // ── 连接生命周期 ─────────────────────────────────────────────────────────

    @Override
    public void channelInactive(ChannelHandlerContext ctx) {
        String phone = ctx.channel().attr(ATTR_PHONE).get();
        sessionManager.remove(ctx.channel());
        if (phone != null) {
            deviceService.setOffline(phone);
            log.info("[808] 连接断开: phone={}", phone);
        }
    }

    @Override
    public void userEventTriggered(ChannelHandlerContext ctx, Object evt) {
        if (evt instanceof IdleStateEvent) {
            String phone = ctx.channel().attr(ATTR_PHONE).get();
            log.warn("[808] 连接空闲超时，关闭: phone={}", phone);
            ctx.close();
        }
    }

    @Override
    public void exceptionCaught(ChannelHandlerContext ctx, Throwable cause) {
        log.error("[808] 异常: channel={}", ctx.channel().id().asShortText(), cause);
        ctx.close();
    }

    // ── 消息分发 ─────────────────────────────────────────────────────────────

    @Override
    protected void channelRead0(ChannelHandlerContext ctx, Message msg) {
        switch (msg.getMsgId()) {
            case MessageId.TERMINAL_REGISTER    -> handleRegister(ctx, msg);
            case MessageId.TERMINAL_AUTH        -> handleAuth(ctx, msg);
            case MessageId.HEARTBEAT            -> handleHeartbeat(ctx, msg);
            case MessageId.LOCATION_REPORT      -> handleLocation(ctx, msg);
            case MessageId.TERMINAL_GENERIC_RESP-> handleTerminalResp(ctx, msg);
            default                             -> handleUnknown(ctx, msg);
        }
    }

    // ── 0x0100 终端注册 ───────────────────────────────────────────────────────

    private void handleRegister(ChannelHandlerContext ctx, Message msg) {
        String phone = msg.getPhone();
        log.info("[808] 终端注册: phone={}", phone);

        RegisterBody body = parseRegisterBody(msg.getBody());

        // 查找或创建设备记录
        Device device = deviceService.getByPhone(phone);
        boolean isNew = device == null;
        if (isNew) device = new Device();

        // 生成鉴权码（8位随机字符串）
        String authCode = UUID.randomUUID().toString().replace("-", "").substring(0, 8).toUpperCase();

        device.setPhone(phone);
        device.setManufacturer(body.getManufacturer());
        device.setTerminalModel(body.getTerminalModel());
        device.setTerminalId(body.getTerminalId());
        device.setPlateNo(body.getPlateNo());
        device.setPlateColor((byte) body.getPlateColor());
        device.setAuthCode(authCode);

        if (isNew) deviceService.save(device);
        else       deviceService.updateById(device);

        // 建立会话
        ctx.channel().attr(ATTR_PHONE).set(phone);
        ctx.channel().attr(ATTR_AUTHED).set(false); // 注册后需鉴权
        sessionManager.register(phone, ctx.channel());

        // 应答 0x8100
        byte[] respBody = buildRegisterRespBody(msg.getSerialNum(), MessageId.REG_OK, authCode);
        ctx.writeAndFlush(buildMessage(phone, MessageId.TERMINAL_REGISTER_RESP, respBody));
    }

    // ── 0x0102 终端鉴权 ───────────────────────────────────────────────────────

    private void handleAuth(ChannelHandlerContext ctx, Message msg) {
        String phone    = msg.getPhone();
        byte[] body     = msg.getBody();
        String authCode = body.length > 1 ? new String(body, 1, body[0] & 0xFF) : "";

        log.info("[808] 终端鉴权: phone={} authCode={}", phone, authCode);

        Device device = deviceService.getByPhone(phone);
        int result;

        if (device == null) {
            result = MessageId.RESULT_FAIL;
            log.warn("[808] 鉴权失败: 设备不存在 phone={}", phone);
        } else if (!authCode.equals(device.getAuthCode())) {
            result = MessageId.RESULT_FAIL;
            log.warn("[808] 鉴权失败: authCode 不匹配 phone={}", phone);
        } else {
            result = MessageId.RESULT_OK;
            ctx.channel().attr(ATTR_PHONE).set(phone);
            ctx.channel().attr(ATTR_AUTHED).set(true);
            sessionManager.register(phone, ctx.channel());
            deviceService.setOnline(phone);
            log.info("[808] 鉴权成功: phone={}", phone);
        }

        ctx.writeAndFlush(buildPlatformGenericResp(phone, msg.getSerialNum(), MessageId.TERMINAL_AUTH, result));
    }

    // ── 0x0002 心跳 ──────────────────────────────────────────────────────────

    private void handleHeartbeat(ChannelHandlerContext ctx, Message msg) {
        log.debug("[808] 心跳: phone={}", msg.getPhone());
        ctx.writeAndFlush(buildPlatformGenericResp(
                msg.getPhone(), msg.getSerialNum(), MessageId.HEARTBEAT, MessageId.RESULT_OK));
    }

    // ── 0x0200 位置信息汇报 ───────────────────────────────────────────────────

    private void handleLocation(ChannelHandlerContext ctx, Message msg) {
        String phone = msg.getPhone();
        if (phone == null) phone = ctx.channel().attr(ATTR_PHONE).get();

        LocationBody loc = parseLocationBody(msg.getBody());
        if (loc == null) {
            log.error("[808] 位置解析失败: phone={}", phone);
            return;
        }

        log.debug("[808] 位置上报: phone={} lat={} lng={} speed={}km/h alarm={}",
                phone, loc.getLat(), loc.getLng(), loc.getSpeedKmh(), loc.getAlarmFlag());

        // 存储位置记录
        Device device = deviceService.getByPhone(phone);
        Long deviceId = device != null ? device.getId() : 0L;

        LocationRecord record = new LocationRecord();
        record.setDeviceId(deviceId);
        record.setPhone(phone);
        record.setLat(loc.getLat());
        record.setLng(loc.getLng());
        record.setAltitude(loc.getAltitude());
        record.setSpeed(loc.getSpeed());
        record.setDirection(loc.getDirection());
        record.setAlarmFlag(loc.getAlarmFlag());
        record.setStatusFlag(loc.getStatusFlag());
        record.setMileage(loc.getMileage());
        record.setGpsTime(loc.getGpsTime());
        locationService.save(record);

        // 更新设备最新位置
        deviceService.updateLocation(phone, loc);

        // 处理报警
        if (loc.hasAlarm()) {
            handleAlarms(phone, deviceId, loc);
        }

        // WebSocket 推送到前端（/topic/location）
        pushLocationToFrontend(phone, loc);

        // 应答平台通用应答
        ctx.writeAndFlush(buildPlatformGenericResp(
                phone, msg.getSerialNum(), MessageId.LOCATION_REPORT, MessageId.RESULT_OK));
    }

    // ── 0x0001 终端通用应答 ───────────────────────────────────────────────────

    private void handleTerminalResp(ChannelHandlerContext ctx, Message msg) {
        byte[] body = msg.getBody();
        if (body.length >= 5) {
            int ackSerial = ((body[0] & 0xFF) << 8) | (body[1] & 0xFF);
            int ackMsgId  = ((body[2] & 0xFF) << 8) | (body[3] & 0xFF);
            int result    = body[4] & 0xFF;
            log.debug("[808] 终端应答: phone={} ackSerial={} ackMsgId=0x{} result={}",
                    msg.getPhone(), ackSerial, String.format("%04X", ackMsgId), result);
        }
    }

    private void handleUnknown(ChannelHandlerContext ctx, Message msg) {
        log.debug("[808] 未知消息 ID=0x{}, phone={}",
                String.format("%04X", msg.getMsgId()), msg.getPhone());
        ctx.writeAndFlush(buildPlatformGenericResp(
                msg.getPhone(), msg.getSerialNum(), msg.getMsgId(), MessageId.RESULT_UNSUPPORTED));
    }

    // ── 解析工具 ─────────────────────────────────────────────────────────────

    private RegisterBody parseRegisterBody(byte[] data) {
        RegisterBody body = new RegisterBody();
        if (data == null || data.length < 16) return body;
        int offset = 0;
        body.setProvinceId(readUint16(data, offset)); offset += 2;
        body.setCityId(readUint16(data, offset));     offset += 2;
        body.setManufacturer(new String(data, offset, 5).trim()); offset += 5;
        body.setTerminalModel(new String(data, offset, 8).trim()); offset += 8;
        body.setTerminalId(new String(data, offset, 7).trim());    offset += 7;
        body.setPlateColor(data[offset] & 0xFF);                   offset += 1;
        if (offset < data.length) {
            try {
                body.setPlateNo(new String(data, offset, data.length - offset,
                        Charset.forName("GBK")).trim());
            } catch (Exception e) {
                body.setPlateNo("");
            }
        }
        return body;
    }

    private LocationBody parseLocationBody(byte[] data) {
        if (data == null || data.length < 28) return null;
        try {
            LocationBody loc = new LocationBody();
            int offset = 0;

            loc.setAlarmFlag(readUint32(data, offset));  offset += 4;
            loc.setStatusFlag(readUint32(data, offset)); offset += 4;

            double lat = readUint32(data, offset) / 1_000_000.0; offset += 4;
            double lng = readUint32(data, offset) / 1_000_000.0; offset += 4;

            // 状态 bit2=南纬, bit3=西经
            if ((loc.getStatusFlag() & 0x04) != 0) lat = -lat;
            if ((loc.getStatusFlag() & 0x08) != 0) lng = -lng;
            loc.setLat(lat);
            loc.setLng(lng);

            loc.setAltitude(readUint16(data, offset));  offset += 2;
            loc.setSpeed(readUint16(data, offset));     offset += 2;
            loc.setDirection(readUint16(data, offset)); offset += 2;

            // BCD 时间 YYMMDDHHmmSS
            loc.setGpsTime(parseBcdTime(data, offset)); offset += 6;

            // 附加信息
            List<AdditionalItem> items = new ArrayList<>();
            while (offset + 2 <= data.length) {
                int id  = data[offset] & 0xFF; offset++;
                int len = data[offset] & 0xFF; offset++;
                if (offset + len > data.length) break;
                AdditionalItem item = new AdditionalItem();
                item.setId(id);
                byte[] itemData = new byte[len];
                System.arraycopy(data, offset, itemData, 0, len);
                item.setData(itemData);
                items.add(item);
                offset += len;
            }
            loc.setAdditionalItems(items);
            return loc;
        } catch (Exception e) {
            log.error("[808] 位置体解析异常", e);
            return null;
        }
    }

    private LocalDateTime parseBcdTime(byte[] data, int offset) {
        int year   = 2000 + bcdToDec(data[offset]);
        int month  = bcdToDec(data[offset + 1]);
        int day    = bcdToDec(data[offset + 2]);
        int hour   = bcdToDec(data[offset + 3]);
        int minute = bcdToDec(data[offset + 4]);
        int second = bcdToDec(data[offset + 5]);
        // 防止非法时间值
        try { return LocalDateTime.of(year, month, day, hour, minute, second); }
        catch (Exception e) { return LocalDateTime.now(); }
    }

    private int bcdToDec(byte b) {
        return ((b & 0xF0) >> 4) * 10 + (b & 0x0F);
    }

    private int readUint16(byte[] data, int offset) {
        return ((data[offset] & 0xFF) << 8) | (data[offset + 1] & 0xFF);
    }

    private long readUint32(byte[] data, int offset) {
        return ((data[offset] & 0xFFL) << 24)
             | ((data[offset + 1] & 0xFFL) << 16)
             | ((data[offset + 2] & 0xFFL) << 8)
             |  (data[offset + 3] & 0xFFL);
    }

    // ── 报警处理 ─────────────────────────────────────────────────────────────

    private void handleAlarms(String phone, Long deviceId, LocationBody loc) {
        long flag = loc.getAlarmFlag();
        String[][] alarmDefs = {
            {"0",  "SOS 紧急报警"},
            {"1",  "超速报警"},
            {"2",  "疲劳驾驶报警"},
            {"8",  "主电源断开"},
            {"25", "车辆碰撞报警"},
            {"26", "车辆侧翻报警"},
        };
        for (String[] def : alarmDefs) {
            int bit = Integer.parseInt(def[0]);
            if ((flag & (1L << bit)) != 0) {
                AlarmRecord alarm = new AlarmRecord();
                alarm.setDeviceId(deviceId);
                alarm.setPhone(phone);
                alarm.setAlarmType(bit);
                alarm.setAlarmDesc(def[1]);
                alarm.setLat(loc.getLat());
                alarm.setLng(loc.getLng());
                alarm.setSpeed(loc.getSpeed());
                alarm.setAlarmTime(loc.getGpsTime() != null ? loc.getGpsTime() : LocalDateTime.now());
                alarm.setStatus((byte) 0);
                alarmService.save(alarm);
                log.warn("[808] 报警! phone={} type={} desc={}", phone, bit, def[1]);
                // WebSocket 推送报警
                try {
                    Map<String, Object> payload = new HashMap<>();
                    payload.put("phone", phone);
                    payload.put("alarmType", bit);
                    payload.put("alarmDesc", def[1]);
                    payload.put("lat", loc.getLat());
                    payload.put("lng", loc.getLng());
                    payload.put("time", alarm.getAlarmTime().toString());
                    messagingTemplate.convertAndSend("/topic/alarm", om.writeValueAsString(payload));
                } catch (Exception e) {
                    log.error("[808] 报警推送失败", e);
                }
            }
        }
    }

    // ── WebSocket 推送 ────────────────────────────────────────────────────────

    private void pushLocationToFrontend(String phone, LocationBody loc) {
        try {
            Map<String, Object> payload = new HashMap<>();
            payload.put("phone",    phone);
            payload.put("lat",      loc.getLat());
            payload.put("lng",      loc.getLng());
            payload.put("speed",    loc.getSpeedKmh());
            payload.put("direction",loc.getDirection());
            payload.put("altitude", loc.getAltitude());
            payload.put("alarm",    loc.hasAlarm());
            payload.put("alarmFlag",loc.getAlarmFlag());
            payload.put("time",     loc.getGpsTime() != null ? loc.getGpsTime().toString() : "");
            messagingTemplate.convertAndSend("/topic/location", om.writeValueAsString(payload));
        } catch (Exception e) {
            log.error("[808] 位置推送失败", e);
        }
    }

    // ── 报文构建工具 ──────────────────────────────────────────────────────────

    /** 构建平台通用应答 0x8001 */
    private Message buildPlatformGenericResp(String phone, int ackSerial, int ackMsgId, int result) {
        byte[] body = {
            (byte) ((ackSerial >> 8) & 0xFF),
            (byte) (ackSerial & 0xFF),
            (byte) ((ackMsgId >> 8) & 0xFF),
            (byte) (ackMsgId & 0xFF),
            (byte) result
        };
        return buildMessage(phone, MessageId.PLATFORM_GENERIC_RESP, body);
    }

    /** 构建终端注册应答 0x8100 */
    private byte[] buildRegisterRespBody(int ackSerial, int result, String authCode) {
        byte[] authBytes = authCode != null ? authCode.getBytes() : new byte[0];
        byte[] body = new byte[3 + authBytes.length];
        body[0] = (byte) ((ackSerial >> 8) & 0xFF);
        body[1] = (byte) (ackSerial & 0xFF);
        body[2] = (byte) result;
        System.arraycopy(authBytes, 0, body, 3, authBytes.length);
        return body;
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
