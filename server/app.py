"""
JT/T 808 资产管理平台 - Python 版
Flask + Flask-SocketIO + SQLite + 808 TCP 服务一体化程序

启动: python app.py
  - REST API:  http://localhost:8080/api/...
  - WebSocket: Socket.IO on port 8080
  - 808 TCP:   port 9090
"""
import sqlite3
import socket
import struct
import threading
import uuid
import logging
import os
import sys
import math
import hashlib
import base64
import time as _time_mod

from datetime import datetime
from flask import Flask, request, jsonify
import re as _re
from flask_socketio import SocketIO, join_room
from flask_cors import CORS
import protocol as p

# ── 日志 ──────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# ── 应用初始化 ─────────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading', logger=False, engineio_logger=False)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 支持通过环境变量指定数据目录（Docker 挂载卷场景）
DB_PATH  = os.environ.get('DB_PATH', os.path.join(BASE_DIR, 'tracker.db'))
# 确保数据目录存在（DB_PATH 含目录时才创建，避免空串报错）
_db_dir = os.path.dirname(DB_PATH)
if _db_dir:
    os.makedirs(_db_dir, exist_ok=True)
TCP_PORT = 9090
HTTP_PORT = 8080

def _hash_pw(pwd: str) -> str:
    return hashlib.sha256(pwd.encode('utf-8')).hexdigest()

# ── SQLite 数据库 ──────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # 提升并发写性能
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

_db_lock = threading.Lock()

def db_exec(sql, params=()):
    """线程安全的写操作"""
    with _db_lock:
        conn = get_db()
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

def db_query(sql, params=()):
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()

def db_query_one(sql, params=()):
    conn = get_db()
    try:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def db_scalar(sql, params=()):
    conn = get_db()
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()

def init_db():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS device (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        phone            TEXT    UNIQUE NOT NULL,
        name             TEXT,
        plate_no         TEXT,
        plate_color      INTEGER DEFAULT 0,
        manufacturer     TEXT,
        terminal_model   TEXT,
        terminal_id      TEXT,
        auth_code        TEXT,
        status           INTEGER DEFAULT 0,
        last_lat         REAL,
        last_lng         REAL,
        last_speed       INTEGER,
        last_location_time TEXT,
        online_time      TEXT,
        offline_time     TEXT,
        created_at       TEXT    DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime')),
        updated_at       TEXT    DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime'))
    );

    CREATE TABLE IF NOT EXISTS location_record (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id   INTEGER,
        phone       TEXT    NOT NULL,
        lat         REAL    NOT NULL,
        lng         REAL    NOT NULL,
        altitude    INTEGER,
        speed       INTEGER,
        direction   INTEGER,
        alarm_flag  INTEGER DEFAULT 0,
        status_flag INTEGER DEFAULT 0,
        mileage     INTEGER,
        gps_time    TEXT,
        created_at  TEXT    DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime'))
    );
    CREATE INDEX IF NOT EXISTS idx_loc_phone ON location_record(phone, gps_time);

    CREATE TABLE IF NOT EXISTS alarm_record (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id   INTEGER,
        phone       TEXT    NOT NULL,
        alarm_type  INTEGER NOT NULL,
        alarm_desc  TEXT,
        lat         REAL,
        lng         REAL,
        speed       INTEGER,
        alarm_time  TEXT,
        status      INTEGER DEFAULT 0,
        handler     TEXT,
        handle_time TEXT,
        handle_note TEXT,
        created_at  TEXT    DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime'))
    );

    CREATE TABLE IF NOT EXISTS sim_card (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        iccid        TEXT UNIQUE NOT NULL,
        imsi         TEXT,
        operator     TEXT DEFAULT '中国移动',
        plan         TEXT,
        balance      REAL DEFAULT 0,
        status       TEXT DEFAULT '正常',
        device_phone TEXT,
        remark       TEXT,
        created_at   TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime'))
    );

    CREATE TABLE IF NOT EXISTS recharge (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        sim_id     INTEGER,
        iccid      TEXT,
        amount     REAL NOT NULL,
        method     TEXT DEFAULT '支付宝',
        plan       TEXT,
        remark     TEXT,
        operator   TEXT DEFAULT '管理员',
        created_at TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime'))
    );

    CREATE TABLE IF NOT EXISTS customer (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        name         TEXT NOT NULL,
        contact      TEXT,
        phone        TEXT,
        email        TEXT,
        status       TEXT DEFAULT '活跃',
        reg_date     TEXT,
        remark       TEXT,
        device_count INTEGER DEFAULT 0,
        created_at   TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime'))
    );

    CREATE TABLE IF NOT EXISTS geo_fence (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT NOT NULL,
        fence_type  TEXT DEFAULT 'circle',
        lat         REAL,
        lng         REAL,
        radius      INTEGER DEFAULT 2000,
        coordinates TEXT,
        adcode      TEXT,
        color       TEXT DEFAULT '#409EFF',
        devices     TEXT,
        created_at  TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime'))
    );

    CREATE TABLE IF NOT EXISTS mark_point (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        name       TEXT NOT NULL,
        lat        REAL NOT NULL,
        lng        REAL NOT NULL,
        remark     TEXT,
        created_at TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime'))
    );

    CREATE TABLE IF NOT EXISTS risk_point (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        name       TEXT NOT NULL,
        lat        REAL NOT NULL,
        lng        REAL NOT NULL,
        level      TEXT DEFAULT 'medium',
        remark     TEXT,
        created_at TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime'))
    );

    CREATE TABLE IF NOT EXISTS command_history (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        phone       TEXT,
        device_name TEXT,
        command     TEXT,
        result      TEXT,
        response    TEXT,
        created_at  TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime'))
    );

    CREATE TABLE IF NOT EXISTS op_log (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        action     TEXT,
        detail     TEXT,
        ip         TEXT DEFAULT '127.0.0.1',
        created_at TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime'))
    );

    CREATE TABLE IF NOT EXISTS admin_user (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        username      TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        created_at    TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime'))
    );
    """)
    conn.commit()

    # 安全追加新列（旧库升级时自动 ALTER，列已存在则忽略）
    for col_def in [
        "alarm_enter  INTEGER DEFAULT 1",
        "alarm_exit   INTEGER DEFAULT 1",
        "alarm_dwell  INTEGER DEFAULT 0",   # 围栏内停留超时告警阈值（秒），0=关闭
        "speed_limit  INTEGER DEFAULT 0",   # 围栏内限速（km/h），0=关闭
        "valid_start  TEXT    DEFAULT ''",  # 每日生效开始时间 HH:MM，空=全天
        "valid_end    TEXT    DEFAULT ''",  # 每日生效结束时间 HH:MM
    ]:
        col_name = col_def.split()[0]
        try:
            conn.execute(f"ALTER TABLE geo_fence ADD COLUMN {col_def}")
            conn.commit()
        except Exception:
            pass  # 列已存在，忽略

    # customer 表：加登录账号 / 密码哈希
    for col_def in [
        "login_name   TEXT",
        "password_hash TEXT",
    ]:
        try:
            conn.execute(f"ALTER TABLE customer ADD COLUMN {col_def}")
            conn.commit()
        except Exception:
            pass

    # device 表：加客户归属
    try:
        conn.execute("ALTER TABLE device ADD COLUMN customer_id INTEGER DEFAULT NULL")
        conn.commit()
    except Exception:
        pass

    # customer 表：加 parent_id 支持下级客户
    try:
        conn.execute("ALTER TABLE customer ADD COLUMN parent_id INTEGER DEFAULT NULL")
        conn.commit()
    except Exception:
        pass

    # geo_fence 表：加 customer_id 支持客户自建围栏（NULL=全局，否则=归属某客户）
    try:
        conn.execute("ALTER TABLE geo_fence ADD COLUMN customer_id INTEGER DEFAULT NULL")
        conn.commit()
    except Exception:
        pass

    # ── 组织隔离：给业务表加 org_id（DEFAULT 1 = 根组织，存量数据自动归根） ──────
    for _tbl in ('device', 'customer', 'geo_fence', 'alarm_record'):
        try:
            conn.execute(f"ALTER TABLE {_tbl} ADD COLUMN org_id INTEGER DEFAULT 1")
            conn.commit()
        except Exception:
            pass  # 列已存在，忽略

    # ── customer 表：个人信息扩展（设备信息页使用） ───────────────────────────────
    for _col in ["gender  TEXT DEFAULT ''",
                 "age     INTEGER DEFAULT NULL",
                 "address TEXT DEFAULT ''"]:
        try:
            conn.execute(f"ALTER TABLE customer ADD COLUMN {_col}")
            conn.commit()
        except Exception:
            pass

    # ── SIM 卡生命周期 ────────────────────────────────────────────────────────────
    for _col in ["expire_date TEXT DEFAULT NULL",       # 套餐到期日 YYYY-MM-DD
                 "monthly_fee REAL DEFAULT 0"]:         # 月租费用（用于成本统计）
        try:
            conn.execute(f"ALTER TABLE sim_card ADD COLUMN {_col}")
            conn.commit()
        except Exception:
            pass

    # ── 设备生命周期 ──────────────────────────────────────────────────────────────
    # lifecycle: 0=未激活 1=已激活 2=已停用 3=已报废
    for _col in ["lifecycle   INTEGER DEFAULT 1",
                 "activated_at  TEXT DEFAULT NULL",
                 "deactivated_at TEXT DEFAULT NULL",
                 "remark TEXT DEFAULT ''"]:
        try:
            conn.execute(f"ALTER TABLE device ADD COLUMN {_col}")
            conn.commit()
        except Exception:
            pass

    # 默认管理员 admin / admin123（已存在则跳过）
    try:
        conn.execute("INSERT OR IGNORE INTO admin_user (username, password_hash) VALUES (?,?)",
                     ('admin', _hash_pw('admin123')))
        conn.commit()
    except Exception:
        pass

    # ── 系统组织 / 用户扩展 / 模块表 ─────────────────────────────────────────
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS sys_org (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            org_name   TEXT    NOT NULL,
            parent_id  INTEGER DEFAULT NULL,
            org_level  INTEGER NOT NULL DEFAULT 1,
            org_code   TEXT    UNIQUE NOT NULL,
            org_path   TEXT    DEFAULT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            is_active  INTEGER NOT NULL DEFAULT 1,
            created_at TEXT    DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime')),
            updated_at TEXT    DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS sys_module (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            module_code TEXT    UNIQUE NOT NULL,
            module_name TEXT    NOT NULL,
            parent_code TEXT    DEFAULT NULL,
            sort_order  INTEGER NOT NULL DEFAULT 0,
            description TEXT    DEFAULT NULL,
            is_system   INTEGER NOT NULL DEFAULT 0,
            deleted     INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT    DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS sys_org_module_auth (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            org_id          INTEGER NOT NULL,
            module_code     TEXT    NOT NULL,
            is_enabled      INTEGER NOT NULL DEFAULT 1,
            granted_by_org  INTEGER NOT NULL DEFAULT 0,
            granted_by_user INTEGER NOT NULL DEFAULT 0,
            granted_at      TEXT    DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime')),
            UNIQUE (org_id, module_code)
        );
        """)
        conn.commit()
    except Exception as e:
        log.warning("系统表创建: %s", e)

    # admin_user 扩展字段（已存在则忽略）
    for _col in [
        "org_id     INTEGER DEFAULT 1",
        "org_level  INTEGER DEFAULT 1",
        "user_type  INTEGER DEFAULT 9",
        "real_name  TEXT    DEFAULT NULL",
        "phone      TEXT    DEFAULT NULL",
        "is_active  INTEGER DEFAULT 1",
        "last_login TEXT    DEFAULT NULL",
        "updated_at TEXT    DEFAULT NULL",
    ]:
        try:
            conn.execute(f"ALTER TABLE admin_user ADD COLUMN {_col}")
            conn.commit()
        except Exception:
            pass

    # 修复旧记录 is_active=NULL → 1
    try:
        conn.execute("UPDATE admin_user SET is_active=1 WHERE is_active IS NULL")
        conn.commit()
    except Exception:
        pass

    # 根组织 & 超管关联初始化
    try:
        conn.execute(
            "INSERT OR IGNORE INTO sys_org (id, org_name, parent_id, org_level, org_code, org_path) "
            "VALUES (1, '总部', NULL, 1, 'HQ', '/1/')"
        )
        conn.execute(
            "UPDATE admin_user SET org_id=1, org_level=1, user_type=9 WHERE username='admin'"
        )
        conn.commit()
    except Exception:
        pass

    conn.close()
    log.info("数据库初始化完成: %s", DB_PATH)


# ── 会话管理 ───────────────────────────────────────────────────────────────────

sessions      = {}        # phone → socket
sessions_lock = threading.Lock()
_serial       = [0]
_serial_lock  = threading.Lock()

# 围栏状态：记录每台设备当前"在哪些围栏内"，用于检测穿越
# phone → set of fence_id
fence_device_inside: dict = {}

# ── P0: 防抖 ─────────────────────────────────────────────────────────────────
# 连续读数一致 FENCE_DEBOUNCE_N 次才确认状态切换，避免边界抖动重复告警
FENCE_DEBOUNCE_N = 3
fence_device_pending: dict = {}        # phone → {fence_id: (last_state:bool|None, count:int)}

# ── P1: 停留超时 ──────────────────────────────────────────────────────────────
fence_device_enter_time:    dict = {}  # phone → {fence_id: datetime} 进入时刻
fence_device_dwell_alarmed: dict = {}  # phone → set of fence_id（已触发滞留告警，离开时清除）


def next_serial():
    with _serial_lock:
        _serial[0] = (_serial[0] + 1) & 0xFFFF
        return _serial[0]


def resolve_phone(bcd_phone: str) -> str:
    """
    BCD 解出的 12 位手机号 → DB 中存储的完整设备标识。
    支持 IMEI（15 位）等长标识：若 DB 存的 phone 以 bcd_phone 结尾则命中。
    """
    row = db_query_one("SELECT phone FROM device WHERE phone=?", (bcd_phone,))
    if row:
        return row['phone']
    row = db_query_one(
        "SELECT phone FROM device WHERE length(phone) > 12 AND phone LIKE ?",
        (f'%{bcd_phone}',)
    )
    return row['phone'] if row else bcd_phone


# ── 电子围栏：几何判断 ────────────────────────────────────────────────────────

def _haversine_m(lat1, lng1, lat2, lng2):
    """两点之间的球面距离（米）"""
    R = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a  = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _point_in_polygon(lng, lat, coords):
    """射线法：判断点 (lng, lat) 是否在多边形 coords=[[lng,lat],...] 内"""
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
            import json as _json
            coords = fence['coordinates']
            if isinstance(coords, str):
                coords = _json.loads(coords)
            return bool(coords) and _point_in_polygon(lng, lat, coords)
    except Exception:
        pass
    return False


