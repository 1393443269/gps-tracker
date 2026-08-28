# ── gevent 猴子补丁（必须在所有其他 import 之前执行）────────────────────────────
# Gunicorn gevent worker 和 python app.py 直接运行两种模式均适用。
# 若 gevent 未安装（开发环境），优雅降级到 threading 模式。
try:
    from gevent import monkey as _gmonkey
    _gmonkey.patch_all(thread=True, socket=True, ssl=True)
    _GEVENT_AVAILABLE = True
except ImportError:
    _GEVENT_AVAILABLE = False

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
from flask import Flask, request, jsonify, send_file as _send_abs
import re as _re
from flask_socketio import SocketIO, join_room
from flask_cors import CORS
import protocol as p
import protocol_zhiling as zl

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
_SIO_MODE = 'gevent' if _GEVENT_AVAILABLE else 'threading'
socketio = SocketIO(app, cors_allowed_origins='*', async_mode=_SIO_MODE, logger=False, engineio_logger=False)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 支持通过环境变量指定数据目录（Docker 挂载卷场景）
DB_PATH  = os.environ.get('DB_PATH', os.path.join(BASE_DIR, 'tracker.db'))
# 确保数据目录存在（DB_PATH 含目录时才创建，避免空串报错）
_db_dir = os.path.dirname(DB_PATH)
if _db_dir:
    os.makedirs(_db_dir, exist_ok=True)
TCP_PORT = 9090
HTTP_PORT = 8080

# 上传文件目录（头像等），支持环境变量指定（Docker 挂载卷持久化）
UPLOAD_DIR = os.environ.get('UPLOAD_DIR', os.path.join(BASE_DIR, 'uploads'))
os.makedirs(UPLOAD_DIR, exist_ok=True)
_ALLOWED_IMG_EXT = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}

def _hash_pw(pwd: str) -> str:
    """用 bcrypt 哈希密码（cost=12）。bcrypt 只处理前 72 字节。"""
    import bcrypt as _bcrypt
    return _bcrypt.hashpw(pwd.encode('utf-8')[:72], _bcrypt.gensalt(rounds=12)).decode('utf-8')

def _verify_pw(plain: str, stored: str) -> bool:
    """验证密码，兼容旧 SHA-256 哈希（自动升级）。异常一律返回 False。"""
    import bcrypt as _bcrypt
    try:
        if stored and (stored.startswith('$2b$') or stored.startswith('$2a$')):
            return _bcrypt.checkpw(plain.encode('utf-8')[:72], stored.encode('utf-8'))
        # 旧 SHA-256 路径（兼容历史数据）
        return hashlib.sha256(plain.encode('utf-8')).hexdigest() == stored
    except Exception:
        return False

# ── SQLite 数据库 ──────────────────────────────────────────────────────────────

# ── 数据库后端抽象层 ─────────────────────────────────────────────────────────
# 通过环境变量 DB_BACKEND 切换：'sqlite'（默认）或 'postgres'。
# 业务代码统一用 ? 占位符；postgres 后端会自动把 ? 转成 %s。
# 迁移到 PG 时只需：装 psycopg2、设 DB_BACKEND=postgres + DATABASE_URL，无需改业务 SQL。
DB_BACKEND = os.environ.get('DB_BACKEND', 'sqlite').lower()

if DB_BACKEND == 'postgres':
    import psycopg2 as _pg
    import psycopg2.extras as _pg_extras
    _PG_DSN = os.environ.get('DATABASE_URL',
                             'postgresql://postgres:postgres@127.0.0.1:5432/gps')

    # ── PG 连接池：防止每请求新建/关闭连接，在高并发下耗尽 max_connections ─────
    from psycopg2.pool import ThreadedConnectionPool as _PgPool
    _pg_pool      = None
    _pg_pool_lock = threading.Lock()

    def _get_pg_pool():
        global _pg_pool
        if _pg_pool is None:
            with _pg_pool_lock:
                if _pg_pool is None:
                    # psycogreen：令 psycopg2 的 I/O 等待协作式让出 gevent 事件循环
                    if _GEVENT_AVAILABLE:
                        try:
                            import psycogreen.gevent as _pcg
                            _pcg.patch_psycopg()
                        except ImportError:
                            pass   # psycogreen 可选；不影响连接池防耗尽功能
                    _pg_pool = _PgPool(
                        minconn=2,
                        maxconn=20,   # 远低于 PG max_connections=100，保留充足余量
                        dsn=_PG_DSN,
                        cursor_factory=_pg_extras.RealDictCursor,
                    )
        return _pg_pool

import re as _re

def _pg_dialect(sql):
    """把 SQLite 方言 SQL 改写成 PostgreSQL 兼容写法（仅处理本项目实际用到的差异）。
    覆盖：自增主键、建表时间默认值、INSERT OR IGNORE、now/date 时间运算。
    注意：Python 侧的 datetime.now().strftime() 是 Python 代码不经过此函数，无需处理。"""
    s = sql
    # 1) 自增主键：INTEGER PRIMARY KEY AUTOINCREMENT -> SERIAL PRIMARY KEY
    s = _re.sub(r'INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT', 'SERIAL PRIMARY KEY', s, flags=_re.I)
    # 2) 建表默认值：DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime')) -> DEFAULT (to_char(now(),'YYYY-MM-DD HH24:MI:SS'))
    s = s.replace("strftime('%Y-%m-%d %H:%M:%S','now','localtime')",
                  "to_char(now(),'YYYY-MM-DD HH24:MI:SS')")
    s = s.replace("strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')",
                  "to_char(now(),'YYYY-MM-DD HH24:MI:SS')")
    # 3) INSERT OR IGNORE -> INSERT ... ON CONFLICT DO NOTHING（PG 需显式 ON CONFLICT）
    #    本项目 OR IGNORE 用于避免重复插入，DO NOTHING 语义等价
    s = _re.sub(r'INSERT\s+OR\s+IGNORE\s+INTO', 'INSERT INTO', s, flags=_re.I)
    _or_ignore = _re.search(r'INSERT\s+OR\s+IGNORE', sql, flags=_re.I)
    if _or_ignore and 'ON CONFLICT' not in s.upper():
        s = s.rstrip().rstrip(';') + ' ON CONFLICT DO NOTHING'
    # 4) date('now') / date('now','+N days') -> PG 的日期运算
    s = s.replace("date('now','start of month')", "date_trunc('month', now())::date")
    s = _re.sub(r"date\('now',\s*'([+-]\d+) days'\)",
                lambda m: f"(now()::date + interval '{m.group(1)} day')::date", s)
    s = s.replace("date('now')", "now()::date")
    # 5) strftime 裸用（查询里对 now 取字符串，若有）
    s = s.replace("strftime('%Y-%m-%d %H:%M:%S','now')", "to_char(now(),'YYYY-MM-DD HH24:MI:SS')")
    return s


def _split_sql(script):
    """把多语句 SQL 脚本按分号拆成单条，忽略 -- 注释，避开字符串字面量内的分号。"""
    out, buf, in_str = [], [], False
    # 先逐行去掉 -- 行注释
    lines = []
    for ln in script.split('\n'):
        # 去掉行内 -- 注释（不在字符串里时）
        q = False; cut = None
        for i, ch in enumerate(ln):
            if ch == "'":
                q = not q
            elif ch == '-' and i+1 < len(ln) and ln[i+1] == '-' and not q:
                cut = i; break
        lines.append(ln if cut is None else ln[:cut])
    text = '\n'.join(lines)
    for ch in text:
        if ch == "'":
            in_str = not in_str
            buf.append(ch)
        elif ch == ';' and not in_str:
            out.append(''.join(buf)); buf = []
        else:
            buf.append(ch)
    if ''.join(buf).strip():
        out.append(''.join(buf))
    return out


def _to_pg(sql):
    """PG 适配：先做方言改写；再把 SQL 里的字面 % 转义成 %%（psycopg2 用 % 做
    参数占位，字面 % 不转义会被当成占位符解析而报 IndexError，如 LIKE '%'||x||'%'）；
    最后把 ? 占位符转成 %s。转义务必在 ?→%s 之前，否则新引入的 %s 会被误转义。"""
    s = _pg_dialect(sql)
    s = s.replace('%', '%%')      # 先转义所有字面 %
    s = s.replace('?', '%s')      # 再把占位符 ? 变成 %s（此时不会被上一步影响）
    return s


class _ConnWrapper:
    """统一 sqlite3 / psycopg2 的接口差异：
    - execute(sql, params) 自动转占位符
    - 提供 row 转 dict 的游标
    这样业务层的 conn.execute(...) 写法在两种后端下都能用。"""
    def __init__(self, raw, backend, pool=None):
        self._raw     = raw
        self._backend = backend
        self._pool    = pool   # 非 None 时 close() 归还连接池而非真正断开
    def execute(self, sql, params=()):
        cur = self._raw.cursor()
        if self._backend == 'postgres':
            cur.execute(_to_pg(sql), params)
        else:
            cur.execute(sql, params)
        return cur
    def executemany(self, sql, seq):
        cur = self._raw.cursor()
        if self._backend == 'postgres':
            cur.executemany(_to_pg(sql), seq)
        else:
            cur.executemany(sql, seq)
        return cur
    def executescript(self, script):
        # SQLite 有原生 executescript；postgres 先做方言转换，再按分号逐条独立执行
        if self._backend == 'sqlite':
            self._raw.executescript(script)
        else:
            # 逐条执行：一条失败(如索引/表已存在)不影响其余，配合 autocommit 各自独立提交
            for stmt in _split_sql(_pg_dialect(script)):
                s = stmt.strip()
                if not s:
                    continue
                # 转义字面 % → %% 防 psycopg2 把 LIKE '%x%' 中的 % 误识别为参数占位符
                s = s.replace('%', '%%')
                cur = self._raw.cursor()
                try:
                    cur.execute(s)
                except Exception as _e:
                    # 幂等建表/加列场景下的"已存在"类错误忽略，其余抛出
                    if 'already exists' not in str(_e).lower():
                        raise
        return self
    def cursor(self):  return self._raw.cursor()
    def commit(self):  self._raw.commit()
    def close(self):
        if self._pool is not None:
            try:
                self._pool.putconn(self._raw)   # 归还连接到池，供后续请求复用
            except Exception:
                try: self._raw.close()
                except Exception: pass
        else:
            self._raw.close()


def get_db():
    if DB_BACKEND == 'postgres':
        pool = _get_pg_pool()
        raw  = pool.getconn()
        try:
            # 关键：开 autocommit，对齐 SQLite 的自动提交语义。
            # 否则 psycopg2 默认把多条语句包进一个事务，某条(如 ALTER 列已存在被 try/except 忽略)
            # 失败会把事务打成中止态，后续全部报 InFailedSqlTransaction，真正的首错被掩盖。
            raw.autocommit = True
            return _ConnWrapper(raw, 'postgres', pool=pool)  # close() 时归还连接池
        except Exception:
            pool.putconn(raw)   # autocommit 设置异常时归还连接，防止泄漏
            raise
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # 提升并发写性能
    conn.execute("PRAGMA synchronous=NORMAL")
    return _ConnWrapper(conn, 'sqlite')

_db_lock = threading.Lock()

def db_exec(sql, params=()):
    """写操作：SQLite 用全局锁防并发写冲突；PG 连接池各连接独立，无需全局锁。"""
    if DB_BACKEND == 'sqlite':
        with _db_lock:
            conn = get_db()
            try:
                conn.execute(sql, params)
                conn.commit()
            finally:
                conn.close()
    else:
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
        r = conn.execute(sql, params).fetchone()
        if r is None:
            return None
        # sqlite3.Row 支持 [0]；psycopg2 RealDictCursor 返回 dict，取第一个值
        return list(r.values())[0] if isinstance(r, dict) else r[0]
    finally:
        conn.close()


# ── 高频位置写入的异步批量落库（削减 SQLite 全局写锁争用）────────────────────────
import queue as _queue

_loc_queue     = _queue.Queue(maxsize=100000)  # 有界队列防 OOM；满时丢弃最新帧并告警
_dev_latest    = {}                 # phone -> 设备最新状态（多次上报只落最后一次）
_dev_latest_lk = threading.Lock()
_devid_cache: dict = {}            # phone → (device_id, expire_ts)；10 分钟 TTL
_DEVID_CACHE_TTL = 600
_alarm_last_ts: dict = {}          # (phone, alarm_type) → last_alarm_unix_ts
_alarm_last_ts_lock = threading.Lock()
_ALARM_DEBOUNCE_SEC = 60           # 同类型报警至少间隔 60 秒

def _get_device_id(phone):
    import time as _t
    now = _t.time()
    entry = _devid_cache.get(phone)
    if entry and now < entry[1]:
        return entry[0]
    row = db_query_one("SELECT id FROM device WHERE phone=?", (phone,))
    did = row['id'] if row else 0
    if did:
        _devid_cache[phone] = (did, now + _DEVID_CACHE_TTL)
    return did

def enqueue_location(loc_row, dev_state):
    """位置上报入队（不落库，立即返回），后台线程批量写。
    loc_row: location_record 的一整行值元组
    dev_state: (phone, last_lat, last_lng, last_speed, last_location_time, status, updated_at)
    """
    try:
        _loc_queue.put_nowait(loc_row)
    except _queue.Full:
        log.warning("[批量写] 位置队列已满(>100000)，丢弃帧 phone=%s；请检查数据库写入是否正常", dev_state[0])
    with _dev_latest_lk:
        _dev_latest[dev_state[0]] = dev_state

