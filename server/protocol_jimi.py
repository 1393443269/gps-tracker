"""
几米 EV41 4G 有线标准协议解析模块
====================================
GT06 协议家族(与 G618G 同源,但字节序、校验、帧格式不同):
- 帧格式: 起始位(2) + 包长度(1或2) + 协议号(1) + 内容(N) + 序列号(2) + CRC(2) + 停止位(2)
- 起始位: 0x78 0x78(包长 1 字节) 或 0x79 0x79(包长 2 字节,内容>255 时)
- 停止位: 固定 0x0D 0x0A
- 多字节字段大端(与 G618G 的小端相反)
- 校验: CRC-ITU("包长度"到"序列号",不含起始位与 CRC 本身)
- 经纬度: 原始 4 字节整数转十进制 ÷ 1800000,符号看航向/状态位
- 时间为 UTC,入库需 +8 小时转北京时间(由上层处理)

规格来源: 《4G有线标准协议 v1.1.01》(几米 EV41)
待真机联调核对: 字节偏移、可选字段有无、BCD 半字节顺序。
"""
import struct
import math as _math

START1 = b'\x78\x78'   # 单字节包长
START2 = b'\x79\x79'   # 双字节包长
STOP   = b'\x0D\x0A'


# ── CRC-ITU 校验(查表法,与协议文档附带算法一致)──────────────────────────────
_CRC_TAB = [
    0x0000,0x1189,0x2312,0x329B,0x4624,0x57AD,0x6536,0x74BF,
    0x8C48,0x9DC1,0xAF5A,0xBED3,0xCA6C,0xDBE5,0xE97E,0xF8F7,
    0x1081,0x0108,0x3393,0x221A,0x56A5,0x472C,0x75B7,0x643E,
    0x9CC9,0x8D40,0xBFDB,0xAE52,0xDAED,0xCB64,0xF9FF,0xE876,
    0x2102,0x308B,0x0210,0x1399,0x6726,0x76AF,0x4434,0x55BD,
    0xAD4A,0xBCC3,0x8E58,0x9FD1,0xEB6E,0xFAE7,0xC87C,0xD9F5,
    0x3183,0x200A,0x1291,0x0318,0x77A7,0x662E,0x54B5,0x453C,
    0xBDCB,0xAC42,0x9ED9,0x8F50,0xFBEF,0xEA66,0xD8FD,0xC974,
    0x4204,0x538D,0x6116,0x709F,0x0420,0x15A9,0x2732,0x36BB,
    0xCE4C,0xDFC5,0xED5E,0xFCD7,0x8868,0x99E1,0xAB7A,0xBAF3,
    0x5285,0x430C,0x7197,0x601E,0x14A1,0x0528,0x37B3,0x263A,
    0xDECD,0xCF44,0xFDDF,0xEC56,0x98E9,0x8960,0xBBFB,0xAA72,
    0x6306,0x728F,0x4014,0x519D,0x2522,0x34AB,0x0630,0x17B9,
    0xEF4E,0xFEC7,0xCC5C,0xDDD5,0xA96A,0xB8E3,0x8A78,0x9BF1,
    0x7387,0x620E,0x5095,0x411C,0x35A3,0x242A,0x16B1,0x0738,
    0xFFCF,0xEE46,0xDCDD,0xCD54,0xB9EB,0xA862,0x9AF9,0x8B70,
    0x8408,0x9581,0xA71A,0xB693,0xC22C,0xD3A5,0xE13E,0xF0B7,
    0x0840,0x19C9,0x2B52,0x3ADB,0x4E64,0x5FED,0x6D76,0x7CFF,
    0x9489,0x8500,0xB79B,0xA612,0xD2AD,0xC324,0xF1BF,0xE036,
    0x18C1,0x0948,0x3BD3,0x2A5A,0x5EE5,0x4F6C,0x7DF7,0x6C7E,
    0xA50A,0xB483,0x8618,0x9791,0xE32E,0xF2A7,0xC03C,0xD1B5,
    0x2942,0x38CB,0x0A50,0x1BD9,0x6F66,0x7EEF,0x4C74,0x5DFD,
    0xB58B,0xA402,0x9699,0x8710,0xF3AF,0xE226,0xD0BD,0xC134,
    0x39C3,0x284A,0x1AD1,0x0B58,0x7FE7,0x6E6E,0x5CF5,0x4D7C,
    0xC60C,0xD785,0xE51E,0xF497,0x8028,0x91A1,0xA33A,0xB2B3,
    0x4A44,0x5BCD,0x6956,0x78DF,0x0C60,0x1DE9,0x2F72,0x3EFB,
    0xD68D,0xC704,0xF59F,0xE416,0x90A9,0x8120,0xB3BB,0xA232,
    0x5AC5,0x4B4C,0x79D7,0x685E,0x1CE1,0x0D68,0x3FF3,0x2E7A,
    0xE70E,0xF687,0xC41C,0xD595,0xA12A,0xB0A3,0x8238,0x93B1,
    0x6B46,0x7ACF,0x4854,0x59DD,0x2D62,0x3CEB,0x0E70,0x1FF9,
    0xF78F,0xE606,0xD49D,0xC514,0xB1AB,0xA022,0x92B9,0x8330,
    0x7BC7,0x6A4E,0x58D5,0x495C,0x3DE3,0x2C6A,0x1EF1,0x0F78,
]