def check_fence_crossing(phone, lat, lng, device_id, gps_time, speed_raw=0, status_flag=0):
    """
    每次收到 0x0200 位置报文后调用。
    P1: GPS质量过滤 → P2: 时间窗口过滤 → P0: 防抖确认 → 进/出/滞留超时/围栏超速告警

    alarm_type 约定:
      100 = 进入围栏   101 = 离开围栏
      102 = 停留超时   103 = 围栏内超速
    """
    import json as _json
    from datetime import datetime as _dt, time as _time

    # ── P1: GPS 质量过滤 ──────────────────────────────────────────────────────
    # JT/T 808 status_flag bit1: 0=未定位, 1=已定位；(0,0) 坐标也视为无效
    if (lat == 0 and lng == 0) or not (status_flag & 0x02):
        return

    # 查询该设备关联的所有围栏（devices 字段是逗号分隔的 phone 列表）
    fences = db_query(
        """SELECT id, name, fence_type, lat, lng, radius, coordinates,
                  COALESCE(alarm_enter,1)   alarm_enter,
                  COALESCE(alarm_exit,1)    alarm_exit,
                  COALESCE(alarm_dwell,0)   alarm_dwell,
                  COALESCE(speed_limit,0)   speed_limit,
                  COALESCE(valid_start,'')  valid_start,
                  COALESCE(valid_end,'')    valid_end
           FROM geo_fence
           WHERE devices = ?
              OR devices LIKE ?
              OR devices LIKE ?
              OR devices LIKE ?""",
        (phone, f'{phone},%', f'%,{phone}', f'%,{phone},%')
    )
    if not fences:
        return

    now_ts    = _dt.now()
    now_time  = now_ts.time()
    speed_kmh = speed_raw / 10.0          # 808 协议单位 → km/h

    prev_inside = fence_device_inside.get(phone, set())
    new_inside  = set()

    for f in fences:
        fid = f['id']

        # ── P2: 生效时间段过滤 ─────────────────────────────────────────────
        vs = (f['valid_start'] or '').strip()
        ve = (f['valid_end']   or '').strip()
        if vs and ve:
            try:
                if not (_time.fromisoformat(vs) <= now_time <= _time.fromisoformat(ve)):
                    # 不在生效时段：保持原确认状态，不触发任何告警
                    if fid in prev_inside:
                        new_inside.add(fid)
                    continue
            except ValueError:
                pass  # 时间格式非法则不过滤

        # 当前点位是否在围栏内
        currently_inside = _is_inside_fence(lat, lng, f)

        # ── P0: 防抖 - 连续 FENCE_DEBOUNCE_N 次同状态才确认切换 ────────────
        pending = fence_device_pending.setdefault(phone, {})
        prev_state, count = pending.get(fid, (None, 0))
        if prev_state == currently_inside:
            count += 1
        else:
            count = 1
        pending[fid] = (currently_inside, count)

        was_inside = fid in prev_inside

        # 防抖未达标：保持原确认状态，继续积累
        if count < FENCE_DEBOUNCE_N:
            if was_inside:
                new_inside.add(fid)
            continue

        # ── 防抖通过，以 currently_inside 为新确认状态 ──────────────────────
        confirmed_inside = currently_inside
        if confirmed_inside:
            new_inside.add(fid)

        # ── 状态刚切换：进入围栏 ──────────────────────────────────────────
        if confirmed_inside and not was_inside:
            if f['alarm_enter']:
                desc = f'进入围栏: {f["name"]}'
                db_exec(
                    "INSERT INTO alarm_record (device_id,phone,alarm_type,alarm_desc,"
                    "lat,lng,speed,alarm_time,status) VALUES (?,?,?,?,?,?,?,?,0)",
                    (device_id, phone, 100, desc, lat, lng, speed_raw, gps_time)
                )
                _sio_emit('alarm', {
                    'phone': phone, 'alarmType': 100, 'alarmDesc': desc,
                    'lat': lat, 'lng': lng, 'time': gps_time, 'fenceName': f['name'],
                }, phone)
                log.info("[围栏] %s 进入围栏「%s」", phone, f['name'])
            # 记录进入时刻，重置滞留告警标记
            fence_device_enter_time.setdefault(phone, {})[fid] = now_ts
            fence_device_dwell_alarmed.get(phone, set()).discard(fid)

        # ── 状态刚切换：离开围栏 ──────────────────────────────────────────
        elif not confirmed_inside and was_inside:
            if f['alarm_exit']:
                desc = f'离开围栏: {f["name"]}'
                db_exec(
                    "INSERT INTO alarm_record (device_id,phone,alarm_type,alarm_desc,"
                    "lat,lng,speed,alarm_time,status) VALUES (?,?,?,?,?,?,?,?,0)",
                    (device_id, phone, 101, desc, lat, lng, speed_raw, gps_time)
                )
                _sio_emit('alarm', {
                    'phone': phone, 'alarmType': 101, 'alarmDesc': desc,
                    'lat': lat, 'lng': lng, 'time': gps_time, 'fenceName': f['name'],
                }, phone)
                log.info("[围栏] %s 离开围栏「%s」", phone, f['name'])
            # 清除进入时刻和滞留告警标记
            fence_device_enter_time.get(phone, {}).pop(fid, None)
            fence_device_dwell_alarmed.get(phone, set()).discard(fid)

        # ── 持续在围栏内：检查滞留超时 & 围栏超速 ────────────────────────
        elif confirmed_inside and was_inside:
            # P1: 停留超时
            dwell_limit = f['alarm_dwell']   # 秒, 0=关闭
            if dwell_limit > 0:
                enter_t = fence_device_enter_time.get(phone, {}).get(fid)
                if enter_t:
                    elapsed = (now_ts - enter_t).total_seconds()
                    if elapsed > dwell_limit:
                        already = fid in fence_device_dwell_alarmed.get(phone, set())
                        if not already:
                            mins = int(elapsed // 60)
                            desc = f'停留超时: {f["name"]}（已停留{mins}分钟）'
                            db_exec(
                                "INSERT INTO alarm_record (device_id,phone,alarm_type,alarm_desc,"
                                "lat,lng,speed,alarm_time,status) VALUES (?,?,?,?,?,?,?,?,0)",
                                (device_id, phone, 102, desc, lat, lng, speed_raw, gps_time)
                            )
                            _sio_emit('alarm', {
                                'phone': phone, 'alarmType': 102, 'alarmDesc': desc,
                                'lat': lat, 'lng': lng, 'time': gps_time, 'fenceName': f['name'],
                            }, phone)
                            fence_device_dwell_alarmed.setdefault(phone, set()).add(fid)
                            log.info("[围栏] %s 在「%s」停留超时 %.0f秒", phone, f['name'], elapsed)

            # P2: 围栏内超速（每报文都检查，不去重——驾驶员应持续收到超速提示）
            speed_lim = f['speed_limit']     # km/h, 0=关闭
            if speed_lim > 0 and speed_kmh > speed_lim:
                desc = f'围栏内超速: {f["name"]}（{speed_kmh:.1f}km/h，限{speed_lim}km/h）'
                db_exec(
                    "INSERT INTO alarm_record (device_id,phone,alarm_type,alarm_desc,"
                    "lat,lng,speed,alarm_time,status) VALUES (?,?,?,?,?,?,?,?,0)",
                    (device_id, phone, 103, desc, lat, lng, speed_raw, gps_time)
                )
                _sio_emit('alarm', {
                    'phone': phone, 'alarmType': 103, 'alarmDesc': desc,
                    'lat': lat, 'lng': lng, 'time': gps_time, 'fenceName': f['name'],
                }, phone)
                log.info("[围栏] %s 围栏「%s」超速 %.1f>%dkm/h", phone, f['name'], speed_kmh, speed_lim)

    fence_device_inside[phone] = new_inside


# ── 808 消息处理函数 ────────────────────────────────────────────────────────────

ALARM_DEFS = [
    (0,  'SOS 紧急报警'),
    (1,  '超速报警'),
    (2,  '疲劳驾驶报警'),
    (8,  '主电源断开'),
    (25, '碰撞报警'),
    (26, '侧翻报警'),
]


def handle_register(sock, phone, serial, body):
    info      = p.parse_register_body(body)
    auth_code = uuid.uuid4().hex[:8].upper()
    now       = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # 若 plate_no 字段携带了完整 IMEI（15 位纯数字），以 IMEI 作为设备标识
    plate_no_raw = info.get('plate_no', '') or ''
    if len(plate_no_raw) == 15 and plate_no_raw.isdigit():
        canonical_phone = plate_no_raw      # 用完整 IMEI 作为 phone
        plate_no_store  = ''               # plate_no 字段留空
    else:
        canonical_phone = resolve_phone(phone)
        plate_no_store  = plate_no_raw

    existing = db_query_one("SELECT id FROM device WHERE phone=?", (canonical_phone,))
    if existing:
        db_exec(
            "UPDATE device SET manufacturer=?,terminal_model=?,terminal_id=?,"
            "plate_no=?,plate_color=?,auth_code=?,updated_at=? WHERE phone=?",
            (info.get('manufacturer'), info.get('terminal_model'), info.get('terminal_id'),
             plate_no_store, info.get('plate_color'), auth_code, now, canonical_phone)
        )
    else:
        # org_id 显式写 1（根组织）；管理员可在设备管理界面手动迁移到子组织
        db_exec(
            "INSERT INTO device (phone,manufacturer,terminal_model,terminal_id,"
            "plate_no,plate_color,auth_code,status,org_id) VALUES (?,?,?,?,?,?,?,0,1)",
            (canonical_phone, info.get('manufacturer'), info.get('terminal_model'),
             info.get('terminal_id'), plate_no_store, info.get('plate_color'), auth_code)
        )

    resp = p.build_register_resp(phone, next_serial(), serial, 0, auth_code)
    sock.sendall(resp)
    log.info("[808] 终端注册: phone=%s canonical=%s auth_code=%s", phone, canonical_phone, auth_code)


def handle_auth(sock, phone, serial, body):
    code_len  = body[0] if body else 0
    auth_code = body[1:1 + code_len].decode('ascii', errors='replace') if len(body) > 1 else ''

    canonical = resolve_phone(phone)
    row = db_query_one("SELECT id, auth_code, status FROM device WHERE phone=?", (canonical,))
    # 严格比对鉴权码，不允许万能 DEFAULT 绕过
    auth_ok = row and row.get('auth_code') == auth_code
    if auth_ok:
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        db_exec("UPDATE device SET status=1, online_time=? WHERE phone=?", (now, canonical))
        result = 0
        log.info("[808] 鉴权成功: phone=%s", canonical)
    else:
        result = 1
        log.warning("[808] 鉴权失败: phone=%s auth=%s", canonical, auth_code)

    sock.sendall(p.build_generic_resp(phone, next_serial(), serial, 0x0102, result))


def handle_heartbeat(sock, phone, serial):
    canonical = resolve_phone(phone)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    db_exec("UPDATE device SET status=1, online_time=? WHERE phone=?", (now, canonical))
    sock.sendall(p.build_generic_resp(phone, next_serial(), serial, 0x0002, 0))
    log.debug("[808] 心跳: phone=%s", canonical)


def handle_location(sock, phone, serial, body):
    loc = p.parse_location_body(body)
    if loc is None:
        log.error("[808] 位置解析失败: phone=%s body_len=%d", phone, len(body))
        return

    canonical  = resolve_phone(phone)
    lat        = loc['lat']
    lng        = loc['lng']
    speed      = loc['speed']
    direction  = loc['direction']
    altitude   = loc['altitude']
    alarm_flag  = loc['alarm_flag']
    status_flag = loc['status_flag']    # bit1=1 表示已定位，围栏检测用
    gps_time    = loc['gps_time'].strftime('%Y-%m-%d %H:%M:%S')
    mileage     = loc.get('mileage')

    row = db_query_one("SELECT id FROM device WHERE phone=?", (canonical,))
    device_id = row['id'] if row else 0

    # 保存位置记录
    db_exec(
        "INSERT INTO location_record (device_id,phone,lat,lng,altitude,speed,direction,"
        "alarm_flag,status_flag,mileage,gps_time) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (device_id, canonical, lat, lng, altitude, speed, direction,
         alarm_flag, loc['status_flag'], mileage, gps_time)
    )

    # 更新设备最新状态
    status = 2 if alarm_flag else 1
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    db_exec(
        "UPDATE device SET last_lat=?,last_lng=?,last_speed=?,last_location_time=?,"
        "status=?,updated_at=? WHERE phone=?",
        (lat, lng, speed, gps_time, status, now, canonical)
    )

    # 处理报警（使用 canonical phone，与 device 表保持一致）
    if alarm_flag:
        for bit, desc in ALARM_DEFS:
            if alarm_flag & (1 << bit):
                db_exec(
                    "INSERT INTO alarm_record (device_id,phone,alarm_type,alarm_desc,"
                    "lat,lng,speed,alarm_time,status) VALUES (?,?,?,?,?,?,?,?,0)",
                    (device_id, canonical, bit, desc, lat, lng, speed, gps_time)
                )
                log.warning("[808] 报警! phone=%s type=%d desc=%s", phone, bit, desc)
                _sio_emit('alarm', {
                    'phone':     canonical,
                    'alarmType': bit,
                    'alarmDesc': desc,
                    'lat':       lat,
                    'lng':       lng,
                    'time':      gps_time,
                }, canonical)

    # WebSocket 推送位置到前端（仅向该设备所属组织的客户端推送）
    _sio_emit('location_update', {
        'phone':     canonical,
        'lat':       lat,
        'lng':       lng,
        'speed':     round(speed / 10.0, 1),
        'direction': direction,
        'altitude':  altitude,
        'alarm':     bool(alarm_flag),
        'alarmFlag': alarm_flag,
        'time':      gps_time,
    }, canonical)

    log.debug("[808] 位置: phone=%s lat=%.6f lng=%.6f speed=%.1fkm/h alarm=%d",
              phone, lat, lng, speed / 10.0, alarm_flag)

    # ── 电子围栏穿越检测 ──────────────────────────────────────────────────────
    try:
        check_fence_crossing(canonical, lat, lng, device_id, gps_time,
                              speed_raw=speed, status_flag=status_flag)
    except Exception as e:
        log.error("[围栏检测] 异常: %s", e)

    # 应答
    sock.sendall(p.build_generic_resp(phone, next_serial(), serial, 0x0200, 0))


# ── TCP 连接处理线程 ────────────────────────────────────────────────────────────

def handle_client(conn, addr):
    log.info("[808] 新连接: %s:%d", addr[0], addr[1])
    buf   = bytearray()
    phone = None
    conn.settimeout(90)

    try:
        while True:
            try:
                data = conn.recv(4096)
            except socket.timeout:
                log.info("[808] 空闲超时: %s phone=%s", addr, phone)
                break
            if not data:
                break

            buf.extend(data)
            frames, buf = p.extract_frames(buf)

            for frame in frames:
                hdr = p.parse_header(frame)
                if not hdr:
                    continue

                msg_id = hdr['msg_id']
                ph     = hdr['phone'] or phone
                serial = hdr['serial']
                body   = hdr['body']

                if ph:
                    phone = ph
                    with sessions_lock:
                        sessions[phone] = conn

                try:
                    if   msg_id == 0x0100: handle_register(conn, ph, serial, body)
                    elif msg_id == 0x0102: handle_auth(conn, ph, serial, body)
                    elif msg_id == 0x0002: handle_heartbeat(conn, ph, serial)
                    elif msg_id == 0x0200: handle_location(conn, ph, serial, body)
                    elif msg_id == 0x0001: pass   # 终端通用应答，忽略
                    else:
                        conn.sendall(p.build_generic_resp(ph, next_serial(), serial, msg_id, 3))
                        log.debug("[808] 未知消息 ID=0x%04X phone=%s", msg_id, ph)
                except Exception as e:
                    log.error("[808] 处理消息异常 ID=0x%04X: %s", msg_id, e, exc_info=True)

    except Exception as e:
        log.error("[808] 连接异常: %s %s", addr, e)
    finally:
        conn.close()
        if phone:
            with sessions_lock:
                sessions.pop(phone, None)
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            try:
                db_exec("UPDATE device SET status=0, offline_time=? WHERE phone=?", (now, resolve_phone(phone)))
            except Exception:
                pass
        log.info("[808] 连接断开: %s phone=%s", addr, phone)


def start_tcp_server():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('0.0.0.0', TCP_PORT))
    srv.listen(256)
    log.info("╔══════════════════════════════════════════╗")
    log.info("║  JT/T 808 TCP 服务启动  端口: %d         ║", TCP_PORT)
    log.info("╚══════════════════════════════════════════╝")
    while True:
        try:
            conn, addr = srv.accept()
            t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
            t.start()
        except Exception as e:
            log.error("[808] accept 错误: %s", e)


# ── REST API ───────────────────────────────────────────────────────────────────

def ok(data=None):
    return jsonify({'code': 200, 'msg': 'success', 'data': data})

def fail(msg, code=500):
    return jsonify({'code': code, 'msg': msg}), code


# ── 设备接口 ──

def _org_scope_ids(request_obj):
    """返回当前管理员可见的 org_id 列表；None 表示无限制（超管）"""
    admin = _current_admin(request_obj)
    scope = _scope_path(admin)
    if scope is None:
        return None
    return [o['id'] for o in _orgs_in_scope(scope)]


def _org_where(scope_ids, existing_conds=None, existing_params=None, col='org_id'):
    """
    根据 scope_ids 生成 WHERE 子句片段和参数列表。
    scope_ids=None → 无额外过滤；
    scope_ids=[]   → 空集（WHERE 1=0）。
    """
    conds  = list(existing_conds  or [])
    params = list(existing_params or [])
    if scope_ids is not None:
        if not scope_ids:
            conds.append("1=0")
        else:
            ph = ','.join('?' * len(scope_ids))
            conds.append(f"{col} IN ({ph})")
            params.extend(scope_ids)
    return conds, params


@app.get('/api/devices/summary')
def device_summary():
    sids = _org_scope_ids(request)
    if sids is not None and not sids:
        return ok({'total': 0, 'online': 0, 'offline': 0, 'alarm': 0})
    base_conds, base_params = _org_where(sids)
    def _count(extra_cond=None):
        conds  = base_conds + ([extra_cond] if extra_cond else [])
        params = base_params + []
        where  = ("WHERE " + " AND ".join(conds)) if conds else ""
        return db_scalar(f"SELECT COUNT(*) FROM device {where}", params)
    total   = _count()
    online  = _count("status=1")
    alarm   = _count("status=2")
    offline = total - online - alarm
    return ok({'total': total, 'online': online, 'offline': offline, 'alarm': alarm})


