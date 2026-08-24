"""
QuecPython / MicroPython  —  JT/T 808-2013 GPS 上报脚本
========================================================
硬件: 移远 EC600x / EC800x 系列（或其他支持 QuecPython 的模组）
协议: JT/T 808-2013  TCP 二进制帧
接入: 平台 TCP 9090 端口

使用步骤:
  1. 修改下方「配置区」参数
  2. 将 _get_gps() 替换为真实 GNSS 读取
  3. 上传到模组并运行: exec(open('device_jt808.py').read())
"""

import usocket
import ustruct as struct
import utime

# ══════════════════════════════════════════════════════════════════
#  配置区  ← 只需改这里
# ══════════════════════════════════════════════════════════════════
SERVER_HOST     = "your.server.ip"   # 平台服务器公网 IP 或域名
SERVER_PORT     = 9090               # JT/T 808 TCP 端口（固定）
PHONE           = "13800000001"      # 设备手机号（需与平台中录入的一致）
REPORT_INTERVAL = 30                 # 位置上报间隔（秒）
# ══════════════════════════════════════════════════════════════════

_serial_no = [0]


# ── 协议工具函数 ──────────────────────────────────────────────────────────────

def _next_serial():
    _serial_no[0] = (_serial_no[0] + 1) & 0xFFFF
    return _serial_no[0]


def _xor(data):
    cs = 0
    for b in data:
        cs ^= b
    return cs


def _escape(data):
    out = bytearray()
    for b in (data if isinstance(data, (bytes, bytearray)) else bytes(data)):
        if b == 0x7E:
            out.extend([0x7D, 0x02])
        elif b == 0x7D:
            out.extend([0x7D, 0x01])
        else:
            out.append(b)
    return bytes(out)


def _unescape(data):
    out = bytearray()
    i = 0
    while i < len(data):
        if data[i] == 0x7D and i + 1 < len(data):
            out.append(0x7D if data[i + 1] == 0x01 else 0x7E)
            i += 2
        else:
            out.append(data[i])
            i += 1
    return bytes(out)


def _phone_to_bcd(phone):
    """手机号 → 6 字节 BCD（左补 0 至 12 位）"""
    padded = phone.zfill(12)[-12:]
    result = bytearray(6)
    for i in range(6):
        result[i] = (int(padded[i * 2]) << 4) | int(padded[i * 2 + 1])
    return bytes(result)


def _encode(msg_id, body=b''):
    """封装完整 808 帧：起始标志 + 转义 + 校验 + 结束标志"""
    props   = len(body) & 0x3FF
    payload = (struct.pack('>HH', msg_id, props)
               + _phone_to_bcd(PHONE)
               + struct.pack('>H', _next_serial())
               + body)
    cs    = _xor(payload)
    frame = payload + bytes([cs])
    return bytes([0x7E]) + _escape(frame) + bytes([0x7E])


