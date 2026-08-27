#!/usr/bin/env python3
"""
GPS 设备模拟器入口（桌面/服务器测试用）
========================================
在普通 Python 3 环境中运行，模拟 JT/T 808 或 MQTT 设备上报。
用法:
  python main.py --mode jt808 --host 127.0.0.1 --port 9090 --phone 13800000001
  python main.py --mode mqtt  --host 127.0.0.1 --port 1883 --phone 13800000001
注意: device_jt808.py / device_mqtt.py / tracker_808.py 为 QuecPython/MicroPython
      嵌入式脚本，不能在 CPython 下直接运行。本文件提供桌面测试替代方案。
"""
import argparse
import socket
import struct
import time
import json
import sys

# ── JT/T 808 协议工具 ────────────────────────────────────────────────────────
def _xor(data):
    cs = 0
    for b in data:
        cs ^= b
    return cs

def _escape(data):
    out = bytearray()
    for b in data:
        if b == 0x7E:
            out.extend([0x7D, 0x02])
        elif b == 0x7D:
            out.extend([0x7D, 0x01])
        else:
            out.append(b)
    return bytes(out)

def _phone_to_bcd(phone):
    padded = phone.zfill(12)[-12:]
    result = bytearray(6)
    for i in range(6):
        result[i] = (int(padded[i * 2]) << 4) | int(padded[i * 2 + 1])
    return bytes(result)

def _encode(msg_id, phone, serial, body=b''):
    props = len(body) & 0x3FF
    payload = (struct.pack('>HH', msg_id, props)
               + _phone_to_bcd(phone)
               + struct.pack('>H', serial)
               + body)
    cs = _xor(payload)
    frame = payload + bytes([cs])
    return bytes([0x7E]) + _escape(frame) + bytes([0x7E])

def _bcd_time():
    t = time.localtime()
    y = t.tm_year - 2000
    def bcd(n): return ((n // 10) << 4) | (n % 10)
    return bytes([bcd(y), bcd(t.tm_mon), bcd(t.tm_mday),
                  bcd(t.tm_hour), bcd(t.tm_min), bcd(t.tm_sec)])

def _build_register(phone, serial):
    body = struct.pack('>HH', 0, 0)
    body += b'QUEC\x00'
    body += b'QP_MOD  '
    body += b'QP00001'
    body += bytes([0])
    body += 'TEST'.encode('gbk')
    return _encode(0x0100, phone, serial, body)

def _build_auth(phone, serial, auth_code):
    code_bytes = auth_code.encode('ascii')
    return _encode(0x0102, phone, serial, bytes([len(code_bytes)]) + code_bytes)

def _build_location(phone, serial, lat, lng, speed=0, direction=0, altitude=50):
    status = 0x02
    if lat < 0: status |= 0x04
    if lng < 0: status |= 0x08
    raw_lat = int(abs(lat) * 1_000_000)
    raw_lng = int(abs(lng) * 1_000_000)
    body = struct.pack('>II', 0, status)
    body += struct.pack('>II', raw_lat, raw_lng)
    body += struct.pack('>H', altitude)
    body += struct.pack('>H', speed * 10)
    body += struct.pack('>H', direction)
    body += _bcd_time()
    return _encode(0x0200, phone, serial, body)

def _build_heartbeat(phone, serial):
    return _encode(0x0002, phone, serial)

def _recv_frame(sock, timeout=5):
    sock.settimeout(timeout)
    buf = bytearray()
    try:
        chunk = sock.recv(512)
        if chunk:
            buf.extend(chunk)
        if 0x7E in buf:
            start = buf.index(0x7E)
            rest = bytearray(buf[start + 1:])
            if 0x7E in rest:
                end = rest.index(0x7E)
                return bytes(rest[:end])
    except socket.timeout:
        pass
    return None

# ── JT/T 808 模拟器 ──────────────────────────────────────────────────────────
def run_jt808(host, port, phone, interval):
    serial = [0]
    def nxt():
        serial[0] = (serial[0] + 1) & 0xFFFF
        return serial[0]

    print(f"[JT808] Connecting to {host}:{port} as {phone}")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((host, port))
    print("[JT808] Connected")

    # Register
    sock.sendall(_build_register(phone, nxt()))
    resp = _recv_frame(sock, 10)
    auth_code = phone[-6:]
    if resp and len(resp) > 14:
        result = resp[14]
        if result == 0 and len(resp) > 15:
            auth_code = resp[15:].decode('ascii', 'ignore').strip('\x00')
            print(f"[JT808] Registered OK, auth={auth_code}")
        else:
            print(f"[JT808] Register result={result}")

    # Auth
    sock.sendall(_build_auth(phone, nxt(), auth_code))
    _recv_frame(sock, 5)
    print("[JT808] Authenticated, starting location reports")

    # Location loop
    lat, lng = 39.9042, 116.4074
    tick = 0
    try:
        while True:
            sock.sendall(_build_location(phone, nxt(), lat, lng, speed=30))
            _recv_frame(sock, 2)
            print(f"[JT808] Report lat={lat:.6f} lng={lng:.6f}")
            lat += 0.0001
            lng += 0.0001
            tick += 1
            if tick % max(1, 60 // interval) == 0:
                sock.sendall(_build_heartbeat(phone, nxt()))
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[JT808] Stopped")
    finally:
        sock.close()

# ── MQTT 模拟器 ──────────────────────────────────────────────────────────────
def run_mqtt(host, port, phone, interval):
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        print("[MQTT] paho-mqtt not installed. Install with: pip install paho-mqtt")
        sys.exit(1)

    topic_gps = f"gps/{phone}"
    topic_status = f"gps/{phone}/status"
    client_id = f"sim_{phone}"

    def on_connect(client, userdata, flags, rc):
        print(f"[MQTT] Connected to {host}:{port} (rc={rc})")
        client.publish(topic_status, json.dumps({"phone": phone, "online": True}))

    client = mqtt.Client(client_id=client_id)
    client.on_connect = on_connect
    client.connect(host, port, keepalive=60)
    client.loop_start()

    lat, lng = 39.9042, 116.4074
    try:
        while True:
            payload = json.dumps({
                "phone": phone, "lat": lat, "lng": lng,
                "speed": 30, "direction": 90, "altitude": 50, "alarm": 0
            })
            client.publish(topic_gps, payload)
            print(f"[MQTT] Published lat={lat:.6f} lng={lng:.6f}")
            lat += 0.0001
            lng += 0.0001
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[MQTT] Stopped")
    finally:
        client.publish(topic_status, json.dumps({"phone": phone, "online": False}))
        client.disconnect()

# ── CLI 入口 ──────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='GPS Device Simulator')
    parser.add_argument('--mode', choices=['jt808', 'mqtt'], default='jt808')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=None)
    parser.add_argument('--phone', default='13800000001')
    parser.add_argument('--interval', type=int, default=30)
    args = parser.parse_args()

    if args.port is None:
        args.port = 9090 if args.mode == 'jt808' else 1883

    if args.mode == 'jt808':
        run_jt808(args.host, args.port, args.phone, args.interval)
    else:
        run_mqtt(args.host, args.port, args.phone, args.interval)
