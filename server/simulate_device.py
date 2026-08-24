"""
808 协议设备模拟器
模拟一个定位器完整的注册 → 鉴权 → 持续上报位置的流程
用法: python simulate_device.py
"""
import socket
import struct
import time
import random
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
import protocol as p

HOST  = '127.0.0.1'
PORT  = 9090
IMEI  = '860116075786353'          # EC800M 真实 IMEI
PHONE = IMEI[-12:]                 # BCD 字段取后 12 位: '116075786353'

serial_counter = [0]

def next_serial():
    serial_counter[0] = (serial_counter[0] + 1) & 0xFFFF
    return serial_counter[0]

def recv_resp(sock, timeout=3):
    """接收并解析一条平台应答"""
    sock.settimeout(timeout)
    buf = bytearray()
    try:
        while True:
            data = sock.recv(512)
            if not data:
                break
            buf.extend(data)
            frames, buf = p.extract_frames(bytearray(buf))
            if frames:
                hdr = p.parse_header(frames[0])
                return hdr
    except socket.timeout:
        pass
    return None

def build_register():
    """0x0100 终端注册"""
    manufacturer   = b'QUECT'          # 5 bytes
    terminal_model = b'EC800M\x00\x00' # 8 bytes
    terminal_id    = b'SIM0001'        # 7 bytes
    plate_color    = bytes([1])        # 蓝色
    # plate_no 携带完整 IMEI（15 位纯数字），服务端以此作为设备唯一标识
    plate_no       = IMEI.encode('ascii')

    body = (
        struct.pack('>HH', 44, 1) +    # 省 + 市
        manufacturer +
        terminal_model +
        terminal_id +
        plate_color +
        plate_no
    )
    return p.encode_message(0x0100, PHONE, next_serial(), body)

def build_auth(auth_code: str):
    """0x0102 终端鉴权"""
    code_bytes = auth_code.encode('ascii')
    body = bytes([len(code_bytes)]) + code_bytes
    return p.encode_message(0x0102, PHONE, next_serial(), body)

def build_location(lat, lng, speed=0, alarm=False):
    """0x0200 位置信息汇报"""
    alarm_flag  = 0x01 if alarm else 0x00  # bit0 = SOS
    status_flag = 0x02  # bit1 = 已定位
    raw_lat = int(abs(lat) * 1_000_000)
    raw_lng = int(abs(lng) * 1_000_000)
    if lat < 0: status_flag |= 0x04  # 南纬
    if lng < 0: status_flag |= 0x08  # 西经

    now = time.localtime()
    bcd_time = bytes([
        (now.tm_year % 100 // 10) << 4 | (now.tm_year % 10),
        (now.tm_mon  //  10) << 4 | (now.tm_mon  % 10),
        (now.tm_mday //  10) << 4 | (now.tm_mday % 10),
        (now.tm_hour //  10) << 4 | (now.tm_hour % 10),
        (now.tm_min  //  10) << 4 | (now.tm_min  % 10),
        (now.tm_sec  //  10) << 4 | (now.tm_sec  % 10),
    ])

    body = (
        struct.pack('>I', alarm_flag) +
        struct.pack('>I', status_flag) +
        struct.pack('>I', raw_lat) +
        struct.pack('>I', raw_lng) +
        struct.pack('>H', 50) +         # 海拔 50m
        struct.pack('>H', speed * 10) + # 速度 (0.1 km/h)
        struct.pack('>H', 90) +         # 方向（东）
        bcd_time
    )
    return p.encode_message(0x0200, PHONE, next_serial(), body)

def main():
    print(f"[模拟器] 连接 {HOST}:{PORT} ...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((HOST, PORT))
    print("[模拟器] 连接成功!")

    # ── 1. 发送注册 0x0100 ────────────────────────────────────────────────────
    print("[模拟器] 发送注册报文...")
    sock.sendall(build_register())

    resp = recv_resp(sock)
    if resp and resp['msg_id'] == 0x8100:
        body  = resp['body']
        result = body[2] if len(body) > 2 else -1
        auth_code = body[3:].decode('ascii', errors='replace') if len(body) > 3 else ''
        print(f"[模拟器] 注册响应: result={result} auth_code={auth_code}")
    else:
        print("[模拟器] 未收到注册应答，使用默认鉴权码 TEST1234")
        auth_code = 'TEST1234'

    time.sleep(0.5)

    # ── 2. 发送鉴权 0x0102 ───────────────────────────────────────────────────
    print(f"[模拟器] 发送鉴权: auth_code={auth_code}")
    sock.sendall(build_auth(auth_code))

    resp = recv_resp(sock)
    if resp and resp['msg_id'] == 0x8001:
        body   = resp['body']
        result = body[4] if len(body) >= 5 else -1
        print(f"[模拟器] 鉴权响应: result={result} ({'成功' if result == 0 else '失败'})")
    else:
        print("[模拟器] 未收到鉴权应答，继续...")

    time.sleep(0.5)

    # ── 3. 模拟在深圳行驶，每 3 秒上报一次位置 ───────────────────────────────
    print("[模拟器] 开始周期性上报位置（Ctrl+C 停止）...")
    lat = 22.5431  # 深圳
    lng = 114.0579
    report_count = 0

    try:
        while True:
            # 模拟车辆行驶（随机偏移）
            lat += random.uniform(-0.0005, 0.0005)
            lng += random.uniform(-0.0005, 0.0005)
            speed = random.randint(30, 80)

            # 每 10 次上报一次 SOS 报警，测试报警功能
            alarm = (report_count % 10 == 9)

            sock.sendall(build_location(lat, lng, speed, alarm=alarm))
            report_count += 1

            if alarm:
                print(f"[模拟器] ⚠ 第{report_count}次上报 [SOS报警]: lat={lat:.6f} lng={lng:.6f} speed={speed}km/h")
            else:
                print(f"[模拟器] 第{report_count}次上报: lat={lat:.6f} lng={lng:.6f} speed={speed}km/h")

            resp = recv_resp(sock, timeout=2)
            time.sleep(3)

    except KeyboardInterrupt:
        print("\n[模拟器] 已停止")
    finally:
        sock.close()

if __name__ == '__main__':
    main()