def crc_itu(data: bytes) -> int:
    """CRC-ITU(CRC-16/X25):初值 0xFFFF,查表,结果取反。"""
    fcs = 0xFFFF
    for b in data:
        fcs = (fcs >> 8) ^ _CRC_TAB[(fcs ^ b) & 0xFF]
    return (~fcs) & 0xFFFF


def _crc_range(frame: bytes):
    """返回一个完整帧里参与 CRC 的字节区间(包长度→序列号)与帧携带的 CRC 值。
    帧结构: START(2) + LEN(1/2) + [协议号+内容+序列号](=LEN) + CRC(2) + STOP(2)。
    CRC 覆盖 "包长度" 到 "序列号",即 LEN 字段本身 + 其后 LEN 个字节中除末尾 CRC 外的部分。
    实际实现: 覆盖 = frame[2 : -4](去掉起始位2 + CRC2 + 停止位2 剩下的),含 LEN 与协议号内容序列号。
    """
    if frame[:2] == START2:
        # 双字节包长
        body = frame[2:-4]   # LEN(2)+协议号+内容+序列号
        crc  = struct.unpack_from('>H', frame, len(frame) - 4)[0]
    else:
        body = frame[2:-4]   # LEN(1)+协议号+内容+序列号
        crc  = struct.unpack_from('>H', frame, len(frame) - 4)[0]
    return body, crc


def verify(frame: bytes) -> bool:
    """校验完整帧的 CRC。帧太短返回 False。宽容:部分设备可忽略校验(由上层决定是否强校验)。"""
    if len(frame) < 8:
        return False
    try:
        body, crc = _crc_range(frame)
        return crc_itu(body) == crc
    except Exception:
        return False


# ── 帧切分(处理并包/粘包)────────────────────────────────────────────────────
def split_frames(buf: bytes):
    """
    从 TCP 字节流切出完整帧。返回 (frames, remain)。
    帧边界靠 起始位 + 包长度 精确定位,不依赖停止位扫描(内容里可能含 0x0D0A)。
    """
    frames = []
    data = bytes(buf)
    i = 0
    n = len(data)
    while i < n - 1:
        # 定位起始位
        if data[i:i+2] == START1:
            two = False
        elif data[i:i+2] == START2:
            two = True
        else:
            i += 1
            continue
        # 读包长度
        if two:
            if i + 4 > n:
                break   # 长度字段未收全
            length = struct.unpack_from('>H', data, i + 2)[0]
            len_field = 2
        else:
            if i + 3 > n:
                break
            length = data[i + 2]
            len_field = 1
        # 整帧长度 = 起始位2 + 包长字段 + length + 停止位2
        frame_len = 2 + len_field + length + 2
        if i + frame_len > n:
            break   # 半包,等后续字节
        frame = data[i:i + frame_len]
        frames.append(frame)
        i += frame_len
    return frames, data[i:]


# ── 大端小工具 ────────────────────────────────────────────────────────────────
def _u16(b, o):  return struct.unpack_from('>H', b, o)[0]
def _u32(b, o):  return struct.unpack_from('>I', b, o)[0]