@app.get('/api/devices')
def list_devices():
    page   = int(request.args.get('page', 1))
    size   = int(request.args.get('size', 20))
    kw     = request.args.get('keyword', '').strip()
    lc     = request.args.get('lifecycle', '').strip()
    st     = request.args.get('status', '').strip()
    offset = (page - 1) * size
    sids   = _org_scope_ids(request)

    conds, params = [], []
    if kw:
        like = f'%{kw}%'
        conds.append("(phone LIKE ? OR name LIKE ? OR plate_no LIKE ?)")
        params += [like, like, like]
    if lc != '':
        try:
            conds.append("lifecycle=?"); params.append(int(lc))
        except ValueError:
            pass
    if st != '':
        try:
            conds.append("status=?"); params.append(int(st))
        except ValueError:
            pass
    conds, params = _org_where(sids, conds, params)

    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    total   = db_scalar(f"SELECT COUNT(*) FROM device {where}", params)
    records = db_query(f"SELECT * FROM device {where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                       params + [size, offset])
    return ok({'records': records, 'total': total, 'page': page, 'size': size})

@app.post('/api/devices')
def create_device():
    data  = request.get_json() or {}
    phone = (data.get('phone') or '').strip()
    if not phone:
        return fail('设备号不能为空')
    if db_query_one("SELECT id FROM device WHERE phone=?", (phone,)):
        return fail('设备号已存在')
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    db_exec(
        "INSERT INTO device (phone,name,plate_no,manufacturer,terminal_model,"
        "terminal_id,plate_color,auth_code,status,org_id,lifecycle,remark,created_at,updated_at)"
        " VALUES (?,?,?,?,?,?,1,'DEFAULT',0,1,0,?,?,?)",
        (phone, data.get('name',''), data.get('plateNo',''),
         data.get('manufacturer',''), data.get('terminalModel',''),
         data.get('terminalId',''), data.get('remark',''), now, now)
    )
    add_op_log('设备新增', f'手动新增设备 {phone}')
    return ok({'message': '创建成功'})


@app.get('/api/devices/<int:did>')
def get_device(did):
    row = db_query_one("SELECT * FROM device WHERE id=?", (did,))
    if not row: return fail('设备不存在', 404)
    return ok(row)

@app.put('/api/devices/<int:did>')
def update_device(did):
    data = request.get_json() or {}
    now  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    row  = db_query_one("SELECT lifecycle FROM device WHERE id=?", (did,))
    if not row:
        return fail('设备不存在', 404)

    new_lc = data.get('lifecycle')
    activated_at   = None
    deactivated_at = None
    if new_lc is not None:
        new_lc = int(new_lc)
        old_lc = row.get('lifecycle', 1)
        if new_lc == 1 and old_lc != 1:   # → 已激活
            activated_at = now
        if new_lc in (2, 3) and old_lc == 1:  # → 停用/报废
            deactivated_at = now

    sql = "UPDATE device SET name=?,plate_no=?,updated_at=?,remark=?"
    args = [data.get('name'), data.get('plateNo') or data.get('plate_no'), now,
            data.get('remark', '')]
    if new_lc is not None:
        sql += ",lifecycle=?"
        args.append(new_lc)
    if activated_at:
        sql += ",activated_at=?"
        args.append(activated_at)
    if deactivated_at:
        sql += ",deactivated_at=?"
        args.append(deactivated_at)
    sql += " WHERE id=?"
    args.append(did)
    db_exec(sql, args)
    return ok()


@app.put('/api/devices/batch_lifecycle')
def batch_lifecycle():
    """批量更新设备生命周期状态"""
    data = request.get_json() or {}
    ids  = data.get('ids', [])
    lc   = int(data.get('lifecycle', 1))
    if not ids:
        return fail('ids 不能为空', 400)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ph  = ','.join('?' * len(ids))
    extra_col = ''
    extra_val = []
    if lc == 1:
        extra_col = ',activated_at=?'; extra_val = [now]
    elif lc in (2, 3):
        extra_col = ',deactivated_at=?'; extra_val = [now]
    db_exec(
        f"UPDATE device SET lifecycle=?,updated_at=?{extra_col} WHERE id IN ({ph})",
        [lc, now] + extra_val + list(ids)
    )
    return ok({'updated': len(ids)})


@app.get('/api/devices/with_customer')
def devices_with_customer():
    """设备信息列表：JOIN customer，返回绑定人员信息 + 围栏数"""
    page   = int(request.args.get('page', 1))
    size   = int(request.args.get('size', 20))
    kw     = request.args.get('keyword', '').strip()
    offset = (page - 1) * size
    sids   = _org_scope_ids(request)

    conds, params = [], []
    if kw:
        like = f'%{kw}%'
        conds.append("(d.phone LIKE ? OR d.name LIKE ? OR c.name LIKE ? OR c.contact LIKE ?)")
        params += [like, like, like, like]
    conds, params = _org_where(sids, conds, params, col='d.org_id')

    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    count_sql = (
        "SELECT COUNT(*) FROM device d "
        "LEFT JOIN customer c ON d.customer_id = c.id " + where
    )
    data_sql = (
        "SELECT d.id, d.phone, d.name, d.terminal_model, d.last_location_time, "
        "       d.status, d.lifecycle, d.activated_at, d.customer_id, "
        "       c.name as role_name, c.contact as real_name, c.gender, c.age, "
        "       c.phone as contact_phone, c.address, c.remark as customer_remark, "
        "       c.login_name as account, "
        "       (SELECT COUNT(*) FROM geo_fence gf "
        "        WHERE gf.devices IS NOT NULL AND gf.devices LIKE '%' || d.phone || '%') "
        "       as fence_count "
        "FROM device d "
        "LEFT JOIN customer c ON d.customer_id = c.id "
        + where +
        " ORDER BY d.updated_at DESC LIMIT ? OFFSET ?"
    )
    total   = db_scalar(count_sql, params)
    records = db_query(data_sql, params + [size, offset])
    return ok({'records': records, 'total': total, 'page': page, 'size': size})


@app.post('/api/devices/<int:did>/bind_customer')
def bind_device_customer(did):
    """将设备绑定到指定客户（customer_id）"""
    data = request.get_json() or {}
    cid  = data.get('customer_id')
    if not cid:
        return fail('customer_id 不能为空', 400)
    dev = db_query_one("SELECT id, phone FROM device WHERE id=?", (did,))
    if not dev:
        return fail('设备不存在', 404)
    cust = db_query_one("SELECT id, name FROM customer WHERE id=?", (cid,))
    if not cust:
        return fail('客户不存在', 404)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    db_exec("UPDATE device SET customer_id=?,updated_at=? WHERE id=?", (cid, now, did))
    add_op_log('设备绑定', f'设备 {dev["phone"]} 绑定至客户 {cust["name"]}')
    return ok()


@app.post('/api/devices/<int:did>/unbind_customer')
def unbind_device_customer(did):
    """解除设备与客户的绑定"""
    dev = db_query_one("SELECT id, phone, customer_id FROM device WHERE id=?", (did,))
    if not dev:
        return fail('设备不存在', 404)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    db_exec("UPDATE device SET customer_id=NULL,updated_at=? WHERE id=?", (now, did))
    add_op_log('设备解绑', f'设备 {dev["phone"]} 已解绑')
    return ok()


# ── 位置接口 ──

@app.get('/api/locations/<phone>/latest')
def latest_location(phone):
    row = db_query_one("SELECT * FROM location_record WHERE phone=? ORDER BY gps_time DESC LIMIT 1", (phone,))
    return ok(row)

@app.get('/api/locations/<phone>/history')
def location_history(phone):
    page   = int(request.args.get('page', 1))
    size   = int(request.args.get('size', 100))
    start  = request.args.get('start')
    end    = request.args.get('end')
    offset = (page - 1) * size
    if start and end:
        total   = db_scalar("SELECT COUNT(*) FROM location_record WHERE phone=? AND gps_time BETWEEN ? AND ?", (phone, start, end))
        records = db_query("SELECT * FROM location_record WHERE phone=? AND gps_time BETWEEN ? AND ? ORDER BY gps_time ASC LIMIT ? OFFSET ?",
                           (phone, start, end, size, offset))
    else:
        total   = db_scalar("SELECT COUNT(*) FROM location_record WHERE phone=?", (phone,))
        records = db_query("SELECT * FROM location_record WHERE phone=? ORDER BY gps_time DESC LIMIT ? OFFSET ?",
                           (phone, size, offset))
    return ok({'records': records, 'total': total, 'page': page})


# ── 报警接口 ──

@app.get('/api/alarms')
def list_alarms():
    page   = int(request.args.get('page', 1))
    size   = int(request.args.get('size', 20))
    status = request.args.get('status')
    phone  = request.args.get('phone', '').strip()
    offset = (page - 1) * size
    sids   = _org_scope_ids(request)

    conds, params = [], []
    if status is not None and status != '':
        conds.append("a.status=?"); params.append(int(status))
    if phone:
        conds.append("a.phone=?"); params.append(phone)

    # 通过 device.org_id 过滤组织范围
    if sids is None:
        pass  # 超管，不限制
    elif not sids:
        return ok({'records': [], 'total': 0, 'page': page})
    else:
        ph = ','.join('?' * len(sids))
        conds.append(f"d.org_id IN ({ph})")
        params.extend(sids)

    # 需要 JOIN device 才能按 org_id 过滤
    if sids is not None:
        base  = "FROM alarm_record a LEFT JOIN device d ON a.phone=d.phone"
        sel   = "a.*"
    else:
        base  = "FROM alarm_record a"
        sel   = "a.*"

    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    total   = db_scalar(f"SELECT COUNT(*) {base} {where}", params)
    records = db_query(f"SELECT {sel} {base} {where} ORDER BY a.alarm_time DESC LIMIT ? OFFSET ?",
                       params + [size, offset])
    return ok({'records': records, 'total': total, 'page': page})

@app.put('/api/alarms/<int:aid>/handle')
def handle_alarm_api(aid):
    data = request.get_json() or {}
    now  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    db_exec("UPDATE alarm_record SET status=1, handler=?, handle_note=?, handle_time=? WHERE id=?",
            (data.get('handler', '管理员'), data.get('note', ''), now, aid))
    return ok()


# ── 指令下发接口 ──

@app.post('/api/commands/text')
def send_text():
    data  = request.get_json() or {}
    phone = data.get('phone', '')
    text  = data.get('text', '')
    if not phone or not text:
        return fail('phone 和 text 不能为空')
    with sessions_lock:
        conn = sessions.get(phone)
    if not conn:
        return fail(f'设备不在线: {phone}', 404)
    try:
        body = bytes([0x01]) + text.encode('utf-8')
        conn.sendall(p.encode_message(0x8300, phone, next_serial(), body))
        return ok()
    except Exception as e:
        return fail(str(e))

@app.post('/api/commands/control')
def terminal_control():
    data  = request.get_json() or {}
    phone = data.get('phone', '')
    cmd   = int(data.get('cmd', 1))
    with sessions_lock:
        conn = sessions.get(phone)
    if not conn:
        return fail(f'设备不在线: {phone}', 404)
    try:
        conn.sendall(p.encode_message(0x8105, phone, next_serial(), struct.pack('>I', cmd)))
        return ok()
    except Exception as e:
        return fail(str(e))

@app.post('/api/commands/track')
def location_track():
    data     = request.get_json() or {}
    phone    = data.get('phone', '')
    interval = int(data.get('interval', 30))
    duration = int(data.get('duration', 0))
    with sessions_lock:
        conn = sessions.get(phone)
    if not conn:
        return fail(f'设备不在线: {phone}', 404)
    try:
        conn.sendall(p.encode_message(0x8202, phone, next_serial(), struct.pack('>HI', interval, duration)))
        return ok()
    except Exception as e:
        return fail(str(e))


# ── 辅助：记操作日志 ───────────────────────────────────────────────────────────

def add_op_log(action, detail, ip='127.0.0.1'):
    db_exec("INSERT INTO op_log (action,detail,ip) VALUES (?,?,?)", (action, detail, ip))


# ── SIM 卡接口 ─────────────────────────────────────────────────────────────────

def _sim_days_left(expire_date):
    """距到期天数；None 表示未设置"""
    if not expire_date:
        return None
    try:
        from datetime import date as _date
        delta = (_date.fromisoformat(expire_date) - _date.today()).days
        return delta
    except Exception:
        return None


@app.get('/api/sims')
def list_sims():
    page    = int(request.args.get('page', 1))
    size    = int(request.args.get('size', 20))
    kw      = request.args.get('keyword', '').strip()
    status  = request.args.get('status', '').strip()
    expiring = request.args.get('expiring', '').strip()   # '7' / '30' = 近N天到期
    offset  = (page - 1) * size
    conds, params = [], []
    if kw:
        conds.append("(iccid LIKE ? OR imsi LIKE ? OR operator LIKE ?)")
        like = f'%{kw}%'
        params += [like, like, like]
    if status:
        conds.append("status=?"); params.append(status)
    if expiring:
        try:
            days = int(expiring)
            conds.append("expire_date IS NOT NULL AND expire_date <= date('now',?) AND expire_date >= date('now')")
            params.append(f'+{days} days')
        except ValueError:
            pass
    where = "WHERE " + " AND ".join(conds) if conds else ""
    total   = db_scalar(f"SELECT COUNT(*) FROM sim_card {where}", params)
    records = db_query(f"SELECT * FROM sim_card {where} ORDER BY expire_date ASC, created_at DESC LIMIT ? OFFSET ?",
                       params + [size, offset])
    # 补充剩余天数
    for r in records:
        r['days_left'] = _sim_days_left(r.get('expire_date'))
    return ok({'records': records, 'total': total, 'page': page})


@app.get('/api/sims/expiring_count')
def sim_expiring_count():
    """首页/报表用：统计7天/30天内到期的SIM卡数量"""
    c7  = db_scalar("SELECT COUNT(*) FROM sim_card WHERE expire_date IS NOT NULL "
                    "AND expire_date <= date('now','+7 days') AND expire_date >= date('now')")
    c30 = db_scalar("SELECT COUNT(*) FROM sim_card WHERE expire_date IS NOT NULL "
                    "AND expire_date <= date('now','+30 days') AND expire_date >= date('now')")
    expired = db_scalar("SELECT COUNT(*) FROM sim_card WHERE expire_date IS NOT NULL "
                        "AND expire_date < date('now')")
    return ok({'expiring7': c7, 'expiring30': c30, 'expired': expired})


@app.post('/api/sims')
def create_sim():
    d = request.get_json() or {}
    iccid = (d.get('iccid') or '').strip()
    if not iccid:
        return fail('ICCID 不能为空', 400)
    try:
        db_exec(
            "INSERT INTO sim_card (iccid,imsi,operator,plan,balance,status,device_phone,remark,expire_date,monthly_fee) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (iccid, d.get('imsi',''), d.get('operator','中国移动'), d.get('plan',''),
             float(d.get('balance', 0)), d.get('status','正常'),
             d.get('device_phone',''), d.get('remark',''),
             d.get('expire_date') or None, float(d.get('monthly_fee', 0)))
        )
        add_op_log('SIM新增', f'新增SIM卡 {iccid}')
    except Exception as e:
        return fail(f'ICCID 已存在或参数错误: {e}', 400)
    return ok()


@app.put('/api/sims/<int:sid>')
def update_sim(sid):
    d = request.get_json() or {}
    db_exec(
        "UPDATE sim_card SET imsi=?,operator=?,plan=?,balance=?,status=?,device_phone=?,remark=?,expire_date=?,monthly_fee=? WHERE id=?",
        (d.get('imsi',''), d.get('operator','中国移动'), d.get('plan',''),
         float(d.get('balance', 0)), d.get('status','正常'),
         d.get('device_phone',''), d.get('remark',''),
         d.get('expire_date') or None, float(d.get('monthly_fee', 0)), sid)
    )
    add_op_log('SIM编辑', f'编辑SIM卡 id={sid}')
    return ok()


@app.delete('/api/sims/<int:sid>')
def delete_sim(sid):
    row = db_query_one("SELECT iccid FROM sim_card WHERE id=?", (sid,))
    if not row: return fail('SIM卡不存在', 404)
    db_exec("DELETE FROM sim_card WHERE id=?", (sid,))
    add_op_log('SIM删除', f'删除SIM卡 {row["iccid"]}')
    return ok()

