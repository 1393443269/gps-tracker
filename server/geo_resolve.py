"""
WiFi / 基站(LBS) 坐标反算模块
================================
G618G 等设备上报的 WiFi(BSSID+RSSI)与基站(MCC/MNC/LAC/CID+RSSI)本身不含经纬度，
需调第三方定位服务换算。本模块对接 **腾讯位置服务·网络定位（后台定位）** Web 服务。

选型说明：高德的 WiFi/基站精准定位需企业资质、无免费额度；腾讯位置服务的网络定位
个人开发者注册即可免费使用（有每日额度），故采用腾讯。

启用方式：设置环境变量 TENCENT_LBS_KEY=<腾讯位置服务 Key>（控制台-应用管理申请）。
未配置 Key 时 resolve_* 返回 None（不阻塞主流程，上报数据照常落库、坐标留空）。

接口规格（https://lbs.qq.com/service/webService/webServiceGuide/location）：
- POST https://apis.map.qq.com/ws/location/v1/network ，Content-Type: application/json
- body: {key, device_id, cellinfo:[{mcc,mnc,lac,cellid,rss}], wifiinfo:[{mac,rssi}]}
- 返回: {status:0, result:{location:{latitude,longitude}}}

坐标系：腾讯返回 GCJ-02（火星坐标），与前端天地图/高德底图一致；若平台其它坐标为
WGS-84，另需转换（此处不转，交由调用方按需处理）。
"""
import os
import json
import logging
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

# 兼容旧变量名 AMAP_KEY：优先读 TENCENT_LBS_KEY，回退 AMAP_KEY（老部署不至于突然失效）
LBS_KEY = (os.environ.get('TENCENT_LBS_KEY') or os.environ.get('AMAP_KEY') or '').strip()
LBS_URL = 'https://apis.map.qq.com/ws/location/v1/network'
_TIMEOUT = 5


def enabled() -> bool:
    """是否已配置第三方定位 Key。"""
    return bool(LBS_KEY)


def _post(cellinfo=None, wifiinfo=None):
    """向腾讯网络定位发 POST 请求，返回解析后的 JSON dict；失败返回 None。
    device_id 必填但对无 SDK 的服务端调用只作去重/统计用，用主基站/首个 wifi 拼一个稳定值。"""
    if not LBS_KEY:
        return None
    # device_id 取一个稳定标识（腾讯要求必填），无有效信息则用固定占位
    dev = 'gps-tracker'
    if cellinfo:
        dev = 'cell-%s' % cellinfo[0].get('cellid', 0)
    elif wifiinfo:
        dev = 'wifi-%s' % (wifiinfo[0].get('mac', '') or '0')
    body = {'key': LBS_KEY, 'device_id': dev}
    if cellinfo:
        body['cellinfo'] = cellinfo
    if wifiinfo:
        body['wifiinfo'] = wifiinfo
    try:
        data = json.dumps(body).encode('utf-8')
        req = urllib.request.Request(
            LBS_URL, data=data, method='POST',
            headers={'Content-Type': 'application/json', 'User-Agent': 'gps-tracker/1.0'})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode('utf-8', errors='replace'))
    except urllib.error.URLError as e:
        log.warning("[geo] 网络请求失败 (body 不含 key): %s", getattr(e, 'reason', e))
        return None
    except Exception as e:
        log.warning("[geo] 请求异常: %s", type(e).__name__)
        return None


def resolve_lbs(cells: list):
    """基站定位。cells: [{'mcc','mnc','lac','cell_id','rssi'}, ...]
    返回 {'lat','lng','source':'lbs'} 或 None（未配置Key/无有效基站/服务失败）。
    信号最强者作为主基站放数组首位，其余作邻居基站。"""
    if not LBS_KEY or not cells:
        return None
    ordered = sorted(cells, key=lambda c: c.get('rssi', -999), reverse=True)
    cellinfo = [{
        'mcc': c.get('mcc', 0), 'mnc': c.get('mnc', 0),
        'lac': c.get('lac', 0), 'cellid': c.get('cell_id', 0),
        'rss': c.get('rssi', 0),
    } for c in ordered]
    return _extract_latlng(_post(cellinfo=cellinfo), 'lbs')


def resolve_wifi(wifis: list):
    """WiFi 定位。wifis: [{'bssid','rssi'}, ...]
    返回 {'lat','lng','source':'wifi'} 或 None。
    注：腾讯规定仅单个 wifi 且无 gps/基站时无法定位，此处不额外拦截，交由服务端判定。"""
    if not LBS_KEY or not wifis:
        return None
    wifiinfo = [{'mac': w.get('bssid', ''), 'rssi': w.get('rssi', 0)}
                for w in wifis if w.get('bssid')]
    if not wifiinfo:
        return None
    return _extract_latlng(_post(wifiinfo=wifiinfo), 'wifi')


def _extract_latlng(data, source):
    """从腾讯返回里取经纬度：result.location.{latitude,longitude}，status==0 才有效。"""
    if not data:
        return None
    try:
        if data.get('status') != 0:
            log.warning("[geo] 腾讯定位失败 status=%s msg=%s",
                        data.get('status'), data.get('message'))
            return None
        loc = (data.get('result') or {}).get('location') or {}
        lat_f = float(loc.get('latitude'))
        lng_f = float(loc.get('longitude'))
        if not (-90 <= lat_f <= 90) or not (-180 <= lng_f <= 180):
            return None  # 非法坐标，服务返回异常值
        return {'lat': lat_f, 'lng': lng_f, 'source': source}
    except (TypeError, ValueError):
        return None
    except Exception:
        return None


# 逆地理编码(坐标→中文地址)。腾讯 WebService·逆地址解析 GET 接口。
# 设备上报的是 WGS-84 坐标，故传 coord_type=1(GPS/WGS84 输入)，避免与默认 GCJ-02 偏移。
_GEOCODER_URL = 'https://apis.map.qq.com/ws/geocoder/v1'

def reverse_geocode(lat, lng):
    """把 WGS-84 经纬度反查成中文地址字符串。未配 Key/失败返回 None。
    返回 result.address(如"广西壮族自治区桂林市七星区..."),不含 POI 以省流量。"""
    if not LBS_KEY:
        return None
    try:
        latf = float(lat); lngf = float(lng)
    except (TypeError, ValueError):
        return None
    if not (-90 <= latf <= 90) or not (-180 <= lngf <= 180):
        return None
    try:
        from urllib.parse import urlencode
        qs = urlencode({'location': '%.6f,%.6f' % (latf, lngf),
                        'key': LBS_KEY, 'coord_type': 1, 'get_poi': 0})
        req = urllib.request.Request(
            _GEOCODER_URL + '?' + qs, method='GET',
            headers={'User-Agent': 'gps-tracker/1.0'})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode('utf-8', errors='replace'))
        if data.get('status') != 0:
            log.warning("[geo] 逆地理编码失败 status=%s msg=%s", data.get('status'), data.get('message'))
            return None
        result = data.get('result') or {}
        addr = result.get('address')
        # 补充推荐地址(更口语,如"XX产业园")优先,否则用标准 address
        formatted = (result.get('formatted_addresses') or {}).get('recommend')
        return (addr or '') + (('　' + formatted) if formatted else '') or None
    except urllib.error.URLError as e:
        log.warning("[geo] 逆地理编码网络失败: %s", getattr(e, 'reason', e))
        return None
    except Exception as e:
        log.warning("[geo] 逆地理编码异常: %s", type(e).__name__)
        return None