def _batch_writer_loop():
    """后台线程：每 0.5 秒把队列里的位置记录 + 设备最新状态批量写入。"""
    while True:
        _time_mod.sleep(0.5)
        # 取出本轮位置记录，单批上限 2000 行，超出的留到下轮，避免单次事务过大
        _BATCH_MAX = 2000
        rows = []
        while len(rows) < _BATCH_MAX:
            try:
                rows.append(_loc_queue.get_nowait())
            except _queue.Empty:
                break
        # 取出并清空设备最新状态快照
        with _dev_latest_lk:
            dev_snapshot = list(_dev_latest.values())
            _dev_latest.clear()
        if not rows and not dev_snapshot:
            continue
        _write_ok = False
        for _attempt in range(3):   # 最多重试 3 次，防止瞬时数据库抖动丢弃位置数据
            try:
                with _db_lock:
                    conn = get_db()
                    try:
                        if rows:
                            if DB_BACKEND == 'postgres':
                                # PG 下用 execute_values 单条 INSERT 批量写，性能远优于 executemany
                                # rows 每项列顺序：device_id,phone,lat,lng,altitude,speed,
                                #                direction,alarm_flag,status_flag,mileage,gps_time
                                _cur = conn.cursor()
                                _pg_extras.execute_values(
                                    _cur,
                                    "INSERT INTO location_record (device_id,phone,lat,lng,altitude,"
                                    "speed,direction,alarm_flag,status_flag,mileage,gps_time) "
                                    "VALUES %s", rows)
                            else:
                                conn.executemany(
                                    "INSERT INTO location_record (device_id,phone,lat,lng,altitude,"
                                    "speed,direction,alarm_flag,status_flag,mileage,gps_time) "
                                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
                        if dev_snapshot:
                            conn.executemany(
                                "UPDATE device SET last_lat=?,last_lng=?,last_speed=?,"
                                "last_location_time=?,status=?,updated_at=? WHERE phone=?",
                                [(d[1],d[2],d[3],d[4],d[5],d[6],d[0]) for d in dev_snapshot])
                        conn.commit()
                    finally:
                        conn.close()
                _write_ok = True
                break
            except Exception as e:
                log.error("[批量写] 落库失败(第%d次): %s", _attempt + 1, e)
        if not _write_ok:
            log.critical("[批量写] 连续 3 次落库失败，%d 条位置记录已丢弃！请检查数据库连接。", len(rows))

_batch_writer_started = False
_batch_writer_start_lock = threading.Lock()

def start_batch_writer():
    """启动批量写线程，加锁防止多 worker 并发调用时双启。"""
    global _batch_writer_started
    with _batch_writer_start_lock:
        if _batch_writer_started:
            return
        _batch_writer_started = True
    threading.Thread(target=_batch_writer_loop, daemon=True).start()
    log.info("[批量写] 位置异步批量落库线程已启动")

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

    CREATE INDEX IF NOT EXISTS idx_alarm_phone_time ON alarm_record(phone, alarm_time);
    CREATE INDEX IF NOT EXISTS idx_alarm_device_time ON alarm_record(device_id, alarm_time);

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
    for _tbl in ('device', 'customer', 'geo_fence', 'alarm_record', 'op_log', 'alarm_rule'):
        try:
            conn.execute(f"ALTER TABLE {_tbl} ADD COLUMN org_id INTEGER DEFAULT 1")
            conn.commit()
        except Exception:
            pass  # 列已存在，忽略

    # device 表：为 customer_id / org_id 补索引（这两列由上面的 ALTER 追加，故索引放此处）
    for _idx_sql in (
        "CREATE INDEX IF NOT EXISTS idx_device_customer ON device(customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_device_org ON device(org_id)",
    ):
        try:
            conn.execute(_idx_sql)
            conn.commit()
        except Exception:
            pass

    # ── customer 表：个人信息扩展（设备信息页使用） ───────────────────────────────
    for _col in ["gender  TEXT DEFAULT ''",
                 "age     INTEGER DEFAULT NULL",
                 "address TEXT DEFAULT ''",
                 "avatar  TEXT DEFAULT ''"]:
        try:
            conn.execute(f"ALTER TABLE customer ADD COLUMN {_col}")
            conn.commit()
        except Exception:
            pass

    # ── 角色表（设备分组：名称/颜色/图标） ──────────────────────────────────────────
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS device_role (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT    NOT NULL,
        color       TEXT    DEFAULT '#409EFF',
        icon_type   TEXT    DEFAULT '圆形',
        description TEXT    DEFAULT '',
        org_id      INTEGER DEFAULT 1,
        created_at  TEXT    DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime'))
    );
    """)
    conn.commit()
    # device 表加 role_id
    try:
        conn.execute("ALTER TABLE device ADD COLUMN role_id INTEGER DEFAULT NULL")
        conn.commit()
    except Exception:
        pass

    # ── 报警规则表（按报警类型配级别/开关/通知/响铃） ─────────────────────────────
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS alarm_rule (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        alarm_type   INTEGER NOT NULL,
        level        TEXT    DEFAULT '普通级别',
        enabled      INTEGER DEFAULT 1,
        notify_page  INTEGER DEFAULT 1,
        notify_sms   INTEGER DEFAULT 0,
        ring_type    TEXT    DEFAULT '响几声',
        org_id       INTEGER DEFAULT 1,
        created_at   TEXT    DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime'))
    );

    CREATE TABLE IF NOT EXISTS platform_setting (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id        INTEGER UNIQUE DEFAULT 1,
        bigscreen_title TEXT DEFAULT '资产管理平台',
        account_title   TEXT DEFAULT '资产管理平台',
        unit_name     TEXT DEFAULT '',
        contact_phone TEXT DEFAULT '',
        email         TEXT DEFAULT '',
        address       TEXT DEFAULT '',
        logo_url      TEXT DEFAULT '',
        enable_batch_cmd INTEGER DEFAULT 1,
        sms_enabled   INTEGER DEFAULT 0,
        sms_total     INTEGER DEFAULT 0,
        sms_used      INTEGER DEFAULT 0,
        created_at    TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime'))
    );

    CREATE TABLE IF NOT EXISTS attendance_record (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        fence_id    INTEGER,
        fence_name  TEXT,
        phone       TEXT,
        device_name TEXT,
        action      TEXT,          -- enter / exit
        event_time  TEXT,
        org_id      INTEGER DEFAULT 1,
        created_at  TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime'))
    );

    CREATE TABLE IF NOT EXISTS health_record (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id   INTEGER,
        phone       TEXT NOT NULL,
        temperature REAL,           -- 体温 ℃
        wrist_temp  REAL,           -- 腕温 ℃
        heart_rate  INTEGER,        -- 心率 bpm
        blood_oxygen INTEGER,       -- 血氧 %
        systolic    INTEGER,        -- 收缩压 mmHg
        diastolic   INTEGER,        -- 舒张压 mmHg
        steps       INTEGER,        -- 计步
        record_time TEXT,
        org_id      INTEGER DEFAULT 1,
        created_at  TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime'))
    );
    CREATE INDEX IF NOT EXISTS idx_health_phone ON health_record(phone, record_time);
    CREATE INDEX IF NOT EXISTS idx_attend_fence ON attendance_record(fence_id, event_time);

    -- 客户独立品牌：每个客户一份白标品牌；字段为 NULL 表示未配置，读取时逐级继承上级、最终回退全站默认
    CREATE TABLE IF NOT EXISTS customer_branding (
        customer_id     INTEGER PRIMARY KEY,
        bigscreen_title TEXT DEFAULT NULL,
        account_title   TEXT DEFAULT NULL,
        unit_name       TEXT DEFAULT NULL,
        contact_phone   TEXT DEFAULT NULL,
        email           TEXT DEFAULT NULL,
        address         TEXT DEFAULT NULL,
        logo_url        TEXT DEFAULT NULL,
        updated_at      TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime'))
    );

    -- 蓝牙信标位置对照表：major/minor 唯一标识一个信标，映射到固定安装坐标（坐标可空，后续维护）
    CREATE TABLE IF NOT EXISTS beacon_location (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        major       INTEGER NOT NULL,
        minor       INTEGER NOT NULL,
        name        TEXT DEFAULT NULL,      -- 信标位置名称（如"3号仓库门口"）
        lat         REAL DEFAULT NULL,      -- 安装点纬度（留空表示未标定）
        lng         REAL DEFAULT NULL,      -- 安装点经度
        org_id      INTEGER DEFAULT 1,
        updated_at  TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime')),
        UNIQUE(major, minor)
    );

    -- 蓝牙信标上报记录：设备扫描到的信标（G618G 0xD6），命中对照表则带出坐标
    CREATE TABLE IF NOT EXISTS beacon_report (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        phone       TEXT NOT NULL,
        major       INTEGER,
        minor       INTEGER,
        rssi        INTEGER,
        lat         REAL DEFAULT NULL,
        lng         REAL DEFAULT NULL,
        report_time TEXT,
        created_at  TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime'))
    );
    CREATE INDEX IF NOT EXISTS idx_beacon_report ON beacon_report(phone, report_time);
    """)
    conn.commit()

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


# ── PG 专用：location_record 按月分区 ─────────────────────────────────────────

def _setup_pg_partitions():
    """PG 专用：确保 location_record 是按月分区表，并预建当前月 + 未来 3 个月的分区。
    - 表为空时自动重建为分区表；有数据时仅打印警告，不强制迁移。
    - SQLite 后端直接返回，无副作用。
    """
    if DB_BACKEND != 'postgres':
        return
    import datetime as _dt
    conn = get_db()
    try:
        # ① 查询 location_record 的表类型（p=分区表 r=普通表）
        cur = conn.execute(
            "SELECT c.relkind FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relname = ? AND n.nspname = 'public'",
            ('location_record',)
        )
        row = cur.fetchone()
        relkind = row['relkind'] if row else None

        if relkind == 'r':
            # 普通表——检查行数，空表才安全重建
            cnt_row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM location_record", ()
            ).fetchone()
            cnt = cnt_row['cnt'] if cnt_row else 0
            if cnt == 0:
                conn.execute("DROP TABLE IF EXISTS location_record CASCADE", ())
                conn.execute(
                    "CREATE TABLE location_record ("
                    "  id          BIGSERIAL,"
                    "  device_id   INTEGER,"
                    "  phone       TEXT             NOT NULL,"
                    "  lat         DOUBLE PRECISION NOT NULL,"
                    "  lng         DOUBLE PRECISION NOT NULL,"
                    "  altitude    INTEGER,"
                    "  speed       INTEGER,"
                    "  direction   INTEGER,"
                    "  alarm_flag  INTEGER          DEFAULT 0,"
                    "  status_flag INTEGER          DEFAULT 0,"
                    "  mileage     INTEGER,"
                    "  gps_time    TEXT,"
                    "  created_at  TIMESTAMPTZ      DEFAULT NOW(),"
                    "  PRIMARY KEY (id, created_at)"
                    ") PARTITION BY RANGE (created_at)",
                    ()
                )
                log.info("[PG分区] location_record 已转为按月分区表")
            else:
                log.warning(
                    "[PG分区] location_record 已有 %d 条数据，跳过自动重建。"
                    "如需分区，请手动迁移后 DROP TABLE location_record CASCADE 再重启。", cnt
                )
                return

        elif relkind == 'p':
            log.debug("[PG分区] location_record 已是分区表，跳过重建")
        else:
            log.warning("[PG分区] location_record 不存在或状态未知(relkind=%s)，跳过", relkind)
            return

        # ② 预建当前月 + 未来 3 个月的分区
        now = _dt.datetime.now()
        for i in range(4):
            base = now.month - 1 + i          # 0-based 月偏移
            y,  m  = now.year + base // 12,       base % 12 + 1
            ny, nm = now.year + (base+1) // 12, (base+1) % 12 + 1
            pname = f"location_record_{y}_{m:02d}"
            try:
                conn.execute(
                    f"CREATE TABLE IF NOT EXISTS {pname}"
                    f" PARTITION OF location_record"
                    f" FOR VALUES FROM ('{y}-{m:02d}-01') TO ('{ny}-{nm:02d}-01')",
                    ()
                )
                log.info("[PG分区] 分区 %s 已就绪", pname)
            except Exception as _pe:
                log.debug("[PG分区] 跳过分区 %s: %s", pname, _pe)

        # ③ 父表建索引（PG 自动传播到所有子分区）
        try:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_loc_phone_time"
                " ON location_record (phone, gps_time)",
                ()
            )
        except Exception as _ie:
            log.debug("[PG分区] 建索引跳过: %s", _ie)

    except Exception as _e:
        log.error("[PG分区] 分区初始化失败: %s", _e)
    finally:
        conn.close()


_partition_maintainer_started = False
_partition_maintainer_lock    = threading.Lock()

def _partition_maintainer_loop():
    """每 24 小时预建一次分区，保证月末跨月时子表已存在。"""
    import time as _t
    while True:
        _t.sleep(24 * 3600)   # 启动时 _setup_pg_partitions() 已建好，先睡一天
        try:
            _setup_pg_partitions()
        except Exception as _e:
            log.warning("[PG分区] 维护线程异常: %s", _e)

def start_partition_maintainer():
    """启动分区维护后台线程（幂等）。SQLite 后端调用无副作用。"""
    global _partition_maintainer_started
    with _partition_maintainer_lock:
        if _partition_maintainer_started:
            return
        _partition_maintainer_started = True
    if DB_BACKEND != 'postgres':
        return
    threading.Thread(target=_partition_maintainer_loop, daemon=True,
                     name='partition-maintainer').start()
    log.info("[PG分区] 分区维护线程已启动")


# ── 会话管理 ───────────────────────────────────────────────────────────────────

sessions      = {}        # phone → socket
sessions_lock = threading.Lock()
_serial       = [0]
_serial_lock  = threading.Lock()

# 围栏状态：记录每台设备当前"在哪些围栏内"，用于检测穿越
# phone → set of fence_id
fence_device_inside: dict = {}
_fence_lock = threading.Lock()   # 保护下面四个围栏状态字典的并发读写

# ── P0: 防抖 ─────────────────────────────────────────────────────────────────
# 连续读数一致 FENCE_DEBOUNCE_N 次才确认状态切换，避免边界抖动重复告警
FENCE_DEBOUNCE_N = 3
fence_device_pending: dict = {}        # phone → {fence_id: (last_state:bool|None, count:int)}

# ── P1: 停留超时 ──────────────────────────────────────────────────────────────
fence_device_enter_time:    dict = {}  # phone → {fence_id: datetime} 进入时刻
fence_device_dwell_alarmed: dict = {}  # phone → set of fence_id（已触发滞留告警，离开时清除）

def _fence_cleanup(phone):
    """设备下线时清理四个围栏状态字典，防止内存泄漏和 phone 复用时的状态污染。"""
    with _fence_lock:
        fence_device_inside.pop(phone, None)
        fence_device_pending.pop(phone, None)
        fence_device_enter_time.pop(phone, None)
        fence_device_dwell_alarmed.pop(phone, None)


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

    # 收集需要在锁外执行的告警动作（db_exec/emit 不能在锁内调用，避免死锁）
    _alarm_actions = []   # list of callables
    _attend_actions = []  # list of (fence_id, fence_name, action)

    with _fence_lock:
        prev_inside = set(fence_device_inside.get(phone, set()))
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
                    _f = dict(f)  # 捕获当前围栏快照，避免闭包引用循环变量
                    _alarm_actions.append(('enter', device_id, phone, 100, desc, lat, lng, speed_raw, gps_time, _f))
                    log.info("[围栏] %s 进入围栏「%s」", phone, f['name'])
                # 考勤记录（独立于报警开关，进出都记）
                _attend_actions.append((f['id'], f['name'], 'enter'))
                # 记录进入时刻，重置滞留告警标记
                fence_device_enter_time.setdefault(phone, {})[fid] = now_ts
                fence_device_dwell_alarmed.get(phone, set()).discard(fid)

            # ── 状态刚切换：离开围栏 ──────────────────────────────────────────
            elif not confirmed_inside and was_inside:
                if f['alarm_exit']:
                    desc = f'离开围栏: {f["name"]}'
                    _f = dict(f)
                    _alarm_actions.append(('exit', device_id, phone, 101, desc, lat, lng, speed_raw, gps_time, _f))
                    log.info("[围栏] %s 离开围栏「%s」", phone, f['name'])
                # 考勤记录（独立于报警开关，进出都记）
                _attend_actions.append((f['id'], f['name'], 'exit'))
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
                                _f = dict(f)
                                _alarm_actions.append(('dwell', device_id, phone, 102, desc, lat, lng, speed_raw, gps_time, _f))
                                fence_device_dwell_alarmed.setdefault(phone, set()).add(fid)
                                log.info("[围栏] %s 在「%s」停留超时 %.0f秒", phone, f['name'], elapsed)

                # P2: 围栏内超速（每报文都检查，不去重——驾驶员应持续收到超速提示）
                speed_lim = f['speed_limit']     # km/h, 0=关闭
                if speed_lim > 0 and speed_kmh > speed_lim:
                    desc = f'围栏内超速: {f["name"]}（{speed_kmh:.1f}km/h，限{speed_lim}km/h）'
                    _f = dict(f)
                    _alarm_actions.append(('speed', device_id, phone, 103, desc, lat, lng, speed_raw, gps_time, _f))
                    log.info("[围栏] %s 围栏「%s」超速 %.1f>%dkm/h", phone, f['name'], speed_kmh, speed_lim)

        fence_device_inside[phone] = new_inside

    # ── 锁外执行 DB 写入和 Socket 推送（避免在锁内调用 IO 操作） ──────────────
    for act in _alarm_actions:
        _kind, _did, _ph, _atype, _desc, _lat, _lng, _spd, _gt, _f = act
        db_exec(
            "INSERT INTO alarm_record (device_id,phone,alarm_type,alarm_desc,"
            "lat,lng,speed,alarm_time,status) VALUES (?,?,?,?,?,?,?,?,0)",
            (_did, _ph, _atype, _desc, _lat, _lng, _spd, _gt)
        )
        _emit_alarm('alarm', {
            'phone': _ph, 'alarmType': _atype, 'alarmDesc': _desc,
            'lat': _lat, 'lng': _lng, 'time': _gt, 'fenceName': _f['name'],
        }, _ph, _atype)
    for _fid, _fname, _action in _attend_actions:
        _record_attendance(_fid, _fname, phone, _action, gps_time)


# ── 808 消息处理函数 ────────────────────────────────────────────────────────────

ALARM_DEFS = [
    (0,  'SOS 紧急报警'),
    (1,  '超速报警'),
    (2,  '疲劳驾驶报警'),
    (8,  '主电源断开'),
    (25, '碰撞报警'),
    (26, '侧翻报警'),
]

# 报警类型全集（含围栏类），供报警规则页选择
ALARM_TYPE_OPTIONS = [
    (0,   'SOS 紧急报警'),
    (1,   '超速报警'),
    (2,   '疲劳驾驶报警'),
    (8,   '主电源断开'),
    (25,  '碰撞报警'),
    (26,  '侧翻报警'),
    (100, '进入围栏'),
    (101, '离开围栏'),
    (102, '停留超时'),
    (103, '围栏内超速'),
]