@app.post('/api/sims/<int:sid>/bind')
def bind_sim(sid):
    d = request.get_json() or {}
    phone = d.get('phone', '')
    db_exec("UPDATE sim_card SET device_phone=? WHERE id=?", (phone, sid))
    add_op_log('SIM绑定', f'SIM卡 id={sid} 绑定到设备 {phone or "解绑"}')
    return ok()


# ── 充值接口 ───────────────────────────────────────────────────────────────────

@app.get('/api/recharges')
def list_recharges():
    page   = int(request.args.get('page', 1))
    size   = int(request.args.get('size', 20))
    sim_id = request.args.get('sim_id')
    offset = (page - 1) * size
    if sim_id:
        total   = db_scalar("SELECT COUNT(*) FROM recharge WHERE sim_id=?", (sim_id,))
        records = db_query("SELECT * FROM recharge WHERE sim_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                           (sim_id, size, offset))
    else:
        total   = db_scalar("SELECT COUNT(*) FROM recharge")
        records = db_query("SELECT * FROM recharge ORDER BY created_at DESC LIMIT ? OFFSET ?",
                           (size, offset))
    return ok({'records': records, 'total': total, 'page': page})

@app.post('/api/recharges')
def create_recharge():
    d      = request.get_json() or {}
    sim_id = d.get('sim_id')
    amount = float(d.get('amount', 0))
    if not sim_id or amount <= 0:
        return fail('sim_id 和 amount 不能为空', 400)
    row = db_query_one("SELECT id, iccid, balance, status FROM sim_card WHERE id=?", (sim_id,))
    if not row: return fail('SIM卡不存在', 404)
    new_balance = float(row['balance']) + amount
    new_status  = '正常' if row['status'] == '欠费' and new_balance >= 0 else row['status']
    db_exec("UPDATE sim_card SET balance=?, status=? WHERE id=?", (new_balance, new_status, sim_id))
    db_exec("INSERT INTO recharge (sim_id,iccid,amount,method,plan,remark,operator) VALUES (?,?,?,?,?,?,?)",
            (sim_id, row['iccid'], amount, d.get('method','支付宝'),
             d.get('plan',''), d.get('remark',''), d.get('operator','管理员')))
    add_op_log('充值', f'SIM卡 {row["iccid"]} 充值 ¥{amount:.2f}')
    return ok({'new_balance': new_balance})


# ── 客户管理接口 ───────────────────────────────────────────────────────────────

@app.get('/api/customers')
def list_customers():
    page   = int(request.args.get('page', 1))
    size   = int(request.args.get('size', 20))
    kw     = request.args.get('keyword', '').strip()
    status = request.args.get('status', '').strip()
    # parent_id='null' 查顶级，parent_id='5' 查 id=5 的子级，不传则全量（搜索模式）
    parent_id_param = request.args.get('parent_id', None)
    offset = (page - 1) * size
    sids   = _org_scope_ids(request)

    conds, params = [], []
    if parent_id_param is not None:
        if parent_id_param.lower() == 'null':
            conds.append("parent_id IS NULL")
        else:
            try:
                conds.append("parent_id=?"); params.append(int(parent_id_param))
            except ValueError:
                pass
    if kw:
        conds.append("(name LIKE ? OR contact LIKE ? OR phone LIKE ? OR login_name LIKE ?)")
        like = f'%{kw}%'
        params += [like, like, like, like]
    if status:
        conds.append("status=?"); params.append(status)
    conds, params = _org_where(sids, conds, params)
    where = "WHERE " + " AND ".join(conds) if conds else ""
    total   = db_scalar(f"SELECT COUNT(*) FROM customer {where}", params)
    records = db_query(f"SELECT * FROM customer {where} ORDER BY id ASC LIMIT ? OFFSET ?",
                       params + [size, offset])
    # 补充 has_children（树形展开用）和 parent_name（搜索模式显示归属用）
    parent_cache = {}
    for r in records:
        r['has_children'] = db_scalar(
            "SELECT COUNT(*) FROM customer WHERE parent_id=?", (r['id'],)) > 0
        pid = r.get('parent_id')
        if pid:
            if pid not in parent_cache:
                p = db_query_one("SELECT name FROM customer WHERE id=?", (pid,))
                parent_cache[pid] = p['name'] if p else '—'
            r['parent_name'] = parent_cache[pid]
        else:
            r['parent_name'] = None
    return ok({'records': records, 'total': total, 'page': page})

@app.post('/api/customers')
def create_customer():
    admin  = _current_admin(request)
    scope  = _scope_path(admin)
    # 新客户归属当前管理员所在组织（超管归根）
    admin_org_id = (admin.get('org_id') or 1) if admin else 1
    d    = request.get_json() or {}
    name = (d.get('name') or '').strip()
    if not name: return fail('客户名称不能为空', 400)
    login_name = (d.get('login_name') or '').strip()
    password   = (d.get('password')   or '').strip()
    # 检查登录账号是否已被占用
    if login_name:
        dup = db_query_one("SELECT id FROM customer WHERE login_name=?", (login_name,))
        if dup:
            return fail('登录账号已被占用', 400)
    with _db_lock:
        conn = get_db()
        try:
            cur = conn.execute(
                "INSERT INTO customer (name,contact,phone,email,status,reg_date,remark,"
                "org_id,gender,age,address) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (name, d.get('contact',''), d.get('phone',''), d.get('email',''),
                 d.get('status','活跃'), d.get('reg_date',''), d.get('remark',''), admin_org_id,
                 d.get('gender',''), d.get('age') or None, d.get('address',''))
            )
            new_id = cur.lastrowid
            # 同步设置登录账号/密码（若提供）
            if login_name and password:
                conn.execute("UPDATE customer SET login_name=?, password_hash=? WHERE id=?",
                             (login_name, _hash_pw(password), new_id))
            elif login_name:
                conn.execute("UPDATE customer SET login_name=? WHERE id=?", (login_name, new_id))
            conn.commit()
        finally:
            conn.close()
    add_op_log('客户新增', f'新增客户 {name}')
    return ok({'id': new_id})

@app.put('/api/customers/<int:cid>')
def update_customer(cid):
    d = request.get_json() or {}
    db_exec("UPDATE customer SET name=?,contact=?,phone=?,email=?,status=?,reg_date=?,remark=?,"
            "gender=?,age=?,address=? WHERE id=?",
            (d.get('name',''), d.get('contact',''), d.get('phone',''), d.get('email',''),
             d.get('status','活跃'), d.get('reg_date',''), d.get('remark',''),
             d.get('gender',''), d.get('age') or None, d.get('address',''), cid))
    # 同步更新登录账号/密码（若提供）
    login_name = (d.get('login_name') or '').strip()
    password   = (d.get('password')   or '').strip()
    if login_name:
        # 检查账号是否被其他客户占用
        dup = db_query_one("SELECT id FROM customer WHERE login_name=? AND id!=?", (login_name, cid))
        if dup:
            return fail('登录账号已被占用', 400)
        if password:
            db_exec("UPDATE customer SET login_name=?, password_hash=? WHERE id=?",
                    (login_name, _hash_pw(password), cid))
        else:
            # 只改账号名，不动密码
            db_exec("UPDATE customer SET login_name=? WHERE id=?", (login_name, cid))
    add_op_log('客户编辑', f'编辑客户 id={cid}')
    return ok()

@app.delete('/api/customers/<int:cid>')
def delete_customer(cid):
    row = db_query_one("SELECT name FROM customer WHERE id=?", (cid,))
    if not row: return fail('客户不存在', 404)
    # 1. 将该客户所有下级客户的设备归还到管理员池（NULL），并删除下级客户
    sub_ids = [r['id'] for r in db_query("SELECT id FROM customer WHERE parent_id=?", (cid,))]
    for sid in sub_ids:
        db_exec("UPDATE device SET customer_id=NULL WHERE customer_id=?", (sid,))
        db_exec("DELETE FROM customer WHERE id=?", (sid,))
    # 2. 将该客户自身的设备归还到管理员池
    db_exec("UPDATE device SET customer_id=NULL WHERE customer_id=?", (cid,))
    db_exec("DELETE FROM customer WHERE id=?", (cid,))
    add_op_log('客户删除', f'删除客户 {row["name"]}，已回收设备至管理员设备池')
    return ok()


# ── 电子围栏接口 ───────────────────────────────────────────────────────────────

@app.get('/api/fences')
def list_fences():
    name   = request.args.get('name', '').strip()
    ftype  = request.args.get('fence_type', '').strip()
    sids   = _org_scope_ids(request)
    conds  = ["customer_id IS NULL"]   # 管理员接口只看全局围栏（非客户私建的）
    args   = []
    if name:
        conds.append("name LIKE ?"); args.append(f'%{name}%')
    if ftype:
        conds.append("fence_type=?"); args.append(ftype)
    conds, args = _org_where(sids, conds, args)
    sql = "SELECT * FROM geo_fence WHERE " + " AND ".join(conds) + " ORDER BY created_at DESC"
    records = db_query(sql, tuple(args))
    return ok(records)

@app.post('/api/fences')
def create_fence():
    admin      = _current_admin(request)
    admin_org  = (admin.get('org_id') or 1) if admin else 1
    d          = request.get_json() or {}
    name       = (d.get('name') or '').strip()
    fence_type = d.get('fence_type', 'circle')
    if not name:
        return fail('name 不能为空', 400)
    ae    = 1 if d.get('alarm_enter', True) else 0
    ax    = 1 if d.get('alarm_exit',  True) else 0
    adwl  = int(d.get('alarm_dwell',  0))
    spdl  = int(d.get('speed_limit',  0))
    vs    = str(d.get('valid_start',  '') or '')
    ve    = str(d.get('valid_end',    '') or '')

    EXTRA_COLS = "alarm_enter,alarm_exit,alarm_dwell,speed_limit,valid_start,valid_end,org_id"
    EXTRA_VALS = (ae, ax, adwl, spdl, vs, ve, admin_org)

    if fence_type == 'circle':
        if d.get('lat') is None or d.get('lng') is None:
            return fail('圆形围栏需要 lat/lng', 400)
        db_exec(
            f"INSERT INTO geo_fence (name,fence_type,lat,lng,radius,color,devices,{EXTRA_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (name, 'circle', float(d['lat']), float(d['lng']),
             int(d.get('radius', 2000)), d.get('color', '#409EFF'), d.get('devices', '')) + EXTRA_VALS
        )
    elif fence_type == 'polygon':
        coords = d.get('coordinates')
        if not coords:
            return fail('多边形围栏需要 coordinates', 400)
        import json as _json
        db_exec(
            f"INSERT INTO geo_fence (name,fence_type,lat,lng,coordinates,color,devices,{EXTRA_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (name, 'polygon', 0.0, 0.0, _json.dumps(coords), d.get('color', '#409EFF'), d.get('devices', '')) + EXTRA_VALS
        )
    elif fence_type == 'administrative':
        if not d.get('adcode'):
            return fail('行政区围栏需要 adcode', 400)
        import json as _json
        db_exec(
            f"INSERT INTO geo_fence (name,fence_type,lat,lng,adcode,coordinates,color,devices,{EXTRA_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (name, 'administrative', 0.0, 0.0, str(d['adcode']),
             _json.dumps(d.get('coordinates', [])),
             d.get('color', '#409EFF'), d.get('devices', '')) + EXTRA_VALS
        )
    else:
        return fail('未知围栏类型', 400)
    add_op_log('围栏新增', f'新建{fence_type}围栏 {name}')
    return ok()

@app.delete('/api/fences/<int:fid>')
def delete_fence(fid):
    row = db_query_one("SELECT name FROM geo_fence WHERE id=?", (fid,))
    if not row: return fail('围栏不存在', 404)
    db_exec("DELETE FROM geo_fence WHERE id=?", (fid,))
    add_op_log('围栏删除', f'删除围栏 {row["name"]}')
    return ok()

@app.route('/api/fences/<int:fid>/devices', methods=['PUT'])
def update_fence_devices(fid):
    """更新围栏关联的设备（手机号列表）"""
    row = db_query_one("SELECT name FROM geo_fence WHERE id=?", (fid,))
    if not row: return fail('围栏不存在', 404)
    d = request.get_json() or {}
    phones = d.get('phones', [])            # 传入手机号数组
    devices_str = ','.join(str(p) for p in phones if p)
    db_exec("UPDATE geo_fence SET devices=? WHERE id=?", (devices_str, fid))
    add_op_log('围栏关联设备', f'围栏 {row["name"]} 关联 {len(phones)} 台设备')
    return ok()

@app.post('/api/fences/batch_delete')
def batch_delete_fences():
    d   = request.get_json() or {}
    ids = d.get('ids', [])
    if not ids:
        return fail('ids 不能为空', 400)
    placeholders = ','.join('?' * len(ids))
    db_exec(f"DELETE FROM geo_fence WHERE id IN ({placeholders})", tuple(ids))
    add_op_log('围栏批量删除', f'批量删除 {len(ids)} 条围栏')
    return ok()

# ── 标注点 ────────────────────────────────────────────────────────────────────
@app.get('/api/mark_points')
def list_mark_points():
    name = request.args.get('name', '').strip()
    sql  = "SELECT * FROM mark_point WHERE 1=1"
    args = []
    if name:
        sql += " AND name LIKE ?"; args.append(f'%{name}%')
    sql += " ORDER BY created_at DESC"
    return ok(db_query(sql, tuple(args)))

@app.post('/api/mark_points')
def create_mark_point():
    d = request.get_json() or {}
    name = (d.get('name') or '').strip()
    if not name or d.get('lat') is None or d.get('lng') is None:
        return fail('name/lat/lng 不能为空', 400)
    db_exec(
        "INSERT INTO mark_point (name,lat,lng,remark) VALUES (?,?,?,?)",
        (name, float(d['lat']), float(d['lng']), d.get('remark', ''))
    )
    return ok()

@app.delete('/api/mark_points/<int:mid>')
def delete_mark_point(mid):
    db_exec("DELETE FROM mark_point WHERE id=?", (mid,))
    return ok()

# ── 共享风险点 ────────────────────────────────────────────────────────────────
@app.get('/api/risk_points')
def list_risk_points():
    return ok(db_query("SELECT * FROM risk_point ORDER BY created_at DESC"))

@app.post('/api/risk_points')
def create_risk_point():
    d = request.get_json() or {}
    name = (d.get('name') or '').strip()
    if not name or d.get('lat') is None or d.get('lng') is None:
        return fail('name/lat/lng 不能为空', 400)
    db_exec(
        "INSERT INTO risk_point (name,lat,lng,level,remark) VALUES (?,?,?,?,?)",
        (name, float(d['lat']), float(d['lng']),
         d.get('level', 'medium'), d.get('remark', ''))
    )
    return ok()

@app.delete('/api/risk_points/<int:rid>')
def delete_risk_point(rid):
    db_exec("DELETE FROM risk_point WHERE id=?", (rid,))
    return ok()


# ── 指令历史接口 ───────────────────────────────────────────────────────────────

