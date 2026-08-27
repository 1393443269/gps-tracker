"""
WiFi / 基站(LBS) 坐标反算模块
================================
G618G 等设备上报的 WiFi(BSSID+RSSI)与基站(MCC/MNC/LAC/CID+RSSI)本身不含经纬度，
需调第三方定位服务换算。本模块按 **高德开放平台 Web 服务** 的入参格式封装请求。

启用方式：设置环境变量 AMAP_KEY=<高德Web服务Key>。
未配置 Key 时 resolve_* 返回 None（不阻塞主流程，上报数据照常落库、坐标留空）。

高德接口参考：https://restapi.amap.com/v3/assistant/coordinate/convert 属坐标转换；
基站/WiFi 定位走「精准定位」类接口。此处封装为可替换的单点，换服务商只改本文件。

坐标系：高德返回 GCJ-02（火星坐标）。若平台其它坐标为 WGS-84，另需转换（predefine 备注）。
"""
import os
import json
import urllib.request
import urllib.parse

AMAP_KEY = os.environ.get('AMAP_KEY', '').strip()
AMAP_LOCATION_URL = 'https://restapi.amap.com/v3/assistant/location'  # 占位：以实际开通的定位接口为准
_TIMEOUT = 5


def enabled() -> bool:
    """是否已配置第三方定位 Key。"""
    return bool(AMAP_KEY)


def _http_get(url: str, params: dict):
    """发起 GET 请求，返回解析后的 JSON dict；失败返回 None。"""
    if not AMAP_KEY:
        return None
    try:
        qs = urllib.parse.urlencode(params)
        req = urllib.request.Request(url + '?' + qs, headers={'User-Agent': 'gps-tracker/1.0'})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode('utf-8', errors='replace'))
    except Exception:
        return None


def resolve_lbs(cells: list):
    """基站定位。cells: [{'mcc','mnc','lac','cell_id','rssi'}, ...]
    返回 {'lat','lng','source':'lbs'} 或 None（未配置Key/无有效基站/服务失败）。

    高德基站定位入参：cdma=0(GSM/LTE)，bts=mcc,mnc,lac,cellid,signal（主基站），
    nearbts=... 近邻基站(可选)。此处取信号最强的作为主基站。
    """
    if not AMAP_KEY or not cells:
        return None
    main = max(cells, key=lambda c: c.get('rssi', -999))
    bts = f"{main.get('mcc',0)},{main.get('mnc',0)},{main.get('lac',0)},{main.get('cell_id',0)},{main.get('rssi',0)}"
    data = _http_get(AMAP_LOCATION_URL, {
        'key': AMAP_KEY,
        'accesstype': 0,      # 0=手机
        'cdma': 0,            # 0=GSM/WCDMA/LTE
        'bts': bts,
        'output': 'json',
    })
    return _extract_latlng(data, 'lbs')


def resolve_wifi(wifis: list):
    """WiFi 定位。wifis: [{'bssid','rssi'}, ...]
    返回 {'lat','lng','source':'wifi'} 或 None。

    高德 WiFi 定位入参：mmac=BSSID,signal,ssid | ...（多个用 | 分隔）。
    """
    if not AMAP_KEY or not wifis:
        return None
    macstr = '|'.join(f"{w.get('bssid','')},{w.get('rssi',0)}," for w in wifis if w.get('bssid'))
    if not macstr:
        return None
    data = _http_get(AMAP_LOCATION_URL, {
        'key': AMAP_KEY,
        'accesstype': 0,
        'mmac': macstr,
        'output': 'json',
    })
    return _extract_latlng(data, 'wifi')


def _extract_latlng(data, source):
    """从高德返回里取经纬度。高德定位结果一般在 result.location = 'lng,lat'。"""
    if not data:
        return None
    try:
        # 兼容两种返回形态：{'result':{'location':'lng,lat'}} 或 {'location':'lng,lat'}
        loc = None
        if isinstance(data.get('result'), dict):
            loc = data['result'].get('location')
        loc = loc or data.get('location')
        if not loc or ',' not in str(loc):
            return None
        lng_s, lat_s = str(loc).split(',')[:2]
        return {'lat': float(lat_s), 'lng': float(lng_s), 'source': source}
    except Exception:
        return None
