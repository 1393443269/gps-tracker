"""
电子围栏纯几何计算(无 Flask / DB / 全局状态依赖)。
从 app.py 原样抽出,函数签名与行为保持完全一致,供 app.py 及后续拆分的模块共用。
"""
import math
import json as _json


def _haversine_m(lat1, lng1, lat2, lng2):
    """两点之间的球面距离(米)"""
    R = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a  = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _point_in_polygon(lng, lat, coords):
    """射线法:判断点 (lng, lat) 是否在多边形 coords=[[lng,lat],...] 内"""
    n, inside, j = len(coords), False, len(coords) - 1
    for i in range(n):
        xi, yi = coords[i][0], coords[i][1]
        xj, yj = coords[j][0], coords[j][1]
        if ((yi > lat) != (yj > lat)) and (lng < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _is_inside_fence(lat, lng, fence):
    """判断坐标是否在围栏内"""
    try:
        ft = fence['fence_type']
        if ft == 'circle':
            return _haversine_m(lat, lng, fence['lat'], fence['lng']) <= (fence['radius'] or 2000)
        elif ft in ('polygon', 'administrative'):
            coords = fence['coordinates']
            if isinstance(coords, str):
                coords = _json.loads(coords)
            return bool(coords) and _point_in_polygon(lng, lat, coords)
    except Exception:
        pass
    return False