@app.get('/api/command-history')
def list_command_history():
    page   = int(request.args.get('page', 1))
    size   = int(request.args.get('size', 20))
    phone  = request.args.get('phone', '').strip()
    offset = (page - 1) * size
    if phone:
        total   = db_scalar("SELECT COUNT(*) FROM command_history WHERE phone=?", (phone,))
        records = db_query("SELECT * FROM command_history WHERE phone=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                           (phone, size, offset))
    else:
        total   = db_scalar("SELECT COUNT(*) FROM command_history")
        records = db_query("SELECT * FROM command_history ORDER BY created_at DESC LIMIT ? OFFSET ?",
                           (size, offset))
    return ok({'records': records, 'total': total, 'page': page})

@app.post('/api/command-history')
def create_command_history():
    d = request.get_json() or {}
    db_exec("INSERT INTO command_history (phone,device_name,command,result,response) VALUES (?,?,?,?,?)",
            (d.get('phone',''), d.get('device_name',''), d.get('command',''),
             d.get('result',''), d.get('response','')))
    return ok()


# ── 操作日志接口 ───────────────────────────────────────────────────────────────

@app.get('/api/oplogs')
def list_oplogs():
    page   = int(request.args.get('page', 1))
    size   = int(request.args.get('size', 20))
    offset = (page - 1) * size
    total   = db_scalar("SELECT COUNT(*) FROM op_log")
    records = db_query("SELECT * FROM op_log ORDER BY created_at DESC LIMIT ? OFFSET ?", (size, offset))
    return ok({'records': records, 'total': total, 'page': page})


# ── 报表统计接口 ───────────────────────────────────────────────────────────────

_DATE_RE = _re.compile(r'^\d{4}-\d{2}-\d{2}$')

@app.get('/api/report/summary')
def report_summary():
    # 时间范围参数（默认近30天）
    days      = int(request.args.get('days', 30))
    start_raw = request.args.get('start', '').strip()
    end_raw   = request.args.get('end', '').strip()

    # 日期格式校验，防止 SQL 注入
    if start_raw and (not _DATE_RE.match(start_raw) or not _DATE_RE.match(end_raw)):
        return fail('日期格式错误，应为 YYYY-MM-DD', 400)

    sids = _org_scope_ids(request)

    def _scoped(tbl, extra=''):
        if sids is None:
            return f"FROM {tbl} WHERE 1=1 {extra}"
        if not sids:
            return f"FROM {tbl} WHERE 1=0 {extra}"
        ph = ','.join('?' * len(sids))
        return f"FROM {tbl} WHERE org_id IN ({ph}) {extra}"

    device_total    = db_scalar(f"SELECT COUNT(*) {_scoped('device')}", sids or [])
    device_online   = db_scalar(f"SELECT COUNT(*) {_scoped('device','AND status=1')}", sids or [])
    device_alarm    = db_scalar(f"SELECT COUNT(*) {_scoped('device','AND status=2')}", sids or [])
    device_active   = db_scalar(f"SELECT COUNT(*) {_scoped('device','AND lifecycle=1')}", sids or [])
    device_inactive = db_scalar(f"SELECT COUNT(*) {_scoped('device','AND lifecycle=0')}", sids or [])
    device_disabled = db_scalar(f"SELECT COUNT(*) {_scoped('device','AND lifecycle IN (2,3)')}", sids or [])

    alarm_total     = db_scalar("SELECT COUNT(*) FROM alarm_record")
    alarm_unhandled = db_scalar("SELECT COUNT(*) FROM alarm_record WHERE status=0")
    # 使用参数化查询，防止日期字段 SQL 注入
    if start_raw:
        alarm_period = db_scalar(
            "SELECT COUNT(*) FROM alarm_record WHERE date(alarm_time) BETWEEN ? AND ?",
            [start_raw, end_raw]
        )
    else:
        alarm_period = db_scalar(
            f"SELECT COUNT(*) FROM alarm_record WHERE date(alarm_time) >= date('now','-{days-1} days')"
        )

    sim_total    = db_scalar("SELECT COUNT(*) FROM sim_card")
    sim_normal   = db_scalar("SELECT COUNT(*) FROM sim_card WHERE status='正常'")
    sim_exp7     = db_scalar("SELECT COUNT(*) FROM sim_card WHERE expire_date IS NOT NULL "
                             "AND expire_date <= date('now','+7 days') AND expire_date >= date('now')")
    sim_exp30    = db_scalar("SELECT COUNT(*) FROM sim_card WHERE expire_date IS NOT NULL "
                             "AND expire_date <= date('now','+30 days') AND expire_date >= date('now')")
    sim_expired  = db_scalar("SELECT COUNT(*) FROM sim_card WHERE expire_date IS NOT NULL "
                             "AND expire_date < date('now')")

    customer_total = db_scalar("SELECT COUNT(*) FROM customer")
    loc_total      = db_scalar("SELECT COUNT(*) FROM location_record")
    recharge_total = db_scalar("SELECT COALESCE(SUM(amount),0) FROM recharge")
    if start_raw:
        recharge_period = db_scalar(
            "SELECT COALESCE(SUM(amount),0) FROM recharge WHERE date(created_at) BETWEEN ? AND ?",
            [start_raw, end_raw]
        )
    else:
        recharge_period = db_scalar(
            f"SELECT COALESCE(SUM(amount),0) FROM recharge WHERE date(created_at) >= date('now','-{days-1} days')"
        )

    # 趋势：按实际天数
    trend_days = min(days, 30)
    alarm_trend = db_query(
        f"SELECT date(alarm_time) as day, COUNT(*) as cnt FROM alarm_record "
        f"WHERE date(alarm_time) >= date('now','-{trend_days-1} days') GROUP BY day ORDER BY day"
    )
    alarm_types = db_query(
        "SELECT alarm_desc, COUNT(*) as cnt FROM alarm_record GROUP BY alarm_desc ORDER BY cnt DESC LIMIT 6"
    )
    loc_trend = db_query(
        f"SELECT date(gps_time) as day, COUNT(*) as cnt FROM location_record "
        f"WHERE date(gps_time) >= date('now','-{trend_days-1} days') GROUP BY day ORDER BY day"
    )

    # 客户排名（按名下设备数）
    customer_rank = db_query(
        "SELECT c.name, COUNT(d.id) as device_count "
        "FROM customer c LEFT JOIN device d ON d.customer_id=c.id "
        "GROUP BY c.id ORDER BY device_count DESC LIMIT 10"
    )

    # 本月新增设备 / 新增客户
    new_devices   = db_scalar("SELECT COUNT(*) FROM device WHERE date(created_at) >= date('now','start of month')")
    new_customers = db_scalar("SELECT COUNT(*) FROM customer WHERE date(created_at) >= date('now','start of month')")

    return ok({
        'device': {
            'total': device_total, 'online': device_online, 'alarm': device_alarm,
            'active': device_active, 'inactive': device_inactive, 'disabled': device_disabled,
            'new_this_month': new_devices,
        },
        'alarm':  {'total': alarm_total, 'unhandled': alarm_unhandled, 'period': alarm_period},
        'sim':    {
            'total': sim_total, 'normal': sim_normal,
            'expiring7': sim_exp7, 'expiring30': sim_exp30, 'expired': sim_expired,
        },
        'customer': {'total': customer_total, 'new_this_month': new_customers, 'rank': customer_rank},
        'location': {'total': loc_total},
        'recharge_total':  float(recharge_total),
        'recharge_period': float(recharge_period),
        'alarm_trend': alarm_trend,
        'alarm_types': alarm_types,
        'loc_trend':   loc_trend,
        'trend_days':  trend_days,
    })


# ── Socket.IO 事件 ─────────────────────────────────────────────────────────────

# ── Socket.IO 多租户隔离：按设备 org_id 分房间推送 ────────────────────────────
_device_org_cache: dict = {}   # phone → org_id，按需缓存，避免每次推送都查库

def _get_device_org(phone: str) -> int:
    """返回设备所属 org_id（先查缓存，再查库，找不到默认 1）"""
    oid = _device_org_cache.get(phone)
    if oid:
        return oid
    row = db_query_one("SELECT org_id FROM device WHERE phone=?", (phone,))
    oid = int(row.get('org_id') or 1) if row else 1
    _device_org_cache[phone] = oid
    return oid

def _sio_emit(event: str, data: dict, phone: str):
    """
    向设备所在组织的管理员和超级管理员推送 Socket.IO 事件。
    超管加入 'broadcast' 房间；普通管理员/客户加入 'org_{id}' 房间。
    """
    org_id = _get_device_org(phone)
    socketio.emit(event, data, room=f'org_{org_id}')  # 该组织管理员/客户
    socketio.emit(event, data, room='broadcast')        # 超级管理员


@socketio.on('connect')
def on_connect():
    """验证 token，加入对应 org 房间；未认证则拒绝连接。"""
    token = request.args.get('token', '')
    if not token:
        return False   # 拒绝匿名连接

    # ── 管理员 token ──
    admin_id = _verify_admin_token(token)
    if admin_id:
        admin = db_query_one(
            "SELECT org_id, user_type FROM admin_user WHERE id=?", (admin_id,))
        if not admin:
            return False
        if (admin.get('user_type') or 0) >= 9:       # 超级管理员
            join_room('broadcast')
        else:                                          # 普通/子组织管理员
            scope = _scope_path(admin)
            for o in (_orgs_in_scope(scope) if scope else []):
                join_room(f'org_{o["id"]}')
        log.debug("[WS] 管理员 %d 已连接", admin_id)
        return True

    # ── 客户门户 token ──
    cid = _verify_token(token)
    if cid:
        cust = db_query_one("SELECT org_id FROM customer WHERE id=?", (cid,))
        if not cust:
            return False
        join_room(f'org_{int(cust.get("org_id") or 1)}')
        log.debug("[WS] 客户 %d 已连接", cid)
        return True

    return False   # 未知/过期 token，拒绝


@socketio.on('disconnect')
def on_disconnect():
    log.debug("[WS] 客户端断开")


@app.get('/api/fences/check/<path:phone>')
def debug_fence_check(phone):
    """
    调试端点：手动对某设备执行一次围栏检测，返回每个围栏的 inside 状态。
    用法：GET /api/fences/check/13800138001
    """
    row = db_query_one("SELECT last_lat, last_lng FROM device WHERE phone=?", (phone,))
    if not row:
        return fail(f'设备不存在: {phone}', 404)
    lat, lng = row['last_lat'], row['last_lng']
    if not lat or not lng:
        return fail('设备无 GPS 坐标，尚未上报位置', 400)

    fences = db_query(
        """SELECT id, name, fence_type, lat, lng, radius, coordinates,
                  COALESCE(alarm_enter,1) alarm_enter,
                  COALESCE(alarm_exit,1)  alarm_exit
           FROM geo_fence
           WHERE devices = ? OR devices LIKE ? OR devices LIKE ? OR devices LIKE ?""",
        (phone, f'{phone},%', f'%,{phone}', f'%,{phone},%')
    )
    result = []
    for f in fences:
        inside = _is_inside_fence(lat, lng, f)
        result.append({
            'fence_id':    f['id'],
            'fence_name':  f['name'],
            'fence_type':  f['fence_type'],
            'inside':      inside,
            'alarm_enter': bool(f['alarm_enter']),
            'alarm_exit':  bool(f['alarm_exit']),
        })

    # 顺便把当前内存里的状态也带出来
    prev = list(fence_device_inside.get(phone, set()))
    return ok({'device': phone, 'lat': lat, 'lng': lng,
               'fences_checked': result,
               'memory_inside_ids': prev})


@app.get('/api/_routes')
def debug_routes():
    """列出所有注册路由（调试用）"""
    routes = [{'rule': str(r.rule), 'methods': sorted(r.methods)} for r in app.url_map.iter_rules()]
    return jsonify(routes)


# ── 管理员登录接口 ─────────────────────────────────────────────────────────────

_ADMIN_SECRET = os.environ.get('ADMIN_SECRET', 'admin_secret_quectel_2024')

def _make_admin_token(admin_id: int) -> str:
    ts  = int(_time_mod.time())
    raw = f"admin:{admin_id}:{ts}"
    sig = hashlib.sha256(f"{raw}:{_ADMIN_SECRET}".encode()).hexdigest()[:20]
    return base64.b64encode(f"{raw}:{sig}".encode()).decode()

def _verify_admin_token(token: str):
    try:
        decoded = base64.b64decode(token).decode()
        _, aid_s, ts_s, sig = decoded.rsplit(':', 3)
        raw = f"admin:{aid_s}:{ts_s}"
        expected = hashlib.sha256(f"{raw}:{_ADMIN_SECRET}".encode()).hexdigest()[:20]
        if sig != expected:
            return None
        if _time_mod.time() - int(ts_s) > 30 * 24 * 3600:
            return None
        return int(aid_s)
    except Exception:
        return None

@app.before_request
def _require_admin_for_api():
    """所有 /api/* 接口（除 /api/auth/* 和 /api/customer/*）都必须携带有效的管理员 token。"""
    path = request.path
    # 只拦截 /api/ 下的接口
    if not path.startswith('/api/'):
        return None
    # 放行 CORS 预检
    if request.method == 'OPTIONS':
        return None
    # 放行：认证接口（登录）
    if path.startswith('/api/auth/'):
        return None
    # 放行：客户门户（有独立的客户 token 校验）
    if path.startswith('/api/customer/'):
        return None
    # 其余所有管理员接口：校验 X-Admin-Token
    token = request.headers.get('X-Admin-Token', '')
    if not token or not _verify_admin_token(token):
        return fail('未授权，请先登录', 401)
    return None


@app.post('/api/auth/login')
def admin_login():
    d        = request.get_json() or {}
    username = (d.get('username') or '').strip()
    password = d.get('password', '')
    if not username or not password:
        return fail('账号和密码不能为空', 400)
    row = db_query_one(
        "SELECT id, username, real_name, org_id, org_level, user_type "
        "FROM admin_user WHERE username=? AND password_hash=? AND COALESCE(is_active,1)=1",
        (username, _hash_pw(password))
    )
    if not row:
        return fail('账号或密码错误', 401)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    db_exec("UPDATE admin_user SET last_login=? WHERE id=?", (now, row['id']))
    token = _make_admin_token(row['id'])
    add_op_log('管理员登录', f'{row["username"]} 登录')
    return ok({
        'token':    token,
        'userId':   row['id'],
        'username': row['username'],
        'realName': row.get('real_name'),
        'orgId':    row.get('org_id') or 1,
        'orgLevel': row.get('org_level') or 1,
        'userType': row.get('user_type') or 9,
    })

@app.post('/api/auth/change_password')
def admin_change_password():
    token = request.headers.get('X-Admin-Token', '')
    if not _verify_admin_token(token):
        return fail('未授权', 401)
    d       = request.get_json() or {}
    old_pwd = d.get('old_password', '')
    new_pwd = (d.get('new_password') or '').strip()
    if not new_pwd or len(new_pwd) < 6:
        return fail('新密码不能少于6位', 400)
    admin_id = _verify_admin_token(token)
    row = db_query_one("SELECT id FROM admin_user WHERE id=? AND password_hash=?",
                       (admin_id, _hash_pw(old_pwd)))
    if not row:
        return fail('原密码错误', 400)
    db_exec("UPDATE admin_user SET password_hash=? WHERE id=?", (_hash_pw(new_pwd), admin_id))
    return ok()


# ── 客户门户：账号 / Token 工具 ────────────────────────────────────────────────

_PORTAL_SECRET = os.environ.get('PORTAL_SECRET', 'portal_secret_quectel_2024')

def _make_token(customer_id: int) -> str:
    ts  = int(_time_mod.time())
    raw = f"{customer_id}:{ts}"
    sig = hashlib.sha256(f"{raw}:{_PORTAL_SECRET}".encode()).hexdigest()[:20]
    return base64.b64encode(f"{raw}:{sig}".encode()).decode()

def _verify_token(token: str):
    """返回 customer_id(int) 或 None（无效/过期）"""
    try:
        decoded = base64.b64decode(token).decode()
        cid_s, ts_s, sig = decoded.rsplit(':', 2)
        raw = f"{cid_s}:{ts_s}"
        expected = hashlib.sha256(f"{raw}:{_PORTAL_SECRET}".encode()).hexdigest()[:20]
        if sig != expected:
            return None
        if _time_mod.time() - int(ts_s) > 30 * 24 * 3600:  # 30天有效
            return None
        return int(cid_s)
    except Exception:
        return None

def _get_portal_customer():
    """从请求头或 query 里拿 token，返回 customer_id 或 None"""
    token = request.headers.get('X-Customer-Token') or request.args.get('token', '')
    return _verify_token(token) if token else None


# ── 客户门户：API ──────────────────────────────────────────────────────────────

@app.post('/api/customer/login')
def portal_login():
    d          = request.get_json() or {}
    login_name = (d.get('login_name') or '').strip()
    password   = d.get('password', '')
    if not login_name or not password:
        return fail('账号和密码不能为空', 400)
    row = db_query_one(
        "SELECT id, name, login_name FROM customer WHERE login_name=? AND password_hash=?",
        (login_name, _hash_pw(password))
    )
    if not row:
        return fail('账号或密码错误', 401)
    token = _make_token(row['id'])
    return ok({'token': token, 'customer': {'id': row['id'], 'name': row['name'], 'login_name': row['login_name']}})

@app.get('/api/customer/me')
def portal_me():
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    row = db_query_one("SELECT id, name, login_name, contact, phone, email FROM customer WHERE id=?", (cid,))
    return ok(row)

@app.get('/api/customer/devices')
def portal_devices():
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    all_cids = _get_all_descendant_cids(cid)
    cid_ph   = ','.join('?' * len(all_cids))
    records  = db_query(
        f"SELECT phone, name, last_lat, last_lng, last_speed, last_location_time, status "
        f"FROM device WHERE customer_id IN ({cid_ph})",
        all_cids
    )
    return ok(records)

@app.get('/api/customer/locations/<path:phone>/latest')
def portal_latest_location(phone):
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    # 确认设备属于该客户（含后代客户）
    all_cids = _get_all_descendant_cids(cid)
    cid_ph   = ','.join('?' * len(all_cids))
    dev = db_query_one(f"SELECT id FROM device WHERE phone=? AND customer_id IN ({cid_ph})",
                       [phone] + all_cids)
    if not dev:
        return fail('设备不存在或无权限', 403)
    row = db_query_one("SELECT * FROM location_record WHERE phone=? ORDER BY gps_time DESC LIMIT 1", (phone,))
    return ok(row)


@app.get('/api/customer/locations/<path:phone>/history')
def portal_location_history(phone):
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    all_cids = _get_all_descendant_cids(cid)
    cid_ph   = ','.join('?' * len(all_cids))
    dev = db_query_one(f"SELECT id FROM device WHERE phone=? AND customer_id IN ({cid_ph})",
                       [phone] + all_cids)
    if not dev:
        return fail('设备不存在或无权限', 403)
    page   = int(request.args.get('page', 1))
    size   = int(request.args.get('size', 500))
    start  = request.args.get('start')
    end    = request.args.get('end')
    offset = (page - 1) * size
    if start and end:
        total   = db_scalar("SELECT COUNT(*) FROM location_record WHERE phone=? AND gps_time BETWEEN ? AND ?",
                             (phone, start, end))
        records = db_query("SELECT * FROM location_record WHERE phone=? AND gps_time BETWEEN ? AND ? ORDER BY gps_time ASC LIMIT ? OFFSET ?",
                           (phone, start, end, size, offset))
    else:
        total   = db_scalar("SELECT COUNT(*) FROM location_record WHERE phone=?", (phone,))
        records = db_query("SELECT * FROM location_record WHERE phone=? ORDER BY gps_time DESC LIMIT ? OFFSET ?",
                           (phone, size, offset))
    return ok({'records': records, 'total': total, 'page': page})


@app.get('/api/customer/alarms')
def portal_alarms():
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    phones = _get_subtree_phones(cid)
    if not phones:
        return ok({'records': [], 'total': 0})
    page   = int(request.args.get('page', 1))
    size   = int(request.args.get('size', 20))
    offset = (page - 1) * size
    ph     = ','.join('?' * len(phones))
    total   = db_scalar(f"SELECT COUNT(*) FROM alarm_record WHERE phone IN ({ph})", phones)
    records = db_query(f"SELECT * FROM alarm_record WHERE phone IN ({ph}) ORDER BY alarm_time DESC LIMIT ? OFFSET ?",
                       phones + [size, offset])
    return ok({'records': records, 'total': total, 'page': page})


@app.post('/api/customer/commands/text')
def portal_send_text():
    """客户向自己名下的设备发送文本指令"""
    cid  = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    data  = request.get_json() or {}
    phone = (data.get('phone') or '').strip()
    text  = (data.get('text')  or '').strip()
    if not phone or not text:
        return fail('phone 和 text 不能为空', 400)
    all_cids = _get_all_descendant_cids(cid)
    cid_ph   = ','.join('?' * len(all_cids))
    dev = db_query_one(f"SELECT id, name FROM device WHERE phone=? AND customer_id IN ({cid_ph})",
                       [phone] + all_cids)
    if not dev:
        return fail('设备不存在或无权限', 403)
    with sessions_lock:
        conn = sessions.get(phone)
    if not conn:
        return fail(f'设备不在线: {phone}', 404)
    try:
        body = bytes([0x01]) + text.encode('utf-8')
        conn.sendall(p.encode_message(0x8300, phone, next_serial(), body))
        db_exec("INSERT INTO command_history (phone,device_name,command,result) VALUES (?,?,?,?)",
                (phone, dev['name'] or phone, text, '已发送'))
        return ok()
    except Exception as e:
        return fail(str(e))


@app.get('/api/customer/commands/history')
def portal_command_history():
    """客户查看自己名下设备的指令历史"""
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    phones = _get_subtree_phones(cid)
    if not phones:
        return ok({'records': [], 'total': 0})
    phone  = request.args.get('phone', '').strip()
    page   = int(request.args.get('page', 1))
    size   = int(request.args.get('size', 20))
    offset = (page - 1) * size
    if phone and phone in phones:
        total   = db_scalar("SELECT COUNT(*) FROM command_history WHERE phone=?", (phone,))
        records = db_query("SELECT * FROM command_history WHERE phone=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                           (phone, size, offset))
    else:
        ph      = ','.join('?' * len(phones))
        total   = db_scalar(f"SELECT COUNT(*) FROM command_history WHERE phone IN ({ph})", phones)
        records = db_query(f"SELECT * FROM command_history WHERE phone IN ({ph}) ORDER BY created_at DESC LIMIT ? OFFSET ?",
                           phones + [size, offset])
    return ok({'records': records, 'total': total, 'page': page})


# ── 客户门户：概览统计 ────────────────────────────────────────────────────────

@app.get('/api/customer/summary')
def portal_summary():
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    phones = _get_subtree_phones(cid)
    total  = len(phones)
    if not phones:
        return ok({'total': 0, 'online': 0, 'offline': 0, 'alarm': 0})
    ph     = ','.join('?' * len(phones))
    online  = db_scalar(f"SELECT COUNT(*) FROM device WHERE phone IN ({ph}) AND status=1", phones)
    offline = db_scalar(f"SELECT COUNT(*) FROM device WHERE phone IN ({ph}) AND status=0", phones)
    alarm   = db_scalar(f"SELECT COUNT(*) FROM device WHERE phone IN ({ph}) AND status=2", phones)
    return ok({'total': total, 'online': online, 'offline': offline, 'alarm': alarm})


# ── 客户门户：设备列表（带分页 / 搜索） ──────────────────────────────────────

@app.get('/api/customer/device_list')
def portal_device_list():
    """带分页+关键词的设备列表，供前端表格使用"""
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    page    = int(request.args.get('page', 1))
    size    = int(request.args.get('size', 20))
    keyword = request.args.get('keyword', '').strip()
    offset  = (page - 1) * size
    base_cols = ("SELECT id, phone, name, plate_no, manufacturer, terminal_model, "
                 "last_lat, last_lng, last_speed, last_location_time, status, customer_id "
                 "FROM device")
    all_cids = _get_all_descendant_cids(cid)
    cid_ph   = ','.join('?' * len(all_cids))
    if keyword:
        kw      = f'%{keyword}%'
        total   = db_scalar(f"SELECT COUNT(*) FROM device WHERE customer_id IN ({cid_ph}) AND (name LIKE ? OR phone LIKE ?)",
                            all_cids + [kw, kw])
        records = db_query(f"{base_cols} WHERE customer_id IN ({cid_ph}) AND (name LIKE ? OR phone LIKE ?) "
                           f"ORDER BY id LIMIT ? OFFSET ?",
                           all_cids + [kw, kw, size, offset])
    else:
        total   = db_scalar(f"SELECT COUNT(*) FROM device WHERE customer_id IN ({cid_ph})", all_cids)
        records = db_query(f"{base_cols} WHERE customer_id IN ({cid_ph}) ORDER BY id LIMIT ? OFFSET ?",
                           all_cids + [size, offset])
    return ok({'records': records, 'total': total, 'page': page})


# ── 客户门户：编辑自己名下设备（名称 / 备注） ─────────────────────────────────

@app.put('/api/customer/devices/<path:phone>/update')
def portal_update_device(phone):
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    all_cids = _get_all_descendant_cids(cid)
    cid_ph   = ','.join('?' * len(all_cids))
    dev = db_query_one(f"SELECT id FROM device WHERE phone=? AND customer_id IN ({cid_ph})",
                       [phone] + all_cids)
    if not dev:
        return fail('设备不存在或无权限', 403)
    d = request.get_json() or {}
    name     = d.get('name', '')
    plate_no = d.get('plateNo', d.get('plate_no', ''))
    db_exec("UPDATE device SET name=?, plate_no=?, updated_at=? WHERE phone=?",
            (name, plate_no, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), phone))
    return ok()


# ── 客户门户：处理报警 ────────────────────────────────────────────────────────

@app.put('/api/customer/alarms/<int:aid>/handle')
def portal_handle_alarm(aid):
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    phones = _get_subtree_phones(cid)
    alarm  = db_query_one("SELECT id, phone FROM alarm_record WHERE id=?", (aid,))
    if not alarm or alarm['phone'] not in phones:
        return fail('无权限', 403)
    d = request.get_json() or {}
    db_exec("UPDATE alarm_record SET status=1, handler=?, handle_time=?, handle_note=? WHERE id=?",
            (d.get('handler', '客户'), datetime.now().strftime('%Y-%m-%d %H:%M:%S'), d.get('note', ''), aid))
    return ok()


# ── 客户门户：下级客户 CRUD ────────────────────────────────────────────────────

@app.get('/api/customer/sub_customers')
def portal_list_sub_customers():
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    page    = int(request.args.get('page', 1))
    size    = int(request.args.get('size', 20))
    keyword = request.args.get('keyword', '').strip()
    offset  = (page - 1) * size
    if keyword:
        kw      = f'%{keyword}%'
        total   = db_scalar("SELECT COUNT(*) FROM customer WHERE parent_id=? AND id!=? AND (name LIKE ? OR contact LIKE ? OR phone LIKE ?)",
                            (cid, cid, kw, kw, kw))
        rows    = db_query("SELECT id,name,contact,phone,email,status,login_name,remark,created_at "
                           "FROM customer WHERE parent_id=? AND id!=? AND (name LIKE ? OR contact LIKE ? OR phone LIKE ?) "
                           "ORDER BY id DESC LIMIT ? OFFSET ?",
                           (cid, cid, kw, kw, kw, size, offset))
    else:
        total   = db_scalar("SELECT COUNT(*) FROM customer WHERE parent_id=? AND id!=?", (cid, cid))
        rows    = db_query("SELECT id,name,contact,phone,email,status,login_name,remark,created_at "
                           "FROM customer WHERE parent_id=? AND id!=? ORDER BY id DESC LIMIT ? OFFSET ?",
                           (cid, cid, size, offset))
    for r in rows:
        r['device_count'] = db_scalar("SELECT COUNT(*) FROM device WHERE customer_id=?", (r['id'],))
    return ok({'records': rows, 'total': total, 'page': page})


@app.post('/api/customer/sub_customers')
def portal_create_sub_customer():
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    d = request.get_json() or {}
    name       = (d.get('name') or '').strip()
    login_name = (d.get('login_name') or '').strip()
    password   = (d.get('password') or '').strip()
    if not name:
        return fail('名称不能为空', 400)
    if login_name:
        dup = db_query_one("SELECT id FROM customer WHERE login_name=?", (login_name,))
        if dup:
            return fail('登录账号已被占用', 400)
    pw_hash = _hash_pw(password) if password else None
    now     = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with _db_lock:
        conn = get_db()
        try:
            cur = conn.execute(
                "INSERT INTO customer (name,contact,phone,email,status,remark,login_name,password_hash,parent_id,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (name, d.get('contact',''), d.get('phone',''), d.get('email',''),
                 '活跃', d.get('remark',''), login_name or None, pw_hash, cid, now)
            )
            conn.commit()
            return ok({'id': cur.lastrowid})
        finally:
            conn.close()


