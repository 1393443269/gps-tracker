"""
QuecPython / MicroPython  —  MQTT GPS 上报脚本
==============================================
硬件: 移远 EC600x / EC800x 系列（或其他支持 QuecPython 的模组）
协议: MQTT 3.1.1，JSON payload
接入: 平台 MQTT broker 1883 端口

Topic 约定:
  设备 → 平台:  gps/{phone}          上报 GPS 位置
  设备 → 平台:  gps/{phone}/status   上线/下线状态（可选）

使用步骤:
  1. 修改下方「配置区」参数
  2. 将 _get_gps() 替换为真实 GNSS 读取
  3. 上传到模组并运行: exec(open('device_mqtt.py').read())
"""

import utime
import ujson

# umqtt.simple 是 MicroPython/QuecPython 内置 MQTT 库
from umqtt.simple import MQTTClient

# ══════════════════════════════════════════════════════════════════
#  配置区  ← 只需改这里
# ══════════════════════════════════════════════════════════════════
MQTT_HOST       = "your.server.ip"   # 平台服务器公网 IP 或域名
MQTT_PORT       = 1883               # MQTT broker 端口（固定）
PHONE           = "13800000001"      # 设备唯一标识（与平台中录入的手机号一致）
REPORT_INTERVAL = 30                 # 位置上报间隔（秒）
# ══════════════════════════════════════════════════════════════════

TOPIC_GPS    = "gps/{}".format(PHONE)
TOPIC_STATUS = "gps/{}/status".format(PHONE)
CLIENT_ID    = "qp_{}".format(PHONE)


# ── GPS 数据获取（替换为真实模组 API） ────────────────────────────────────────

def _get_gps():
    """
    返回: (lat, lng, speed_kmh, direction, altitude)
         或 None（GPS 未定位）

    ── 替换示例（移远 EC600x GNSS）────────────────────────────────────────────
    from gnss import GnssGetData
    info = GnssGetData()
    if info and info.lat != 0:
        return info.lat, info.lon, int(info.speed), int(info.course), int(info.altitude)
    return None
    ──────────────────────────────────────────────────────────────────────────
    """
    # 测试坐标（北京天安门），请替换为真实 GNSS 读取
    return 39.9042, 116.4074, 60, 90, 50


# ── 主循环 ────────────────────────────────────────────────────────────────────

def run():
    client = None

    while True:
        try:
            # 1. 建立 MQTT 连接
            print("[MQTT] 连接 {}:{} ...".format(MQTT_HOST, MQTT_PORT))
            client = MQTTClient(
                CLIENT_ID.encode(),
                MQTT_HOST,
                port=MQTT_PORT,
                keepalive=60
            )
            client.connect()
            print("[MQTT] 已连接")

            # 2. 上线状态
            client.publish(
                TOPIC_STATUS.encode(),
                ujson.dumps({"phone": PHONE, "online": True}).encode()
            )

            # 3. 循环上报位置
            ping_tick = 0
            while True:
                gps = _get_gps()

                if gps:
                    lat, lng, speed, direction, altitude = gps
                    payload = ujson.dumps({
                        "phone":     PHONE,
                        "lat":       lat,
                        "lng":       lng,
                        "speed":     speed,
                        "direction": direction,
                        "altitude":  altitude,
                        "alarm":     0,
                    })
                    client.publish(TOPIC_GPS.encode(), payload.encode())
                    print("[MQTT] 上报 lat={:.6f} lng={:.6f} spd={}km/h".format(
                          lat, lng, speed))
                else:
                    print("[MQTT] GPS 未定位，跳过本次上报")

                # MQTT keepalive ping（每 50s 一次）
                ping_tick += REPORT_INTERVAL
                if ping_tick >= 50:
                    client.ping()
                    ping_tick = 0

                utime.sleep(REPORT_INTERVAL)

        except Exception as e:
            print("[MQTT] 异常: {} — 10s 后重连".format(e))
            if client:
                try:
                    client.publish(
                        TOPIC_STATUS.encode(),
                        ujson.dumps({"phone": PHONE, "online": False}).encode()
                    )
                    client.disconnect()
                except: pass
                client = None
            utime.sleep(10)


# 脚本入口
run()