def _get_alarm_rule(alarm_type, org_id=1):
    """返回该报警类型在指定组织下的规则 dict；先查本组织，再回退根组织，最后默认。"""
    row = db_query_one(
        "SELECT * FROM alarm_rule WHERE alarm_type=? AND org_id=? LIMIT 1",
        (alarm_type, org_id))
    if not row and org_id != 1:
        row = db_query_one(
            "SELECT * FROM alarm_rule WHERE alarm_type=? AND org_id=1 LIMIT 1",
            (alarm_type,))
    if row:
        return row
    return {'enabled': 1, 'notify_page': 1, 'notify_sms': 0,
            'ring_type': '响几声', 'level': '普通级别'}


def _emit_alarm(event, data, phone, alarm_type):
    """按报警规则决定是否推送到前端；附带 level/ringType 供前端提示。
    规则关闭则完全静默（不推送）。按设备所属组织取规则。"""
    dev = db_query_one("SELECT org_id FROM device WHERE phone=?", (phone,))
    org_id = (dev.get('org_id') if dev else 1) or 1
    rule = _get_alarm_rule(alarm_type, org_id)
    if not rule.get('enabled', 1):
        return   # 该类报警已被规则关闭
    if rule.get('notify_page', 1):
        data = dict(data)
        data['level']    = rule.get('level', '普通级别')
        data['ringType'] = rule.get('ring_type', '响几声')
        _sio_emit(event, data, phone)
    # 短信推送：记录消耗（真实短信网关需另接），此处累加已用条数
    if rule.get('notify_sms', 0):
        try:
            db_exec("UPDATE platform_setting SET sms_used=sms_used+1 WHERE org_id=1 AND sms_enabled=1")
        except Exception:
            pass