@app.put('/api/customer/sub_customers/<int:sid>')
def portal_update_sub_customer(sid):
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    if not db_query_one("SELECT id FROM customer WHERE id=? AND parent_id=?", (sid, cid)):
        return fail('无权限或不存在', 404)
    d          = request.get_json() or {}
    name       = (d.get('name') or '').strip()
    login_name = (d.get('login_name') or '').strip()
    password   = (d.get('password') or '').strip()
    if not name:
        return fail('名称不能为空', 400)
    if login_name:
        dup = db_query_one("SELECT id FROM customer WHERE login_name=? AND id!=?", (login_name, sid))
        if dup:
            return fail('登录账号已被占用', 400)
    existing = db_query_one("SELECT password_hash FROM customer WHERE id=?", (sid,))
    new_hash = _hash_pw(password) if password else existing['password_hash']
    db_exec("UPDATE customer SET name=?,contact=?,phone=?,email=?,remark=?,login_name=?,password_hash=?,status=? WHERE id=?",
            (name, d.get('contact',''), d.get('phone',''), d.get('email',''),
             d.get('remark',''), login_name or None, new_hash, d.get('status','活跃'), sid))
    return ok()


@app.delete('/api/customer/sub_customers/<int:sid>')
def portal_delete_sub_customer(sid):
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    if not db_query_one("SELECT id FROM customer WHERE id=? AND parent_id=?", (sid, cid)):
        return fail('无权限或不存在', 404)
    # 设备回归父客户设备池（不归 NULL，归父客户）
    db_exec("UPDATE device SET customer_id=? WHERE customer_id=?", (cid, sid))
    db_exec("DELETE FROM customer WHERE id=?", (sid,))
    return ok()


@app.get('/api/customer/sub_customers/<int:sid>/devices')
def portal_sub_customer_devices(sid):
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    if not db_query_one("SELECT id FROM customer WHERE id=? AND parent_id=?", (sid, cid)):
        return fail('无权限或不存在', 404)
    records = db_query(
        "SELECT id,phone,name,status,last_lat,last_lng,last_location_time FROM device WHERE customer_id=?", (sid,)
    )
    return ok(records)


@app.put('/api/customer/sub_customers/<int:sid>/devices')
def portal_assign_sub_customer_devices(sid):
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    if not db_query_one("SELECT id FROM customer WHERE id=? AND parent_id=?", (sid, cid)):
        return fail('无权限或不存在', 404)
    d      = request.get_json() or {}
    phones = d.get('phones', [])
    # 只能分配归属自己或该子客户的设备
    allowed = {r['phone'] for r in db_query(
        "SELECT phone FROM device WHERE customer_id=? OR customer_id=?", (cid, sid)
    )}
    phones = [p for p in phones if p in allowed]
    # 先把该子客户的设备归还给父客户，再重新分配
    db_exec("UPDATE device SET customer_id=? WHERE customer_id=?", (cid, sid))
    for phone in phones:
        db_exec("UPDATE device SET customer_id=? WHERE phone=?", (sid, phone))
    return ok()


# ── 客户门户：电子围栏 CRUD（customer_id 隔离） ───────────────────────────────

@app.get('/api/customer/fences')
def portal_list_fences():
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    # 返回该客户创建的围栏
    rows = db_query("SELECT * FROM geo_fence WHERE customer_id=? ORDER BY id DESC", (cid,))
    return ok(rows)


@app.post('/api/customer/fences')
def portal_create_fence():
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    d       = request.get_json() or {}
    name    = (d.get('name') or '').strip()
    if not name:
        return fail('围栏名称不能为空', 400)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with _db_lock:
        conn = get_db()
        try:
            cur = conn.execute(
                "INSERT INTO geo_fence (name,fence_type,lat,lng,radius,coordinates,color,devices,customer_id,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (name, d.get('fence_type','circle'), d.get('lat'), d.get('lng'),
                 d.get('radius', 2000), d.get('coordinates'), d.get('color','#409EFF'),
                 d.get('devices',''), cid, now)
            )
            conn.commit()
            return ok({'id': cur.lastrowid})
        finally:
            conn.close()


@app.put('/api/customer/fences/<int:fid>')
def portal_update_fence(fid):
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    if not db_query_one("SELECT id FROM geo_fence WHERE id=? AND customer_id=?", (fid, cid)):
        return fail('无权限或不存在', 404)
    d = request.get_json() or {}
    db_exec("UPDATE geo_fence SET name=?,fence_type=?,lat=?,lng=?,radius=?,coordinates=?,color=? WHERE id=?",
            (d.get('name'), d.get('fence_type'), d.get('lat'), d.get('lng'),
             d.get('radius',2000), d.get('coordinates'), d.get('color','#409EFF'), fid))
    return ok()


@app.delete('/api/customer/fences/<int:fid>')
def portal_delete_fence(fid):
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    if not db_query_one("SELECT id FROM geo_fence WHERE id=? AND customer_id=?", (fid, cid)):
        return fail('无权限或不存在', 404)
    db_exec("DELETE FROM geo_fence WHERE id=?", (fid,))
    return ok()


@app.put('/api/customer/fences/<int:fid>/devices')
def portal_fence_devices(fid):
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    if not db_query_one("SELECT id FROM geo_fence WHERE id=? AND customer_id=?", (fid, cid)):
        return fail('无权限或不存在', 404)
    d      = request.get_json() or {}
    phones = d.get('phones', [])
    # 只能关联整个子树的设备（含后代客户）
    my_phones = set(_get_subtree_phones(cid))
    phones = [p for p in phones if p in my_phones]
    db_exec("UPDATE geo_fence SET devices=? WHERE id=?", (','.join(phones), fid))
    return ok()


# ── 客户门户：全量设备池（自己直属 + 所有子账号持有，供分配界面使用） ──────────