def _bcd_time():
    """当前时间 → 6 字节 BCD（YYMMDDHHmmSS）"""
    t = utime.localtime()
    y = t[0] - 2000
    def bcd(n): return ((n // 10) << 4) | (n % 10)
    return bytes([bcd(y), bcd(t[1]), bcd(t[2]), bcd(t[3]), bcd(t[4]), bcd(t[5])])


# ── 报文构造 ──────────────────────────────────────────────────────────────────

def _build_register():
    """0x0100 终端注册"""
    body  = struct.pack('>HH', 0, 0)   # 省ID、市ID
    body += b'QUEC\x00'                # 厂商 5B
    body += b'QP_MOD  '               # 型号 8B
    body += b'QP00001'                # 终端ID 7B
    body += bytes([0])                 # 车牌颜色
    body += 'TEST'.encode('gbk')      # 车牌号
    return _encode(0x0100, body)


def _build_auth(auth_code):
    """0x0102 终端鉴权（JT/T 808-2019 格式：长度字节 + 鉴权码）"""
    code_bytes = auth_code.encode('ascii')
    return _encode(0x0102, bytes([len(code_bytes)]) + code_bytes)


def _build_location(lat, lng, speed_kmh=0, direction=0, altitude=0, alarm=0):
    """
    0x0200 位置信息汇报
    lat/lng: 十进制度（负值表示南纬/西经）
    speed_kmh: km/h（整数）
    direction: 0-359 度
    altitude: 米
    """
    status = 0x02                                  # bit1=1: 已定位
    if lat < 0: status |= 0x04                    # 南纬
    if lng < 0: status |= 0x08                    # 西经

    raw_lat = int(abs(lat) * 1_000_000)
    raw_lng = int(abs(lng) * 1_000_000)

    body  = struct.pack('>II', alarm, status)
    body += struct.pack('>II', raw_lat, raw_lng)
    body += struct.pack('>H', altitude)
    body += struct.pack('>H', speed_kmh * 10)      # 单位 0.1 km/h
    body += struct.pack('>H', direction)
    body += _bcd_time()
    return _encode(0x0200, body)


def _build_heartbeat():
    """0x0002 终端心跳"""
    return _encode(0x0002)


# ── GPS 数据获取（替换为真实模组 API） ────────────────────────────────────────

def _get_gps():
    """
    返回: (lat, lng, speed_kmh, direction, altitude)

    ── 替换示例（移远 EC600x GNSS）────────────────────────────────────────────
    from gnss import GnssGetData
    info = GnssGetData()        # 返回包含 lat/lon/speed 的对象
    if info and info.lat != 0:
        return info.lat, info.lon, int(info.speed), int(info.course), int(info.altitude)
    return None                 # GPS 未定位时返回 None
    ──────────────────────────────────────────────────────────────────────────
    """
    # 测试坐标（北京天安门），请替换为真实 GNSS 读取
    return 39.9042, 116.4074, 0, 0, 50


# ── 接收一个完整 808 帧 ────────────────────────────────────────────────────────

def _recv_frame(sock):
    buf = bytearray()
    try:
        for _ in range(50):                        # 最多等 5 秒（50 × 100ms）
            try:
                chunk = sock.recv(256)
                if chunk:
                    buf.extend(chunk)
            except Exception:
                pass
            if 0x7E in buf:
                start = buf.index(0x7E)
                rest  = bytearray(buf[start + 1:])
                if 0x7E in rest:
                    end = rest.index(0x7E)
                    return _unescape(bytes(rest[:end]))
            utime.sleep_ms(100)
    except Exception:
        pass
    return None


# ── 主循环 ────────────────────────────────────────────────────────────────────

def run():
    auth_code = PHONE[-6:]   # 默认 auth（注册成功后覆盖）
    sock = None

    while True:
        try:
            # 1. 建立 TCP 连接
            print("[808] 连接 {}:{} ...".format(SERVER_HOST, SERVER_PORT))
            sock = usocket.socket(usocket.AF_INET, usocket.SOCK_STREAM)
            sock.settimeout(30)
            addr = usocket.getaddrinfo(SERVER_HOST, SERVER_PORT)[0][-1]
            sock.connect(addr)
            print("[808] 已连接")

            # 2. 注册 0x0100
            sock.sendall(_build_register())
            resp = _recv_frame(sock)
            # 0x8100 帧结构（含 12B 头 + body + 1B 校验）:
            #   [0:2]  msg_id=0x8100
            #   [2:4]  属性
            #   [4:10] phone BCD
            #   [10:12] 流水号
            #   [12:14] ack_serial（body 开始）
            #   [14]   result (0=成功)
            #   [15:-1] auth_code
            #   [-1]   XOR 校验字节（_recv_frame 未剥离）
            if resp and len(resp) > 14:
                result = resp[14]
                if result == 0 and len(resp) > 15:
                    auth_code = resp[15:-1].decode('ascii', 'ignore').strip('\x00')
                    print("[808] 注册成功 auth={}".format(auth_code))
                else:
                    print("[808] 注册 result={}，使用默认 auth".format(result))

            # 3. 鉴权 0x0102
            sock.sendall(_build_auth(auth_code))
            _recv_frame(sock)
            print("[808] 鉴权完成，开始上报位置")

            # 4. 循环上报
            tick = 0
            while True:
                gps = _get_gps()
                if gps:
                    lat, lng, speed, direction, altitude = gps
                    sock.sendall(_build_location(lat, lng, speed, direction, altitude))
                    _recv_frame(sock)
                    print("[808] 上报 lat={:.6f} lng={:.6f} spd={}km/h".format(lat, lng, speed))
                else:
                    print("[808] GPS 未定位，跳过本次上报")

                # 每 60s 额外发一次心跳（避免 REPORT_INTERVAL≥61 时除数为零）
                tick += 1
                _hb_every = max(1, 60 // REPORT_INTERVAL)
                if tick % _hb_every == 0:
                    sock.sendall(_build_heartbeat())

                utime.sleep(REPORT_INTERVAL)

        except Exception as e:
            print("[808] 异常: {} — 10s 后重连".format(e))
            if sock:
                try: sock.close()
                except: pass
                sock = None
            utime.sleep(10)


# 脚本入口
run()