def _proto_and_body(frame: bytes):
    """从完整帧取 (协议号, 内容payload, 序列号)。payload 不含协议号/序列号/CRC。"""
    if frame[:2] == START2:
        off = 4          # 起始2 + 包长2
    else:
        off = 3          # 起始2 + 包长1
    msg_id = frame[off]
    # payload 从协议号之后,到 序列号(2)+CRC(2)+停止(2)=6 字节之前
    payload = frame[off + 1 : -6]
    serial  = _u16(frame, len(frame) - 6)
    return msg_id, payload, serial


def _imei_from_bcd(b: bytes) -> str:
    """8 字节 BCD → 15 位 IMEI(首半字节通常为 0,取后 15 位)。"""
    s = ''.join('%02X' % x for x in b)
    return s[-15:] if len(s) >= 15 else s


def _parse_datetime(p, o):
    """6 字节 年月日时分秒(十进制),返回 (dict, UTC 时间元组)。年份为 2000+。"""
    yy, mo, dd, hh, mi, ss = p[o], p[o+1], p[o+2], p[o+3], p[o+4], p[o+5]
    return {'year': 2000 + yy, 'month': mo, 'day': dd,
            'hour': hh, 'minute': mi, 'second': ss}


def _parse_gps(p, o):
    """从偏移 o 解析 GPS 段: 长度/星数(1)+纬度(4)+经度(4)+速度(1)+航向状态(2)。
    返回 (dict, 新偏移)。经纬度 ÷1800000,符号看状态位。"""
    gps_len_sat = p[o]; o += 1
    sats = gps_len_sat & 0x0F
    raw_lat = _u32(p, o); o += 4
    raw_lng = _u32(p, o); o += 4
    speed = p[o]; o += 1
    course_status = _u16(p, o); o += 2
    lat = raw_lat / 1800000.0
    lng = raw_lng / 1800000.0
    # 航向/状态位(bit10=已定位, bit11=经度东西(1东?), bit10..见协议;此处按常见 GT06 约定,
    # 待真机核对): bit11=1 东经,0 西经; bit10=1 北纬,0 南纬; bit12=GPS已定位。
    course = course_status & 0x03FF
    gps_fixed = bool(course_status & 0x1000)
    east = bool(course_status & 0x0800)
    north = bool(course_status & 0x0400)
    if not east: lng = -lng
    if not north: lat = -lat
    return ({'sats': sats, 'lat': lat, 'lng': lng, 'speed': speed,
             'course': course, 'gps_fixed': gps_fixed,
             'raw_lat': raw_lat, 'raw_lng': raw_lng}, o)


# 报警类型编码(协议附录,字节1)
ALARM_MAP = {
    0x00: '正常',     0x01: 'SOS求救',   0x02: '断电报警',   0x03: '震动报警',
    0x04: '进围栏',   0x05: '出围栏',    0x06: '超速报警',   0x09: '位移报警',
    0x0A: '进GPS盲区', 0x0B: '出GPS盲区', 0x0C: '开机报警',   0x0D: 'GPS首次定位',
    0x0E: '外电低电',  0x0F: '外电低电保护', 0x10: '换卡报警', 0x11: '关机报警',
    0x13: '拆卸报警',  0x14: '门报警',    0x15: '低电关机',   0x16: '声控报警',
    0x17: '伪基站报警', 0x18: '开盖报警',  0x19: '内部电池低电',
}

_BAT_LEVEL = {0:'无电',1:'极低',2:'很低',3:'低',4:'中',5:'高',6:'极高'}
_SIG_LEVEL = {0:'无信号',1:'极弱',2:'较弱',3:'良好',4:'强'}


