# -*- coding: utf-8 -*-
"""L744(几米 KKS 超长待机)协议自测:用协议文档 V1.2 里的示例报文验证解析。"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import protocol_jimi as j

def hx(s): return bytes.fromhex(s.replace(' ', ''))

CASES = [
    # (名称, 示例报文, 期望 type, 校验点函数)
    ("登录 0x01", "78 78 11 01 07 52 53 36 78 90 02 42 70 00 32 01 00 05 12 79 0D 0A",
     'register', lambda r: r.get('imei')),
    ("心跳 0x23", "78 78 0B 23 C0 01 22 04 00 01 00 08 18 72 0D 0A",
     'heartbeat', lambda r: r.get('voltage')),
    ("校时 0x8A", "78 78 05 8A 00 06 88 29 0D 0A",
     'time_sync', lambda r: True),
    ("定位 0xA0", "78 78 29 a0 15 06 16 01 0f 1a cf 03 c8 13 6c 0c 31 ad e0 28 15 64 01 cc 00 00 00 39 13 00 00 00 00 03 d1 aa 0c 01 00 00 00 bb 88 d6 0d 0a",
     'location', lambda r: True),
    ("报警 0xA4", "78 78 2d a4 15 06 16 01 0e 34 cf 05 07 79 58 0d b1 b9 40 04 14 29 10 01 cc 00 00 00 45 2a 00 00 00 00 01 61 da 72 52 06 04 02 01 ff 02 8c 9b 53 0d 0a",
     'alarm', lambda r: True),
]

print("=" * 60)
print("L744 / KKS 协议自测(示例报文来自协议文档 V1.2)")
print("=" * 60)
allok = True
for name, hexs, want_type, check in CASES:
    frame = hx(hexs)
    r = j.parse(frame)
    crc_ok = r.get('checksum_ok')
    typ = r.get('type')
    type_ok = (typ == want_type)
    field_ok = False
    try:
        field_ok = bool(check(r))
    except Exception as e:
        field_ok = False
    ok = crc_ok and type_ok and field_ok
    allok = allok and ok
    flag = "✓" if ok else "✗"
    print("\n[%s] %s" % (flag, name))
    print("    CRC 校验 : %s" % ("通过" if crc_ok else "失败"))
    print("    类型     : %s (期望 %s) %s" % (typ, want_type, "OK" if type_ok else "不符"))
    # 打印关键字段
    show = {k: r[k] for k in ('imei','voltage','battery_pct','signal','lat','lng','speed',
                              'course','gps_fixed','alarm_code','alarm_text','fence_no',
                              'datetime','sats') if k in r}
    for k, v in show.items():
        if isinstance(v, float): v = round(v, 6)
        print("    %-9s: %s" % (k, v))

# 下行帧构造自测:构造后再解析,验证自洽
print("\n" + "=" * 60)
print("下行帧构造自测(构造→回读 CRC)")
print("=" * 60)
for name, builder in [
    ("登录回复 0x01", lambda: j.build_login_reply(5)),
    ("心跳回复 0x23", lambda: j.build_heartbeat_reply_23(8)),
    ("校时回复 0x8A", lambda: j.build_time_reply((26, 9, 11, 8, 30, 0), 6)),
    ("指令下发 0x80", lambda: j.build_command("SOS,A,,,15821491622#", 0, 1)),
]:
    fr = builder()
    ok = j.verify(fr)
    allok = allok and ok
    print("[%s] %-14s : %s" % ("✓" if ok else "✗", name, fr.hex()))

# 分帧/粘包自测:把登录+心跳两帧拼一起,验证 split_frames 能切开
print("\n" + "=" * 60)
print("分帧/粘包自测(两帧粘连 → split_frames 切分)")
print("=" * 60)
glue = hx("78 78 11 01 07 52 53 36 78 90 02 42 70 00 32 01 00 05 12 79 0D 0A") + \
       hx("78 78 0B 23 C0 01 22 04 00 01 00 08 18 72 0D 0A")
frames, remain = j.split_frames(glue)
split_ok = (len(frames) == 2 and len(remain) == 0)
allok = allok and split_ok
print("[%s] 切出帧数=%d(期望2) 剩余字节=%d(期望0)" % ("✓" if split_ok else "✗", len(frames), len(remain)))
if len(frames) == 2:
    print("    帧1 type=%s, 帧2 type=%s" % (j.parse(frames[0]).get('type'), j.parse(frames[1]).get('type')))

# 0x94 通用信息包(ASCII 键值,含 ICCID/IMSI)自测
print("\n" + "=" * 60)
print("通用包 0x94 键值提取自测")
print("=" * 60)
# 构造一个 0x94 / sub=0x04 的键值文本包(用 _build 反向造,保证 CRC 正确)
kv_text = b"\x04IMSI=460083929500988;ICCID=898604b910227165291;SOS=,,;"
frame94 = j._build(0x94, kv_text, serial=1)
r94 = j.parse(frame94)
kv_ok = (r94.get('type') == 'info' and r94.get('iccid') == '898604b910227165291'
         and r94.get('imsi') == '460083929500988')
allok = allok and kv_ok
print("[%s] type=%s sub=0x%02X" % ("✓" if kv_ok else "✗", r94.get('type'), r94.get('sub_type') or 0))
print("    iccid=%s" % r94.get('iccid'))
print("    imsi =%s" % r94.get('imsi'))

print("\n" + "=" * 60)
print("总结果: %s" % ("全部通过 ✓" if allok else "存在失败项 ✗"))
print("=" * 60)
sys.exit(0 if allok else 1)