@app.get('/api/customer/pool_devices')
def portal_pool_devices():
    """返回该客户整个层级下的所有设备（自己的 + 各子账号的），供分配设备弹窗使用"""
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    # 递归取所有后代客户 id（含自身）
    all_ids = _get_all_descendant_cids(cid)
    ph      = ','.join('?' * len(all_ids))
    records = db_query(
        f"SELECT d.id, d.phone, d.name, d.status, d.customer_id, "
        f"c.name AS holder_name "
        f"FROM device d "
        f"LEFT JOIN customer c ON d.customer_id = c.id "
        f"WHERE d.customer_id IN ({ph}) "
        f"ORDER BY d.id",
        all_ids
    )
    return ok(records)


# ── 客户门户：SIM 卡（只读自己名下设备的SIM，可改信息/充值，不能新增/绑定/删除） ──

def _get_all_descendant_cids(cid):
    """获取 cid 及其所有后代客户的 ID 列表（递归 CTE，支持任意层级）"""
    rows = db_query(
        "WITH RECURSIVE tree(id) AS ("
        "  SELECT ? "
        "  UNION ALL "
        "  SELECT c.id FROM customer c JOIN tree t ON c.parent_id = t.id"
        ") SELECT id FROM tree",
        (cid,)
    )
    return [r['id'] for r in rows]


def _get_subtree_phones(cid):
    """获取 cid 及所有后代客户名下的所有设备号"""
    all_cids = _get_all_descendant_cids(cid)
    if not all_cids:
        return []
    ph = ','.join('?' * len(all_cids))
    return [r['phone'] for r in db_query(f"SELECT phone FROM device WHERE customer_id IN ({ph})", all_cids)]


def _portal_sim_phones(cid):
    """返回该客户及所有后代客户名下所有设备的手机号集合（递归）"""
    return _get_subtree_phones(cid)


@app.get('/api/customer/sims')
def portal_list_sims():
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    phones = _portal_sim_phones(cid)
    if not phones:
        return ok({'records': [], 'total': 0, 'page': 1})
    page    = int(request.args.get('page', 1))
    size    = int(request.args.get('size', 20))
    keyword = request.args.get('keyword', '').strip()
    status  = request.args.get('status',  '').strip()
    offset  = (page - 1) * size
    ph      = ','.join('?' * len(phones))
    cond    = f"device_phone IN ({ph})"
    args    = phones[:]
    if keyword:
        kw    = f'%{keyword}%'
        cond += " AND (iccid LIKE ? OR imsi LIKE ? OR operator LIKE ?)"
        args += [kw, kw, kw]
    if status:
        cond += " AND status=?"
        args.append(status)
    total   = db_scalar(f"SELECT COUNT(*) FROM sim_card WHERE {cond}", args)
    records = db_query(f"SELECT * FROM sim_card WHERE {cond} ORDER BY id LIMIT ? OFFSET ?",
                       args + [size, offset])
    return ok({'records': records, 'total': total, 'page': page})


@app.put('/api/customer/sims/<int:sid>')
def portal_update_sim(sid):
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    phones = _portal_sim_phones(cid)
    sim    = db_query_one("SELECT id, device_phone FROM sim_card WHERE id=?", (sid,))
    if not sim or sim['device_phone'] not in phones:
        return fail('无权限', 403)
    d = request.get_json() or {}
    db_exec("UPDATE sim_card SET operator=?, plan=?, status=?, remark=? WHERE id=?",
            (d.get('operator', '中国移动'), d.get('plan', ''),
             d.get('status', '正常'), d.get('remark', ''), sid))
    return ok()


@app.post('/api/customer/sims/<int:sid>/recharge')
def portal_recharge_sim(sid):
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    phones = _portal_sim_phones(cid)
    sim    = db_query_one("SELECT id, iccid, device_phone, balance, status FROM sim_card WHERE id=?", (sid,))
    if not sim or sim['device_phone'] not in phones:
        return fail('无权限', 403)
    d      = request.get_json() or {}
    amount = float(d.get('amount', 0))
    if amount <= 0:
        return fail('充值金额必须大于 0', 400)
    new_balance = round(float(sim['balance']) + amount, 2)
    new_status  = '正常' if sim['status'] == '欠费' and new_balance >= 0 else sim['status']
    db_exec("UPDATE sim_card SET balance=?, status=? WHERE id=?", (new_balance, new_status, sid))
    cust = db_query_one("SELECT name FROM customer WHERE id=?", (cid,))
    db_exec("INSERT INTO recharge (sim_id, iccid, amount, method, plan, remark, operator) "
            "VALUES (?,?,?,?,?,?,?)",
            (sid, sim['iccid'], amount, d.get('method', '支付宝'),
             d.get('plan', ''), d.get('remark', ''),
             cust['name'] if cust else '客户'))
    return ok({'new_balance': new_balance})


# ── 客户门户：充值记录（仅自己名下SIM的记录） ────────────────────────────────────

@app.get('/api/customer/recharges')
def portal_list_recharges():
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    phones = _portal_sim_phones(cid)
    if not phones:
        return ok({'records': [], 'total': 0, 'page': 1})
    ph      = ','.join('?' * len(phones))
    sim_ids = [r['id'] for r in db_query(f"SELECT id FROM sim_card WHERE device_phone IN ({ph})", phones)]
    if not sim_ids:
        return ok({'records': [], 'total': 0, 'page': 1})
    page   = int(request.args.get('page', 1))
    size   = int(request.args.get('size', 20))
    offset = (page - 1) * size
    sp     = ','.join('?' * len(sim_ids))
    # 支持按 sim_id 筛选（只接受客户自己名下的 sim_id）
    sim_filter = request.args.get('sim_id', '').strip()
    try:
        sim_filter_int = int(sim_filter) if sim_filter else None
    except ValueError:
        sim_filter_int = None
    if sim_filter_int and sim_filter_int in sim_ids:
        total   = db_scalar("SELECT COUNT(*) FROM recharge WHERE sim_id=?", (sim_filter_int,))
        records = db_query("SELECT * FROM recharge WHERE sim_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                           (sim_filter_int, size, offset))
    else:
        total   = db_scalar(f"SELECT COUNT(*) FROM recharge WHERE sim_id IN ({sp})", sim_ids)
        records = db_query(f"SELECT * FROM recharge WHERE sim_id IN ({sp}) "
                           f"ORDER BY created_at DESC LIMIT ? OFFSET ?",
                           sim_ids + [size, offset])
    return ok({'records': records, 'total': total, 'page': page})


@app.post('/api/customer/recharges')
def portal_create_recharge():
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    phones = _portal_sim_phones(cid)
    d      = request.get_json() or {}
    sim_id = int(d.get('sim_id', 0) or 0)
    sim    = db_query_one("SELECT id, iccid, device_phone, balance, status FROM sim_card WHERE id=?", (sim_id,))
    if not sim or sim['device_phone'] not in phones:
        return fail('无权限', 403)
    amount = float(d.get('amount', 0) or 0)
    if amount <= 0:
        return fail('充值金额必须大于 0', 400)
    new_balance = round(float(sim['balance']) + amount, 2)
    new_status  = '正常' if sim['status'] == '欠费' and new_balance >= 0 else sim['status']
    db_exec("UPDATE sim_card SET balance=?, status=? WHERE id=?", (new_balance, new_status, sim_id))
    cust = db_query_one("SELECT name FROM customer WHERE id=?", (cid,))
    db_exec("INSERT INTO recharge (sim_id, iccid, amount, method, plan, remark, operator) "
            "VALUES (?,?,?,?,?,?,?)",
            (sim_id, sim['iccid'], amount, d.get('method', '支付宝'),
             d.get('plan', ''), d.get('remark', ''),
             cust['name'] if cust else '客户'))
    return ok({'new_balance': new_balance})


# ── 管理端：给客户设置账号密码 / 分配设备 ─────────────────────────────────────

@app.put('/api/customers/<int:cid>/password')
def set_customer_password(cid):
    d   = request.get_json() or {}
    pwd = (d.get('password') or '').strip()
    login_name = (d.get('login_name') or '').strip()
    if not login_name:
        return fail('login_name 不能为空', 400)
    row = db_query_one("SELECT id, password_hash FROM customer WHERE id=?", (cid,))
    if not row:
        return fail('客户不存在', 404)
    # 检查 login_name 唯一性（排除自身）
    dup = db_query_one("SELECT id FROM customer WHERE login_name=? AND id!=?", (login_name, cid))
    if dup:
        return fail('登录账号已被占用', 400)
    # pwd == '__KEEP__' 表示只更新账号名，不改密码
    if pwd == '__KEEP__' or not pwd:
        new_hash = row['password_hash']   # 保持原哈希
    else:
        new_hash = _hash_pw(pwd)
    db_exec("UPDATE customer SET login_name=?, password_hash=? WHERE id=?",
            (login_name, new_hash, cid))
    add_op_log('客户账号', f'客户 id={cid} 设置登录账号 {login_name}')
    return ok()

@app.get('/api/customers/<int:cid>/devices')
def list_customer_devices(cid):
    """列出归属于该客户的设备"""
    records = db_query(
        "SELECT id, phone, name, status, last_lat, last_lng, last_location_time FROM device WHERE customer_id=?",
        (cid,)
    )
    return ok(records)

@app.put('/api/customers/<int:cid>/devices')
def assign_customer_devices(cid):
    """管理员将一组设备（phone 列表）分配给客户；phones=[] 则全部解绑"""
    row = db_query_one("SELECT name FROM customer WHERE id=?", (cid,))
    if not row:
        return fail('客户不存在', 404)
    d      = request.get_json() or {}
    phones = d.get('phones', [])
    # 只清该客户的直属设备（子客户设备由客户自己管理，不联动）
    db_exec("UPDATE device SET customer_id=NULL WHERE customer_id=?", (cid,))
    for phone in phones:
        db_exec("UPDATE device SET customer_id=? WHERE phone=?", (cid, phone))
    add_op_log('分配设备', f'客户 {row["name"]} 分配 {len(phones)} 台设备')
    return ok()


# ── 组织管理 ───────────────────────────────────────────────────────────────────

def _org_to_camel(r):
    """将 sys_org 数据行转为前端 camelCase 格式（递归处理 children）"""
    return {
        'id':        r.get('id'),
        'orgName':   r.get('org_name'),
        'parentId':  r.get('parent_id'),
        'orgLevel':  r.get('org_level'),
        'orgCode':   r.get('org_code'),
        'orgPath':   r.get('org_path'),
        'sortOrder': r.get('sort_order', 0),
        'isActive':  bool(r.get('is_active', 1)),
        'createdAt': r.get('created_at'),
        'children':  [_org_to_camel(c) for c in (r.get('children') or [])],
    }


def _build_org_tree(orgs):
    """扁平列表 → 嵌套树（camelCase 输出）"""
    by_id = {o['id']: dict(o, children=[]) for o in orgs}
    roots = []
    for node in by_id.values():
        pid = node.get('parent_id')
        if pid and pid in by_id:
            by_id[pid]['children'].append(node)
        else:
            roots.append(node)
    for node in by_id.values():
        node['children'].sort(key=lambda x: (x.get('sort_order') or 0, x.get('id') or 0))
    roots.sort(key=lambda x: (x.get('sort_order') or 0, x.get('id') or 0))
    return [_org_to_camel(r) for r in roots]


# ── 权限范围辅助函数 ──────────────────────────────────────────────────────────────

def _current_admin(req):
    """从请求 token 取出当前管理员行（含 org_id / user_type）"""
    token = req.headers.get('X-Admin-Token', '')
    aid = _verify_admin_token(token)
    if not aid:
        return None
    return db_query_one(
        "SELECT id, org_id, org_level, user_type FROM admin_user WHERE id=?", (aid,)
    )


def _scope_path(admin):
    """
    返回该管理员的可见范围 org_path 前缀。
    None  → 无限制（根超管）
    str   → 只能看到该前缀下的组织（含自身）
    """
    if not admin:
        return ''   # 没有 admin 信息，最严格限制
    # 超级管理员 且 属于根组织 → 无限制
    if admin.get('user_type') == 9 and (admin.get('org_id') or 1) == 1:
        return None
    org_id = admin.get('org_id') or 1
    org = db_query_one("SELECT org_path FROM sys_org WHERE id=?", (org_id,))
    return (org.get('org_path') or f'/{org_id}/') if org else f'/{org_id}/'


def _orgs_in_scope(scope):
    """
    查出当前管理员范围内的所有组织行。
    scope=None → 全部；scope=str → 用 org_path LIKE 过滤
    """
    if scope is None:
        return db_query("SELECT * FROM sys_org ORDER BY sort_order, id")
    return db_query(
        "SELECT * FROM sys_org WHERE org_path=? OR org_path LIKE ? ORDER BY sort_order, id",
        (scope, scope + '%')
    )


def _org_in_scope(scope, org_id):
    """判断某个组织 ID 是否在当前管理员的可见范围内"""
    if scope is None:
        return True
    org = db_query_one("SELECT org_path FROM sys_org WHERE id=?", (org_id,))
    if not org:
        return False
    path = org.get('org_path') or ''
    return path == scope or path.startswith(scope)


@app.get('/api/org/tree')
def org_tree():
    admin  = _current_admin(request)
    scope  = _scope_path(admin)
    orgs   = _orgs_in_scope(scope)
    return ok(_build_org_tree(orgs))


@app.get('/api/org/children')
def org_children():
    """当前登录管理员所在组织的直接下级"""
    token = request.headers.get('X-Admin-Token', '')
    aid = _verify_admin_token(token)
    admin = db_query_one("SELECT org_id FROM admin_user WHERE id=?", (aid,)) if aid else None
    org_id = (admin or {}).get('org_id') or 1
    rows = db_query("SELECT * FROM sys_org WHERE parent_id=? ORDER BY sort_order, id", (org_id,))
    return ok([_org_to_camel(r) for r in rows])


@app.get('/api/org/<int:oid>/children')
def org_children_of(oid):
    rows = db_query("SELECT * FROM sys_org WHERE parent_id=? ORDER BY sort_order, id", (oid,))
    return ok([_org_to_camel(r) for r in rows])


@app.post('/api/org')
def create_org():
    admin  = _current_admin(request)
    scope  = _scope_path(admin)
    d        = request.get_json() or {}
    org_name = (d.get('orgName') or d.get('org_name') or '').strip()
    org_code = (d.get('orgCode') or d.get('org_code') or '').strip()
    if not org_name or not org_code:
        return fail('组织名称和编码不能为空', 400)
    if db_query_one("SELECT id FROM sys_org WHERE org_code=?", (org_code,)):
        return fail('组织编码已存在', 400)
    parent_id = d.get('parentId') or d.get('parent_id')
    # 只能在自己的范围内创建子组织
    if parent_id and not _org_in_scope(scope, int(parent_id)):
        return fail('无权在该组织下创建子组织', 403)
    if parent_id:
        parent = db_query_one("SELECT org_level, org_path FROM sys_org WHERE id=?", (int(parent_id),))
        if not parent:
            return fail('父组织不存在', 400)
        org_level = (parent.get('org_level') or 1) + 1
    else:
        parent, org_level = None, 1
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with _db_lock:
        conn = get_db()
        try:
            cur = conn.execute(
                "INSERT INTO sys_org (org_name, parent_id, org_level, org_code, sort_order, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                (org_name, parent_id, org_level, org_code,
                 int(d.get('sortOrder') or d.get('sort_order') or 0), now, now)
            )
            new_id = cur.lastrowid
            if parent:
                org_path = (parent.get('org_path') or f'/{parent_id}/') + f'{new_id}/'
            else:
                org_path = f'/{new_id}/'
            conn.execute("UPDATE sys_org SET org_path=? WHERE id=?", (org_path, new_id))
            conn.commit()
            row = dict(conn.execute("SELECT * FROM sys_org WHERE id=?", (new_id,)).fetchone())
        finally:
            conn.close()
    return ok(_org_to_camel(row))


def _collect_org_subtree(root_id):
    """递归收集 root_id 及其所有后代组织 ID（含自身）"""
    result = [root_id]
    children = db_query("SELECT id FROM sys_org WHERE parent_id=?", (root_id,))
    for c in children:
        result.extend(_collect_org_subtree(c['id']))
    return result