def _record_attendance(fence_id, fence_name, phone, action, event_time):
    """记录设备进出围栏的考勤事件"""
    try:
        dev = db_query_one("SELECT name, org_id FROM device WHERE phone=?", (phone,))
        db_exec(
            "INSERT INTO attendance_record (fence_id,fence_name,phone,device_name,action,event_time,org_id) "
            "VALUES (?,?,?,?,?,?,?)",
            (fence_id, fence_name, phone, (dev.get('name') if dev else '') or '',
             action, event_time, (dev.get('org_id') if dev else 1) or 1)
        )
    except Exception as e:
        log.error("[考勤] 记录失败: %s", e)


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
        log.warning("[808] 鉴权失败断开连接 phone=%s auth=%s", canonical, auth_code)
        # 鉴权失败：从 sessions 中删除，防止未鉴权设备被下发指令
        with sessions_lock:
            sessions.pop(canonical, None)
        sock.sendall(p.build_generic_resp(phone, next_serial(), serial, 0x0102, result))
        return 'close'

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
    _gt = loc.get('gps_time')
    gps_time = (_gt.strftime('%Y-%m-%d %H:%M:%S')
                if _gt else datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    mileage     = loc.get('mileage')

    # GPS 字段合理性校验
    if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        log.warning("[808] 非法坐标丢弃 phone=%s lat=%s lng=%s", phone, lat, lng)
        return
    speed = min(max(speed, 0), 5000)   # 限速 5000 km/h（合理上限）
    direction = int(direction) % 360   # 方向截断到 0-359

    device_id = _get_device_id(canonical)   # 缓存查询，免每次上报查库

    # 位置记录 + 设备最新状态：异步批量落库（削减写锁争用，大幅降低上报延迟）
    status = 2 if alarm_flag else 1
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    enqueue_location(
        (device_id, canonical, lat, lng, altitude, speed, direction,
         alarm_flag, loc['status_flag'], mileage, gps_time),
        (canonical, lat, lng, speed, gps_time, status, now)
    )

    # 处理报警（使用 canonical phone，与 device 表保持一致）
    if alarm_flag:
        import time as _t
        for bit, desc in ALARM_DEFS:
            if alarm_flag & (1 << bit):
                _alarm_key = (canonical, bit)
                _now_ts = _t.time()
                with _alarm_last_ts_lock:
                    last_ts = _alarm_last_ts.get(_alarm_key, 0)
                    if _now_ts - last_ts >= _ALARM_DEBOUNCE_SEC:
                        _alarm_last_ts[_alarm_key] = _now_ts
                        should_alarm = True
                    else:
                        should_alarm = False
                if should_alarm:
                    db_exec(
                        "INSERT INTO alarm_record (device_id,phone,alarm_type,alarm_desc,"
                        "lat,lng,speed,alarm_time,status) VALUES (?,?,?,?,?,?,?,?,0)",
                        (device_id, canonical, bit, desc, lat, lng, speed, gps_time)
                    )
                    log.warning("[808] 报警! phone=%s type=%d desc=%s", phone, bit, desc)
                    _emit_alarm('alarm', {
                        'phone':     canonical,
                        'alarmType': bit,
                        'alarmDesc': desc,
                        'lat':       lat,
                        'lng':       lng,
                        'time':      gps_time,
                    }, canonical, bit)

    # 查该设备的角色颜色/形状，随推送带给前端地图渲染
    role_row = db_query_one(
        "SELECT r.name AS role_name, r.color AS role_color, r.icon_type AS role_icon "
        "FROM device LEFT JOIN device_role r ON device.role_id = r.id WHERE device.phone=?",
        (canonical,))
    role_name  = role_row.get('role_name')  if role_row else None
    role_color = role_row.get('role_color') if role_row else None
    role_icon  = role_row.get('role_icon')  if role_row else None

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
        'roleName':  role_name,
        'roleColor': role_color,
        'roleIcon':  role_icon,
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

import protocol_g618g as g618
import geo_resolve

def handle_g618g_frame(conn, frame, phone_holder):
    """处理一个 G618G 上报帧：解析并落库。phone_holder 是 [phone] 单元素列表（可变引用）。"""
    r = g618.parse(frame)
    typ = r.get('type')
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    if typ == 'register':          # 0xF0 建立连接：记录 IMEI、回复 F1、登记会话
        imei = r.get('imei')
        phone_holder[0] = imei
        with sessions_lock:
            sessions[imei] = conn
        # 设备不存在则自动创建
        row = db_query_one("SELECT id FROM device WHERE phone=?", (imei,))
        if not row:
            db_exec("INSERT INTO device (phone,name,manufacturer,terminal_model,status,"
                    "org_id,lifecycle,created_at,updated_at) VALUES (?,?,?,?,1,1,1,?,?)",
                    (imei, 'G618G-'+imei[-6:], 'OFERT', 'G618G', now, now))
        else:
            db_exec("UPDATE device SET status=1,online_time=?,updated_at=? WHERE phone=?", (now, now, imei))
        # 回复 F1（时间戳用当前秒）
        import time as _t
        conn.sendall(g618.build_login_reply(int(_t.time())))
        log.info("[G618G] 设备上线 IMEI=%s", imei)

    elif typ == 'heartbeat':       # 0xF9 心跳：回复保持连接 + 更新电量
        conn.sendall(g618.build_heartbeat_reply())
        imei = phone_holder[0]
        if imei:
            db_exec("UPDATE device SET updated_at=? WHERE phone=?", (now, imei))

    elif typ == 'location':        # 0x03 位置：更新设备最新位置 + 写轨迹（走异步批量）
        imei = phone_holder[0]
        if imei and r.get('valid'):
            _lat = r.get('lat', 0)
            _lng = r.get('lng', 0)
            if not (math.isfinite(_lat) and math.isfinite(_lng)):
                log.warning("[G618G] NaN/Inf 坐标丢弃 phone=%s", imei)
                return typ
            if not (-90 <= _lat <= 90) or not (-180 <= _lng <= 180):
                log.warning("[G618G] 非法坐标丢弃 phone=%s lat=%s lng=%s", imei, _lat, _lng)
                return typ
            gps_time = datetime.fromtimestamp(r['timestamp']).strftime('%Y-%m-%d %H:%M:%S') if r.get('timestamp') else now
            did = _get_device_id(imei)
            enqueue_location(
                (did, imei, r['lat'], r['lng'], 0, 0, 0, 0, 2, None, gps_time),
                (imei, r['lat'], r['lng'], 0, gps_time, 1, now)
            )

    elif typ in ('alarm', 'alarm2'):   # 0x02/0x21 报警
        imei = phone_holder[0]
        if imei:
            did = _get_device_id(imei)
            desc = '、'.join(r.get('alarms') or ['报警'])
            db_exec("INSERT INTO alarm_record (device_id,phone,alarm_type,alarm_desc,alarm_time,status) "
                    "VALUES (?,?,?,?,?,0)", (did, imei, r.get('warn_bits', 0), desc, now))
            has_sos = any('SOS' in a for a in (r.get('alarms') or []))
            _emit_alarm('alarm', {'phone': imei, 'alarmDesc': desc, 'time': now},
                        imei, 0 if has_sos else 99)

    elif typ == 'iccid':           # 0xF3 SIM ICCID
        imei = phone_holder[0]
        if imei:
            db_exec("UPDATE device SET remark=? WHERE phone=?", ('ICCID:'+r['iccid'], imei))

    elif typ == 'charge':          # 0xC3 充电状态
        pass   # 可扩展：记录充电事件

    elif typ == 'wifi_lbs':        # 0xA4 WiFi+基站：调第三方服务换算经纬度后落库
        imei = phone_holder[0]
        if imei:
            ts = r.get('timestamp')
            gps_time = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S') if ts else now
            # 优先 WiFi 定位，其次基站定位；未配置 AMAP_KEY 时 resolve 返回 None
            fix = None
            if geo_resolve.enabled():
                fix = geo_resolve.resolve_wifi(r.get('wifis') or []) \
                      or geo_resolve.resolve_lbs(r.get('cells') or [])
            if fix:
                did = _get_device_id(imei)
                # loc_type: 3=WiFi, 4=基站(区别于 GPS=2)
                loc_type = 3 if fix['source'] == 'wifi' else 4
                enqueue_location(
                    (did, imei, fix['lat'], fix['lng'], 0, 0, 0, 0, loc_type, None, gps_time),
                    (imei, fix['lat'], fix['lng'], 0, gps_time, 1, now)
                )
            else:
                # 未配 Key 或换算失败：仅调试日志，不落坐标（数据不丢，可后续补算）
                log.debug("[G618G] WiFi/基站定位未换算 imei=%s wifi=%d cell=%d key=%s",
                          imei, len(r.get('wifis') or []), len(r.get('cells') or []),
                          'Y' if geo_resolve.enabled() else 'N')

    elif typ == 'ble':             # 0xD6 蓝牙信标：结构化落库到 beacon_report(坐标靠对照表后补)
        imei = phone_holder[0]
        if imei:
            ts = r.get('timestamp')
            rt = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S') if ts else now
            for b in (r.get('beacons') or []):
                # 查信标对照表，命中且有坐标则一并记录
                loc = db_query_one(
                    "SELECT lat, lng FROM beacon_location WHERE major=? AND minor=?",
                    (b.get('major'), b.get('minor')))
                lat = loc['lat'] if loc else None
                lng = loc['lng'] if loc else None
                db_exec("INSERT INTO beacon_report (phone,major,minor,rssi,lat,lng,report_time) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (imei, b.get('major'), b.get('minor'), b.get('rssi'), lat, lng, rt))
                # 命中且有坐标：同时更新设备最新位置(定位类型 5=蓝牙信标)
                if lat is not None and lng is not None:
                    did = _get_device_id(imei)
                    enqueue_location(
                        (did, imei, lat, lng, 0, 0, 0, 0, 5, None, rt),
                        (imei, lat, lng, 0, rt, 1, now)
                    )

    return typ


def handle_client(conn, addr):
    log.info("[TCP] 新连接: %s:%d", addr[0], addr[1])
    buf   = bytearray()
    phone = None
    proto = None   # None=未定, '808', 'g618'
    conn.settimeout(90)

    try:
        while True:
            try:
                data = conn.recv(4096)
            except socket.timeout:
                log.info("[TCP] 空闲超时: %s phone=%s", addr, phone)
                break
            if not data:
                break

            buf.extend(data)
            if len(buf) > 65536:  # 64KB 上限，恶意无标志字节数据保护
                log.warning("[TCP] 缓冲区超限(>64KB)，断开连接 addr=%s", addr)
                break

            # ── 协议识别：首字节 0xBD → G618G；0x7E → JT/T808 ──
            if proto is None and len(buf) >= 1:
                proto = 'g618' if buf[0] == 0xBD else '808'

            if proto == 'g618':
                g_frames, buf = g618.split_frames(bytes(buf))
                buf = bytearray(buf)
                ph_holder = [phone]
                for gf in g_frames:
                    try:
                        handle_g618g_frame(conn, gf, ph_holder)
                    except Exception as e:
                        log.error("[G618G] 处理异常: %s", e, exc_info=True)
                phone = ph_holder[0]
                continue

            frames, buf = p.extract_frames(buf)

            _should_close = False
            for frame in frames:
                try:
                    hdr = p.parse_header(frame)
                except (ValueError, struct.error) as e:
                    log.warning("[%s] 帧头解析失败 addr=%s err=%s", proto or '?', addr, e)
                    continue
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
                    elif msg_id == 0x0102:
                        if handle_auth(conn, ph, serial, body) == 'close':
                            _should_close = True
                            break
                    elif msg_id == 0x0002: handle_heartbeat(conn, ph, serial)
                    elif msg_id == 0x0200: handle_location(conn, ph, serial, body)
                    elif msg_id == 0x0001: pass   # 终端通用应答，忽略
                    else:
                        conn.sendall(p.build_generic_resp(ph, next_serial(), serial, msg_id, 3))
                        log.debug("[808] 未知消息 ID=0x%04X phone=%s", msg_id, ph)
                except Exception as e:
                    log.error("[808] 处理消息异常 ID=0x%04X: %s", msg_id, e, exc_info=True)

            if _should_close:
                break

    except Exception as e:
        log.error("[808] 连接异常: %s %s", addr, e)
    finally:
        conn.close()
        if phone:
            with sessions_lock:
                sessions.pop(phone, None)
            _fence_cleanup(phone)   # 清理围栏状态，防内存泄漏和 phone 复用时状态污染
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            try:
                db_exec("UPDATE device SET status=0, offline_time=? WHERE phone=?", (now, resolve_phone(phone)))
            except Exception:
                pass
        log.info("[808] 连接断开: %s phone=%s", addr, phone)


_TCP_CONN_SEM = threading.Semaphore(500)   # 最大并发 TCP 连接数，防止线程耗尽 OOM

def _handle_client_guarded(conn, addr):
    """用信号量包裹 handle_client，连接退出后自动释放槽位。"""
    try:
        handle_client(conn, addr)
    finally:
        _TCP_CONN_SEM.release()

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
            if not _TCP_CONN_SEM.acquire(blocking=False):
                log.warning("[808] 连接数已达上限(500)，拒绝 %s", addr)
                conn.close()
                continue
            t = threading.Thread(target=_handle_client_guarded, args=(conn, addr), daemon=True)
            t.start()
        except Exception as e:
            log.error("[808] accept 错误: %s", e)


# ── REST API ───────────────────────────────────────────────────────────────────

_MAX_PAGE_SIZE = 500   # 全局分页 size 上限，防止传 size=999999 触发全表扫描 DoS

def _page_params(default_size=20, max_size=_MAX_PAGE_SIZE):
    """安全解析分页参数：捕获 ValueError，并对 size/page 做范围约束。"""
    try:
        page = max(1, int(request.args.get('page', 1)))
    except (ValueError, TypeError):
        page = 1
    try:
        size = min(max_size, max(1, int(request.args.get('size', default_size))))
    except (ValueError, TypeError):
        size = default_size
    return page, size

def ok(data=None):
    resp = jsonify({'code': 200, 'msg': 'success', 'data': data})
    resp.headers['Cache-Control'] = 'no-store'
    return resp

def fail(msg, code=500):
    resp = jsonify({'code': code, 'msg': msg})
    resp.headers['Cache-Control'] = 'no-store'
    return resp, code


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


@app.get('/api/ping')
def api_ping():
    """健康探针：Docker healthcheck / 负载均衡存活检测用。"""
    return ok({'status': 'ok'})


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
    page, size = _page_params(20)
    kw     = request.args.get('keyword', '').strip()
    lc     = request.args.get('lifecycle', '').strip()
    st     = request.args.get('status', '').strip()
    offset = (page - 1) * size
    sids   = _org_scope_ids(request)

    conds, params = [], []
    if kw:
        like = f'%{kw}%'
        conds.append("(device.phone LIKE ? OR device.name LIKE ? OR device.plate_no LIKE ?)")
        params += [like, like, like]
    if lc != '':
        try:
            conds.append("device.lifecycle=?"); params.append(int(lc))
        except ValueError:
            pass
    if st != '':
        try:
            conds.append("device.status=?"); params.append(int(st))
        except ValueError:
            pass
    # org 过滤用带表名的列，避免 JOIN 后 org_id 歧义
    conds, params = _org_where(sids, conds, params, col='device.org_id')

    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    # JOIN 角色表，带出角色颜色/形状供地图与列表按角色渲染
    base = "FROM device LEFT JOIN device_role r ON device.role_id = r.id"
    total   = db_scalar(f"SELECT COUNT(*) {base} {where}", params)
    records = db_query(
        f"SELECT device.*, r.name AS role_name, r.color AS role_color, "
        f"r.icon_type AS role_icon {base} "
        f"{where} ORDER BY device.updated_at DESC LIMIT ? OFFSET ?",
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
    sids = _org_scope_ids(request)
    if sids is not None:
        if not sids:
            return fail('设备不存在或无权限', 404)
        ph = ','.join(['?'] * len(sids))
        row = db_query_one(f"SELECT * FROM device WHERE id=? AND org_id IN ({ph})",
                           [did] + sids)
    else:
        row = db_query_one("SELECT * FROM device WHERE id=?", (did,))
    if not row:
        return fail('设备不存在或无权限', 404)
    row = dict(row)
    row.pop('auth_code', None)   # 鉴权码属设备内部凭据，不对外暴露
    return ok(row)

@app.put('/api/devices/<int:did>')
def update_device(did):
    data = request.get_json() or {}
    now  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    sids = _org_scope_ids(request)
    if sids is not None:
        if not sids:
            return fail('设备不存在或无权限', 404)
        ph = ','.join(['?'] * len(sids))
        row = db_query_one(f"SELECT lifecycle FROM device WHERE id=? AND org_id IN ({ph})",
                           [did] + sids)
    else:
        row = db_query_one("SELECT lifecycle FROM device WHERE id=?", (did,))
    if not row:
        return fail('设备不存在或无权限', 404)

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
    # 组织范围校验：非超管只能操作自己权限范围内的设备（补齐此前遗漏的越权点）
    sids = _org_scope_ids(request)
    scope_sql = ''
    scope_args = []
    if sids is not None:
        if not sids:
            return fail('无权限', 403)
        scope_ph = ','.join('?' * len(sids))
        scope_sql = f" AND org_id IN ({scope_ph})"
        scope_args = list(sids)
    db_exec(
        f"UPDATE device SET lifecycle=?,updated_at=?{extra_col} WHERE id IN ({ph}){scope_sql}",
        [lc, now] + extra_val + list(ids) + scope_args
    )
    return ok({'updated': len(ids)})


def _customer_and_descendants(cid):
    """返回客户 cid 及其所有下级（递归）的 id 列表"""
    result = [cid]
    frontier = [cid]
    seen = {cid}
    while frontier:
        ph = ','.join('?' * len(frontier))
        children = db_query(f"SELECT id FROM customer WHERE parent_id IN ({ph})", frontier)
        frontier = []
        for row in children:
            kid = row['id']
            if kid not in seen:
                seen.add(kid)
                result.append(kid)
                frontier.append(kid)
    return result


@app.get('/api/devices/with_customer')
def devices_with_customer():
    """设备信息列表：JOIN customer，返回绑定人员信息 + 围栏数。
    支持三种查询：customer_id（含子账户）、terminal_model（设备型号）、imei。"""
    page, size = _page_params(20)
    kw        = request.args.get('keyword', '').strip()
    cust_id   = request.args.get('customer_id', '').strip()
    model     = request.args.get('terminal_model', '').strip()
    imei      = request.args.get('imei', '').strip()
    role_id   = request.args.get('role_id', '').strip()
    offset = (page - 1) * size
    sids   = _org_scope_ids(request)

    conds, params = [], []
    if kw:
        like = f'%{kw}%'
        conds.append("(d.phone LIKE ? OR d.name LIKE ? OR c.name LIKE ? OR c.contact LIKE ?)")
        params += [like, like, like, like]
    # 查询①：按账户 + 其所有子账户
    if cust_id:
        try:
            ids = _customer_and_descendants(int(cust_id))
            ph  = ','.join('?' * len(ids))
            conds.append(f"d.customer_id IN ({ph})")
            params += ids
        except ValueError:
            pass
    # 查询②：按设备型号
    if model:
        conds.append("d.terminal_model LIKE ?")
        params.append(f'%{model}%')
    # 查询③：按 IMEI 号
    if imei:
        conds.append("d.phone LIKE ?")
        params.append(f'%{imei}%')
    # 查询④：按角色（role_id=none 表示未分配）
    if role_id:
        if role_id == 'none':
            conds.append("d.role_id IS NULL")
        else:
            try:
                conds.append("d.role_id=?"); params.append(int(role_id))
            except ValueError:
                pass
    conds, params = _org_where(sids, conds, params, col='d.org_id')

    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    count_sql = (
        "SELECT COUNT(*) FROM device d "
        "LEFT JOIN customer c ON d.customer_id = c.id "
        "LEFT JOIN device_role r ON d.role_id = r.id " + where
    )
    data_sql = (
        "SELECT d.id, d.phone, d.name, d.terminal_model, d.last_location_time, "
        "       d.status, d.lifecycle, d.activated_at, d.customer_id, d.role_id, "
        "       r.name as role_name, r.color as role_color, r.icon_type, "
        "       c.contact as real_name, c.gender, c.age, c.avatar, "
        "       c.phone as contact_phone, c.address, c.remark as customer_remark, "
        "       c.login_name as account, "
        "       (SELECT COUNT(*) FROM geo_fence gf "
        "        WHERE gf.devices IS NOT NULL AND gf.devices LIKE '%' || d.phone || '%') "
        "       as fence_count "
        "FROM device d "
        "LEFT JOIN customer c ON d.customer_id = c.id "
        "LEFT JOIN device_role r ON d.role_id = r.id "
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


@app.post('/api/devices/batch_bind')
def batch_bind_devices():
    """批量绑定/转移设备到指定客户（ids + customer_id）。
    未绑定设备 → 批量绑定；已绑定设备 → 转移到新客户。"""
    data = request.get_json() or {}
    ids  = data.get('ids', [])
    cid  = data.get('customer_id')
    if not ids:
        return fail('ids 不能为空', 400)
    if not cid:
        return fail('customer_id 不能为空', 400)
    cust = db_query_one("SELECT id, name FROM customer WHERE id=?", (cid,))
    if not cust:
        return fail('客户不存在', 404)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ph  = ','.join('?' * len(ids))
    db_exec(f"UPDATE device SET customer_id=?,updated_at=? WHERE id IN ({ph})",
            [cid, now] + list(ids))
    add_op_log('批量绑定', f'{len(ids)} 台设备绑定/转移至客户 {cust["name"]}')
    return ok({'updated': len(ids)})


@app.put('/api/devices/<int:did>/role')
def set_device_role(did):
    """给单台设备设置/清除角色（role_id）。role_id 为空则清除。"""
    data = request.get_json() or {}
    rid  = data.get('role_id')
    dev  = db_query_one("SELECT id, phone FROM device WHERE id=?", (did,))
    if not dev:
        return fail('设备不存在', 404)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if rid:
        role = db_query_one("SELECT id, name FROM device_role WHERE id=?", (rid,))
        if not role:
            return fail('角色不存在', 404)
        db_exec("UPDATE device SET role_id=?,updated_at=? WHERE id=?", (rid, now, did))
        add_op_log('设备分配角色', f'设备 {dev["phone"]} 分配角色 {role["name"]}')
    else:
        db_exec("UPDATE device SET role_id=NULL,updated_at=? WHERE id=?", (now, did))
        add_op_log('设备清除角色', f'设备 {dev["phone"]} 清除角色')
    return ok()


@app.post('/api/devices/batch_role')
def batch_set_device_role():
    """批量给设备设置角色（ids + role_id）。role_id 为空则清除。"""
    data = request.get_json() or {}
    ids  = data.get('ids', [])
    rid  = data.get('role_id')
    if not ids:
        return fail('ids 不能为空', 400)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ph  = ','.join('?' * len(ids))
    if rid:
        role = db_query_one("SELECT id, name FROM device_role WHERE id=?", (rid,))
        if not role:
            return fail('角色不存在', 404)
        db_exec(f"UPDATE device SET role_id=?,updated_at=? WHERE id IN ({ph})",
                [rid, now] + list(ids))
        add_op_log('批量分配角色', f'{len(ids)} 台设备分配角色 {role["name"]}')
    else:
        db_exec(f"UPDATE device SET role_id=NULL,updated_at=? WHERE id IN ({ph})",
                [now] + list(ids))
        add_op_log('批量清除角色', f'{len(ids)} 台设备清除角色')
    return ok({'updated': len(ids)})


@app.post('/api/devices/batch_unbind')
def batch_unbind_devices():
    """批量解绑设备（ids）"""
    data = request.get_json() or {}
    ids  = data.get('ids', [])
    if not ids:
        return fail('ids 不能为空', 400)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ph  = ','.join('?' * len(ids))
    db_exec(f"UPDATE device SET customer_id=NULL,updated_at=? WHERE id IN ({ph})",
            [now] + list(ids))
    add_op_log('批量解绑', f'{len(ids)} 台设备已解绑')
    return ok({'updated': len(ids)})


@app.post('/api/devices/batch_command')
def batch_command_devices():
    """批量下发文本指令（phones + text）。仅对在线设备下发。"""
    data   = request.get_json() or {}
    phones = data.get('phones', [])
    text   = (data.get('text') or '').strip()
    if not phones:
        return fail('phones 不能为空', 400)
    if len(phones) > 500:
        return fail('单次批量下发不超过 500 台设备', 400)
    if not text:
        return fail('指令内容不能为空', 400)
    sent, offline = 0, 0
    for phone in phones:
        with sessions_lock:
            conn = sessions.get(phone)
        if not conn:
            offline += 1
            continue
        try:
            body = bytes([0x01]) + text.encode('gbk', errors='replace')
            conn.sendall(p.encode_message(0x8300, phone, next_serial(), body))
            sent += 1
        except Exception:
            offline += 1
    add_op_log('批量下发', f'向 {len(phones)} 台设备下发指令，成功 {sent} 台')
    return ok({'sent': sent, 'offline': offline})


@app.get('/api/devices/export')
def export_devices():
    """导出所有设备（含客户/角色信息）为 JSON，前端转 CSV。不分页。"""
    sids = _org_scope_ids(request)
    conds, params = _org_where(sids, col='d.org_id')
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    rows = db_query(
        "SELECT d.phone, d.name, d.terminal_model, d.status, d.lifecycle, "
        "       d.last_location_time, d.activated_at, "
        "       c.name as account_name, c.login_name as account, "
        "       c.contact as real_name, c.phone as contact_phone, "
        "       r.name as role_name "
        "FROM device d "
        "LEFT JOIN customer c ON d.customer_id = c.id "
        "LEFT JOIN device_role r ON d.role_id = r.id "
        + where + " ORDER BY d.updated_at DESC",
        params
    )
    return ok({'records': rows, 'total': len(rows)})


# ── 角色（设备分组）接口 ──────────────────────────────────────────────────────────

@app.get('/api/roles')
def list_roles():
    sids = _org_scope_ids(request)
    conds, params = _org_where(sids)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    # 带设备数统计
    sql = (
        "SELECT r.*, "
        "(SELECT COUNT(*) FROM device d WHERE d.role_id = r.id) as device_count "
        "FROM device_role r " + where +
        " ORDER BY r.created_at ASC"
    )
    records = db_query(sql, params)
    return ok({'records': records, 'total': len(records)})


@app.post('/api/roles')
def create_role():
    admin = _current_admin(request)
    admin_org_id = (admin.get('org_id') or 1) if admin else 1
    d = request.get_json() or {}
    name = (d.get('name') or '').strip()
    if not name:
        return fail('角色名称不能为空', 400)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    db_exec(
        "INSERT INTO device_role (name,color,icon_type,description,org_id,created_at) "
        "VALUES (?,?,?,?,?,?)",
        (name, d.get('color', '#409EFF'), d.get('icon_type', '圆形'),
         d.get('description', ''), admin_org_id, now)
    )
    add_op_log('角色新增', f'新增角色 {name}')
    return ok()


@app.put('/api/roles/<int:rid>')
def update_role(rid):
    d = request.get_json() or {}
    name = (d.get('name') or '').strip()
    if not name:
        return fail('角色名称不能为空', 400)
    row = db_query_one("SELECT id FROM device_role WHERE id=?", (rid,))
    if not row:
        return fail('角色不存在', 404)
    db_exec(
        "UPDATE device_role SET name=?,color=?,icon_type=?,description=? WHERE id=?",
        (name, d.get('color', '#409EFF'), d.get('icon_type', '圆形'),
         d.get('description', ''), rid)
    )
    add_op_log('角色编辑', f'编辑角色 id={rid}')
    return ok()


@app.delete('/api/roles/<int:rid>')
def delete_role(rid):
    row = db_query_one("SELECT name FROM device_role WHERE id=?", (rid,))
    if not row:
        return fail('角色不存在', 404)
    # 解除该角色下所有设备的角色绑定
    db_exec("UPDATE device SET role_id=NULL WHERE role_id=?", (rid,))
    db_exec("DELETE FROM device_role WHERE id=?", (rid,))
    add_op_log('角色删除', f'删除角色 {row["name"]}')
    return ok()


@app.put('/api/roles/<int:rid>/assign')
def assign_role_devices(rid):
    """批量将设备（按 phone 列表）分配到该角色"""
    d = request.get_json() or {}
    phones = d.get('phones', [])
    row = db_query_one("SELECT id FROM device_role WHERE id=?", (rid,))
    if not row:
        return fail('角色不存在', 404)
    # 先清除该角色下已分配设备（重新赋值语义）
    db_exec("UPDATE device SET role_id=NULL WHERE role_id=?", (rid,))
    if phones:
        ph = ','.join('?' * len(phones))
        db_exec(f"UPDATE device SET role_id=? WHERE phone IN ({ph})", [rid] + list(phones))
    add_op_log('角色分配', f'角色 id={rid} 分配 {len(phones)} 台设备')
    return ok()


# ── 位置接口 ──

@app.get('/api/locations/<phone>/latest')
def latest_location(phone):
    sids = _org_scope_ids(request)
    if sids is not None:
        if not sids:
            return fail('设备不存在或无权限', 403)
        ph = ','.join(['?'] * len(sids))
        dev_check = db_query_one(f"SELECT id FROM device WHERE phone=? AND org_id IN ({ph})",
                                 [phone] + sids)
        if not dev_check:
            return fail('设备不存在或无权限', 403)
    # 优先从 device 缓存字段读（O(1) 主键查），无记录时回退到 location_record
    dev = db_query_one(
        "SELECT last_lat AS lat, last_lng AS lng, last_speed AS speed, "
        "last_location_time AS gps_time FROM device WHERE phone=?", (phone,))
    if dev and dev.get('lat'):
        return ok(dev)
    row = db_query_one(
        "SELECT lat, lng, altitude, speed, direction, alarm_flag, gps_time, created_at "
        "FROM location_record WHERE phone=? ORDER BY gps_time DESC LIMIT 1", (phone,))
    return ok(row)

@app.get('/api/locations/<phone>/history')
def location_history(phone):
    sids = _org_scope_ids(request)
    if sids is not None:
        if not sids:
            return fail('设备不存在或无权限', 403)
        ph = ','.join(['?'] * len(sids))
        dev_check = db_query_one(f"SELECT id FROM device WHERE phone=? AND org_id IN ({ph})",
                                 [phone] + sids)
        if not dev_check:
            return fail('设备不存在或无权限', 403)
    page, size = _page_params(100, max_size=1000)
    start  = request.args.get('start', '')
    end    = request.args.get('end', '')
    offset = (page - 1) * size
    if start and not _DATE_RE.match(start):
        return fail('start 日期格式错误，应为 YYYY-MM-DD', 400)
    if end and not _DATE_RE.match(end):
        return fail('end 日期格式错误，应为 YYYY-MM-DD', 400)
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
    page, size = _page_params(20)
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


@app.post('/api/alarms/batch_handle')
def batch_handle_alarms():
    """批量处理报警（ids 列表）"""
    data = request.get_json() or {}
    ids  = data.get('ids', [])
    if not ids:
        return fail('ids 不能为空', 400)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ph  = ','.join('?' * len(ids))
    db_exec(
        f"UPDATE alarm_record SET status=1, handler=?, handle_note=?, handle_time=? "
        f"WHERE id IN ({ph}) AND status=0",
        [data.get('handler', '管理员'), data.get('note', ''), now] + list(ids)
    )
    add_op_log('批量处理报警', f'批量处理 {len(ids)} 条报警')
    return ok({'handled': len(ids)})


# ── 报警规则接口 ───────────────────────────────────────────────────────────────

@app.get('/api/alarm-types')
def alarm_types():
    """返回可配置的报警类型列表"""
    return ok([{'type': t, 'name': n} for t, n in ALARM_TYPE_OPTIONS])


@app.get('/api/alarm-rules')
def list_alarm_rules():
    sids = _org_scope_ids(request)
    conds, params = _org_where(sids)   # 只看本组织范围内的报警规则
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    rows = db_query(f"SELECT * FROM alarm_rule {where} ORDER BY id ASC", params)
    name_map = {t: n for t, n in ALARM_TYPE_OPTIONS}
    for r in rows:
        r['alarm_type_name'] = name_map.get(r['alarm_type'], f'类型{r["alarm_type"]}')
    return ok({'records': rows, 'total': len(rows)})


@app.post('/api/alarm-rules')
def create_alarm_rule():
    admin = _current_admin(request)
    admin_org_id = (admin.get('org_id') or 1) if admin else 1
    d = request.get_json() or {}
    atype = d.get('alarm_type')
    if atype is None:
        return fail('报警类型不能为空', 400)
    db_exec(
        "INSERT INTO alarm_rule (alarm_type,level,enabled,notify_page,notify_sms,ring_type,org_id) "
        "VALUES (?,?,?,?,?,?,?)",
        (int(atype), d.get('level', '普通级别'), 1 if d.get('enabled', True) else 0,
         1 if d.get('notify_page', True) else 0, 1 if d.get('notify_sms', False) else 0,
         d.get('ring_type', '响几声'), admin_org_id)
    )
    add_op_log('报警规则新增', f'新增报警规则 type={atype}')
    return ok()


@app.put('/api/alarm-rules/<int:rid>')
def update_alarm_rule(rid):
    d = request.get_json() or {}
    row = db_query_one("SELECT alarm_type FROM alarm_rule WHERE id=?", (rid,))
    if not row:
        return fail('规则不存在', 404)
    # alarm_type 未传时沿用原值，避免部分更新（如只切开关）时 int(None) 崩溃
    atype = d.get('alarm_type')
    if atype is None:
        atype = row['alarm_type']
    else:
        try:
            atype = int(atype)
        except (TypeError, ValueError):
            return fail('报警类型格式错误', 400)
    db_exec(
        "UPDATE alarm_rule SET alarm_type=?,level=?,enabled=?,notify_page=?,notify_sms=?,ring_type=? WHERE id=?",
        (atype, d.get('level', '普通级别'),
         1 if d.get('enabled', True) else 0, 1 if d.get('notify_page', True) else 0,
         1 if d.get('notify_sms', False) else 0, d.get('ring_type', '响几声'), rid)
    )
    add_op_log('报警规则编辑', f'编辑报警规则 id={rid}')
    return ok()


@app.delete('/api/alarm-rules/<int:rid>')
def delete_alarm_rule(rid):
    row = db_query_one("SELECT id FROM alarm_rule WHERE id=?", (rid,))
    if not row:
        return fail('规则不存在', 404)
    db_exec("DELETE FROM alarm_rule WHERE id=?", (rid,))
    add_op_log('报警规则删除', f'删除报警规则 id={rid}')
    return ok()


# ── 考勤统计接口 ───────────────────────────────────────────────────────────────

@app.get('/api/attendance')
def list_attendance():
    """按围栏聚合的考勤统计：每个围栏的设备进出次数、设备数"""
    sids = _org_scope_ids(request)
    conds, params = _org_where(sids)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    rows = db_query(
        "SELECT fence_id, fence_name, "
        "       COUNT(DISTINCT phone) as device_count, "
        "       SUM(CASE WHEN action='enter' THEN 1 ELSE 0 END) as enter_count, "
        "       SUM(CASE WHEN action='exit'  THEN 1 ELSE 0 END) as exit_count, "
        "       MAX(event_time) as last_time "
        "FROM attendance_record " + where +
        " GROUP BY fence_id, fence_name ORDER BY last_time DESC",
        params
    )
    return ok({'records': rows, 'total': len(rows)})


@app.get('/api/attendance/detail')
def attendance_detail():
    """某围栏的考勤明细（可按日期过滤）"""
    fence_id = request.args.get('fence_id', '').strip()
    day      = request.args.get('day', '').strip()
    page, size = _page_params(50)
    offset   = (page - 1) * size
    sids     = _org_scope_ids(request)
    conds, params = [], []
    if fence_id:
        try:
            conds.append("fence_id=?"); params.append(int(fence_id))
        except ValueError:
            return fail('fence_id 格式错误', 400)
    if day and _DATE_RE.match(day):
        conds.append("date(event_time)=?"); params.append(day)
    conds, params = _org_where(sids, conds, params)   # 组织隔离
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    total   = db_scalar(f"SELECT COUNT(*) FROM attendance_record {where}", params)
    records = db_query(
        f"SELECT * FROM attendance_record {where} ORDER BY event_time DESC LIMIT ? OFFSET ?",
        params + [size, offset]
    )
    return ok({'records': records, 'total': total, 'page': page})


# ── 健康数据接口 ───────────────────────────────────────────────────────────────

@app.get('/api/health')
def list_health():
    """健康数据查询：按归属账号/日期/IMEI，返回每设备最新一条"""
    kw   = request.args.get('keyword', '').strip()
    day  = request.args.get('day', '').strip()
    page, size = _page_params(20)
    offset = (page - 1) * size
    sids = _org_scope_ids(request)

    conds, params = [], []
    if kw:
        like = f'%{kw}%'
        conds.append("(h.phone LIKE ? OR d.name LIKE ? OR c.name LIKE ?)")
        params += [like, like, like]
    if day and _DATE_RE.match(day):
        conds.append("date(h.record_time)=?"); params.append(day)
    conds, params = _org_where(sids, conds, params, col='h.org_id')
    where = ("WHERE " + " AND ".join(conds)) if conds else ""

    base = ("FROM health_record h "
            "LEFT JOIN device d ON h.phone = d.phone "
            "LEFT JOIN customer c ON d.customer_id = c.id ")
    total   = db_scalar(f"SELECT COUNT(*) {base} {where}", params)
    records = db_query(
        "SELECT h.*, d.name as device_name, c.name as account "
        + base + where +
        " ORDER BY h.record_time DESC LIMIT ? OFFSET ?",
        params + [size, offset]
    )
    return ok({'records': records, 'total': total, 'page': page})


@app.post('/api/health')
def create_health():
    """设备/网关上报健康数据（供 MQTT 或第三方推送写入）"""
    d = request.get_json() or {}
    phone = (d.get('phone') or '').strip()
    if not phone:
        return fail('phone 不能为空', 400)
    dev = db_query_one("SELECT id, org_id FROM device WHERE phone=?", (phone,))
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    db_exec(
        "INSERT INTO health_record (device_id,phone,temperature,wrist_temp,heart_rate,"
        "blood_oxygen,systolic,diastolic,steps,record_time,org_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (dev['id'] if dev else 0, phone, d.get('temperature'), d.get('wrist_temp'),
         d.get('heart_rate'), d.get('blood_oxygen'), d.get('systolic'),
         d.get('diastolic'), d.get('steps'), d.get('record_time') or now,
         dev['org_id'] if dev else 1)
    )
    return ok()


# ── 平台设置接口 ───────────────────────────────────────────────────────────────

def _get_platform_setting(org_id=1):
    row = db_query_one("SELECT * FROM platform_setting WHERE org_id=?", (org_id,))
    if not row:
        db_exec("INSERT OR IGNORE INTO platform_setting (org_id) VALUES (?)", (org_id,))
        row = db_query_one("SELECT * FROM platform_setting WHERE org_id=?", (org_id,))
    return row


# ── 客户独立品牌 ───────────────────────────────────────────────────────────────
# 7 个可白标的品牌字段（运营配置 sms_* / enable_batch_cmd 不在此列，仍全站一份）
BRAND_FIELDS = ['bigscreen_title', 'account_title', 'unit_name',
                'contact_phone', 'email', 'address', 'logo_url']


def _get_customer_branding_row(cid):
    """取某客户自己那份品牌（未配置返回 None）"""
    return db_query_one("SELECT * FROM customer_branding WHERE customer_id=?", (cid,))


def _resolve_branding(cid):
    """
    解析客户 cid 最终生效的品牌：逐字段沿「自己→父级→…→顶级」继承，
    任一字段在某级已配置（非 NULL 非空）即采用；整条链都没配的字段回退全站默认。
    返回含全部 BRAND_FIELDS 的 dict。
    """
    base = _get_platform_setting(1)                       # 全站默认（管理员那份）
    result = {f: (base.get(f) or '') for f in BRAND_FIELDS}
    if not cid:
        return result
    remaining = set(BRAND_FIELDS)
    for anc in _customer_ancestors(cid):                  # 有序：自己在前，顶级在后
        if not remaining:
            break
        row = _get_customer_branding_row(anc)
        if not row:
            continue
        for f in list(remaining):
            v = row.get(f)
            if v is not None and str(v).strip() != '':
                result[f] = v
                remaining.discard(f)
    return result


@app.get('/api/platform-setting')
def get_platform_setting():
    # 客户登录：返回其专属品牌（继承解析）+ 全站运营配置的只读展示
    cid = _get_portal_customer()
    if cid:
        brand = _resolve_branding(cid)
        base  = _get_platform_setting(1)
        # 运营配置沿用全站值（客户端不显示，仅避免字段缺失）
        for k in ['enable_batch_cmd', 'sms_enabled', 'sms_total', 'sms_used']:
            brand[k] = base.get(k)
        return ok(brand)
    return ok(_get_platform_setting(1))


@app.put('/api/platform-setting')
def update_platform_setting():
    d = request.get_json() or {}
    is_admin = bool(_verify_admin_token(request.headers.get('X-Admin-Token', '')))

    # ── 客户：只写自己那份品牌（customer_branding），运营配置一律不碰 ──
    if not is_admin:
        cid = _get_portal_customer()
        if not cid:
            return fail('未授权，请先登录', 401)
        vals = [ (d.get(f) if (d.get(f) is not None and str(d.get(f)).strip() != '') else None)
                 for f in BRAND_FIELDS ]
        cols = ','.join(BRAND_FIELDS)
        ph   = ','.join(['?'] * len(BRAND_FIELDS))
        setc = ','.join(f"{f}=excluded.{f}" for f in BRAND_FIELDS)
        db_exec(
            f"INSERT INTO customer_branding (customer_id,{cols}) VALUES (?,{ph}) "
            f"ON CONFLICT(customer_id) DO UPDATE SET {setc}, "
            f"updated_at=strftime('%Y-%m-%d %H:%M:%S','now','localtime')",
            [cid] + vals
        )
        return ok()

    # ── 管理员：写全站默认（platform_setting），可改运营配置 ──
    db_exec(
        "UPDATE platform_setting SET bigscreen_title=?,account_title=?,unit_name=?,"
        "contact_phone=?,email=?,address=?,logo_url=?,enable_batch_cmd=?,"
        "sms_enabled=?,sms_total=? WHERE org_id=1",
        (d.get('bigscreen_title', '资产管理平台'), d.get('account_title', '资产管理平台'),
         d.get('unit_name', ''), d.get('contact_phone', ''), d.get('email', ''),
         d.get('address', ''), d.get('logo_url', ''),
         1 if d.get('enable_batch_cmd', True) else 0,
         1 if d.get('sms_enabled', False) else 0, int(d.get('sms_total', 0) or 0))
    )
    add_op_log('平台设置', '更新平台设置')
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
        # 天禧协议规定 0x8300 文本/命令字符串用 GBK 编码
        body = bytes([0x01]) + text.encode('gbk', errors='replace')
        conn.sendall(p.encode_message(0x8300, phone, next_serial(), body))
        return ok()
    except Exception as e:
        log.warning("[指令下发] 失败 phone=%s err=%s", phone, e)
        return fail('指令下发失败，请稍后重试')

@app.post('/api/commands/control')
def terminal_control():
    """终端控制。天禧设备(默认)走命令字符串经 0x8300 下发；proto='808' 时用标准
    0x8105 终端控制报文(兼容标准808设备)。
    天禧: {"phone","action":"reset|upload|query"}；标准: {"phone","proto":"808","cmd":4}"""
    data  = request.get_json() or {}
    phone = data.get('phone', '')
    proto = data.get('proto', 'zhiling')
    with sessions_lock:
        conn = sessions.get(phone)
    if not conn:
        return fail(f'设备不在线: {phone}', 404)
    try:
        if proto == '808':
            cmd = int(data.get('cmd', 1))
            conn.sendall(p.encode_message(0x8105, phone, next_serial(), struct.pack('>I', cmd)))
            sent = f'0x8105 cmd={cmd}'
        else:
            action = data.get('action', 'reset')
            _map = {'reset': zl.build_reset, 'upload': zl.build_upload_now, 'query': zl.build_query_status}
            if action not in _map:
                return fail(f'不支持的天禧控制动作: {action}（reset/upload/query）')
            sent = send_zhiling_cmd(conn, phone, _map[action]())
        add_op_log('终端控制', f'phone={phone} {sent}')
        return ok({'sent': sent})
    except Exception as e:
        log.warning("[指令下发] 失败 phone=%s err=%s", phone, e)
        return fail('指令下发失败，请稍后重试')

@app.post('/api/commands/track')
def location_track():
    """临时位置跟踪。天禧设备(默认)用「设置上传频率」命令实现；proto='808' 时用
    标准 0x8202 临时位置跟踪报文。天禧: {"phone","interval":10}(秒)；
    标准: {"phone","proto":"808","interval","duration"}"""
    data     = request.get_json() or {}
    phone    = data.get('phone', '')
    proto    = data.get('proto', 'zhiling')
    interval = int(data.get('interval', 30))
    duration = int(data.get('duration', 0))
    with sessions_lock:
        conn = sessions.get(phone)
    if not conn:
        return fail(f'设备不在线: {phone}', 404)
    try:
        if proto == '808':
            conn.sendall(p.encode_message(0x8202, phone, next_serial(), struct.pack('>HI', interval, duration)))
            sent = f'0x8202 interval={interval} duration={duration}'
        else:
            # 天禧无「临时跟踪」独立指令，用设置上传频率实现(运动/静止/心跳三段)，间隔最小3秒
            iv = max(3, interval)
            sent = send_zhiling_cmd(conn, phone, zl.build_set_interval(iv, iv, max(iv, 30)))
        add_op_log('位置跟踪', f'phone={phone} {sent}')
        return ok({'sent': sent})
    except Exception as e:
        log.warning("[指令下发] 失败 phone=%s err=%s", phone, e)
        return fail('指令下发失败，请稍后重试')


# ── G618G 下行指令接口 ─────────────────────────────────────────────────────────

_G618G_CMD_MAP = {
    'set_freq':          lambda d: g618.build_set_freq(int(d.get('interval', 10))),
    'reboot':            lambda d: g618.build_reboot(),
    'shutdown':          lambda d: g618.build_shutdown(),
    'set_server_ip':     lambda d: g618.build_set_server_ip(d['ip'], int(d['port'])),
    'set_loc_priority':  lambda d: g618.build_set_loc_priority([int(x) for x in d['priorities']]),
    'ble_broadcast':     lambda d: g618.build_set_ble_broadcast(bool(d.get('on', True))),
    'fall_alarm':        lambda d: g618.build_set_fall_alarm(bool(d.get('on', True))),
    'button_shutdown':   lambda d: g618.build_set_button_shutdown(bool(d.get('on', True))),
    'sleep':             lambda d: g618.build_set_sleep(bool(d.get('on', True))),
    'sos_button':        lambda d: g618.build_set_sos_button(bool(d.get('on', True))),
    'charge_power':      lambda d: g618.build_set_charge_power(bool(d.get('on', True))),
    'long_connection':   lambda d: g618.build_set_long_connection(bool(d.get('on', True))),
}

@app.post('/api/commands/g618g')
def g618g_command():
    """G618G 设备下行指令。
    Body: {"phone": "<IMEI>", "cmd": "<命令名>", ...参数}
    支持的 cmd: set_freq, reboot, shutdown, set_server_ip, set_loc_priority,
               ble_broadcast, fall_alarm, button_shutdown, sleep, sos_button,
               charge_power, long_connection
    """
    data  = request.get_json() or {}
    phone = data.get('phone', '')
    cmd   = data.get('cmd', '')
    if not phone or not cmd:
        return fail('phone 和 cmd 不能为空')
    builder = _G618G_CMD_MAP.get(cmd)
    if not builder:
        return fail(f'不支持的 G618G 指令: {cmd}，支持: {", ".join(_G618G_CMD_MAP.keys())}')
    # IP 格式校验（set_server_ip 命令）
    _IPV4_RE = _re.compile(r'^(\d{1,3}\.){3}\d{1,3}$')
    if cmd == 'set_server_ip':
        ip = data.get('ip', '')
        try:
            port = int(data.get('port', 0))
        except (ValueError, TypeError):
            port = 0
        if not _IPV4_RE.match(ip) or not (1 <= port <= 65535):
            return fail('IP 地址或端口格式错误')
    with sessions_lock:
        conn = sessions.get(phone)
    if not conn:
        return fail(f'设备不在线: {phone}', 404)
    try:
        payload = builder(data)
        # G618G 短连接设备需在下行窗口连续发两次（间隔 <20ms）
        conn.sendall(payload)
        import time as _t; _t.sleep(0.01)
        conn.sendall(payload)
        # 记录指令历史
        db_exec("INSERT INTO command_history (phone,device_name,command,result,response) VALUES (?,?,?,?,?)",
                (phone, 'G618G-'+phone[-6:], cmd, 'success', ''))
        add_op_log('G618G指令下发', f'phone={phone} cmd={cmd}')
        return ok({'cmd': cmd, 'phone': phone})
    except Exception as e:
        db_exec("INSERT INTO command_history (phone,device_name,command,result,response) VALUES (?,?,?,?,?)",
                (phone, 'G618G-'+phone[-6:], cmd, 'fail', str(e)))
        log.warning("[指令下发] 失败 phone=%s err=%s", phone, e)
        return fail('指令下发失败，请稍后重试')


# ── 天禧(智令 *XXX#)下行指令接口 ───────────────────────────────────────────────
# 天禧协议规定：参数设置/查询/控制统一走「0x8300 类型0x01 下发命令字符串」，
# 不使用标准 808 的 0x8103/0x8105/0x8202。命令字符串按协议用 GBK 编码。

def send_zhiling_cmd(conn, phone, cmd_str):
    """把智令命令字符串 *XXX# 经 0x8300(类型0x01, GBK) 下发给天禧设备。"""
    body = bytes([0x01]) + cmd_str.encode('gbk', errors='replace')
    conn.sendall(p.encode_message(0x8300, phone, next_serial(), body))
    return cmd_str

@app.post('/api/commands/zhiling')
def zhiling_command():
    """天禧(智令)设备下行指令。
    Body: {"phone": "<终端号>", "cmd": "<命令名>", ...参数}
    命令名取自 protocol_zhiling.AVAILABLE_COMMANDS（set_ip/set_interval/reset/
    upload/set_volume/set_card_info/send_message/ota_http/set_family/
    set_sos_numbers/set_ntrip/set_apn 等 22 条）。
    参数按各命令的 params 定义在 body 里同名给出。"""
    data  = request.get_json() or {}
    phone = data.get('phone', '')
    cmd   = data.get('cmd', '')
    if not phone or not cmd:
        return fail('phone 和 cmd 不能为空')
    spec = zl.AVAILABLE_COMMANDS.get(cmd)
    if not spec:
        return fail(f'不支持的天禧指令: {cmd}，支持: {", ".join(zl.AVAILABLE_COMMANDS.keys())}')
    with sessions_lock:
        conn = sessions.get(phone)
    if not conn:
        return fail(f'设备不在线: {phone}', 404)
    # 按命令声明的 params 顺序从 body 取值，缺参即报错（避免拼出错误命令串）
    try:
        args = []
        for pname in spec['params']:
            if pname not in data:
                return fail(f'缺少参数: {pname}（命令 {cmd} 需要 {spec["params"]}）')
            args.append(data[pname])
        # set_sos_numbers 的构造函数是变参 *numbers，其 params 为单个 'numbers'；
        # 传数组则展开为多个号码，传单个字符串则包成单元素，避免整串被当一个号码
        if cmd == 'set_sos_numbers' and len(args) == 1:
            nums = args[0] if isinstance(args[0], (list, tuple)) else [args[0]]
            cmd_str = spec['func'](*nums)
        else:
            cmd_str = spec['func'](*args)
    except Exception as e:
        return fail(f'构造指令失败: {e}')
    try:
        send_zhiling_cmd(conn, phone, cmd_str)
        db_exec("INSERT INTO command_history (phone,device_name,command,result,response) VALUES (?,?,?,?,?)",
                (phone, resolve_phone(phone), f'{cmd} {cmd_str}', 'success', ''))
        add_op_log('天禧指令下发', f'phone={phone} cmd={cmd} str={cmd_str}')
        return ok({'cmd': cmd, 'phone': phone, 'cmd_str': cmd_str})
    except Exception as e:
        db_exec("INSERT INTO command_history (phone,device_name,command,result,response) VALUES (?,?,?,?,?)",
                (phone, resolve_phone(phone), f'{cmd} {cmd_str}', 'fail', str(e)))
        log.warning("[指令下发] 失败 phone=%s err=%s", phone, e)
        return fail('指令下发失败，请稍后重试')


# ── 蓝牙信标位置对照表管理（major/minor → 坐标）────────────────────────────────

@app.get('/api/beacons')
def list_beacons():
    """信标对照表列表。"""
    rows = db_query("SELECT * FROM beacon_location ORDER BY major, minor")
    return ok({'records': rows, 'total': len(rows)})

@app.post('/api/beacons')
def create_beacon():
    """新增/更新信标位置。Body: {major, minor, name?, lat?, lng?}
    major+minor 已存在则更新（UNIQUE 约束）。"""
    data = request.get_json() or {}
    if 'major' not in data or 'minor' not in data:
        return fail('major 和 minor 不能为空')
    try:
        major = int(data['major']); minor = int(data['minor'])
    except (ValueError, TypeError):
        return fail('major/minor 必须为整数')
    name = data.get('name')
    lat  = data.get('lat'); lng = data.get('lng')
    exist = db_query_one("SELECT id FROM beacon_location WHERE major=? AND minor=?", (major, minor))
    if exist:
        db_exec("UPDATE beacon_location SET name=?, lat=?, lng=?, "
                "updated_at=strftime('%Y-%m-%d %H:%M:%S','now','localtime') WHERE major=? AND minor=?",
                (name, lat, lng, major, minor))
        add_op_log('信标更新', f'major={major} minor={minor} lat={lat} lng={lng}')
    else:
        db_exec("INSERT INTO beacon_location (major, minor, name, lat, lng) VALUES (?,?,?,?,?)",
                (major, minor, name, lat, lng))
        add_op_log('信标新增', f'major={major} minor={minor}')
    return ok()

@app.delete('/api/beacons/<int:bid>')
def delete_beacon(bid):
    db_exec("DELETE FROM beacon_location WHERE id=?", (bid,))
    add_op_log('信标删除', f'id={bid}')
    return ok()

@app.get('/api/beacons/reports')
def list_beacon_reports():
    """信标上报记录（分页）。可按 phone 过滤。"""
    phone = request.args.get('phone', '')
    page, size = _page_params(20, max_size=100)
    offset = (page - 1) * size
    conds, params = [], []
    if phone:
        conds.append("phone=?"); params.append(phone)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    total = db_scalar(f"SELECT COUNT(*) FROM beacon_report {where}", params)
    rows  = db_query(f"SELECT * FROM beacon_report {where} ORDER BY report_time DESC LIMIT ? OFFSET ?",
                     params + [size, offset])
    return ok({'records': rows, 'total': total, 'page': page, 'size': size})


# ── 辅助：记操作日志 ───────────────────────────────────────────────────────────

def add_op_log(action, detail, ip=None):
    # 记录操作人所属组织与真实 IP，供操作日志按组织隔离
    org_id = 1
    try:
        admin = _current_admin(request)
        if admin:
            org_id = admin.get('org_id') or 1
        if ip is None:
            ip = (request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
                  or request.remote_addr or '127.0.0.1')
    except Exception:
        if ip is None:
            ip = '127.0.0.1'
    db_exec("INSERT INTO op_log (action,detail,ip,org_id) VALUES (?,?,?,?)",
            (action, detail, ip, org_id))


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
    page, size = _page_params(20)
    kw      = request.args.get('keyword', '').strip()
    status  = request.args.get('status', '').strip()
    expiring = request.args.get('expiring', '').strip()   # '7' / '30' = 近N天到期
    offset  = (page - 1) * size
    conds, params = [], []
    if kw:
        conds.append("(iccid LIKE ? OR imsi LIKE ? OR operator LIKE ? OR device_phone LIKE ?)")
        like = f'%{kw}%'
        params += [like, like, like, like]
    if status:
        conds.append("status=?"); params.append(status)
    if expiring:
        try:
            days = int(expiring)
            # 在 Python 端算好日期字符串，用普通参数传入，兼容 SQLite / PG
            # （_pg_dialect 的日期改写只匹配字面量 date('now','+N days')，不匹配参数占位符形式）
            from datetime import timedelta as _td
            _expire_limit = (datetime.now() + _td(days=days)).strftime('%Y-%m-%d')
            _today = datetime.now().strftime('%Y-%m-%d')
            conds.append("expire_date IS NOT NULL AND expire_date <= ? AND expire_date >= ?")
            params.append(_expire_limit)
            params.append(_today)
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
    page, size = _page_params(20)
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
    row = db_query_one("SELECT id, iccid FROM sim_card WHERE id=?", (sim_id,))
    if not row: return fail('SIM卡不存在', 404)
    # 原子 UPDATE：balance = balance + amount，再用 CASE 修正欠费状态
    # 避免先 SELECT 再计算再 UPDATE 之间的余额竞态。
    # 扣费 UPDATE 与充值记录 INSERT 放到同一连接的事务里，避免二者不一致
    # （参考 create_customer 的事务写法：_db_lock + get_db + 一次 commit）
    with _db_lock:
        conn = get_db()
        try:
            conn.execute(
                "UPDATE sim_card SET balance = ROUND(balance + ?, 2), "
                "status = CASE WHEN status='欠费' AND (balance + ?) >= 0 THEN '正常' ELSE status END "
                "WHERE id=?",
                (amount, amount, sim_id)
            )
            conn.execute(
                "INSERT INTO recharge (sim_id,iccid,amount,method,plan,remark,operator) VALUES (?,?,?,?,?,?,?)",
                (sim_id, row['iccid'], amount, d.get('method','支付宝'),
                 d.get('plan',''), d.get('remark',''), d.get('operator','管理员')))
            new_balance_row = conn.execute("SELECT balance FROM sim_card WHERE id=?", (sim_id,)).fetchone()
            conn.commit()
        finally:
            conn.close()
    new_balance = float(new_balance_row['balance']) if new_balance_row else 0.0
    add_op_log('充值', f'SIM卡 {row["iccid"]} 充值 ¥{amount:.2f}')
    return ok({'new_balance': new_balance})


# ── 客户管理接口 ───────────────────────────────────────────────────────────────

@app.get('/api/customers')
def list_customers():
    page, size = _page_params(20)
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
    # 补充 has_children 和 parent_name — 批量查询消除 N+1
    for r in records:
        r.pop('password_hash', None)   # 绝不把密码哈希返回给前端

    if records:
        ids        = [r['id'] for r in records]
        parent_ids = list({r['parent_id'] for r in records if r.get('parent_id')})

        # 一次查出所有"有子级"的 parent_id
        ph = ','.join(['?'] * len(ids))
        child_rows = db_query(f"SELECT DISTINCT parent_id FROM customer WHERE parent_id IN ({ph})", ids)
        has_child_set = {row['parent_id'] for row in child_rows}

        # 一次查出所有父级名称
        parent_name_map = {}
        if parent_ids:
            ph2 = ','.join(['?'] * len(parent_ids))
            p_rows = db_query(f"SELECT id, name FROM customer WHERE id IN ({ph2})", parent_ids)
            parent_name_map = {row['id']: row['name'] for row in p_rows}

        for r in records:
            r['has_children'] = r['id'] in has_child_set
            pid = r.get('parent_id')
            r['parent_name'] = parent_name_map.get(pid) if pid else None
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
    # 设备信息页的编辑弹窗只提交部分字段；未提交的字段保留原值，避免被清空
    cur = db_query_one(
        "SELECT name,email,status,reg_date,avatar FROM customer WHERE id=?", (cid,))
    if not cur:
        return fail('客户不存在', 404)
    pick = lambda k, col: d[k] if (k in d) else (cur.get(col) or '')
    name     = pick('name', 'name')
    email    = pick('email', 'email')
    status   = d.get('status') if ('status' in d) else (cur.get('status') or '活跃')
    reg_date = pick('reg_date', 'reg_date')
    avatar   = pick('avatar', 'avatar')
    db_exec("UPDATE customer SET name=?,contact=?,phone=?,email=?,status=?,reg_date=?,remark=?,"
            "gender=?,age=?,address=?,avatar=? WHERE id=?",
            (name, d.get('contact',''), d.get('phone',''), email,
             status, reg_date, d.get('remark',''),
             d.get('gender',''), d.get('age') or None, d.get('address',''),
             avatar, cid))
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


@app.post('/api/upload/avatar')
def upload_avatar():
    """上传头像图片，返回可访问的 URL。仅接受图片类型。"""
    import imghdr as _imghdr
    f = request.files.get('file')
    if not f or not f.filename:
        return fail('未收到文件', 400)
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in _ALLOWED_IMG_EXT:
        return fail('仅支持 jpg/png/gif/webp 图片', 400)
    # 新增：读文件头验证真实 MIME
    file_bytes = f.read(512)
    f.seek(0)
    img_type = _imghdr.what(None, h=file_bytes)
    if img_type not in ('jpeg', 'png', 'gif', 'webp'):
        return fail('文件内容与扩展名不符，请上传真实图片文件', 400)
    # 用时间戳+随机串命名，避免覆盖和路径穿越
    fname = f'avatar_{uuid.uuid4().hex}{ext}'
    fpath = os.path.join(UPLOAD_DIR, fname)
    f.save(fpath)
    url = f'/uploads/{fname}'
    return ok({'url': url})


@app.get('/uploads/<path:filename>')
def serve_upload(filename):
    """提供上传文件（头像等）的访问。防路径穿越 + Token 鉴权（不在 /api/ 前缀下，需手动验证）。"""
    # 需要有效的管理员或客户 Token，防止无鉴权枚举上传文件
    # 图片文件（Logo/头像）允许匿名访问，文件名已是UUID，枚举风险可控
    ext = os.path.splitext(filename)[1].lower()
    public_exts = {'.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.ico'}
    if ext not in public_exts:
        admin_ok = bool(_verify_admin_token(request.headers.get('X-Admin-Token', '')))
        cust_tok = request.headers.get('X-Customer-Token', '') or request.args.get('token', '')
        cust_ok  = bool(_verify_token(cust_tok))
        if not admin_ok and not cust_ok:
            return fail('未授权', 401)
    full_path = os.path.realpath(os.path.join(UPLOAD_DIR, filename))
    upload_root = os.path.realpath(UPLOAD_DIR)
    if not full_path.startswith(upload_root + os.sep) and full_path != upload_root:
        return fail('非法路径', 400)
    if not os.path.isfile(full_path):
        return fail('文件不存在', 404)
    return _send_abs(full_path)


@app.delete('/api/customers/<int:cid>')
def delete_customer(cid):
    # 组织范围校验：非超管只能删除自己权限范围内的客户（补齐此前遗漏的越权点）
    sids = _org_scope_ids(request)
    if sids is not None:
        if not sids:
            return fail('客户不存在或无权限', 403)
        ph = ','.join(['?'] * len(sids))
        row = db_query_one(f"SELECT name FROM customer WHERE id=? AND org_id IN ({ph})",
                           [cid] + list(sids))
    else:
        row = db_query_one("SELECT name FROM customer WHERE id=?", (cid,))
    if not row: return fail('客户不存在或无权限', 404)
    # 递归收集所有后代 ID（含孙级及更深层），防止只删一层导致数据孤岛
    def _all_descendants(root_id):
        result, stack = [], [root_id]
        while stack:
            pid = stack.pop()
            children = [r['id'] for r in db_query("SELECT id FROM customer WHERE parent_id=?", (pid,))]
            result.extend(children)
            stack.extend(children)
        return result
    all_sub_ids = _all_descendants(cid)
    all_cids    = [cid] + all_sub_ids   # 本客户 + 全部后代

    # 1. 一次 IN 查询收集所有客户名下的设备 ID（消除 N+1）
    device_ids = []
    if all_cids:
        cph = ','.join(['?'] * len(all_cids))
        device_ids = [r['id'] for r in
                      db_query(f"SELECT id FROM device WHERE customer_id IN ({cph})", list(all_cids))]

    # 2. 级联清理：一次 IN 删除这些设备的报警记录（历史轨迹保留，设备归还管理员池后仍可查）
    alarm_count = 0
    if device_ids:
        dph = ','.join(['?'] * len(device_ids))
        cnt = db_scalar(f"SELECT COUNT(*) FROM alarm_record WHERE device_id IN ({dph})", list(device_ids))
        alarm_count = cnt or 0
        db_exec(f"DELETE FROM alarm_record WHERE device_id IN ({dph})", list(device_ids))

    # 3. 回收所有后代客户的设备，再删后代客户记录
    for sid in all_sub_ids:
        db_exec("UPDATE device SET customer_id=NULL WHERE customer_id=?", (sid,))
        db_exec("DELETE FROM customer WHERE id=?", (sid,))

    # 4. 将该客户自身的设备归还到管理员池，再删自身
    db_exec("UPDATE device SET customer_id=NULL WHERE customer_id=?", (cid,))
    db_exec("DELETE FROM customer WHERE id=?", (cid,))

    add_op_log('客户删除',
               f'删除客户 {row["name"]}（含 {len(all_sub_ids)} 个子账户）；'
               f'回收设备 {len(device_ids)} 台至管理员池；'
               f'清理报警记录 {alarm_count} 条')
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
    lat = float(d['lat'])
    lng = float(d['lng'])
    if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        return fail('坐标超出有效范围', 400)
    db_exec(
        "INSERT INTO mark_point (name,lat,lng,remark) VALUES (?,?,?,?)",
        (name, lat, lng, d.get('remark', ''))
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
    lat = float(d['lat'])
    lng = float(d['lng'])
    if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        return fail('坐标超出有效范围', 400)
    db_exec(
        "INSERT INTO risk_point (name,lat,lng,level,remark) VALUES (?,?,?,?,?)",
        (name, lat, lng, d.get('level', 'medium'), d.get('remark', ''))
    )
    return ok()

@app.delete('/api/risk_points/<int:rid>')
def delete_risk_point(rid):
    db_exec("DELETE FROM risk_point WHERE id=?", (rid,))
    return ok()


# ── 指令历史接口 ───────────────────────────────────────────────────────────────

@app.get('/api/command-history')
def list_command_history():
    page, size = _page_params(20)
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
    page, size = _page_params(20)
    offset = (page - 1) * size
    sids   = _org_scope_ids(request)
    conds, params = _org_where(sids)   # 按当前管理员组织范围过滤
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    total   = db_scalar(f"SELECT COUNT(*) FROM op_log {where}", params)
    records = db_query(f"SELECT * FROM op_log {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                       params + [size, offset])
    return ok({'records': records, 'total': total, 'page': page})


# ── 报表统计接口 ───────────────────────────────────────────────────────────────

_DATE_RE = _re.compile(r'^\d{4}-\d{2}-\d{2}$')

@app.get('/api/report/summary')
def report_summary():
    # 时间范围参数（默认近30天，最多365天）
    try:
        days = min(365, max(1, int(request.args.get('days', 30))))
    except (ValueError, TypeError):
        days = 30
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

    if sids is not None:
        _alarm_sids = sids if sids else []
        _alarm_ph   = ','.join(['?'] * len(_alarm_sids)) if _alarm_sids else '0'
        alarm_total = db_scalar(
            f"SELECT COUNT(*) FROM alarm_record ar "
            f"JOIN device d ON ar.device_id=d.id WHERE d.org_id IN ({_alarm_ph})",
            _alarm_sids)
        alarm_unhandled = db_scalar(
            f"SELECT COUNT(*) FROM alarm_record ar "
            f"JOIN device d ON ar.device_id=d.id WHERE d.org_id IN ({_alarm_ph}) AND ar.status=0",
            _alarm_sids)
    else:
        alarm_total     = db_scalar("SELECT COUNT(*) FROM alarm_record")
        alarm_unhandled = db_scalar("SELECT COUNT(*) FROM alarm_record WHERE status=0")
    # 使用参数化查询，防止日期字段 SQL 注入
    if start_raw:
        alarm_period = db_scalar(
            "SELECT COUNT(*) FROM alarm_record WHERE date(alarm_time) BETWEEN ? AND ?",
            [start_raw, end_raw]
        )
    else:
        from datetime import timedelta
        _start_date = (datetime.now() - timedelta(days=days - 1)).strftime('%Y-%m-%d')
        alarm_period = db_scalar(
            "SELECT COUNT(*) FROM alarm_record WHERE date(alarm_time) >= ?",
            [_start_date]
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
        from datetime import timedelta as _td
        _rstart_date = (datetime.now() - _td(days=days - 1)).strftime('%Y-%m-%d')
        recharge_period = db_scalar(
            "SELECT COALESCE(SUM(amount),0) FROM recharge WHERE date(created_at) >= ?",
            [_rstart_date]
        )

    # 趋势：按实际天数
    trend_days = min(days, 30)
    from datetime import timedelta as _trd
    _trend_date = (datetime.now() - _trd(days=trend_days - 1)).strftime('%Y-%m-%d')
    alarm_trend = db_query(
        "SELECT date(alarm_time) as day, COUNT(*) as cnt FROM alarm_record "
        "WHERE date(alarm_time) >= ? GROUP BY day ORDER BY day",
        [_trend_date]
    )
    alarm_types = db_query(
        "SELECT alarm_desc, COUNT(*) as cnt FROM alarm_record GROUP BY alarm_desc ORDER BY cnt DESC LIMIT 6"
    )
    loc_trend = db_query(
        "SELECT date(gps_time) as day, COUNT(*) as cnt FROM location_record "
        "WHERE date(gps_time) >= ? GROUP BY day ORDER BY day",
        [_trend_date]
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
_device_org_cache: dict = {}        # phone → (org_id, expire_ts)；5 分钟 TTL，防止设备迁移组织后推错房间
_ORG_CACHE_TTL = 300                # 秒

def _get_device_org(phone: str) -> int:
    """返回设备所属 org_id（TTL 缓存 5 分钟，迁移组织后最迟 5 分钟生效）"""
    import time as _t
    now = _t.time()
    entry = _device_org_cache.get(phone)
    if entry and now < entry[1]:
        return entry[0]
    row = db_query_one("SELECT org_id FROM device WHERE phone=?", (phone,))
    oid = int(row.get('org_id') or 1) if row else 1
    _device_org_cache[phone] = (oid, now + _ORG_CACHE_TTL)
    return oid

def _invalidate_device_org_cache(phone: str):
    """设备迁移组织时主动失效缓存，让下次推送立即读到新 org_id。"""
    _device_org_cache.pop(phone, None)

def _customer_ancestors(cid):
    """返回客户 cid 及其所有上级客户 id（上级能看下级设备，故推送要覆盖整条链）"""
    result = []
    seen = set()
    cur = cid
    while cur and cur not in seen:
        seen.add(cur)
        result.append(cur)
        row = db_query_one("SELECT parent_id FROM customer WHERE id=?", (cur,))
        cur = row.get('parent_id') if row else None
    return result

def _sio_emit(event: str, data: dict, phone: str):
    """
    推送 Socket.IO 事件：
    - 管理员/超管：按设备 org_id 推到 org_{id} 房间 + broadcast（超管）
    - 客户：只推到设备归属客户及其上级客户的 cust_{id} 房间（严格按 customer 隔离，
      不再按 org 广播，避免同组织其他客户收到不属于自己的设备轨迹）
    """
    # customer_id 会随绑定变化，实时查库不缓存；org_id 用缓存
    row = db_query_one("SELECT org_id, customer_id FROM device WHERE phone=?", (phone,))
    org_id = int(row.get('org_id') or 1) if row else 1
    cid    = row.get('customer_id') if row else None
    socketio.emit(event, data, room=f'org_{org_id}')   # 该组织管理员
    socketio.emit(event, data, room='broadcast')        # 超级管理员
    # 归属客户及其上级客户
    if cid:
        for c in _customer_ancestors(cid):
            socketio.emit(event, data, room=f'cust_{c}')


@socketio.on('connect')
def on_connect(auth):
    """验证 token，加入对应 org 房间；未认证则拒绝连接。"""
    # 优先从 Socket.IO auth 字段读取（不会出现在 URL/日志中）；
    # 兼容旧客户端仍通过 query 传 token 的场景
    token = (auth or {}).get('token', '') or request.args.get('token', '')
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
        cust = db_query_one("SELECT id FROM customer WHERE id=?", (cid,))
        if not cust:
            return False
        # 只加入自己的客户房间（不再按 org 广播，避免收到同组织其他客户的设备轨迹）
        join_room(f'cust_{cid}')
        log.debug("[WS] 客户 %d 已连接", cid)
        return True

    return False   # 未知/过期 token，拒绝


@socketio.on('disconnect')
def on_disconnect():
    log.debug("[WS] 客户端断开")


_DEBUG_MODE = os.environ.get('DEBUG_MODE', '0') == '1'

@app.get('/api/fences/check/<path:phone>')
def debug_fence_check(phone):
    """
    调试端点：手动对某设备执行一次围栏检测，返回每个围栏的 inside 状态。
    生产环境须设置 DEBUG_MODE=0（默认），仅本地调试时开启 DEBUG_MODE=1。
    用法：GET /api/fences/check/13800138001
    """
    if not _DEBUG_MODE:
        return fail('调试接口已禁用，设置 DEBUG_MODE=1 可启用', 403)
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
    """列出所有注册路由（调试用，生产须设置 DEBUG_MODE=0）"""
    if not _DEBUG_MODE:
        return fail('调试接口已禁用，设置 DEBUG_MODE=1 可启用', 403)
    routes = [{'rule': str(r.rule), 'methods': sorted(r.methods)} for r in app.url_map.iter_rules()]
    return jsonify(routes)


# ── 管理员登录接口 ─────────────────────────────────────────────────────────────

def _require_secret(env_name: str) -> str:
    """读取签名密钥。生产必须通过环境变量注入；缺失则拒绝启动（fail-closed），
    杜绝用硬编码默认密钥继续运行导致 token 可被离线伪造。
    仅当显式设置 ALLOW_DEV_SECRET=1（本地开发）时，才允许回退到临时开发密钥。"""
    val = os.environ.get(env_name, '').strip()
    if val:
        return val
    if os.environ.get('ALLOW_DEV_SECRET') == '1':
        log.warning("[安全] %s 未设置，正在使用开发临时密钥（仅限本地！生产务必注入 %s）", env_name, env_name)
        return f'DEV_ONLY_{env_name}_do_not_use_in_prod'
    raise RuntimeError(
        f"[安全] 环境变量 {env_name} 未设置，拒绝启动。请注入强随机密钥，"
        f"或本地开发时设置 ALLOW_DEV_SECRET=1。")

_ADMIN_SECRET = _require_secret('ADMIN_SECRET')

def _make_admin_token(admin_id: int) -> str:
    import hmac as _hmac
    ts  = int(_time_mod.time())
    raw = f"admin:{admin_id}:{ts}"
    sig = _hmac.new(_ADMIN_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()  # 完整 256 bit
    return base64.b64encode(f"{raw}:{sig}".encode()).decode()

def _verify_admin_token(token: str):
    import hmac as _hmac
    try:
        decoded = base64.b64decode(token).decode()
        _, aid_s, ts_s, sig = decoded.rsplit(':', 3)
        raw = f"admin:{aid_s}:{ts_s}"
        expected = _hmac.new(_ADMIN_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()
        if not _hmac.compare_digest(sig, expected):   # 常量时间比较，防时序攻击
            return None
        if _time_mod.time() - int(ts_s) > 30 * 24 * 3600:
            return None
        aid = int(aid_s)
        # 检查 admin 账号是否仍然活跃；DB 查询失败时不阻断（避免 DB 抖动误登出）
        try:
            row = db_query_one("SELECT is_active FROM admin_user WHERE id=?", (aid,))
            if row is not None and not row.get('is_active', 1):
                return None  # 明确禁用才拒绝
        except Exception:
            pass  # DB 异常不影响已签发的有效 token
        return aid
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
    # 放行：读取平台白标设置（名称/Logo），客户门户与登录页均需展示，只读无敏感数据
    if path == '/api/platform-setting' and request.method == 'GET':
        return None
    # 放行：客户保存平台品牌设置（PUT）。持有效客户 token 即可，
    # 保存逻辑内部仅允许客户改品牌字段，短信/功能配置保留原值。
    if path == '/api/platform-setting' and request.method == 'PUT' and _get_portal_customer():
        return None
    # 放行：客户上传图片（Logo/头像）。持有效客户 token 即可，接口只存图返回 URL，无敏感操作。
    if path == '/api/upload/avatar' and _get_portal_customer():
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
        "SELECT id, username, real_name, org_id, org_level, user_type, password_hash "
        "FROM admin_user WHERE username=? AND COALESCE(is_active,1)=1",
        (username,)
    )
    if not row or not _verify_pw(password, row.get('password_hash') or ''):
        return fail('账号或密码错误', 401)
    # 如果是旧 SHA-256 哈希，自动升级为 bcrypt
    if not (row['password_hash'].startswith('$2b$') or row['password_hash'].startswith('$2a$')):
        db_exec("UPDATE admin_user SET password_hash=? WHERE username=?",
                (_hash_pw(password), row['username']))
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

@app.post('/api/auth/logout')
def admin_logout():
    """管理员注销（前端清除 token，服务端无状态）"""
    return ok({'message': '注销成功'})

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
    row = db_query_one("SELECT password_hash FROM admin_user WHERE id=?", (admin_id,))
    if not row or not _verify_pw(old_pwd, row['password_hash']):
        return fail('原密码错误', 400)
    db_exec("UPDATE admin_user SET password_hash=? WHERE id=?", (_hash_pw(new_pwd), admin_id))
    return ok()


# ── 客户门户：账号 / Token 工具 ────────────────────────────────────────────────

_PORTAL_SECRET = _require_secret('PORTAL_SECRET')

def _make_token(customer_id: int) -> str:
    import hmac as _hmac
    ts  = int(_time_mod.time())
    raw = f"{customer_id}:{ts}"
    sig = _hmac.new(_PORTAL_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return base64.b64encode(f"{raw}:{sig}".encode()).decode()

def _verify_token(token: str):
    """返回 customer_id(int) 或 None（无效/过期/已禁用）"""
    import hmac as _hmac
    try:
        decoded = base64.b64decode(token).decode()
        cid_s, ts_s, sig = decoded.rsplit(':', 2)
        raw = f"{cid_s}:{ts_s}"
        expected = _hmac.new(_PORTAL_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()
        if not _hmac.compare_digest(sig, expected):
            return None
        if _time_mod.time() - int(ts_s) > 30 * 24 * 3600:  # 30天有效
            return None
        customer_id = int(cid_s)
        # 检查客户账号是否仍然活跃；DB 查询失败时不阻断（避免 DB 抖动误登出）
        try:
            row = db_query_one("SELECT status FROM customer WHERE id=?", (customer_id,))
            if row is not None and row.get('status') != '活跃':
                return None  # 明确禁用才拒绝
        except Exception:
            pass  # DB 异常不影响已签发的有效 token
        return customer_id
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
        "SELECT id, name, login_name, password_hash FROM customer WHERE login_name=? AND status='活跃'",
        (login_name,)
    )
    if not row or not _verify_pw(password, row.get('password_hash') or ''):
        return fail('账号或密码错误', 401)
    # 如果是旧 SHA-256 哈希，自动升级为 bcrypt
    if not (row['password_hash'].startswith('$2b$') or row['password_hash'].startswith('$2a$')):
        db_exec("UPDATE customer SET password_hash=? WHERE id=?",
                (_hash_pw(password), row['id']))
    token = _make_token(row['id'])
    return ok({'token': token, 'customer': {'id': row['id'], 'name': row['name'], 'login_name': row['login_name']}})

@app.post('/api/customer/logout')
def portal_logout():
    """客户注销"""
    return ok({'message': '注销成功'})

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
    page, size = _page_params(100, max_size=1000)
    start  = request.args.get('start', '')
    end    = request.args.get('end', '')
    offset = (page - 1) * size
    if start and not _DATE_RE.match(start):
        return fail('start 日期格式错误，应为 YYYY-MM-DD', 400)
    if end and not _DATE_RE.match(end):
        return fail('end 日期格式错误，应为 YYYY-MM-DD', 400)
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
    page, size = _page_params(20)
    offset = (page - 1) * size
    ph     = ','.join('?' * len(phones))
    total   = db_scalar(f"SELECT COUNT(*) FROM alarm_record WHERE phone IN ({ph})", phones)
    records = db_query(f"SELECT * FROM alarm_record WHERE phone IN ({ph}) ORDER BY alarm_time DESC LIMIT ? OFFSET ?",
                       phones + [size, offset])
    return ok({'records': records, 'total': total, 'page': page})


# ── 客户门户：考勤统计（仅本人及下级名下设备）──────────────────────────────────
@app.get('/api/customer/attendance')
def portal_attendance():
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    phones = _get_subtree_phones(cid)
    if not phones:
        return ok({'records': [], 'total': 0})
    ph = ','.join('?' * len(phones))
    rows = db_query(
        "SELECT fence_id, fence_name, "
        "       COUNT(DISTINCT phone) as device_count, "
        "       SUM(CASE WHEN action='enter' THEN 1 ELSE 0 END) as enter_count, "
        "       SUM(CASE WHEN action='exit'  THEN 1 ELSE 0 END) as exit_count, "
        "       MAX(event_time) as last_time "
        f"FROM attendance_record WHERE phone IN ({ph}) "
        "GROUP BY fence_id, fence_name ORDER BY last_time DESC",
        phones
    )
    return ok({'records': rows, 'total': len(rows)})


@app.get('/api/customer/attendance/detail')
def portal_attendance_detail():
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    phones = _get_subtree_phones(cid)
    if not phones:
        return ok({'records': [], 'total': 0, 'page': 1})
    fence_id = request.args.get('fence_id', '').strip()
    day      = request.args.get('day', '').strip()
    page, size = _page_params(50)
    offset   = (page - 1) * size
    ph = ','.join('?' * len(phones))
    conds  = [f"phone IN ({ph})"]
    params = list(phones)
    if fence_id:
        try:
            conds.append("fence_id=?"); params.append(int(fence_id))
        except ValueError:
            return fail('fence_id 格式错误', 400)
    if day and _DATE_RE.match(day):
        conds.append("date(event_time)=?"); params.append(day)
    where = "WHERE " + " AND ".join(conds)
    total   = db_scalar(f"SELECT COUNT(*) FROM attendance_record {where}", params)
    records = db_query(
        f"SELECT * FROM attendance_record {where} ORDER BY event_time DESC LIMIT ? OFFSET ?",
        params + [size, offset])
    return ok({'records': records, 'total': total, 'page': page})


# ── 客户门户：健康数据（仅本人及下级名下设备）──────────────────────────────────
@app.get('/api/customer/health')
def portal_health():
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    phones = _get_subtree_phones(cid)
    if not phones:
        return ok({'records': [], 'total': 0, 'page': 1})
    kw   = request.args.get('keyword', '').strip()
    day  = request.args.get('day', '').strip()
    page, size = _page_params(20)
    offset = (page - 1) * size
    ph = ','.join('?' * len(phones))
    conds  = [f"h.phone IN ({ph})"]
    params = list(phones)
    if kw:
        like = f'%{kw}%'
        conds.append("(h.phone LIKE ? OR d.name LIKE ? OR c.name LIKE ?)")
        params += [like, like, like]
    if day and _DATE_RE.match(day):
        conds.append("date(h.record_time)=?"); params.append(day)
    where = "WHERE " + " AND ".join(conds)
    base = ("FROM health_record h "
            "LEFT JOIN device d ON h.phone = d.phone "
            "LEFT JOIN customer c ON d.customer_id = c.id ")
    total   = db_scalar(f"SELECT COUNT(*) {base} {where}", params)
    records = db_query(
        "SELECT h.*, d.name as device_name, c.name as account "
        + base + where +
        " ORDER BY h.record_time DESC LIMIT ? OFFSET ?",
        params + [size, offset])
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
        body = bytes([0x01]) + text.encode('gbk', errors='replace')
        conn.sendall(p.encode_message(0x8300, phone, next_serial(), body))
        db_exec("INSERT INTO command_history (phone,device_name,command,result) VALUES (?,?,?,?)",
                (phone, dev['name'] or phone, text, '已发送'))
        return ok()
    except Exception as e:
        log.warning("[指令下发] 失败 phone=%s err=%s", phone, e)
        return fail('指令下发失败，请稍后重试')


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
    page, size = _page_params(20)
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
    page, size = _page_params(20)
    keyword = request.args.get('keyword', '').strip()
    offset  = (page - 1) * size
    base_cols = ("SELECT device.id, device.phone, device.name, device.plate_no, "
                 "device.manufacturer, device.terminal_model, device.last_lat, device.last_lng, "
                 "device.last_speed, device.last_location_time, device.status, device.customer_id, "
                 "r.name AS role_name, r.color AS role_color, r.icon_type AS role_icon "
                 "FROM device LEFT JOIN device_role r ON device.role_id = r.id")
    all_cids = _get_all_descendant_cids(cid)
    cid_ph   = ','.join('?' * len(all_cids))
    if keyword:
        kw      = f'%{keyword}%'
        total   = db_scalar(f"SELECT COUNT(*) FROM device WHERE customer_id IN ({cid_ph}) AND (name LIKE ? OR phone LIKE ?)",
                            all_cids + [kw, kw])
        records = db_query(f"{base_cols} WHERE device.customer_id IN ({cid_ph}) AND (device.name LIKE ? OR device.phone LIKE ?) "
                           f"ORDER BY device.id LIMIT ? OFFSET ?",
                           all_cids + [kw, kw, size, offset])
    else:
        total   = db_scalar(f"SELECT COUNT(*) FROM device WHERE customer_id IN ({cid_ph})", all_cids)
        records = db_query(f"{base_cols} WHERE device.customer_id IN ({cid_ph}) ORDER BY device.id LIMIT ? OFFSET ?",
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
    page, size = _page_params(20)
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
    # 批量查设备数（单条 GROUP BY 替代 N 次循环）
    if rows:
        sub_ids = [r['id'] for r in rows]
        ph = ','.join(['?'] * len(sub_ids))
        dc_rows = db_query(
            f"SELECT customer_id, COUNT(*) AS cnt FROM device WHERE customer_id IN ({ph}) GROUP BY customer_id",
            sub_ids)
        dc_map = {row['customer_id']: row['cnt'] for row in dc_rows}
        for r in rows:
            r['device_count'] = dc_map.get(r['id'], 0)
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
    page, size = _page_params(20)
    keyword = request.args.get('keyword', '').strip()
    status  = request.args.get('status',  '').strip()
    offset  = (page - 1) * size
    ph      = ','.join('?' * len(phones))
    cond    = f"device_phone IN ({ph})"
    args    = phones[:]
    if keyword:
        kw    = f'%{keyword}%'
        cond += " AND (iccid LIKE ? OR imsi LIKE ? OR operator LIKE ? OR device_phone LIKE ?)"
        args += [kw, kw, kw, kw]
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
    # 原子 UPDATE：消除余额读写竞态
    db_exec(
        "UPDATE sim_card SET balance = ROUND(balance + ?, 2), "
        "status = CASE WHEN status='欠费' AND (balance + ?) >= 0 THEN '正常' ELSE status END "
        "WHERE id=?",
        (amount, amount, sid)
    )
    updated = db_query_one("SELECT balance FROM sim_card WHERE id=?", (sid,))
    new_balance = float(updated['balance']) if updated else 0.0
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
    page, size = _page_params(20)
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
    # 原子 UPDATE：消除余额读写竞态
    db_exec(
        "UPDATE sim_card SET balance = ROUND(balance + ?, 2), "
        "status = CASE WHEN status='欠费' AND (balance + ?) >= 0 THEN '正常' ELSE status END "
        "WHERE id=?",
        (amount, amount, sim_id)
    )
    updated = db_query_one("SELECT balance FROM sim_card WHERE id=?", (sim_id,))
    new_balance = float(updated['balance']) if updated else 0.0
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
    sids = _org_scope_ids(request)
    if sids is not None:
        if not sids:
            return fail('客户不存在或无权限', 403)
        ph = ','.join(['?'] * len(sids))
        cust = db_query_one(f"SELECT id FROM customer WHERE id=? AND org_id IN ({ph})",
                            [cid] + sids)
        if not cust:
            return fail('客户不存在或无权限', 403)
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
    sids = _org_scope_ids(request)
    if sids is not None:
        if not sids:
            return fail('客户不存在或无权限', 403)
        ph = ','.join(['?'] * len(sids))
        cust = db_query_one(f"SELECT id FROM customer WHERE id=? AND org_id IN ({ph})",
                            [cid] + sids)
        if not cust:
            return fail('客户不存在或无权限', 403)
    records = db_query(
        "SELECT id, phone, name, status, last_lat, last_lng, last_location_time FROM device WHERE customer_id=?",
        (cid,)
    )
    return ok(records)

@app.put('/api/customers/<int:cid>/devices')
def assign_customer_devices(cid):
    """管理员将一组设备（phone 列表）分配给客户；phones=[] 则全部解绑"""
    sids = _org_scope_ids(request)
    if sids is not None:
        if not sids:
            return fail('客户不存在或无权限', 403)
        ph = ','.join(['?'] * len(sids))
        cust = db_query_one(f"SELECT id FROM customer WHERE id=? AND org_id IN ({ph})",
                            [cid] + sids)
        if not cust:
            return fail('客户不存在或无权限', 403)
    row = db_query_one("SELECT name FROM customer WHERE id=?", (cid,))
    if not row:
        return fail('客户不存在', 404)
    d      = request.get_json() or {}
    phones = d.get('phones', [])
    # 分配前校验：非超管只能分配自己 org scope 内的设备（补齐此前遗漏的越权点）
    if sids is not None and phones:
        scope_ph = ','.join(['?'] * len(sids))
        ph_q     = ','.join(['?'] * len(phones))
        valid = db_query(
            f"SELECT phone FROM device WHERE phone IN ({ph_q}) AND org_id IN ({scope_ph})",
            list(phones) + list(sids))
        valid_phones = {r['phone'] for r in valid}
        phones = [p for p in phones if p in valid_phones]  # 只分配有权限的设备
    # 只清该客户的直属设备（子客户设备由客户自己管理，不联动）
    db_exec("UPDATE device SET customer_id=NULL WHERE customer_id=?", (cid,))
    # 批量分配：单条 WHERE phone IN(…) 替代 N 次循环，减少锁竞争
    if phones:
        ph = ','.join(['?'] * len(phones))
        db_exec(f"UPDATE device SET customer_id=? WHERE phone IN ({ph})", [cid] + list(phones))
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
    注意：不可返回 ''（空字符串），空串会匹配所有 org_path LIKE '%'，等同于无限制。
    """
    if not admin:
        return '__NONE__'   # 没有 admin 信息，用不可能存在的前缀实现最严格限制
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
    # 组织范围校验：只能查看自己权限范围内组织的子级，防止跨组织结构探测
    admin = _current_admin(request)
    scope = _scope_path(admin)
    if not _org_in_scope(scope, oid):
        return fail('无权访问该组织', 403)
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
    if not admin:
        return fail('未登录', 401)
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
    # ── 越权提权防护 ──────────────────────────────────────────────────────────
    # 操作者只能创建 user_type ≤ 自身 user_type 的账号，杜绝低权限管理员提权为超管
    operator_type  = int(admin.get('user_type') or 1)
    requested_type = int(d.get('userType') or d.get('user_type') or 1)
    new_user_type  = min(requested_type, operator_type)
    # ─────────────────────────────────────────────────────────────────────────
    db_exec(
        "INSERT INTO admin_user (username,password_hash,real_name,phone,org_id,org_level,user_type,is_active,created_at) "
        "VALUES (?,?,?,?,?,?,?,1,?)",
        (username, _hash_pw(password),
         d.get('realName') or d.get('real_name') or '',
         d.get('phone') or '',
         org_id,
         (org['org_level'] if org else 1),
         new_user_type,
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
    admin  = _current_admin(request)
    scope  = _scope_path(admin)
    target = db_query_one("SELECT id, username, user_type, org_id FROM admin_user WHERE id=?", (uid,))
    if not target:
        return fail('用户不存在', 404)
    # 组织范围校验：只能重置自己权限范围内组织的用户密码（补齐此前遗漏的越权点）
    if not _org_in_scope(scope, target['org_id']):
        return fail('无权重置该用户密码', 403)
    # 内置超管只有超管本人能改，防止低权限管理员接管
    if target['user_type'] == 9 and target['username'] == 'admin' and not (admin and admin.get('user_type') == 9):
        return fail('无权重置超级管理员密码', 403)
    d       = request.get_json() or {}
    new_pwd = (d.get('newPassword') or d.get('new_password') or '').strip()
    if not new_pwd or len(new_pwd) < 6:
        return fail('新密码不能少于 6 位', 400)
    db_exec("UPDATE admin_user SET password_hash=? WHERE id=?", (_hash_pw(new_pwd), uid))
    add_op_log('重置密码', f'重置用户 {target["username"]}（id={uid}）密码')
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
MQTT_USER = os.environ.get("MQTT_USER", "")
MQTT_PASS = os.environ.get("MQTT_PASS", "")


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

        device = db_query_one("SELECT id FROM device WHERE phone=?", (phone,))
        if not device:
            # 未注册设备上报数据：跳过落库，避免产生 device_id=NULL 的孤儿记录
            log.warning("[MQTT] 未知设备 phone=%s，消息已跳过（请先在设备管理中注册该设备）", phone)
            return
        device_id = device['id']
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # 走批量写队列（与 TCP 808 路径统一），避免在 paho 回调线程里直接持 _db_lock
        loc_row  = (device_id, phone, lat, lng, altitude, speed_raw, direction, 0, 0, None, gps_time)
        dev_state = (phone, lat, lng, speed_raw, gps_time, 1, now)
        enqueue_location(loc_row, dev_state)

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
    """后台 MQTT 订阅线程，broker 不可达时自动重连"""
    import time as _t
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

    while True:
        try:
            client = _mqtt.Client(client_id='tracker-server', clean_session=True)
            client.on_connect = _on_connect
            client.on_message = _mqtt_on_message
            if MQTT_USER:
                client.username_pw_set(MQTT_USER, MQTT_PASS)
            client.connect(MQTT_BROKER, MQTT_PORT_NUM, keepalive=60)
            log.info("[MQTT] 正在连接 broker %s:%d ...", MQTT_BROKER, MQTT_PORT_NUM)
            client.loop_forever()
            log.warning("[MQTT] loop_forever 退出，5 秒后重连")
        except Exception as e:
            log.warning("[MQTT] 连接异常，5 秒后重连: %s", e)
        _t.sleep(5)


# ── 前端静态文件托管（生产 build / 演示用） ────────────────────────────────────
_DIST = os.path.normpath(os.path.join(BASE_DIR, '..', 'frontend', 'dist'))

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def _serve_spa(path):
    # 尝试直接返回静态文件（assets/ 等）
    if path:
        fp = os.path.realpath(os.path.join(_DIST, path.replace('/', os.sep)))
        dist_root = os.path.realpath(_DIST)
        # realpath 检查：防止 ../../../etc/passwd 之类路径穿越
        if not fp.startswith(dist_root + os.sep) and fp != dist_root:
            return fail('非法路径', 400)
        if os.path.isfile(fp):
            return _send_abs(fp)
    # 其余全部返回 index.html（Vue Router 接管）
    return _send_abs(os.path.join(_DIST, 'index.html'))


# ── 主入口 ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    init_db()
    _setup_pg_partitions()      # PG：location_record 转分区表 + 预建未来月份（SQLite 跳过）
    start_partition_maintainer()  # PG：每日预建分区的维护线程

    # 启动位置异步批量落库线程（削减 SQLite 写锁争用）
    start_batch_writer()

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
