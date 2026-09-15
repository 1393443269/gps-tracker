"""
电子围栏纯几何计算(无 Flask / DB / 全局状态依赖)。
从 app.py 原样抽出,函数签名与行为保持完全一致,供 app.py 及后续拆分的模块共用。
"""
import math
import json as _json
import logging

log = logging.getLogger(__name__)


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


def _point_to_polygon_dist_m(lat, lng, coords):
    """点到多边形各边的最短距离(米)。用局部平面近似投影。coords=[[lng,lat],...]"""
    m_per_lat = 111320.0
    m_per_lng = 111320.0 * math.cos(math.radians(lat))
    px, py = lng * m_per_lng, lat * m_per_lat
    best = float('inf')
    j = len(coords) - 1
    for i in range(len(coords)):
        ax, ay = coords[i][0] * m_per_lng, coords[i][1] * m_per_lat
        bx, by = coords[j][0] * m_per_lng, coords[j][1] * m_per_lat
        dx, dy = bx - ax, by - ay
        l2 = dx*dx + dy*dy
        t = ((px-ax)*dx + (py-ay)*dy) / l2 if l2 > 0 else 0.0
        t = max(0.0, min(1.0, t))
        cx, cy = ax + t*dx, ay + t*dy
        d = math.hypot(px-cx, py-cy)
        if d < best:
            best = d
        j = i
    return best


def _is_inside_fence_buffered(lat, lng, fence, buffer_m):
    """带缓冲带的"在内"判定:用于离开回滞。在内、或在外但距边界<=buffer_m 都算在内。"""
    try:
        ft = fence['fence_type']
        if ft == 'circle':
            return _haversine_m(lat, lng, fence['lat'], fence['lng']) <= (fence['radius'] or 2000) + buffer_m
        elif ft in ('polygon', 'administrative'):
            coords = fence['coordinates']
            if isinstance(coords, str):
                coords = _json.loads(coords)
            if not coords:
                return False
            if _point_in_polygon(lng, lat, coords):
                return True
            return _point_to_polygon_dist_m(lat, lng, coords) <= buffer_m
    except Exception as e:
        log.warning("[围栏] 缓冲判定异常 fence_id=%s: %s", (fence or {}).get('id'), e)
    return False


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
    except Exception as e:
        # 围栏数据损坏(坐标为 None、coordinates JSON 无效、除零等)会使判定恒为"外部",
        # 导致进出告警静默失效。记 warning 便于排查,不再吞异常。
        log.warning("[围栏] 几何判定异常 fence_id=%s type=%s: %s",
                    (fence or {}).get('id'), (fence or {}).get('fence_type'), e)
    return False
