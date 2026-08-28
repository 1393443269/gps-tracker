"""
JT/T 808-2013 协议工具库
包含帧提取、编解码、BCD 转换等核心函数
"""
import struct
from datetime import datetime

FLAG   = 0x7E
ESCAPE = 0x7D


# ── 帧处理 ────────────────────────────────────────────────────────────────────

def unescape(data: bytes) -> bytes:
    """反转义: 0x7D01→0x7D, 0x7D02→0x7E"""
    result = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b == ESCAPE and i + 1 < len(data):
            nxt = data[i + 1]
            if nxt == 0x01:
                result.append(ESCAPE)
            elif nxt == 0x02:
                result.append(FLAG)
            else:
                result.append(b)
                result.append(nxt)
            i += 2
        else:
            result.append(b)
            i += 1
    return bytes(result)


def escape(data: bytes) -> bytes:
    """转义: 0x7E→0x7D02, 0x7D→0x7D01"""
    result = bytearray()
    for b in data:
        if b == FLAG:
            result.extend([ESCAPE, 0x02])
        elif b == ESCAPE:
            result.extend([ESCAPE, 0x01])
        else:
            result.append(b)
    return bytes(result)


def xor_checksum(data: bytes) -> int:
    cs = 0
    for b in data:
        cs ^= b
    return cs


def extract_frames(buf: bytearray):
    """
    从 TCP 字节缓冲区提取完整的 808 帧（已校验）
    返回: (frames: list[bytes], remaining: bytearray)
    每个 frame = 去掉首尾 0x7E 与校验字节后的有效载荷
    """
    frames = []
    while True:
        # 1. 找起始 0x7E
        try:
            start = buf.index(FLAG)
        except ValueError:
            buf = bytearray()
            break

        buf = buf[start + 1:]  # 跳过起始标志

        # 2. 跳过连续的 0x7E（帧间隔）
        while buf and buf[0] == FLAG:
            buf = buf[1:]
        if not buf:
            buf = bytearray([FLAG])
            break

        # 3. 找结束 0x7E（跳过转义序列中的 0x7E）
        end = -1
        i = 0
        while i < len(buf):
            if buf[i] == FLAG:
                end = i
                break
            elif buf[i] == ESCAPE and i + 1 < len(buf):
                i += 2   # 跳过转义对
            else:
                i += 1

        if end < 0:
            # 帧尚不完整，回退起始标志等待更多数据
            buf = bytearray([FLAG]) + buf
            break

        # 4. 提取帧内容
        frame_escaped = bytes(buf[:end])
        buf = buf[end + 1:]

        raw = unescape(frame_escaped)

        # 最小帧长: 2(ID)+2(属性)+6(手机)+2(流水号)+1(校验)=13
        if len(raw) < 13:
            continue

        # 5. 校验
        if xor_checksum(raw[:-1]) != raw[-1]:
            continue

        frames.append(raw[:-1])  # 去掉校验字节

    return frames, buf


# ── 报文头解析 ─────────────────────────────────────────────────────────────────

def parse_header(data: bytes) -> dict:
    """解析报文头，返回完整 header 字典（含 body bytes）"""
    msg_id    = struct.unpack('>H', data[0:2])[0]
    props     = struct.unpack('>H', data[2:4])[0]
    body_len  = props & 0x3FF
    sub_pkg   = bool((props >> 13) & 1)
    phone     = bcd_to_phone(data[4:10])
    serial    = struct.unpack('>H', data[10:12])[0]

    if sub_pkg:
        if len(data) < 16:
            raise ValueError(f'子包帧数据不足 16 字节(实际 {len(data)} 字节)')
        body_start = 16
    else:
        body_start = 12
    if body_start + body_len > len(data):
        raise ValueError(f'body_len {body_len} 超出帧长度 {len(data)}')
    body = data[body_start: body_start + body_len]

    return {
        'msg_id': msg_id,
        'phone':  phone,
        'serial': serial,
        'body':   body,
    }


# ── 报文体解析 ─────────────────────────────────────────────────────────────────

def parse_register_body(body: bytes) -> dict:
    """0x0100 终端注册体"""
    if len(body) < 25:
        return {}
    try:
        manufacturer  = body[4:9].decode('ascii',  errors='replace').strip('\x00').strip()
        terminal_model= body[9:17].decode('ascii', errors='replace').strip('\x00').strip()
        terminal_id   = body[17:24].decode('ascii',errors='replace').strip('\x00').strip()
        plate_color   = body[24] if len(body) > 24 else 0
        plate_no      = body[25:25+64].decode('gbk', errors='replace').strip() if len(body) > 25 else ''
        return {
            'manufacturer':  manufacturer,
            'terminal_model':terminal_model,
            'terminal_id':   terminal_id,
            'plate_color':   plate_color,
            'plate_no':      plate_no,
        }
    except Exception:
        return {}