@app.delete('/api/org/<int:oid>')
def delete_org(oid):
    admin = _current_admin(request)
    scope = _scope_path(admin)
    if oid == 1:
        return fail('根组织不可删除', 400)
    # 不能删除自己所在的组织
    if admin and admin.get('org_id') == oid:
        return fail('不能删除您自己所在的组织', 403)
    if not _org_in_scope(scope, oid):
        return fail('无权删除该组织', 403)
    row = db_query_one("SELECT org_name FROM sys_org WHERE id=?", (oid,))
    if not row:
        return fail('组织不存在', 404)

    cascade = request.args.get('cascade', '0') == '1'
    all_ids = _collect_org_subtree(oid)
    ph      = ','.join('?' * len(all_ids))

    if not cascade:
        # 非级联：检查是否有子组织或用户
        if len(all_ids) > 1:
            return fail(f'该组织下有 {len(all_ids)-1} 个子组织，请先删除或使用级联删除', 400)
        if db_scalar("SELECT COUNT(*) FROM admin_user WHERE org_id=?", (oid,)):
            return fail('该组织下还有用户，请先移除后再删除', 400)

    # 级联/普通删除
    user_cnt = db_scalar(f"SELECT COUNT(*) FROM admin_user WHERE org_id IN ({ph})", all_ids)
    db_exec(f"DELETE FROM admin_user WHERE org_id IN ({ph})", all_ids)
    db_exec(f"DELETE FROM sys_org_module_auth WHERE org_id IN ({ph})", all_ids)
    # 从叶节点往上删，避免外键顺序问题（SQLite 无外键约束可直接删）
    for oid_del in reversed(all_ids):
        db_exec("DELETE FROM sys_org WHERE id=?", (oid_del,))
    add_op_log('删除组织', f'删除组织 {row["org_name"]}（含 {len(all_ids)-1} 子组织 / {user_cnt} 用户）')
    return ok({'deletedOrgs': len(all_ids), 'deletedUsers': user_cnt})


@app.put('/api/org/<int:oid>')
def update_org(oid):
    admin = _current_admin(request)
    scope = _scope_path(admin)
    if not _org_in_scope(scope, oid):
        return fail('无权修改该组织', 403)
    if not db_query_one("SELECT id FROM sys_org WHERE id=?", (oid,)):
        return fail('组织不存在', 404)
    d   = request.get_json() or {}
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    db_exec(
        "UPDATE sys_org SET org_name=?, sort_order=?, is_active=?, updated_at=? WHERE id=?",
        (d.get('orgName') or d.get('org_name', ''),
         int(d.get('sortOrder') or d.get('sort_order') or 0),
         1 if d.get('isActive', d.get('is_active', True)) else 0,
         now, oid)
    )
    return ok()


# ── 系统用户管理 ────────────────────────────────────────────────────────────────

def _row_to_user(r):
    return {
        'id':        r.get('id'),
        'username':  r.get('username'),
        'realName':  r.get('real_name'),
        'phone':     r.get('phone'),
        'orgId':     r.get('org_id'),
        'orgLevel':  r.get('org_level'),
        'userType':  r.get('user_type'),
        'isActive':  bool(r.get('is_active', 1)),
        'lastLogin': r.get('last_login'),
        'createdAt': r.get('created_at'),
    }


@app.get('/api/sys/users')
def list_sys_users():
    admin  = _current_admin(request)
    scope  = _scope_path(admin)
    org_id = request.args.get('orgId') or request.args.get('org_id')

    if scope is None:
        # 超管：可查全部
        if org_id:
            rows = db_query(
                "SELECT id,username,real_name,phone,org_id,org_level,user_type,is_active,last_login,created_at "
                "FROM admin_user WHERE org_id=? ORDER BY id", (int(org_id),)
            )
        else:
            rows = db_query(
                "SELECT id,username,real_name,phone,org_id,org_level,user_type,is_active,last_login,created_at "
                "FROM admin_user ORDER BY id"
            )
    else:
        # 非超管：只能看自己范围内的用户
        scope_ids = [o['id'] for o in _orgs_in_scope(scope)]
        if not scope_ids:
            return ok([])
        ph = ','.join('?' * len(scope_ids))
        if org_id and int(org_id) in scope_ids:
            rows = db_query(
                "SELECT id,username,real_name,phone,org_id,org_level,user_type,is_active,last_login,created_at "
                f"FROM admin_user WHERE org_id=? ORDER BY id", (int(org_id),)
            )
        else:
            rows = db_query(
                "SELECT id,username,real_name,phone,org_id,org_level,user_type,is_active,last_login,created_at "
                f"FROM admin_user WHERE org_id IN ({ph}) ORDER BY id", scope_ids
            )
    return ok([_row_to_user(r) for r in rows])


@app.post('/api/sys/users')
def create_sys_user():
    admin    = _current_admin(request)
    scope    = _scope_path(admin)
    d        = request.get_json() or {}
    username = (d.get('username') or '').strip()
    password = (d.get('password') or '').strip()
    org_id   = int(d.get('orgId') or d.get('org_id') or 1)
    if not _org_in_scope(scope, org_id):
        return fail('无权在该组织下创建用户', 403)
    if not username or not password:
        return fail('用户名和密码不能为空', 400)
    if len(password) < 6:
        return fail('密码不能少于 6 位', 400)
    if db_query_one("SELECT id FROM admin_user WHERE username=?", (username,)):
        return fail('用户名已存在', 400)
    org  = db_query_one("SELECT org_level FROM sys_org WHERE id=?", (org_id,))
    now  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    db_exec(
        "INSERT INTO admin_user (username,password_hash,real_name,phone,org_id,org_level,user_type,is_active,created_at) "
        "VALUES (?,?,?,?,?,?,?,1,?)",
        (username, _hash_pw(password),
         d.get('realName') or d.get('real_name') or '',
         d.get('phone') or '',
         org_id,
         (org['org_level'] if org else 1),
         int(d.get('userType') or d.get('user_type') or 1),
         now)
    )
    return ok()


@app.put('/api/sys/users/<int:uid>')
def update_sys_user(uid):
    admin  = _current_admin(request)
    scope  = _scope_path(admin)
    target = db_query_one("SELECT id, org_id FROM admin_user WHERE id=?", (uid,))
    if not target:
        return fail('用户不存在', 404)
    if not _org_in_scope(scope, target['org_id']):
        return fail('无权修改该用户', 403)
    d   = request.get_json() or {}
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    db_exec(
        "UPDATE admin_user SET real_name=?, phone=?, is_active=?, updated_at=? WHERE id=?",
        (d.get('realName') or d.get('real_name') or '',
         d.get('phone') or '',
         1 if d.get('isActive', d.get('is_active', True)) else 0,
         now, uid)
    )
    return ok()


@app.put('/api/sys/users/<int:uid>/password')
def reset_sys_user_password(uid):
    if not db_query_one("SELECT id FROM admin_user WHERE id=?", (uid,)):
        return fail('用户不存在', 404)
    d       = request.get_json() or {}
    new_pwd = (d.get('newPassword') or d.get('new_password') or '').strip()
    if not new_pwd or len(new_pwd) < 6:
        return fail('新密码不能少于 6 位', 400)
    db_exec("UPDATE admin_user SET password_hash=? WHERE id=?", (_hash_pw(new_pwd), uid))
    return ok()


@app.delete('/api/sys/users/<int:uid>')
def delete_sys_user(uid):
    admin  = _current_admin(request)
    scope  = _scope_path(admin)
    row = db_query_one("SELECT id, username, user_type, org_id FROM admin_user WHERE id=?", (uid,))
    if not row:
        return fail('用户不存在', 404)
    if row['user_type'] == 9 and row['username'] == 'admin':
        return fail('内置超级管理员不可删除', 400)
    if not _org_in_scope(scope, row['org_id']):
        return fail('无权删除该用户', 403)
    db_exec("DELETE FROM admin_user WHERE id=?", (uid,))
    add_op_log('删除用户', f'删除系统用户 {row["username"]}（id={uid}）')
    return ok()


# ── 模块授权管理 ─────────────────────────────────────────────────────────────────

_DEFAULT_MODULES = [
    ('ASSET',           '资产管理',     None,     10),
    ('FENCE',           '电子围栏',     None,     20),
    ('ALERT',           '告警中心',     None,     30),
    ('TRACK',           '轨迹回放',     None,     40),
    ('MAP',             '地图大屏',     None,     50),
    ('REPORT',          '报表中心',     None,     60),
    ('SYS',             '系统管理',     None,     70),
    ('REPORT_ASSET',    '资产报表',     'REPORT',  1),
    ('REPORT_ALERT',    '告警报表',     'REPORT',  2),
    ('SYS_USER',        '用户管理',     'SYS',     1),
    ('SYS_ORG',         '组织管理',     'SYS',     2),
    ('SYS_MODULE_AUTH', '模块授权配置', 'SYS',     3),
]


def _ensure_modules():
    for code, name, parent, order in _DEFAULT_MODULES:
        db_exec(
            "INSERT OR IGNORE INTO sys_module (module_code,module_name,parent_code,sort_order) VALUES (?,?,?,?)",
            (code, name, parent, order)
        )


def _module_to_camel(m):
    """将 sys_module 数据行转为前端 camelCase 格式（递归处理 children）"""
    return {
        'id':          m.get('id'),
        'moduleCode':  m.get('module_code'),
        'moduleName':  m.get('module_name'),
        'parentCode':  m.get('parent_code'),
        'sortOrder':   m.get('sort_order', 0),
        'description': m.get('description'),
        'isSystem':    bool(m.get('is_system', 0)),
        'children':    [_module_to_camel(c) for c in (m.get('children') or [])],
    }


def _build_module_tree(modules):
    by_code = {m['module_code']: dict(m, children=[]) for m in modules}
    roots = []
    for m in by_code.values():
        pc = m.get('parent_code')
        if pc and pc in by_code:
            by_code[pc]['children'].append(m)
        else:
            roots.append(m)
    for m in by_code.values():
        m['children'].sort(key=lambda x: x.get('sort_order') or 0)
    roots.sort(key=lambda x: x.get('sort_order') or 0)
    return [_module_to_camel(r) for r in roots]


@app.get('/api/modules/tree')
def module_tree():
    _ensure_modules()
    mods = db_query("SELECT * FROM sys_module WHERE deleted=0 ORDER BY sort_order, id")
    return ok(_build_module_tree(mods))


@app.get('/api/modules/org/<int:org_id>/auth')
def get_org_module_auth(org_id):
    admin = _current_admin(request)
    scope = _scope_path(admin)
    if not _org_in_scope(scope, org_id):
        return fail('无权查看该组织的模块授权', 403)
    _ensure_modules()
    # 若该组织没有任何授权记录，默认返回全部模块（总部级别）
    has_auth = db_scalar("SELECT COUNT(*) FROM sys_org_module_auth WHERE org_id=?", (org_id,))
    if not has_auth:
        mods = db_query("SELECT module_code FROM sys_module WHERE deleted=0")
        enabled = [m['module_code'] for m in mods]
    else:
        auth_rows = db_query(
            "SELECT module_code FROM sys_org_module_auth WHERE org_id=? AND is_enabled=1",
            (org_id,)
        )
        enabled = [r['module_code'] for r in auth_rows]
    return ok({'orgId': org_id, 'enabledCodes': enabled})


@app.post('/api/modules/org/<int:org_id>/auth')
def save_org_module_auth(org_id):
    admin = _current_admin(request)
    scope = _scope_path(admin)
    if not _org_in_scope(scope, org_id):
        return fail('无权配置该组织的模块授权', 403)
    if not db_query_one("SELECT id FROM sys_org WHERE id=?", (org_id,)):
        return fail('组织不存在', 404)
    _ensure_modules()
    d             = request.get_json() or {}
    enabled_codes = set(d.get('enabledCodes', []))
    mods          = db_query("SELECT module_code FROM sys_module WHERE deleted=0")
    now           = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with _db_lock:
        conn = get_db()
        try:
            for m in mods:
                code  = m['module_code']
                is_en = 1 if code in enabled_codes else 0
                conn.execute(
                    "INSERT INTO sys_org_module_auth (org_id,module_code,is_enabled,granted_at) "
                    "VALUES (?,?,?,?) ON CONFLICT(org_id,module_code) "
                    "DO UPDATE SET is_enabled=excluded.is_enabled, granted_at=excluded.granted_at",
                    (org_id, code, is_en, now)
                )
            conn.commit()
        finally:
            conn.close()
    return ok()


# ── MQTT 接入（兼容 MQTT 客户端直连，broker 未运行时自动跳过） ─────────────────

MQTT_BROKER = os.environ.get('MQTT_BROKER', 'localhost')
MQTT_PORT_NUM = int(os.environ.get('MQTT_PORT', 1883))


def _mqtt_on_message(client, userdata, msg):
    """处理设备通过 MQTT 上报的 GPS 数据"""
    try:
        import json as _js
        topic = msg.topic                          # gps/{phone}
        phone = topic.split('/')[-1]
        if not phone or phone == 'status':
            return
        data      = _js.loads(msg.payload.decode('utf-8'))
        lat       = float(data.get('lat', 0))
        lng       = float(data.get('lng', 0))
        speed_raw = int(float(data.get('speed', 0)) * 10)   # 存 0.1km/h 单位
        direction = int(data.get('direction', 0))
        altitude  = int(data.get('altitude', 0))
        alarm     = int(data.get('alarm', 0))
        gps_time  = data.get('time') or datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        if lat == 0 and lng == 0:
            return

        device    = db_query_one("SELECT id FROM device WHERE phone=?", (phone,))
        device_id = device['id'] if device else None
        now       = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        db_exec(
            "INSERT INTO location_record (device_id,phone,lat,lng,altitude,speed,direction,gps_time,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (device_id, phone, lat, lng, altitude, speed_raw, direction, gps_time, now)
        )
        db_exec(
            "UPDATE device SET last_lat=?,last_lng=?,last_speed=?,last_location_time=?,status=1 WHERE phone=?",
            (lat, lng, speed_raw, gps_time, phone)
        )
        _sio_emit('location_update', {
            'phone': phone, 'lat': lat, 'lng': lng,
            'speed': speed_raw / 10, 'direction': direction,
        }, phone)
        log.info("[MQTT] GPS phone=%s lat=%.6f lng=%.6f speed=%.1fkm/h",
                 phone, lat, lng, speed_raw / 10)

        # 报警处理（alarm 非零时记录）
        if alarm:
            db_exec(
                "INSERT INTO alarm_record (device_id,phone,alarm_type,alarm_desc,"
                "lat,lng,alarm_time,created_at,status) VALUES (?,?,?,?,?,?,?,?,0)",
                (device_id, phone, alarm, 'MQTT 报警 0x{:08X}'.format(alarm), lat, lng, now, now)
            )
    except Exception as e:
        log.error("[MQTT] 消息处理异常: %s", e)


def start_mqtt_subscriber():
    """后台 MQTT 订阅线程，broker 不可达时静默退出"""
    try:
        import paho.mqtt.client as _mqtt
    except ImportError:
        log.warning("[MQTT] paho-mqtt 未安装，MQTT 接入已跳过（pip install paho-mqtt）")
        return

    def _on_connect(client, userdata, flags, rc):
        if rc == 0:
            client.subscribe('gps/#')
            log.info("[MQTT] 已连接 broker %s:%d，订阅 gps/#", MQTT_BROKER, MQTT_PORT_NUM)
        else:
            log.warning("[MQTT] 连接失败 rc=%d", rc)

    try:
        client = _mqtt.Client(client_id='tracker-server', clean_session=True)
        client.on_connect = _on_connect
        client.on_message = _mqtt_on_message
        client.connect(MQTT_BROKER, MQTT_PORT_NUM, keepalive=60)
        log.info("[MQTT] 正在连接 broker %s:%d ...", MQTT_BROKER, MQTT_PORT_NUM)
        client.loop_forever()
    except Exception as e:
        log.warning("[MQTT] broker 不可达，MQTT 接入已跳过: %s", e)


# ── 前端静态文件托管（生产 build / 演示用） ────────────────────────────────────
_DIST = os.path.normpath(os.path.join(BASE_DIR, '..', 'frontend', 'dist'))

from flask import send_file as _send_abs

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def _serve_spa(path):
    # 尝试直接返回静态文件（assets/ 等）
    if path:
        fp = os.path.join(_DIST, path.replace('/', os.sep))
        if os.path.isfile(fp):
            return _send_abs(fp)
    # 其余全部返回 index.html（Vue Router 接管）
    return _send_abs(os.path.join(_DIST, 'index.html'))


# ── 主入口 ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    init_db()

    # 后台启动 808 TCP 服务线程
    tcp_thread = threading.Thread(target=start_tcp_server, daemon=True)
    tcp_thread.start()

    # 后台启动 MQTT 订阅线程（broker 不可达时自动跳过）
    mqtt_thread = threading.Thread(target=start_mqtt_subscriber, daemon=True)
    mqtt_thread.start()

    log.info("╔══════════════════════════════════════════╗")
    log.info("║  REST API 启动  http://0.0.0.0:%d       ║", HTTP_PORT)
    log.info("╚══════════════════════════════════════════╝")

    socketio.run(app, host='0.0.0.0', port=HTTP_PORT, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)