# ── 上报报文解析 ─────────────────────────────────────────────────────────────
def parse(frame: bytes):
    """
    解析一个完整帧。返回 dict:{'msg_id','type','serial','checksum_ok', ...字段}。
    无法解析返回 {'type':'unknown'/'invalid', 'raw'}。
    """
    if len(frame) < 8 or frame[:2] not in (START1, START2):
        return {'type': 'invalid', 'raw': frame.hex()}
    try:
        msg_id, p, serial = _proto_and_body(frame)
    except Exception as e:
        return {'type': 'invalid', 'raw': frame.hex(), 'error': str(e)}

    r = {'msg_id': msg_id, 'serial': serial, 'checksum_ok': verify(frame)}
    try:
        if msg_id == 0x01:        # 登录包
            if len(p) < 8:
                return {**r, 'type': 'register', 'parse_error': 'short payload'}
            imei = _imei_from_bcd(p[0:8])
            r.update(type='register', imei=imei,
                     type_code=_u16(p, 8) if len(p) >= 10 else 0,
                     tz_lang=_u16(p, 10) if len(p) >= 12 else 0)

        elif msg_id == 0x13:      # 心跳包(EV41 等:电压等级为 1 字节 0~6 档)
            if len(p) < 2:
                return {**r, 'type': 'heartbeat', 'parse_error': 'short payload'}
            term_info = p[0]
            bat = p[1] if len(p) >= 2 else 0
            sig = p[2] if len(p) >= 3 else 0
            r.update(type='heartbeat',
                     term_info=term_info,
                     gps_fixed=bool(term_info & 0x40),
                     acc_high=bool(term_info & 0x02),
                     armed=bool(term_info & 0x01),
                     charging=bool(term_info & 0x04),
                     bat_level=bat, bat_text=_BAT_LEVEL.get(bat, '?'),
                     battery_pct=min(100, bat * 100 // 6) if bat <= 6 else bat,
                     signal=sig, signal_text=_SIG_LEVEL.get(sig, '?'))

        elif msg_id == 0x23:      # 心跳包(L744/L745 KKS 超长待机)
            # 负载布局(KKS 协议 2.2.2):终端信息(1) + 电压(2,÷100 得伏特) + GSM 信号(1) + 语言/扩展(2)
            # 示例: 78 78 0B 23 C0 01 22 04 00 01 00 08 <crc> → term=0xC0, 电压=0x0122/100=2.90V, 信号=0x04
            if len(p) < 1:
                return {**r, 'type': 'heartbeat', 'parse_error': 'short payload'}
            term_info = p[0]
            voltage = (_u16(p, 1) / 100.0) if len(p) >= 3 else None   # 单位 V
            sig = p[3] if len(p) >= 4 else 0
            # 单节锂电粗略估算电量百分比(3.3V→0%,4.2V→100%),需真机标定电压-电量曲线
            batt_pct = None
            if voltage is not None:
                batt_pct = max(0, min(100, round((voltage - 3.3) / (4.2 - 3.3) * 100)))
            r.update(type='heartbeat',
                     term_info=term_info,
                     gps_fixed=bool(term_info & 0x40),
                     acc_high=bool(term_info & 0x02),
                     armed=bool(term_info & 0x01),
                     charging=bool(term_info & 0x04),
                     low_bat_alarm=((term_info >> 3) & 0x07) == 0x03,
                     voltage=voltage, battery_pct=batt_pct,
                     signal=sig, signal_text=_SIG_LEVEL.get(sig, '?'))

        elif msg_id == 0x8A:      # 校时请求
            r.update(type='time_sync')

        elif msg_id == 0xA0:      # GPS 定位包
            if len(p) < 6 + 12:
                return {**r, 'type': 'location', 'parse_error': 'short payload'}
            dt = _parse_datetime(p, 0)
            gps, o = _parse_gps(p, 6)
            if not (_math.isfinite(gps['lat']) and _math.isfinite(gps['lng'])):
                return {**r, 'type': 'location', 'valid': False, 'parse_error': 'NaN/Inf'}
            r.update(type='location', datetime=dt, valid=gps['gps_fixed'], **gps)

        elif msg_id in (0xA3, 0xA4, 0xA5):   # 报警包(A4 多围栏,A5 纯LBS)
            r['type'] = 'alarm'
            r['proto'] = msg_id
            if msg_id in (0xA3, 0xA4):
                if len(p) < 6 + 12:
                    return {**r, 'parse_error': 'short payload'}
                dt = _parse_datetime(p, 0)
                gps, o = _parse_gps(p, 6)
                r.update(datetime=dt, **gps)
                # 报警语言字段(字节1=报警类型)在 LBS 段之后,位置随基站长度浮动;
                # 简化:从帧尾部倒推 报警语言(2)。真机联调核对偏移。
                if len(p) >= 2:
                    alarm_code = p[-2]
                    r.update(alarm_code=alarm_code,
                             alarm_text=ALARM_MAP.get(alarm_code, '未知'))
                if msg_id == 0xA4 and len(p) >= 3:
                    r['fence_no'] = p[-3]   # 围栏编号(可选字段)
            else:  # 0xA5 纯 LBS 报警,无 GPS
                if len(p) >= 2:
                    alarm_code = p[-2]
                    r.update(alarm_code=alarm_code,
                             alarm_text=ALARM_MAP.get(alarm_code, '未知'))

        elif msg_id == 0xA1:      # LBS 多基站定位
            r.update(type='lbs', raw=p.hex())

        elif msg_id == 0xA7:      # LBS 地址请求
            r.update(type='lbs_addr_req', raw=p.hex())

        elif msg_id == 0x21:      # 终端在线指令回复
            r.update(type='cmd_ack', raw=p.hex())

        elif msg_id == 0x94:      # 通用信息包(0x04 终端状态同步 / 0x0A ICCID 等)
            sub = p[0] if p else None
            r.update(type='info', sub_type=sub, raw=p.hex())
            # 0x04/0x08/0x22 等为 ASCII 键值文本(形如 "IMSI=...;ICCID=...;SOS=..;FENCE1,..."),
            # 提取常用字段;0x0A 为 16 进制 ICCID(BCD)。真机核对各字段编码。
            if sub in (0x04, 0x08, 0x22, 0xFF) and len(p) > 1:
                try:
                    text = p[1:].split(b'\x00', 1)[0].decode('ascii', errors='ignore')
                    kv = {}
                    for seg in text.split(';'):
                        if '=' in seg:
                            k, v = seg.split('=', 1)
                            kv[k.strip()] = v.strip()
                    r['info_text'] = text
                    r['info_kv'] = kv
                    if 'ICCID' in kv: r['iccid'] = kv['ICCID']
                    if 'IMSI' in kv:  r['imsi'] = kv['IMSI']
                except Exception:
                    pass
            elif sub == 0x0A and len(p) >= 11:
                r['iccid'] = ''.join('%02X' % x for x in p[1:11])

        else:
            r.update(type='unknown', raw=frame.hex())
    except Exception as e:
        r.update(type='parse_error', error=str(e), raw=frame.hex())
    return r


# ── 下发/回复构造(平台→设备)─────────────────────────────────────────────────
def _build(msg_id: int, content: bytes, serial: int = 1, two_byte_len: bool = False) -> bytes:
    """构造一个完整下行帧。content 不含协议号/序列号/CRC。"""
    body = bytes([msg_id]) + content + struct.pack('>H', serial)   # 协议号+内容+序列号
    if two_byte_len or len(body) > 0xFF:
        length = struct.pack('>H', len(body) + 2)   # +2 为 CRC 长度计入
        start = START2
        crc_input = length + body
    else:
        length = bytes([len(body) + 2])
        start = START1
        crc_input = length + body
    crc = struct.pack('>H', crc_itu(crc_input))
    return start + crc_input + crc + STOP


def build_login_reply(serial: int = 1) -> bytes:
    """登录包回复(协议号 0x01,无内容)。设备 5 秒内需收到,否则重发。"""
    return _build(0x01, b'', serial)


def build_heartbeat_reply(serial: int = 1) -> bytes:
    """心跳回复(协议号 0x13,无内容)。EV41 等旧机型用。"""
    return _build(0x13, b'', serial)


def build_heartbeat_reply_23(serial: int = 1) -> bytes:
    """L744/L745 心跳回复(协议号 0x23,无内容)。设备 5 秒内需收到,否则超时重启。"""
    return _build(0x23, b'', serial)


def build_time_reply(dt_tuple, serial: int = 1) -> bytes:
    """校时回复(协议号 0x8A + 6 字节 UTC 年月日时分秒,年份取后两位)。
    dt_tuple = (year, month, day, hour, minute, second)。"""
    y, mo, d, h, mi, s = dt_tuple
    content = bytes([y % 100, mo, d, h, mi, s])
    return _build(0x8A, content, serial)


def build_command(text: str, server_flag: int = 0, serial: int = 1) -> bytes:
    """在线指令下发(协议号 0x80): 指令长度(1) + 服务器标志(4) + 指令内容 + 语言(2)。
    text 为兼容短信的 ASCII 指令,如 'SOS,A,,,158xxxx#'。"""
    body = text.encode('ascii', errors='replace')
    content = bytes([len(body) + 4]) + struct.pack('>I', server_flag) + body + b'\x00\x01'
    return _build(0x80, content, serial)
