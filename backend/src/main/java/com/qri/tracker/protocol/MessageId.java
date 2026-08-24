package com.qri.tracker.protocol;

/**
 * JT/T 808 消息 ID 常量
 */
public final class MessageId {

    private MessageId() {}

    // ── 终端上行消息 ──────────────────────────────────────────────────────────
    /** 终端通用应答 */
    public static final int TERMINAL_GENERIC_RESP = 0x0001;
    /** 终端心跳 */
    public static final int HEARTBEAT = 0x0002;
    /** 终端注册 */
    public static final int TERMINAL_REGISTER = 0x0100;
    /** 终端注销 */
    public static final int TERMINAL_LOGOUT = 0x0101;
    /** 终端鉴权 */
    public static final int TERMINAL_AUTH = 0x0102;
    /** 位置信息汇报 */
    public static final int LOCATION_REPORT = 0x0200;
    /** 位置信息查询应答 */
    public static final int LOCATION_QUERY_RESP = 0x0201;

    // ── 平台下行消息 ──────────────────────────────────────────────────────────
    /** 平台通用应答 */
    public static final int PLATFORM_GENERIC_RESP = 0x8001;
    /** 终端注册应答 */
    public static final int TERMINAL_REGISTER_RESP = 0x8100;
    /** 查询终端属性 */
    public static final int QUERY_TERMINAL_ATTR = 0x8107;
    /** 文本信息下发 */
    public static final int TEXT_MESSAGE = 0x8300;
    /** 设置圆形区域 */
    public static final int SET_CIRCLE_AREA = 0x8600;
    /** 终端控制(重启/关机/恢复出厂) */
    public static final int TERMINAL_CONTROL = 0x8105;
    /** 临时位置跟踪控制 */
    public static final int LOCATION_TRACK = 0x8202;

    // ── 平台通用应答 Result 枚举 ──────────────────────────────────────────────
    public static final int RESULT_OK           = 0;
    public static final int RESULT_FAIL         = 1;
    public static final int RESULT_MSG_ERROR    = 2;
    public static final int RESULT_UNSUPPORTED  = 3;

    // ── 终端注册应答 Result ───────────────────────────────────────────────────
    public static final int REG_OK              = 0;
    public static final int REG_VEHICLE_EXIST   = 1;
    public static final int REG_NO_VEHICLE      = 2;
    public static final int REG_TERMINAL_EXIST  = 3;
    public static final int REG_NO_TERMINAL     = 4;
}