# ── JT808 附加信息结构化解析 ─────────────────────────────────────────────────

def _parse_extra_wifi(data: bytes) -> dict:
    """0x54 WiFi定位数据: wifi_count(1B) + [mac(6B)+rssi(1B)]*n"""
    result = {'count': 0, 'aps': []}
    if len(data) < 1:
        return result
    count = data[0]
    result['count'] = count
    offset = 1
    for _ in range(count):
        if offset + 7 > len(data):
            break
        mac = ':'.join(f'{b:02X}' for b in data[offset:offset+6])
        rssi = data[offset+6] if data[offset+6] < 128 else data[offset+6] - 256
        result['aps'].append({'mac': mac, 'rssi': rssi})
        offset += 7
    return result


def _parse_extra_lbs(data: bytes) -> dict:
    """0xE1 基站定位数据: mcc(2B)+mnc(1B)+lac(2B)+cell_id(4B)+rssi(1B)"""
    result = {}
    if len(data) < 10:
        return result
    result['mcc'] = struct.unpack('>H', data[0:2])[0]
    result['mnc'] = data[2]
    result['lac'] = struct.unpack('>H', data[3:5])[0]
    result['cell_id'] = struct.unpack('>I', data[5:9])[0]
    rssi = data[9] if data[9] < 128 else data[9] - 256
    result['rssi'] = rssi
    return result


def _parse_extra_beacon(data: bytes) -> dict:
    """0x67 蓝牙信标: beacon_count(1B) + [major(2B)+minor(2B)+rssi(1B)]*n"""
    result = {'count': 0, 'beacons': []}
    if len(data) < 1:
        return result
    count = data[0]
    result['count'] = count
    offset = 1
    for _ in range(count):
        if offset + 5 > len(data):
            break
        major = struct.unpack('>H', data[offset:offset+2])[0]
        minor = struct.unpack('>H', data[offset+2:offset+4])[0]
        rssi = data[offset+4] if data[offset+4] < 128 else data[offset+4] - 256
        result['beacons'].append({'major': major, 'minor': minor, 'rssi': rssi})
        offset += 5
    return result


def _parse_extra_rtk(data: bytes) -> dict:
    """0xE3 RTK高精度信息: status(1B)+lat(4B)+lng(4B)+alt(4B)"""
    result = {}
    if len(data) < 1:
        return result
    result['status'] = data[0]  # 0=无效,1=单点,2=差分,4=固定解,5=浮点解
    if len(data) >= 13:
        raw_lat = struct.unpack('>I', data[1:5])[0]
        raw_lng = struct.unpack('>I', data[5:9])[0]
        result['lat'] = raw_lat / 1_000_000.0
        result['lng'] = raw_lng / 1_000_000.0
        if len(data) >= 13:
            result['altitude'] = struct.unpack('>I', data[9:13])[0]
    return result


def _parse_extra_battery(data: bytes) -> dict:
    """0xFB 电池信息: level(1B)+voltage(2B)+charge_state(1B)"""
    result = {}
    if len(data) >= 1:
        result['level'] = data[0]
    if len(data) >= 3:
        result['voltage'] = struct.unpack('>H', data[1:3])[0]
    if len(data) >= 4:
        result['charge_state'] = data[3]  # 0=未充电,1=充电中,2=已充满
    return result


