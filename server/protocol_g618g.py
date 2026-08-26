"""
G618G 4G Cat.1 定位设备 TCP 协议解析模块（上海欧孚）
========================================================
私有协议，与 JT/T 808 不同：
- 帧头 token = 0xBD 0xBD 0xBD 0xBD（上报）；0xF1 连接回复头部是 4 字节时间戳
- Message ID = 1 字节
- 多字节字段小端；经纬度是 IEEE754 double（8字节小端）
- 校验和 = 0xFF - (除校验字节外所有字节之和 & 0xFF)
- 支持并包：一个 TCP 包内多条报文，以 0xBD 0xBD 0xBD 0xBD 循环切分

规格来源：G618G-4GCat.1-tcp通信协议V2.0
"""
import struct

TOKEN = b'\xBD\xBD\xBD\xBD'


# ── 校验 ──────────────────────────────────────────────────────────────────────
def checksum(data: bytes) -> int:
    """0xFF - (所有字节之和 & 0xFF)"""
    return (0xFF - (sum(data) & 0xFF)) & 0xFF


def verify(frame: bytes) -> bool:
    """校验一个完整帧（末字节为校验和）。宽容：帧太短返回 False。"""
    if len(frame) < 2:
        return False
    return checksum(frame[:-1]) == frame[-1]


# ── 帧切分（处理并包）──────────────────────────────────────────────────────────
def split_frames(buf: bytes):
    """
    从 TCP 字节流里切出一个个完整帧（上报帧，0xBD 头）。
    返回 (frames, remain)：frames 是完整帧列表，remain 是剩余未成帧的字节。
    以下一个 TOKEN 或流末尾作为帧边界（协议保证报文不跨包截断）。
    """
    frames = []
    i = buf.find(TOKEN)
    if i < 0:
        return frames, buf[-3:] if len(buf) >= 3 else buf  # 保留可能的半个token
    # 丢弃 token 之前的垃圾
    buf = buf[i:]
    while True:
        # 找下一个 token 作为本帧结束
        nxt = buf.find(TOKEN, len(TOKEN))
        if nxt < 0:
            # 没有下一个 token，剩下的全是本帧（可能不完整）——本协议一包一批完整报文
            frames.append(buf)
            return frames, b''
        frames.append(buf[:nxt])
        buf = buf[nxt:]


# ── 小工具 ────────────────────────────────────────────────────────────────────
def _u16(b, o):  return struct.unpack_from('<H', b, o)[0]
def _i16(b, o):  return struct.unpack_from('<h', b, o)[0]
def _u32(b, o):  return struct.unpack_from('<I', b, o)[0]
def _i32(b, o):  return struct.unpack_from('<i', b, o)[0]
def _u64(b, o):  return struct.unpack_from('<Q', b, o)[0]
def _double(b, o): return struct.unpack_from('<d', b, o)[0]


# ── 上报报文解析 ─────────────────────────────────────────────────────────────
def parse(frame: bytes):
    """
    解析一个上报帧（0xBD 头）。返回 dict：{'msg_id', 'type', ...字段}
    无法解析返回 {'msg_id', 'type':'unknown', 'raw'}。
    """
    if len(frame) < 6 or frame[:4] != TOKEN:
        return {'type': 'invalid', 'raw': frame.hex()}
    msg_id = frame[4]
    p = frame[5:-1]   # payload（去掉 token+msgid 和末尾校验）
    r = {'msg_id': msg_id, 'checksum_ok': verify(frame)}

    try:
        if msg_id == 0xF0:      # 请求连接（含 IMEI）
            imei = _u64(p, 0)
            r.update(type='register', imei=str(imei), version=_u16(p, 8) if len(p) >= 10 else 0)

        elif msg_id == 0xF9:    # 心跳/电量信号
            r.update(type='heartbeat',
                     bat_type=p[0], bat_raw=_u16(p, 1),
                     signal_type=p[3], signal=_i16(p, 4),
                     other_type=p[6], num=_u32(p, 7),
                     timestamp=_u32(p, 11) if len(p) >= 15 else None,
                     battery_pct=_bat_pct(p[0], _u16(p, 1)))

        elif msg_id == 0x02:    # 报警上传-1
            warn = _u16(p, 0)
            r.update(type='alarm', warn_bits=warn, alarms=_decode_warn02(warn),
                     timestamp=_u32(p, 2) if len(p) >= 6 else None)

        elif msg_id == 0x21:    # 报警上传-2（关机类型）
            atype = _u16(p, 0)
            warn = _u32(p, 2)
            r.update(type='alarm2', alarm_type=atype, warn_bits=warn,
                     alarms=_decode_warn21(warn),
                     timestamp=_u32(p, 6) if len(p) >= 10 else None)

        elif msg_id == 0x03:    # GPS/BDS 位置
            lon = _double(p, 0)
            lat = _double(p, 8)
            ns = chr(p[16]); ew = chr(p[17]); st = chr(p[18])
            if ns == 'S': lat = -abs(lat)
            if ew == 'W': lon = -abs(lon)
            r.update(type='location', lat=lat, lng=lon, valid=(st == 'A'),
                     timestamp=_u32(p, 19) if len(p) >= 23 else None)

        elif msg_id == 0xA4:    # WiFi + 基站
            r.update(type='wifi_lbs', **_parse_a4(p))

        elif msg_id == 0xD6:    # 蓝牙信标
            r.update(type='ble', **_parse_d6(p))

        elif msg_id == 0xF3:    # SIM ICCID
            r.update(type='iccid', iccid=p[:10].hex())

        elif msg_id == 0xC3:    # 充电状态（上报）
            r.update(type='charge', status=p[0],
                     status_text={0: '开始充电', 1: '结束充电', 2: '充满'}.get(p[0], '未知'),
                     timestamp=_u32(p, 1) if len(p) >= 5 else None)

        elif msg_id == 0xA9:    # 状态参数（版本）
            r.update(type='version', raw=p.hex())

        elif msg_id == 0xE9:    # 设备状态（频率）
            r.update(type='dev_status',
                     loc_flag=p[3] if len(p) > 3 else None,
                     loc_freq=_u16(p, 4) if len(p) >= 6 else None,
                     health_flag=p[6] if len(p) > 6 else None,
                     health_freq=_u16(p, 7) if len(p) >= 9 else None)

        elif msg_id == 0xC0:    # 下行反馈
            n = p[0]
            r.update(type='cmd_ack', ack_ids=list(p[1:1 + n]))

        else:
            r.update(type='unknown', raw=frame.hex())
    except Exception as e:
        r.update(type='parse_error', error=str(e), raw=frame.hex())
    return r