def parse_location_body(body: bytes):
    """
    0x0200 位置信息汇报体
    返回解析后的字典，或 None（数据太短/解析失败）
    """
    if len(body) < 28:
        return None
    try:
        alarm_flag  = struct.unpack('>I', body[0:4])[0]
        status_flag = struct.unpack('>I', body[4:8])[0]
        raw_lat     = struct.unpack('>I', body[8:12])[0]
        raw_lng     = struct.unpack('>I', body[12:16])[0]
        altitude    = struct.unpack('>H', body[16:18])[0]
        speed       = struct.unpack('>H', body[18:20])[0]   # 单位 0.1 km/h
        direction   = struct.unpack('>H', body[20:22])[0]
        gps_time    = _parse_bcd_time(body, 22)

        lat = raw_lat / 1_000_000.0
        lng = raw_lng / 1_000_000.0
        if status_flag & 0x04: lat = -lat   # 南纬
        if status_flag & 0x08: lng = -lng   # 西经

        # 解析附加信息（扩展）
        mileage = None
        wifi_data = None       # 0x54 WiFi
        lbs_data = None        # 0xE1 基站
        beacon_data = None     # 0x67 蓝牙信标
        rtk_data = None        # 0xE3 RTK
        battery_data = None    # 0xFB 电池
        extra_raw = {}         # 所有附加项原始数据
        offset = 28
        while offset + 2 <= len(body):
            item_id  = body[offset];     offset += 1
            item_len = body[offset];     offset += 1
            if offset + item_len > len(body):
                break
            item_data = body[offset: offset + item_len]
            extra_raw[hex(item_id)] = item_data.hex()
            if item_id == 0x01 and item_len == 4:   # 里程 (km, 0.1km)
                mileage = struct.unpack('>I', item_data)[0]
            elif item_id == 0x54 and item_len >= 2:  # WiFi 定位
                wifi_data = _parse_extra_wifi(item_data)
            elif item_id == 0xE1 and item_len >= 2:  # 基站定位
                lbs_data = _parse_extra_lbs(item_data)
            elif item_id == 0x67 and item_len >= 2:  # 蓝牙信标
                beacon_data = _parse_extra_beacon(item_data)
            elif item_id == 0xE3 and item_len >= 4:  # RTK 状态
                rtk_data = _parse_extra_rtk(item_data)
            elif item_id == 0xFB and item_len >= 2:  # 电池信息
                battery_data = _parse_extra_battery(item_data)
            offset += item_len

        return {
            'alarm_flag':  alarm_flag,
            'status_flag': status_flag,
            'lat':         lat,
            'lng':         lng,
            'altitude':    altitude,
            'speed':       speed,
            'direction':   direction,
            'gps_time':    gps_time,
            'mileage':      mileage,
            'wifi_data':    wifi_data,
            'lbs_data':     lbs_data,
            'beacon_data':  beacon_data,
            'rtk_data':     rtk_data,
            'battery_data': battery_data,
            'extra_raw':    extra_raw,
        }
    except Exception:
        return None


def _parse_bcd_time(data: bytes, offset: int) -> datetime:
    """解析 BCD 时间 YYMMDDHHmmSS（6字节）"""
    def b(i): return (data[offset + i] >> 4) * 10 + (data[offset + i] & 0xF)
    try:
        return datetime(2000 + b(0), b(1), b(2), b(3), b(4), b(5))
    except Exception:
        return None  # 不用服务器时间伪造设备时间戳


# ── BCD 工具 ──────────────────────────────────────────────────────────────────

def bcd_to_phone(data: bytes) -> str:
    s = ''.join(f'{b:02x}' for b in data)
    s = s.rstrip('f')
    return s.lstrip('0') or '0'


def phone_to_bcd(phone: str) -> bytes:
    """手机号 → 6 字节 BCD（左补 0 至 12 位）"""
    padded = phone.zfill(12)[-12:]
    result = []
    for i in range(6):
        hi = int(padded[i * 2],     16)
        lo = int(padded[i * 2 + 1], 16)
        result.append((hi << 4) | lo)
    return bytes(result)


# ── 报文编码 ──────────────────────────────────────────────────────────────────

def encode_message(msg_id: int, phone: str, serial: int, body: bytes = b'') -> bytes:
    """编码完整 808 报文（含 0x7E 帧标志）"""
    props   = len(body) & 0x3FF
    payload = (
        struct.pack('>H', msg_id) +
        struct.pack('>H', props) +
        phone_to_bcd(phone) +
        struct.pack('>H', serial) +
        body
    )
    cs    = xor_checksum(payload)
    frame = payload + bytes([cs])
    return bytes([FLAG]) + escape(frame) + bytes([FLAG])


def build_generic_resp(phone: str, serial_out: int, ack_serial: int, ack_msg_id: int, result: int) -> bytes:
    """平台通用应答 0x8001"""
    body = struct.pack('>HHB', ack_serial, ack_msg_id, result)
    return encode_message(0x8001, phone, serial_out, body)


def build_register_resp(phone: str, serial_out: int, ack_serial: int, result: int, auth_code: str = '') -> bytes:
    """终端注册应答 0x8100"""
    body = struct.pack('>HB', ack_serial, result)
    if result == 0 and auth_code:
        body += auth_code.encode('ascii')
    return encode_message(0x8100, phone, serial_out, body)