def _bat_pct(bat_type, val):
    """电量归一化到百分比"""
    if bat_type == 0:   return min(100, (val + 1) * 25)      # 0-3 → 25/50/75/100
    if bat_type == 1:   return min(100, val * 20)            # 0-4 → 0/20/.../100
    if bat_type == 2:   return min(100, val)                 # 0-100
    return val                                               # 电压值


def _decode_warn02(w):
    out = []
    if w & 0x0001: out.append('低电量')
    if w & 0x0002: out.append('SOS报警')
    if w & 0x0004: out.append('关机')
    if w & 0x0080: out.append('SOS取消')
    if w & 0x4000: out.append('跌落报警')
    return out


def _decode_warn21(w):
    out = []
    if w & 0x01: out.append('主动关机')
    if w & 0x02: out.append('低电关机')
    if w & 0x04: out.append('充电关机')
    return out


def _parse_a4(p):
    o = 0
    ts = _u32(p, o); o += 4
    cell_cnt = p[o]; o += 1
    cells = []
    for _ in range(cell_cnt):
        cells.append({'mcc': _u16(p, o), 'mnc': _u16(p, o + 2),
                      'lac': _u16(p, o + 4), 'cell_id': _u32(p, o + 6),
                      'rssi': _i16(p, o + 10)})
        o += 12
    wifi_cnt = p[o]; o += 1
    wifis = []
    for _ in range(wifi_cnt):
        mac = ':'.join('%02X' % b for b in p[o:o + 6])
        wifis.append({'bssid': mac, 'rssi': _i32(p, o + 6)})
        o += 10
    return {'timestamp': ts, 'cells': cells, 'wifis': wifis}


def _parse_d6(p):
    o = 0
    typ = p[o]; o += 1
    groups = p[o]; o += 1
    beacons = []
    ts = None
    for _ in range(groups):
        ts = _u32(p, o); o += 4
        cnt = p[o]; o += 1
        for _ in range(cnt):
            major = _u16(p, o); minor = _u16(p, o + 2)
            rssi = struct.unpack_from('<b', p, o + 4)[0]
            beacons.append({'major': major, 'minor': minor, 'rssi': rssi})
            o += 5
    return {'timestamp': ts, 'beacons': beacons}


# ── 下发指令构造（平台→设备）─────────────────────────────────────────────────
def _build(msg_id: int, payload: bytes = b'') -> bytes:
    frame = TOKEN + bytes([msg_id]) + payload
    return frame + bytes([checksum(frame)])


def build_login_reply(timestamp: int) -> bytes:
    """0xF1 连接回复：时间戳头 + F1 + token + 校验。共 10 字节。"""
    body = struct.pack('<I', timestamp) + b'\xF1' + TOKEN
    return body + bytes([checksum(body)])


def build_heartbeat_reply() -> bytes:
    """F9 心跳的固定回复（让设备保持连接）"""
    return b'\xBD\xBD\xBD\xBD\xF3\x01'


def build_set_freq(interval_min: int) -> bytes:
    """0x17 设置定位上报频率（全天单时段）"""
    seg = bytes([1]) + struct.pack('<H', interval_min) + bytes([0, 0, 23, 59])
    payload = seg + b'\x00' * 21   # 后 3 个时段空
    return _build(0x17, payload)


def build_reboot() -> bytes:
    return _build(0x77, b'\x00')


def build_shutdown() -> bytes:
    return _build(0x77, b'\x01')


def build_set_server_ip(ip: str, port: int) -> bytes:
    """0xC3 改 IP:端口"""
    octets = bytes(int(x) for x in ip.split('.'))
    payload = bytes([1]) + struct.pack('<H', port) + bytes([len(octets)]) + octets
    return _build(0xC3, payload)
