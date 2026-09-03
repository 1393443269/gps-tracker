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

# ── 登录失败限流器（进程内、线程安全、内存有界，防撞库）────────────────────────
# 规则：同一 (来源标识, 来源IP) 在 _LOGIN_WINDOW 秒内失败超过 _LOGIN_MAX_FAILS 次，
# 则临时锁定 _LOGIN_WINDOW 秒（滑动窗口，随时间自动解除）。登录成功清零该键。
# 不引入新依赖；用 dict + Lock 保护；每次访问顺带清理过期条目防内存泄漏。
_LOGIN_WINDOW    = 15 * 60   # 时间窗口/锁定时长（秒）
_LOGIN_MAX_FAILS = 10        # 窗口内允许的最大失败次数
_login_fail_map  = {}        # key -> [timestamp, ...]（仅保留窗口内的失败时间戳）
_login_fail_lock = threading.Lock()


def _login_rl_key(scope, ident):
    """构造限流键：来源标识（用户名/账号）+ 来源IP。"""
    ip = (request.remote_addr or '-')
    return f'{scope}|{(ident or "").lower()}|{ip}'


def _login_is_locked(key):
    """检查该键是否已被锁定（窗口内失败次数达到阈值）。顺带清理过期条目。"""
    now = _time_mod.time()
    with _login_fail_lock:
        # 全局清理：移除所有已完全过期的键，保证内存有界
        expired = [k for k, ts in _login_fail_map.items()
                   if not ts or ts[-1] <= now - _LOGIN_WINDOW]
        for k in expired:
            del _login_fail_map[k]
        ts = _login_fail_map.get(key)
        if not ts:
            return False
        # 只保留窗口内的失败时间戳
        ts = [t for t in ts if t > now - _LOGIN_WINDOW]
        if ts:
            _login_fail_map[key] = ts
        else:
            _login_fail_map.pop(key, None)
        return len(ts) >= _LOGIN_MAX_FAILS


def _login_record_fail(key):
    """记录一次失败。"""
    now = _time_mod.time()
    with _login_fail_lock:
        ts = [t for t in _login_fail_map.get(key, []) if t > now - _LOGIN_WINDOW]
        ts.append(now)
        _login_fail_map[key] = ts


def _login_clear(key):
    """登录成功后清零该键。"""
    with _login_fail_lock:
        _login_fail_map.pop(key, None)

# ── 应用初始化 ─────────────────────────────────────────────────────────────────
# app / socketio / CORS 已下沉至 core/extensions.py(中立单例源头),用于打破
# 「后台线程用 socketio ↔ socketio 定义在 app.py」的循环 import。此处 import
# 保持本文件内所有 @app.route / @socketio.on / socketio.emit 用法不变。
from core.extensions import app, socketio

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

# 鉴权与权限(token/scope/密码哈希)已抽至 core/security.py;下面 import 保持调用处不变。
# 整组同模块以保 _org_scope_ids→_current_admin 前向调用链不断。依赖 app 的
# @app.before_request 拦截器与 @app.route 登录接口仍留本文件,从该模块 import 所需函数。
from core.security import (
    _hash_pw, _verify_pw, _require_secret,
    _ADMIN_SECRET, _make_admin_token, _verify_admin_token,
    _PORTAL_SECRET, _make_token, _verify_token, _get_portal_customer,
    _current_admin, _scope_path, _orgs_in_scope, _org_in_scope,
    _org_scope_ids, _org_where,
)

# ── SQLite 数据库 ──────────────────────────────────────────────────────────────

# ── 数据库后端抽象层已抽至 core/db.py（零依赖 app.py 的单向依赖）──────────────
# 通过环境变量 DB_BACKEND 切换：'sqlite'（默认）或 'postgres'。
# 业务代码统一用 ? 占位符；postgres 后端会自动把 ? 转成 %s。
# 下面 import 保持 app.py 内所有原调用处（db_exec/db_query/get_db/_db_lock/
# DB_BACKEND/_pg_dialect/_to_pg 等）无需改动。
from core import db as _dbmod
from core.db import (
    DB_BACKEND, _db_lock, get_db, db_exec, db_query, db_query_one, db_scalar,
    _to_pg, _pg_dialect, _split_sql, _ConnWrapper,
)
# PG 专用符号：仅 postgres 后端存在（批量写线程用 _pg_extras.execute_values）。
# 用 getattr 兜底，sqlite 后端下为 None，与原 app.py「if DB_BACKEND=='postgres'」判断兼容。
_pg_extras   = getattr(_dbmod, '_pg_extras', None)
_get_pg_pool = getattr(_dbmod, '_get_pg_pool', None)
import re as _re


# ── 高频位置写入的异步批量落库（削减 SQLite 全局写锁争用）────────────────────────
# 共享容器(队列/缓存/锁)已抽至 core/state.py;此处 import 保持调用处不变。
# 重业务函数(_get_device_id/enqueue_location/_batch_writer_loop)仍在本文件。
import queue as _queue
from core.state import (
    _loc_queue, _dev_latest, _dev_latest_lk,
    _devid_cache, _DEVID_CACHE_TTL,
    _alarm_last_ts, _alarm_last_ts_lock, _ALARM_DEBOUNCE_SEC,
)

# 批量写线程(_get_device_id/enqueue_location/_batch_writer_loop/start_batch_writer)
# 已抽至 core/ingest.py;下面 import 保持调用处不变。start_batch_writer 供
# gunicorn post_fork 通过 app re-export 调用。
from core.ingest import (
    _get_device_id, enqueue_location, start_batch_writer,
)

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

    -- 围栏↔设备关联表(查询加速层)：与 geo_fence.devices 逗号串并存双写。
    -- devices 字段仍是唯一真相源、UI/兼容查询继续依赖；本表仅用于消除
    -- ingest 每帧热路径 "devices LIKE '%phone%'" 的前导通配全表扫，走
    -- idx_fence_device_phone 等值索引。任何改 devices 处必须同步此表。
    CREATE TABLE IF NOT EXISTS fence_device (
        fence_id INTEGER NOT NULL,
        phone    TEXT    NOT NULL,
        PRIMARY KEY (fence_id, phone)
    );
    CREATE INDEX IF NOT EXISTS idx_fence_device_phone ON fence_device(phone);

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

    -- 待发指令队列：G618G 等低功耗设备平时休眠，下发指令时多半离线。
    -- 点提交时若设备不在线，指令存入本表(status='pending')，设备下次注册上线时自动补发。
    -- 补发成功后 status 置 'sent'；payload_hex 为已构造好的下行帧(十六进制)，
    -- 上线补发时直接发送、无需重新构造，避免依赖 app 层的指令构造函数。
    CREATE TABLE IF NOT EXISTS pending_command (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        phone       TEXT    NOT NULL,
        cmd         TEXT,
        payload_hex TEXT,
        status      TEXT    DEFAULT 'pending',
        created_at  TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime')),
        sent_at     TEXT
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

    # device_role 表：加 customer_id 支持客户自建角色/分组（NULL=管理端全局，否则=归属某客户）。
    # 每客户独立角色：客户只看/管 customer_id=自己的角色，管理端看 customer_id IS NULL 的全局角色。
    try:
        conn.execute("ALTER TABLE device_role ADD COLUMN customer_id INTEGER DEFAULT NULL")
        conn.commit()
    except Exception:
        pass

    # mark_point / risk_point 表：加 customer_id 支持客户自建标注点/风险点（每客户独立，
    # NULL=管理端全局）。客户只看/管 customer_id=自己的点。
    for _pt in ('mark_point', 'risk_point'):
        try:
            conn.execute(f"ALTER TABLE {_pt} ADD COLUMN customer_id INTEGER DEFAULT NULL")
            conn.commit()
        except Exception:
            pass

    # sim_card 表：加 msisdn(SIM卡手机号)、imei(所插设备的IMEI)。
    # device 表：加 imei(设备真实IMEI，从808位置报文0xF6解析)、iccid(从0xF1解析)。
    # 说明：设备注册头里的 phone 可能只是终端ID(非IMEI)，真实IMEI/ICCID 走位置附加字段上报。
    for _col in ("ALTER TABLE sim_card ADD COLUMN msisdn TEXT DEFAULT ''",
                 "ALTER TABLE sim_card ADD COLUMN imei TEXT DEFAULT ''",
                 "ALTER TABLE device ADD COLUMN imei TEXT DEFAULT ''",
                 "ALTER TABLE device ADD COLUMN iccid TEXT DEFAULT ''"):
        try:
            conn.execute(_col)
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

    -- 传感器数据通用表：为以后接入温湿度/气体(环境类)、开关/水位/电量(状态类)等传感器铺路。
    -- 通用结构：数值类走 value+unit，非数值/状态类走 value_text。sensor_type 区分传感器类别。
    CREATE TABLE IF NOT EXISTS sensor_data (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        device_phone TEXT,              -- 关联设备(IMEI/phone)
        sensor_type  TEXT,              -- 传感器类型: temperature/humidity/gas/switch/water_level/battery ...
        value        REAL,             -- 数值(温度/湿度/水位/电量等)
        value_text   TEXT,             -- 非数值类(如开关 on/off、文本状态)
        unit         TEXT,             -- 单位 ℃/%/cm/...
        ts           TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime')),  -- 上报时间
        org_id       INTEGER DEFAULT 1  -- 组织隔离(与平台其它表一致)
    );
    CREATE INDEX IF NOT EXISTS idx_sensor_device_ts ON sensor_data(device_phone, ts);
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
                 "remark TEXT DEFAULT ''",
                 "last_battery INTEGER DEFAULT NULL",       # 最新电量百分比(0-100)，设备心跳带出
                 "last_battery_time TEXT DEFAULT NULL",     # 电量上报时间
                 "low_bat_mode INTEGER DEFAULT 0",          # 0=正常频率, 1=已切低电量频率（防重复下发标志）
                 # ── 在线/离线判定专用字段 ──────────────────────────────────────
                 # last_seen：设备最后一次收到「任何上报」(注册/鉴权/心跳/定位/G618/MQTT)的时间。
                 #   与 last_location_time(仅定位刷新，前端显示"最后定位时间")分离：判在线看通信活性，
                 #   显示看最后定位，两者不再互相打架(修复"发心跳但无定位→被误判离线"的闪烁)。
                 "last_seen TEXT DEFAULT NULL",
                 # expected_interval_sec：该设备的「期望上报间隔」(秒)，离线阈值 = 本值×倍数+冗余。
                 #   建档按型号给默认值；G618G 的 0xE9 状态报文带真实频率时会回写更新。
                 #   NULL/<=0 时离线扫描回退到按型号名的静态阈值。
                 "expected_interval_sec INTEGER DEFAULT NULL",
                 # ── 四态判定(在线/休眠/离线/停用) ────────────────────────────
                 # presence_state：比 status(0/1/2)更细的呈现态，不动 status 以保前端兼容,前端渐进切换。
                 #   取值:'online'在线 / 'sleeping'休眠(G618正常省电,仍算广义在线) /
                 #        'offline'离线失联 / 'disabled'停用(生命周期下线)。
                 "presence_state TEXT DEFAULT 'online'",
                 # offline_reason：置离线时联查电量/充电/指令日志推断的失联原因，供运维定位。
                 #   枚举:'power_off'主动关机 / 'battery_drain'电量耗尽 / 'charge_off'充电关机 /
                 #        'net_lost'疑似断网 / 'migrated'已迁移(下发过改IP) / 'unknown'未知。
                 "offline_reason TEXT DEFAULT NULL",
                 # ── 设备卡片补充字段(实时地图信息卡片用) ────────────────────────
                 # last_signal：最新信号强度(百分比),G618 的 0xF9 心跳带出。
                 "last_signal INTEGER DEFAULT NULL",
                 # last_loc_type：最新定位方式。1基站/2WIFI/3综合/5蓝牙,其余(含0/空)按 GPS。
                 "last_loc_type INTEGER DEFAULT NULL",
                 # last_address：最新定位的逆地理编码中文地址(腾讯 LBS 反查,失败则空)。
                 "last_address TEXT DEFAULT NULL"]:
        try:
            conn.execute(f"ALTER TABLE device ADD COLUMN {_col}")
            conn.commit()
        except Exception:
            pass

    # 默认管理员 admin（已存在则跳过）。初始密码优先取环境变量 INIT_ADMIN_PASSWORD；
    # 未设置则兜底 admin123 以保证开箱可用，但启动时打 WARNING 强烈提示立即修改。
    try:
        _init_admin_pw = os.environ.get('INIT_ADMIN_PASSWORD')
        if not _init_admin_pw:
            _init_admin_pw = 'admin123'
            log.warning('未设置 INIT_ADMIN_PASSWORD，管理员 admin 使用默认弱口令 admin123，'
                        '请立即登录后修改密码或通过环境变量 INIT_ADMIN_PASSWORD 设置强密码！')
        conn.execute("INSERT OR IGNORE INTO admin_user (username, password_hash) VALUES (?,?)",
                     ('admin', _hash_pw(_init_admin_pw)))
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

    # ── fence_device 存量回填 ────────────────────────────────────────────────
    # 把现有 geo_fence.devices 逗号串拆开灌进关联表。INSERT OR IGNORE 幂等：
    # 主键 (fence_id, phone) 去重，重启多次不会产生重复行，也不覆盖已双写数据。
    try:
        _rows = conn.execute("SELECT id, devices FROM geo_fence").fetchall()
        for _r in _rows:
            _fid  = _r['id']
            _devs = _r['devices'] or ''
            for _ph in _devs.split(','):
                _ph = _ph.strip()
                if _ph:
                    conn.execute(
                        "INSERT OR IGNORE INTO fence_device (fence_id, phone) VALUES (?,?)",
                        (_fid, _ph))
        conn.commit()
    except Exception:
        log.exception("fence_device 回填失败(不阻断启动)")

    conn.close()
    log.info("数据库初始化完成: %s", DB_PATH)


# ── PG 专用：location_record 按月分区 ─────────────────────────────────────────
# _setup_pg_partitions/_partition_maintainer_loop/start_partition_maintainer
# 已抽至 core/ingest.py;下面 import 保持调用处不变。_setup_pg_partitions 与
# start_partition_maintainer 供 gunicorn post_fork 通过 app re-export 调用。
# 一并 import 本批其余接入函数:resolve_phone(被 REST zhiling_command 反向调用)、
# _get_alarm_rule / _record_attendance(供 _emit_alarm / check_fence_crossing 调用)。
from core.ingest import (
    _setup_pg_partitions, start_partition_maintainer,
    resolve_phone, _get_alarm_rule, _record_attendance,
)


# ── 会话管理 ───────────────────────────────────────────────────────────────────
# 会话表/流水号/围栏状态字典/清理函数/next_serial 已抽至 core/state.py。
# 这是打破「TCP 线程 ↔ REST 路由」双向耦合的关键:两侧都从 core.state 单向 import。
# 此处 import 保持本文件内所有原调用处不变。
from core.state import (
    sessions, sessions_lock, _serial, _serial_lock,
    fence_device_inside, _fence_lock, FENCE_DEBOUNCE_N,
    fence_device_pending, fence_device_enter_time, fence_device_dwell_alarmed,
    _fence_cleanup, next_serial,
)


# resolve_phone 已抽至 core/ingest.py(见下方 import)。被 REST 路由 zhiling_command
# 反向调用,故 app.py re-export。


# ── 电子围栏：几何判断 ────────────────────────────────────────────────────────
# 纯几何函数已抽至 common/geometry.py（无 Flask/DB/状态依赖);此处 import 保持调用处不变。
from common.geometry import _haversine_m, _point_in_polygon, _is_inside_fence


# check_fence_crossing 已抽至 core/ingest.py(见下方 import)。被 REST 调试端点
# debug_fence_check 及 handle_location 调用,故 app.py re-export。


# ── 808 消息处理函数 ────────────────────────────────────────────────────────────

# ALARM_DEFS 已随 handle_location 一并抽至 core/ingest.py(仅该函数使用)。

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


# _get_alarm_rule 已抽至 core/ingest.py(见文件上部 import)。


# _emit_alarm 已抽至 core/ingest.py(见下方 import);被 handle_location /
# handle_g618g_frame / check_fence_crossing 调用,故 app.py re-export。
# _record_attendance 已抽至 core/ingest.py(见文件上部 import)。

# handle_register / handle_auth / handle_heartbeat / handle_location /
# handle_g618g_frame 均已抽至 core/ingest.py。它们被 TCP handle_client 调用,
# check_fence_crossing 被 debug_fence_check 调用,_sio_emit 被 _mqtt_on_message
# 调用,_customer_ancestors 被 _resolve_branding 调用;下面 re-export 保持调用处不变。
# 打破 socketio 循环依赖:ingest.py 用 from core.extensions import socketio,不 import app。
from core.ingest import (
    _customer_ancestors, _sio_emit, _emit_alarm, check_fence_crossing,
    handle_register, handle_auth, handle_heartbeat, handle_location,
    handle_g618g_frame,
)


# ── TCP 连接处理线程 ────────────────────────────────────────────────────────────

import protocol_g618g as g618
import geo_resolve

# TCP 连接处理线程(handle_client/_handle_client_guarded/start_tcp_server +
# TCP_MAX_CONN/_TCP_CONN_SEM)已抽至 core/ingest.py;下面 import 保持调用处不变。
# start_tcp_server 供 gunicorn post_fork 通过 app re-export 调用。
from core.ingest import handle_client, start_tcp_server, TCP_MAX_CONN


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


def _num_or_none(v, cast=float, lo=None, hi=None):
    """把请求值安全转成数值:转换失败或超出 [lo,hi] 范围返回 None(供接口判定并返回 400),
    避免裸 int()/float() 遇脏输入直接抛未捕获异常导致 500。"""
    if v is None or v == '':
        return None
    try:
        n = cast(v)
    except (ValueError, TypeError):
        return None
    if lo is not None and n < lo:
        return None
    if hi is not None and n > hi:
        return None
    return n

def ok(data=None):
    resp = jsonify({'code': 200, 'msg': 'success', 'data': data})
    resp.headers['Cache-Control'] = 'no-store'
    return resp

def fail(msg, code=500):
    resp = jsonify({'code': code, 'msg': msg})
    resp.headers['Cache-Control'] = 'no-store'
    return resp, code


# ── 设备接口 ──
# _org_scope_ids / _org_where 已随鉴权组抽至 core/security.py(见文件顶部 import)。


@app.get('/api/ping')
def api_ping():
    """健康探针：Docker healthcheck / 负载均衡存活检测用。"""
    return ok({'status': 'ok'})


# ── 在线判定：关机报警驱动 ────────────────────────────────────────────────────
# 设备保持出厂默认(短连接+休眠)最省电。离线判定不再用时间窗口，改为直接读 device.status
# 字段：设备任何上报(注册/心跳/定位)即被写入侧置 status=1(在线)，收到关机报警(0x21)置 0(离线)，
# 报警置 2。写入侧(core/ingest.py)已实时维护该字段，读取侧直接信任它即可。
# 注意：此方案下没电耗尽/失联/死机若无关机报警上报，设备仍显示在线(纯信号驱动的已知取舍)。
ONLINE_WINDOW_MIN = int(os.environ.get('ONLINE_WINDOW_MIN', '25'))  # 兼容保留，当前判定不使用

def _online_since():
    """兼容保留：返回 now-窗口 的时间字符串。关机报警驱动方案下已不用于在线判定。"""
    from datetime import timedelta as _td
    return (datetime.now() - _td(minutes=ONLINE_WINDOW_MIN)).strftime('%Y-%m-%d %H:%M:%S')

def _is_online_row(row):
    """在线判定：直接读 status 字段。status==1 为在线(报警2/离线0 均非在线)。"""
    return bool(row) and row.get('status') == 1


@app.get('/api/devices/summary')
def device_summary():
    sids = _org_scope_ids(request)
    if sids is not None and not sids:
        return ok({'total': 0, 'online': 0, 'offline': 0, 'alarm': 0})
    base_conds, base_params = _org_where(sids)
    def _count(extra_cond=None, extra_params=None):
        conds  = base_conds + ([extra_cond] if extra_cond else [])
        params = base_params + (extra_params or [])
        where  = ("WHERE " + " AND ".join(conds)) if conds else ""
        return db_scalar(f"SELECT COUNT(*) FROM device {where}", params)
    total   = _count()
    # 关机报警驱动：直接读 status 字段。在线=status=1，报警=status=2，其余为离线。
    alarm   = _count("status=2")
    online  = _count("status=1")
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
            _st = int(st)
            # 关机报警驱动：直接按 status 字段过滤(1在线/0离线/2报警)
            if _st == 2:
                conds.append("device.status=2")
            elif _st == 1:
                conds.append("device.status=1")
            elif _st == 0:
                conds.append("device.status=0")
        except ValueError:
            pass
    # org 过滤用带表名的列，避免 JOIN 后 org_id 歧义
    conds, params = _org_where(sids, conds, params, col='device.org_id')

    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    # JOIN 角色表，带出角色颜色/形状供地图与列表按角色渲染
    # JOIN 客户表，带出归属客户名(customer_name)供设备查询详情/列表显示
    base = ("FROM device LEFT JOIN device_role r ON device.role_id = r.id "
            "LEFT JOIN customer c ON device.customer_id = c.id")
    total   = db_scalar(f"SELECT COUNT(*) {base} {where}", params)
    records = db_query(
        f"SELECT device.*, r.name AS role_name, r.color AS role_color, "
        f"r.icon_type AS role_icon, c.name AS customer_name, c.contact AS real_name {base} "
        f"{where} ORDER BY device.updated_at DESC LIMIT ? OFFSET ?",
        params + [size, offset])
    # auth_code 是设备 JT808 鉴权码(内部凭据),与单设备详情接口一致,列表也不得外泄,
    # 否则任意管理员可拉全平台鉴权码离线伪造设备身份。
    for _r in records:
        _r.pop('auth_code', None)
        # 在线状态直接用 status 字段(关机报警驱动)，不再按时间窗口覆盖。
    return ok({'records': records, 'total': total, 'page': page, 'size': size})

@app.post('/api/devices')
def create_device():
    data  = request.get_json() or {}
    phone = (data.get('phone') or '').strip()
    imei  = (data.get('imei') or '').strip()
    if not phone:
        return fail('设备号不能为空')
    if not imei:
        return fail('IMEI 为必填项')   # 手动新增设备时 IMEI 必填
    if db_query_one("SELECT id FROM device WHERE phone=?", (phone,)):
        return fail('设备号已存在')
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    db_exec(
        "INSERT INTO device (phone,name,plate_no,manufacturer,terminal_model,imei,"
        "terminal_id,plate_color,auth_code,status,org_id,lifecycle,remark,created_at,updated_at)"
        " VALUES (?,?,?,?,?,?,?,1,'DEFAULT',0,?,0,?,?,?)",
        (phone, data.get('name',''), data.get('plateNo',''),
         data.get('manufacturer',''), data.get('terminalModel',''), imei,
         data.get('terminalId',''), _admin_org_id(), data.get('remark',''), now, now)
    )
    add_op_log('设备新增', f'手动新增设备 {phone}')
    return ok({'message': '创建成功'})


@app.post('/api/devices/import')
def import_devices():
    """批量导入设备。请求体:{"rows":[{deviceNo,imei,name,plateNo,terminalModel,remark}, ...]}
    规则:设备号(deviceNo)与 IMEI 至少填一个;主键 phone 优先取设备号、没有则用 IMEI;
         库内已存在或本批内重复的跳过;其余建档。兼容旧模板单列 phone(当设备号)。
    返回:{created, skipped, failed, details:[{row,phone,status,reason}]}
    """
    data = request.get_json() or {}
    rows = data.get('rows')
    if not isinstance(rows, list) or not rows:
        return fail('导入数据为空')
    if len(rows) > 5000:
        return fail('单次导入不能超过 5000 条,请分批导入')

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    created = skipped = failed = 0
    details = []
    seen_in_batch = set()   # 本批内已处理的 phone,防止文件内自身重复

    for idx, r in enumerate(rows):
        rownum = idx + 1
        if not isinstance(r, dict):
            failed += 1
            details.append({'row': rownum, 'phone': '', 'status': 'failed', 'reason': '行格式错误'})
            continue
        # 设备号(terminal_id)与 IMEI 二选一即可:主键 phone 优先取设备号,没有则用 IMEI
        dev_no = (str(r.get('deviceNo') or r.get('terminalId') or '')).strip()
        imei   = (str(r.get('imei') or '')).strip()
        # 兼容旧模板单列「IMEI/设备号」→ phone 字段:若没分开填,则拿它当设备号
        if not dev_no and not imei:
            _legacy = (str(r.get('phone') or '')).strip()
            if _legacy:
                dev_no = _legacy
        phone = dev_no or imei   # 唯一标识
        if not phone:
            failed += 1
            details.append({'row': rownum, 'phone': '', 'status': 'failed', 'reason': '设备号和 IMEI 至少填一个'})
            continue
        if phone in seen_in_batch:
            skipped += 1
            details.append({'row': rownum, 'phone': phone, 'status': 'skipped', 'reason': '文件内重复'})
            continue
        seen_in_batch.add(phone)
        if db_query_one("SELECT id FROM device WHERE phone=?", (phone,)):
            skipped += 1
            details.append({'row': rownum, 'phone': phone, 'status': 'skipped', 'reason': '设备号/IMEI 已存在'})
            continue
        try:
            db_exec(
                "INSERT INTO device (phone,name,plate_no,manufacturer,terminal_model,"
                "terminal_id,imei,plate_color,auth_code,status,org_id,lifecycle,remark,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,1,'DEFAULT',0,1,0,?,?,?)",
                (phone, str(r.get('name', '') or ''), str(r.get('plateNo', '') or ''),
                 str(r.get('manufacturer', '') or ''), str(r.get('terminalModel', '') or ''),
                 dev_no, imei, str(r.get('remark', '') or ''), now, now)
            )
            created += 1
            details.append({'row': rownum, 'phone': phone, 'status': 'created', 'reason': ''})
        except Exception as e:
            failed += 1
            details.append({'row': rownum, 'phone': phone, 'status': 'failed', 'reason': str(e)[:120]})

    add_op_log('设备批量导入', f'导入 {created} 台,跳过 {skipped} 台,失败 {failed} 台')
    return ok({'created': created, 'skipped': skipped, 'failed': failed, 'details': details})


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
        "       d.terminal_id, d.imei, "
        # 设备卡片补充:电量/最后通信/信号/定位方式/逆地理地址/呈现态
        "       d.last_battery, d.last_seen, d.last_signal, d.last_loc_type, "
        "       d.last_address, d.presence_state, d.offline_reason, "
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
    # 组织范围校验：非超管的设备与目标客户都必须在权限范围内（防单设备越权）
    sids = _org_scope_ids(request)
    if sids is not None and not sids:
        return fail('无权限', 403)
    if sids is not None:
        scope_ph = ','.join('?' * len(sids))
        dev = db_query_one(
            f"SELECT id, phone FROM device WHERE id=? AND org_id IN ({scope_ph})",
            [did] + list(sids))
        cust = db_query_one(
            f"SELECT id, name FROM customer WHERE id=? AND org_id IN ({scope_ph})",
            [cid] + list(sids))
    else:
        dev  = db_query_one("SELECT id, phone FROM device WHERE id=?", (did,))
        cust = db_query_one("SELECT id, name FROM customer WHERE id=?", (cid,))
    if not dev:
        return fail('设备不存在或无权限', 404)
    if not cust:
        return fail('客户不存在或无权限', 404)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    db_exec("UPDATE device SET customer_id=?,updated_at=? WHERE id=?", (cid, now, did))
    add_op_log('设备绑定', f'设备 {dev["phone"]} 绑定至客户 {cust["name"]}')
    return ok()


@app.post('/api/devices/<int:did>/unbind_customer')
def unbind_device_customer(did):
    """解除设备与客户的绑定"""
    # 组织范围校验：非超管只能解绑权限范围内的设备（防单设备越权）
    sids = _org_scope_ids(request)
    if sids is not None and not sids:
        return fail('无权限', 403)
    if sids is not None:
        scope_ph = ','.join('?' * len(sids))
        dev = db_query_one(
            f"SELECT id, phone, customer_id FROM device WHERE id=? AND org_id IN ({scope_ph})",
            [did] + list(sids))
    else:
        dev = db_query_one("SELECT id, phone, customer_id FROM device WHERE id=?", (did,))
    if not dev:
        return fail('设备不存在或无权限', 404)
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
    # 组织范围校验：非超管只能操作自己权限范围内的设备与目标客户（补齐此前遗漏的越权点）
    sids = _org_scope_ids(request)
    scope_sql = ''
    scope_args = []
    if sids is not None:
        if not sids:
            return fail('无权限', 403)
        scope_ph = ','.join('?' * len(sids))
        scope_sql = f" AND org_id IN ({scope_ph})"
        scope_args = list(sids)
        # 目标客户必须在操作者 scope 内，防止把设备转移到越权客户
        cust = db_query_one(
            f"SELECT id, name FROM customer WHERE id=? AND org_id IN ({scope_ph})",
            [cid] + list(sids))
    else:
        cust = db_query_one("SELECT id, name FROM customer WHERE id=?", (cid,))
    if not cust:
        return fail('客户不存在或无权限', 404)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ph  = ','.join('?' * len(ids))
    db_exec(f"UPDATE device SET customer_id=?,updated_at=? WHERE id IN ({ph}){scope_sql}",
            [cid, now] + list(ids) + scope_args)
    add_op_log('批量绑定', f'{len(ids)} 台设备绑定/转移至客户 {cust["name"]}')
    return ok({'updated': len(ids)})


@app.put('/api/devices/<int:did>/role')
def set_device_role(did):
    """给单台设备设置/清除角色（role_id）。role_id 为空则清除。"""
    data = request.get_json() or {}
    rid  = data.get('role_id')
    # 组织范围校验：非超管只能操作权限范围内的设备（防单设备越权）
    sids = _org_scope_ids(request)
    if sids is not None and not sids:
        return fail('无权限', 403)
    if sids is not None:
        scope_ph = ','.join('?' * len(sids))
        dev = db_query_one(
            f"SELECT id, phone FROM device WHERE id=? AND org_id IN ({scope_ph})",
            [did] + list(sids))
    else:
        dev = db_query_one("SELECT id, phone FROM device WHERE id=?", (did,))
    if not dev:
        return fail('设备不存在或无权限', 404)
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
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ph  = ','.join('?' * len(ids))
    if rid:
        role = db_query_one("SELECT id, name FROM device_role WHERE id=?", (rid,))
        if not role:
            return fail('角色不存在', 404)
        db_exec(f"UPDATE device SET role_id=?,updated_at=? WHERE id IN ({ph}){scope_sql}",
                [rid, now] + list(ids) + scope_args)
        add_op_log('批量分配角色', f'{len(ids)} 台设备分配角色 {role["name"]}')
    else:
        db_exec(f"UPDATE device SET role_id=NULL,updated_at=? WHERE id IN ({ph}){scope_sql}",
                [now] + list(ids) + scope_args)
        add_op_log('批量清除角色', f'{len(ids)} 台设备清除角色')
    return ok({'updated': len(ids)})


@app.post('/api/devices/batch_unbind')
def batch_unbind_devices():
    """批量解绑设备（ids）"""
    data = request.get_json() or {}
    ids  = data.get('ids', [])
    if not ids:
        return fail('ids 不能为空', 400)
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
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ph  = ','.join('?' * len(ids))
    db_exec(f"UPDATE device SET customer_id=NULL,updated_at=? WHERE id IN ({ph}){scope_sql}",
            [now] + list(ids) + scope_args)
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
    sent, offline, denied = 0, 0, 0
    for phone in phones:
        if not _device_in_scope(phone):
            denied += 1                  # 越权设备静默跳过,不下发也不泄露存在性
            continue
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
    return ok({'sent': sent, 'offline': offline, 'denied': denied})


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


def _role_in_scope(rid):
    """角色(device_role)按 org_id 隔离:超管无限制;普通管理员仅本组织范围内的角色可读写。
    返回 True 表示当前管理员有权操作该角色。"""
    sids = _org_scope_ids(request)
    if sids is None:
        return True   # 超管
    if not sids:
        return False
    ph = ','.join('?' * len(sids))
    return bool(db_query_one(
        f"SELECT id FROM device_role WHERE id=? AND org_id IN ({ph})", [rid] + sids))


@app.put('/api/roles/<int:rid>')
def update_role(rid):
    d = request.get_json() or {}
    name = (d.get('name') or '').strip()
    if not name:
        return fail('角色名称不能为空', 400)
    if not _role_in_scope(rid):
        return fail('角色不存在或无权限', 403)
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
    if not _role_in_scope(rid):
        return fail('角色不存在或无权限', 403)
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
    if not _role_in_scope(rid):
        return fail('角色不存在或无权限', 403)
    # 只允许分配当前管理员组织范围内的设备，越权 phone 静默忽略
    sids = _org_scope_ids(request)
    if sids is not None:
        if not sids:
            return fail('无权限', 403)
        dev_ph = ','.join('?' * len(sids))
        allowed = {r['phone'] for r in db_query(
            f"SELECT phone FROM device WHERE org_id IN ({dev_ph})", sids)}
        phones = [p for p in phones if p in allowed]
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
        _st = _num_or_none(status, int)
        if _st is None:
            return fail('status 参数无效', 400)
        conds.append("a.status=?"); params.append(_st)
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
        sel   = "a.*, d.terminal_id, d.imei"
    else:
        base  = "FROM alarm_record a LEFT JOIN device d ON a.phone=d.phone"
        sel   = "a.*, d.terminal_id, d.imei"

    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    total   = db_scalar(f"SELECT COUNT(*) {base} {where}", params)
    records = db_query(f"SELECT {sel} {base} {where} ORDER BY a.alarm_time DESC LIMIT ? OFFSET ?",
                       params + [size, offset])
    return ok({'records': records, 'total': total, 'page': page})

@app.put('/api/alarms/<int:aid>/handle')
def handle_alarm_api(aid):
    data = request.get_json() or {}
    now  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    # 组织范围校验：报警经 device.org_id 隔离，普通管理员只能处理本组织范围内的报警，
    # 防止跨组织把他人报警标记为已处理、掩盖真实告警(与 batch_handle_alarms 一致)。
    sids = _org_scope_ids(request)
    if sids is not None:
        if not sids:
            return fail('报警不存在或无权限', 403)
        org_ph = ','.join('?' * len(sids))
        chk = db_query_one(
            f"SELECT a.id FROM alarm_record a LEFT JOIN device d ON a.phone=d.phone "
            f"WHERE a.id=? AND d.org_id IN ({org_ph})", [aid] + sids)
        if not chk:
            return fail('报警不存在或无权限', 403)
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
    # 先按当前管理员组织范围筛出可处理的报警 id(报警经 device.org_id 隔离),
    # 越权 id 静默忽略,防止跨组织把他人未处理报警标记为已处理、掩盖真实告警。
    sids = _org_scope_ids(request)
    id_ph = ','.join('?' * len(ids))
    if sids is None:
        allowed_ids = list(ids)          # 超管,无限制
    elif not sids:
        return fail('无可处理的报警', 404)
    else:
        org_ph = ','.join('?' * len(sids))
        rows = db_query(
            f"SELECT a.id FROM alarm_record a LEFT JOIN device d ON a.phone=d.phone "
            f"WHERE a.id IN ({id_ph}) AND d.org_id IN ({org_ph})",
            list(ids) + sids)
        allowed_ids = [r['id'] for r in rows]
    if not allowed_ids:
        return fail('无可处理的报警', 404)
    ph  = ','.join('?' * len(allowed_ids))
    db_exec(
        f"UPDATE alarm_record SET status=1, handler=?, handle_note=?, handle_time=? "
        f"WHERE id IN ({ph}) AND status=0",
        [data.get('handler', '管理员'), data.get('note', ''), now] + allowed_ids
    )
    add_op_log('批量处理报警', f'批量处理 {len(allowed_ids)} 条报警')
    return ok({'handled': len(allowed_ids)})


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


def _alarm_rule_in_scope(rid):
    """报警规则(alarm_rule)按 org_id 隔离:超管无限制;普通管理员仅本组织范围内可读写。"""
    sids = _org_scope_ids(request)
    if sids is None:
        return True
    if not sids:
        return False
    ph = ','.join('?' * len(sids))
    return bool(db_query_one(
        f"SELECT id FROM alarm_rule WHERE id=? AND org_id IN ({ph})", [rid] + sids))


@app.put('/api/alarm-rules/<int:rid>')
def update_alarm_rule(rid):
    d = request.get_json() or {}
    row = db_query_one("SELECT alarm_type FROM alarm_rule WHERE id=?", (rid,))
    if not row:
        return fail('规则不存在', 404)
    if not _alarm_rule_in_scope(rid):
        return fail('规则不存在或无权限', 403)
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
    if not _alarm_rule_in_scope(rid):
        return fail('规则不存在或无权限', 403)
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
    # JOIN device 带出终端ID/IMEI(供前端"设备号"列显示 terminal_id||phone)。
    # where 里的列(fence_id/event_time/org_id)无表歧义,直接沿用。
    records = db_query(
        f"SELECT attendance_record.*, d.terminal_id, d.imei FROM attendance_record "
        f"LEFT JOIN device d ON attendance_record.phone = d.phone {where} "
        f"ORDER BY attendance_record.event_time DESC LIMIT ? OFFSET ?",
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
        "SELECT h.*, d.name as device_name, d.terminal_id, d.imei, c.name as account "
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

def _device_in_scope(phone):
    """校验设备 phone 是否在当前管理员的组织可见范围内。
    返回 True=有权限(含超管无限制);False=无权限或设备不存在。
    用于指令下发等按 phone 操作的接口,防止低权限管理员越权控制他人设备。"""
    sids = _org_scope_ids(request)
    if sids is None:
        return True                      # 超管,无限制
    if not sids:
        return False                     # 空范围
    ph = ','.join('?' * len(sids))
    return db_query_one(
        f"SELECT 1 FROM device WHERE phone=? AND org_id IN ({ph})",
        [phone] + sids) is not None


@app.post('/api/commands/text')
def send_text():
    data  = request.get_json() or {}
    phone = data.get('phone', '')
    text  = data.get('text', '')
    if not phone or not text:
        return fail('phone 和 text 不能为空')
    if not _device_in_scope(phone):
        return fail('设备不存在或无权限', 403)
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
    if not _device_in_scope(phone):
        return fail('设备不存在或无权限', 403)
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
    if not _device_in_scope(phone):
        return fail('设备不存在或无权限', 403)
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
    if not _device_in_scope(phone):
        return fail('设备不存在或无权限', 403)
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
    # 先构造下行帧（离线入队也要用，故提前构造并校验参数合法性）
    try:
        payload = builder(data)
    except Exception as e:
        log.warning("[指令下发] 构造失败 phone=%s cmd=%s err=%s", phone, cmd, e)
        return fail(f'指令参数错误: {e}')

    with sessions_lock:
        conn = sessions.get(phone)
    # 设备离线：存入待发队列，下次上线自动补发（G618G 低功耗设备平时休眠，这是常态）
    if not conn:
        db_exec("INSERT INTO pending_command (phone,cmd,payload_hex,status) VALUES (?,?,?,?)",
                (phone, cmd, payload.hex(), 'pending'))
        db_exec("INSERT INTO command_history (phone,device_name,command,result,response) VALUES (?,?,?,?,?)",
                (phone, 'G618G-'+phone[-6:], cmd, 'queued', '设备离线，已加入待发队列，上线后自动下发'))
        add_op_log('G618G指令入队', f'phone={phone} cmd={cmd}（设备离线，待上线补发）')
        return ok({'cmd': cmd, 'phone': phone, 'queued': True,
                   'message': '设备当前离线，指令已排队，设备下次上线时自动下发'})
    try:
        # G618G 短连接设备需在下行窗口连续发两次（间隔 <20ms）
        conn.sendall(payload)
        import time as _t; _t.sleep(0.01)
        conn.sendall(payload)
        # 记录指令历史
        db_exec("INSERT INTO command_history (phone,device_name,command,result,response) VALUES (?,?,?,?,?)",
                (phone, 'G618G-'+phone[-6:], cmd, 'success', ''))
        add_op_log('G618G指令下发', f'phone={phone} cmd={cmd}')
        return ok({'cmd': cmd, 'phone': phone, 'queued': False})
    except Exception as e:
        # 在线发送失败（连接刚断等）：转入待发队列，避免指令丢失
        db_exec("INSERT INTO pending_command (phone,cmd,payload_hex,status) VALUES (?,?,?,?)",
                (phone, cmd, payload.hex(), 'pending'))
        db_exec("INSERT INTO command_history (phone,device_name,command,result,response) VALUES (?,?,?,?,?)",
                (phone, 'G618G-'+phone[-6:], cmd, 'queued', f'实时下发失败已转待发队列: {e}'))
        log.warning("[指令下发] 实时失败转入队 phone=%s err=%s", phone, e)
        return ok({'cmd': cmd, 'phone': phone, 'queued': True,
                   'message': '实时下发失败，已转入待发队列，设备下次上线时自动下发'})


# ── 天禧(智令 *XXX#)下行指令接口 ───────────────────────────────────────────────
# 天禧协议规定：参数设置/查询/控制统一走「0x8300 类型0x01 下发命令字符串」，
# 不使用标准 808 的 0x8103/0x8105/0x8202。命令字符串按协议用 GBK 编码。

def build_zhiling_frame(phone, cmd_str):
    """构造天禧 0x8300(类型0x01, GBK) 完整下行帧字节，供直发或离线入队复用。"""
    body = bytes([0x01]) + cmd_str.encode('gbk', errors='replace')
    return p.encode_message(0x8300, phone, next_serial(), body)

def send_zhiling_cmd(conn, phone, cmd_str):
    """把智令命令字符串 *XXX# 经 0x8300(类型0x01, GBK) 下发给天禧设备。"""
    conn.sendall(build_zhiling_frame(phone, cmd_str))
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
    if not _device_in_scope(phone):
        return fail('设备不存在或无权限', 403)
    spec = zl.AVAILABLE_COMMANDS.get(cmd)
    if not spec:
        return fail(f'不支持的天禧指令: {cmd}，支持: {", ".join(zl.AVAILABLE_COMMANDS.keys())}')
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
    # 预构造下行帧（在线直发 / 离线入队都用它，故提前构造）
    try:
        frame = build_zhiling_frame(phone, cmd_str)
    except Exception as e:
        return fail(f'构造指令帧失败: {e}')
    with sessions_lock:
        conn = sessions.get(phone)
    # 设备离线：入待发队列，下次上线自动补发。
    # 天禧 LT115 是「短连接」——报完即断、在线窗口极短，直发极易发给已断开的连接而丢失，
    # 故一律入队、由注册/鉴权成功时的补发逻辑在连接活跃窗口内送达（QS 验证：直发收不到回复）。
    if not conn:
        db_exec("INSERT INTO pending_command (phone,cmd,payload_hex,status) VALUES (?,?,?,?)",
                (phone, cmd, frame.hex(), 'pending'))
        db_exec("INSERT INTO command_history (phone,device_name,command,result,response) VALUES (?,?,?,?,?)",
                (phone, resolve_phone(phone), f'{cmd} {cmd_str}', 'queued', '设备离线，已入待发队列，上线后自动下发'))
        add_op_log('天禧指令入队', f'phone={phone} cmd={cmd} str={cmd_str}（设备离线，待上线补发）')
        return ok({'cmd': cmd, 'phone': phone, 'cmd_str': cmd_str, 'queued': True,
                   'message': '设备当前离线，指令已排队，设备下次上线时自动下发'})
    try:
        conn.sendall(frame)
        db_exec("INSERT INTO command_history (phone,device_name,command,result,response) VALUES (?,?,?,?,?)",
                (phone, resolve_phone(phone), f'{cmd} {cmd_str}', 'success', ''))
        add_op_log('天禧指令下发', f'phone={phone} cmd={cmd} str={cmd_str}')
        return ok({'cmd': cmd, 'phone': phone, 'cmd_str': cmd_str, 'queued': False})
    except Exception as e:
        # 直发失败（连接刚断）：转入待发队列，避免丢失
        db_exec("INSERT INTO pending_command (phone,cmd,payload_hex,status) VALUES (?,?,?,?)",
                (phone, cmd, frame.hex(), 'pending'))
        db_exec("INSERT INTO command_history (phone,device_name,command,result,response) VALUES (?,?,?,?,?)",
                (phone, resolve_phone(phone), f'{cmd} {cmd_str}', 'queued', f'实时下发失败已转待发队列: {e}'))
        log.warning("[指令下发] 天禧实时失败转入队 phone=%s err=%s", phone, e)
        return ok({'cmd': cmd, 'phone': phone, 'cmd_str': cmd_str, 'queued': True,
                   'message': '实时下发失败，已转入待发队列，设备下次上线时自动下发'})


# ── 蓝牙信标位置对照表管理（major/minor → 坐标）────────────────────────────────

@app.get('/api/beacons')
def list_beacons():
    """信标对照表列表。"""
    conds, params = [], []
    conds, params = _org_scope_conds(conds, params)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    rows = db_query(f"SELECT * FROM beacon_location {where} ORDER BY major, minor", params)
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
    _org = _admin_org_id()
    exist = db_query_one("SELECT id FROM beacon_location WHERE major=? AND minor=? AND org_id=?", (major, minor, _org))
    if exist:
        db_exec("UPDATE beacon_location SET name=?, lat=?, lng=?, "
                "updated_at=strftime('%Y-%m-%d %H:%M:%S','now','localtime') WHERE major=? AND minor=? AND org_id=?",
                (name, lat, lng, major, minor, _org))
        add_op_log('信标更新', f'major={major} minor={minor} lat={lat} lng={lng}')
    else:
        db_exec("INSERT INTO beacon_location (major, minor, name, lat, lng, org_id) VALUES (?,?,?,?,?,?)",
                (major, minor, name, lat, lng, _org))
        add_op_log('信标新增', f'major={major} minor={minor}')
    return ok()

@app.delete('/api/beacons/<int:bid>')
def delete_beacon(bid):
    if not _row_org_ok('beacon_location', bid):
        return fail('无权操作该信标')
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


def _admin_org_id():
    """当前管理员所属组织 id(用于新建资源的 org 归属)。取不到默认 1。"""
    admin = _current_admin(request)
    return (admin.get('org_id') or 1) if admin else 1


def _org_scope_conds(conds, params, col='org_id'):
    """给已有 conds/params 追加组织范围过滤(通用资源表用)。
    超管(sids=None)不加;空范围加 1=0;否则 col IN (...)。返回 (conds, params)。"""
    sids = _org_scope_ids(request)
    if sids is None:
        return conds, params
    if not sids:
        conds.append("1=0"); return conds, params
    ph = ','.join('?' * len(sids))
    conds.append(f"{col} IN ({ph})"); params.extend(sids)
    return conds, params


def _row_org_ok(table, rid):
    """校验某表某行是否在当前管理员组织范围内(update/delete 前调用)。"""
    sids = _org_scope_ids(request)
    if sids is None:
        return True
    if not sids:
        return False
    ph = ','.join('?' * len(sids))
    return bool(db_query_one(
        f"SELECT 1 FROM {table} WHERE id=? AND org_id IN ({ph})", [rid] + sids))


@app.get('/api/sims')
def list_sims():
    page, size = _page_params(20)
    kw      = request.args.get('keyword', '').strip()
    status  = request.args.get('status', '').strip()
    expiring = request.args.get('expiring', '').strip()   # '7' / '30' = 近N天到期
    offset  = (page - 1) * size
    conds, params = [], []
    conds, params = _org_scope_conds(conds, params)   # 组织隔离
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
    # JOIN device 带出绑定设备的终端ID(设备号显示优先用它)和真实IMEI(兜底 sim_card.imei 为空)
    records = db_query(
        f"SELECT sim_card.*, d.terminal_id AS dev_terminal_id, d.imei AS dev_imei, d.name AS dev_name "
        f"FROM sim_card LEFT JOIN device d ON sim_card.device_phone = d.phone "
        f"{where} ORDER BY expire_date ASC, created_at DESC LIMIT ? OFFSET ?",
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
            "INSERT INTO sim_card (iccid,imsi,msisdn,imei,operator,plan,balance,status,device_phone,remark,expire_date,monthly_fee,org_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (iccid, d.get('imsi',''), d.get('msisdn',''), d.get('imei',''),
             d.get('operator','中国移动'), d.get('plan',''),
             float(d.get('balance', 0)), d.get('status','正常'),
             d.get('device_phone',''), d.get('remark',''),
             d.get('expire_date') or None, float(d.get('monthly_fee', 0)), _admin_org_id())
        )
        add_op_log('SIM新增', f'新增SIM卡 {iccid}')
    except Exception as e:
        return fail(f'ICCID 已存在或参数错误: {e}', 400)
    return ok()


@app.put('/api/sims/<int:sid>')
def update_sim(sid):
    if not _row_org_ok('sim_card', sid):
        return fail('SIM卡不存在或无权限', 403)
    d = request.get_json() or {}
    db_exec(
        "UPDATE sim_card SET imsi=?,msisdn=?,imei=?,operator=?,plan=?,balance=?,status=?,device_phone=?,remark=?,expire_date=?,monthly_fee=? WHERE id=?",
        (d.get('imsi',''), d.get('msisdn',''), d.get('imei',''),
         d.get('operator','中国移动'), d.get('plan',''),
         float(d.get('balance', 0)), d.get('status','正常'),
         d.get('device_phone',''), d.get('remark',''),
         d.get('expire_date') or None, float(d.get('monthly_fee', 0)), sid)
    )
    add_op_log('SIM编辑', f'编辑SIM卡 id={sid}')
    return ok()


@app.delete('/api/sims/<int:sid>')
def delete_sim(sid):
    if not _row_org_ok('sim_card', sid):
        return fail('SIM卡不存在或无权限', 403)
    row = db_query_one("SELECT iccid FROM sim_card WHERE id=?", (sid,))
    if not row: return fail('SIM卡不存在', 404)
    db_exec("DELETE FROM sim_card WHERE id=?", (sid,))
    add_op_log('SIM删除', f'删除SIM卡 {row["iccid"]}')
    return ok()

@app.post('/api/sims/<int:sid>/bind')
def bind_sim(sid):
    if not _row_org_ok('sim_card', sid):
        return fail('SIM卡不存在或无权限', 403)
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
    conds, params = [], []
    if sim_id:
        conds.append("sim_id=?"); params.append(sim_id)
    conds, params = _org_scope_conds(conds, params)
    where = "WHERE " + " AND ".join(conds) if conds else ""
    total   = db_scalar(f"SELECT COUNT(*) FROM recharge {where}", params)
    records = db_query(f"SELECT * FROM recharge {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                       params + [size, offset])
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
                "INSERT INTO recharge (sim_id,iccid,amount,method,plan,remark,operator,org_id) VALUES (?,?,?,?,?,?,?,?)",
                (sim_id, row['iccid'], amount, d.get('method','支付宝'),
                 d.get('plan',''), d.get('remark',''), d.get('operator','管理员'), _admin_org_id()))
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
    # 危险类型(svg/html/xml 等可被浏览器执行脚本)强制以附件下载，防 XSS；
    # 图片(png/jpg/gif/webp/ico)保持 inline 以便页面/Logo 直接显示。
    # 注意：只由后端设 Content-Disposition，nginx 不再重复加，避免响应头重复致浏览器拒绝加载。
    _danger_exts = {'.svg', '.html', '.htm', '.xml', '.xhtml', '.js'}
    as_attach = ext in _danger_exts
    return _send_abs(full_path, as_attachment=as_attach)


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

def _sync_fence_devices(fence_id, devices_str, conn=None):
    """把某围栏的关联设备(fence_device 表)与其 devices 逗号串全量对齐：
    先删该围栏所有关联行，再按新 devices 逐条 INSERT OR IGNORE。
    devices 字段仍是真相源，本表是查询加速层——凡改 geo_fence.devices 处均须调用此函数。

    conn 为 None 时走 db_exec(自带 _db_lock，用于未持锁的调用点)；
    传入 conn 时直接用该连接(调用方已持 _db_lock 并自行 commit，避免 SQLite 重入死锁)。
    删除围栏(devices_str 传空串/None)时只做清理，等价于清空关联。
    """
    phones = [p.strip() for p in (devices_str or '').split(',') if p.strip()]
    if conn is not None:
        conn.execute("DELETE FROM fence_device WHERE fence_id=?", (fence_id,))
        for ph in phones:
            conn.execute(
                "INSERT OR IGNORE INTO fence_device (fence_id, phone) VALUES (?,?)",
                (fence_id, ph))
    else:
        db_exec("DELETE FROM fence_device WHERE fence_id=?", (fence_id,))
        for ph in phones:
            db_exec(
                "INSERT OR IGNORE INTO fence_device (fence_id, phone) VALUES (?,?)",
                (fence_id, ph))


@app.get('/api/fences')
def list_fences():
    name    = request.args.get('name', '').strip()
    ftype   = request.args.get('fence_type', '').strip()
    cust_id = request.args.get('customer_id', '').strip()
    sids    = _org_scope_ids(request)
    args    = []
    # 默认只看全局围栏(customer_id IS NULL)。管理员传 customer_id 时改为查看该账号
    # 及其所有下级子账号私建的围栏(账号围栏查看)。
    if cust_id:
        try:
            cids = _customer_and_descendants(int(cust_id))
            ph   = ','.join('?' * len(cids))
            conds = [f"customer_id IN ({ph})"]
            args += cids
        except ValueError:
            conds = ["customer_id IS NULL"]
    else:
        conds = ["customer_id IS NULL"]   # 管理员接口默认只看全局围栏（非客户私建的）
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
    adwl  = _num_or_none(d.get('alarm_dwell', 0), int, lo=0)
    spdl  = _num_or_none(d.get('speed_limit', 0), int, lo=0)
    if adwl is None or spdl is None:
        return fail('alarm_dwell/speed_limit 需为非负整数', 400)
    vs    = str(d.get('valid_start',  '') or '')
    ve    = str(d.get('valid_end',    '') or '')

    EXTRA_COLS = "alarm_enter,alarm_exit,alarm_dwell,speed_limit,valid_start,valid_end,org_id"
    EXTRA_VALS = (ae, ax, adwl, spdl, vs, ve, admin_org)

    devices_str = d.get('devices', '') or ''
    if fence_type == 'circle':
        # 坐标/半径范围校验:非法值会使围栏永远命中不了(静默失效,进出告警不触发),
        # 必须拦在入库前而非存进去。
        lat = _num_or_none(d.get('lat'), float, lo=-90,  hi=90)
        lng = _num_or_none(d.get('lng'), float, lo=-180, hi=180)
        radius = _num_or_none(d.get('radius', 2000), int, lo=1)
        if lat is None or lng is None:
            return fail('圆形围栏需要合法的 lat(-90~90)/lng(-180~180)', 400)
        if radius is None:
            return fail('radius 需为正整数', 400)
        ins_sql = f"INSERT INTO geo_fence (name,fence_type,lat,lng,radius,color,devices,{EXTRA_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        ins_params = (name, 'circle', lat, lng,
                      radius, d.get('color', '#409EFF'), devices_str) + EXTRA_VALS
    elif fence_type == 'polygon':
        coords = d.get('coordinates')
        if not coords:
            return fail('多边形围栏需要 coordinates', 400)
        import json as _json
        ins_sql = f"INSERT INTO geo_fence (name,fence_type,lat,lng,coordinates,color,devices,{EXTRA_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        ins_params = (name, 'polygon', 0.0, 0.0, _json.dumps(coords), d.get('color', '#409EFF'), devices_str) + EXTRA_VALS
    elif fence_type == 'administrative':
        if not d.get('adcode'):
            return fail('行政区围栏需要 adcode', 400)
        import json as _json
        ins_sql = f"INSERT INTO geo_fence (name,fence_type,lat,lng,adcode,coordinates,color,devices,{EXTRA_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        ins_params = (name, 'administrative', 0.0, 0.0, str(d['adcode']),
                      _json.dumps(d.get('coordinates', [])),
                      d.get('color', '#409EFF'), devices_str) + EXTRA_VALS
    else:
        return fail('未知围栏类型', 400)

    # 单连接内插入围栏并同步 fence_device：拿到 lastrowid 后在同一 conn(同一锁)双写，
    # 保证 devices 串与关联表原子对齐，不会出现只写其一的中间态。
    with _db_lock:
        conn = get_db()
        try:
            new_id = conn.execute(ins_sql, ins_params).lastrowid
            _sync_fence_devices(new_id, devices_str, conn=conn)
            conn.commit()
        finally:
            conn.close()
    add_op_log('围栏新增', f'新建{fence_type}围栏 {name}')
    return ok({'id': new_id})

def _admin_fence_or_none(fid):
    """取管理员有权操作的围栏(本组织范围内的全局围栏 customer_id IS NULL),
    与 list_fences 的可见范围一致。无权/不存在返回 None,防止跨组织或删客户私有围栏。"""
    sids = _org_scope_ids(request)
    conds, params = _org_where(sids, ["id=?"], [fid])
    where = " AND ".join(conds)
    return db_query_one(
        f"SELECT name FROM geo_fence WHERE {where} AND customer_id IS NULL", params)


@app.delete('/api/fences/<int:fid>')
def delete_fence(fid):
    row = _admin_fence_or_none(fid)
    if not row: return fail('围栏不存在或无权限', 404)
    db_exec("DELETE FROM geo_fence WHERE id=?", (fid,))
    _sync_fence_devices(fid, '')   # 双写：清理该围栏在加速表中的关联
    add_op_log('围栏删除', f'删除围栏 {row["name"]}')
    return ok()

@app.route('/api/fences/<int:fid>/devices', methods=['PUT'])
def update_fence_devices(fid):
    """更新围栏关联的设备（手机号列表）"""
    row = _admin_fence_or_none(fid)
    if not row: return fail('围栏不存在或无权限', 404)
    d = request.get_json() or {}
    phones = d.get('phones', [])            # 传入手机号数组
    devices_str = ','.join(str(p) for p in phones if p)
    db_exec("UPDATE geo_fence SET devices=? WHERE id=?", (devices_str, fid))
    _sync_fence_devices(fid, devices_str)   # 双写：全量替换该围栏的关联设备
    add_op_log('围栏关联设备', f'围栏 {row["name"]} 关联 {len(phones)} 台设备')
    return ok()

@app.post('/api/fences/batch_delete')
def batch_delete_fences():
    d   = request.get_json() or {}
    ids = d.get('ids', [])
    if not ids:
        return fail('ids 不能为空', 400)
    # 仅删除当前管理员可见范围内的全局围栏,越权 id 静默忽略(不误删他人/客户围栏)
    sids = _org_scope_ids(request)
    id_ph = ','.join('?' * len(ids))
    conds, params = _org_where(sids, [f"id IN ({id_ph})"], list(ids))
    where = " AND ".join(conds)
    allowed = db_query(
        f"SELECT id FROM geo_fence WHERE {where} AND customer_id IS NULL", params)
    allowed_ids = [r['id'] for r in allowed]
    if not allowed_ids:
        return fail('无可删除的围栏', 404)
    del_ph = ','.join('?' * len(allowed_ids))
    db_exec(f"DELETE FROM geo_fence WHERE id IN ({del_ph})", tuple(allowed_ids))
    # 双写：清理这些围栏在加速表中的关联
    db_exec(f"DELETE FROM fence_device WHERE fence_id IN ({del_ph})", tuple(allowed_ids))
    add_op_log('围栏批量删除', f'批量删除 {len(allowed_ids)} 条围栏')
    return ok({'deleted': len(allowed_ids)})

# ── 标注点 ────────────────────────────────────────────────────────────────────
@app.get('/api/mark_points')
def list_mark_points():
    name = request.args.get('name', '').strip()
    conds, args = ["1=1"], []
    if name:
        conds.append("name LIKE ?"); args.append(f'%{name}%')
    conds, args = _org_scope_conds(conds, args)
    sql = "SELECT * FROM mark_point WHERE " + " AND ".join(conds) + " ORDER BY created_at DESC"
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
        "INSERT INTO mark_point (name,lat,lng,remark,org_id) VALUES (?,?,?,?,?)",
        (name, lat, lng, d.get('remark', ''), _admin_org_id())
    )
    return ok()

@app.delete('/api/mark_points/<int:mid>')
def delete_mark_point(mid):
    if not _row_org_ok('mark_point', mid):
        return fail('无权限', 403)
    db_exec("DELETE FROM mark_point WHERE id=?", (mid,))
    return ok()

# ── 共享风险点 ────────────────────────────────────────────────────────────────
@app.get('/api/risk_points')
def list_risk_points():
    conds, params = _org_scope_conds(["1=1"], [])
    sql = "SELECT * FROM risk_point WHERE " + " AND ".join(conds) + " ORDER BY created_at DESC"
    return ok(db_query(sql, tuple(params)))

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
        "INSERT INTO risk_point (name,lat,lng,level,remark,org_id) VALUES (?,?,?,?,?,?)",
        (name, lat, lng, d.get('level', 'medium'), d.get('remark', ''), _admin_org_id())
    )
    return ok()

@app.delete('/api/risk_points/<int:rid>')
def delete_risk_point(rid):
    if not _row_org_ok('risk_point', rid):
        return fail('无权限', 403)
    db_exec("DELETE FROM risk_point WHERE id=?", (rid,))
    return ok()


# ── 指令历史接口 ───────────────────────────────────────────────────────────────

@app.get('/api/command-history')
def list_command_history():
    page, size = _page_params(20)
    phone  = request.args.get('phone', '').strip()
    offset = (page - 1) * size
    conds, params = [], []
    if phone:
        conds.append("phone=?"); params.append(phone)
    # 按当前管理员组织范围过滤
    conds, params = _org_scope_conds(conds, params, col='org_id')
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    total   = db_scalar(f"SELECT COUNT(*) FROM command_history {where}", params)
    records = db_query(f"SELECT * FROM command_history {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                       params + [size, offset])
    return ok({'records': records, 'total': total, 'page': page})

@app.post('/api/command-history')
def create_command_history():
    d = request.get_json() or {}
    db_exec("INSERT INTO command_history (phone,device_name,command,result,response,org_id) VALUES (?,?,?,?,?,?)",
            (d.get('phone',''), d.get('device_name',''), d.get('command',''),
             d.get('result',''), d.get('response',''), _admin_org_id()))
    return ok()


# ── 传感器数据接口(通用框架)────────────────────────────────────────────────────
# 为以后接入环境类(温湿度/气体)、状态类(开关/水位/电量)传感器铺路，现只搭骨架。
# 控制类(执行器下发指令)复用现有 pending_command + /api/commands/g618g + _flush_pending_commands。

@app.post('/api/sensor-data')
def create_sensor_data():
    """上报一条传感器数据(程序/设备上报)。org_id 用当前管理员组织。"""
    d = request.get_json() or {}
    db_exec("INSERT INTO sensor_data (device_phone,sensor_type,value,value_text,unit,org_id) "
            "VALUES (?,?,?,?,?,?)",
            (d.get('device_phone',''), d.get('sensor_type',''), d.get('value'),
             d.get('value_text',''), d.get('unit',''), _admin_org_id()))
    return ok()

@app.get('/api/sensor-data')
def list_sensor_data():
    """传感器数据列表查询，支持 device_phone/sensor_type 过滤 + 分页，按 ts DESC。"""
    page, size  = _page_params(20)
    device_phone = request.args.get('device_phone', '').strip()
    sensor_type  = request.args.get('sensor_type', '').strip()
    offset = (page - 1) * size
    conds, params = [], []
    if device_phone:
        conds.append("device_phone=?"); params.append(device_phone)
    if sensor_type:
        conds.append("sensor_type=?"); params.append(sensor_type)
    # 按当前管理员组织范围过滤(组织隔离)
    conds, params = _org_scope_conds(conds, params, col='org_id')
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    total   = db_scalar(f"SELECT COUNT(*) FROM sensor_data {where}", params)
    records = db_query(f"SELECT * FROM sensor_data {where} ORDER BY ts DESC LIMIT ? OFFSET ?",
                       params + [size, offset])
    return ok({'records': records, 'total': total, 'page': page})

@app.get('/api/sensor-data/latest')
def latest_sensor_data():
    """查某设备各类传感器的最新值：返回该设备每种 sensor_type 的最新一条。"""
    device_phone = request.args.get('device_phone', '').strip()
    if not device_phone:
        return fail('device_phone 不能为空', 400)
    conds, params = ["device_phone=?"], [device_phone]
    # 组织隔离
    conds, params = _org_scope_conds(conds, params, col='org_id')
    where = "WHERE " + " AND ".join(conds)
    # 子查询取每种 sensor_type 的最大 id(id 自增，等价于最新一条，避免 ts 相同时歧义)
    records = db_query(
        f"SELECT * FROM sensor_data {where} AND id IN ("
        f"  SELECT MAX(id) FROM sensor_data {where} GROUP BY sensor_type"
        f") ORDER BY sensor_type ASC",
        params + params)
    return ok({'records': records})


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

# _customer_ancestors / _sio_emit 已抽至 core/ingest.py(见文件上部 import)。
# _customer_ancestors 被 REST _resolve_branding 调用、_sio_emit 被 _mqtt_on_message
# 调用,故 app.py re-export(见 core.ingest import 块)。


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
# _require_secret/_ADMIN_SECRET/_make_admin_token/_verify_admin_token 已抽至
# core/security.py(见文件顶部 import)。下面拦截器与登录接口依赖 app,保留在此。

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
    # 放行：健康探针（Docker healthcheck / 负载均衡存活检测），仅返回 {status:ok}，无敏感数据
    if path == '/api/ping':
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
    admin_id = _verify_admin_token(token)
    if not token or not admin_id:
        return fail('未授权，请先登录', 401)
    # 账号级功能权限：非超管账号，访问受菜单保护的接口前缀时，需拥有对应菜单权限。
    # 未配置权限的账号视为拥有全部(向后兼容),不拦截。放行 GET 读接口只拦写操作,
    # 避免误伤跨页面公共读取——仅对明确归属某菜单的写接口(POST/PUT/DELETE)做拦截。
    if request.method in ('POST', 'PUT', 'DELETE', 'PATCH'):
        try:
            me = db_query_one("SELECT username, user_type FROM admin_user WHERE id=?", (admin_id,))
            if me and not _admin_is_super(me):
                allowed = _get_account_menu_keys(admin_id)
                if allowed is not None:            # None=未配置=全部放行
                    allowed_set = set(allowed)
                    for _menu, _prefixes in _MENU_API_PREFIX.items():
                        if _menu in allowed_set:
                            continue               # 有该菜单权限,不拦
                        for _pfx in _prefixes:
                            if path == _pfx or path.startswith(_pfx + '/'):
                                return fail('无该功能的操作权限', 403)
        except Exception as _e:
            log.warning("[账号权限] 接口校验异常(放行): %s", _e)
    return None


@app.post('/api/auth/login')
def admin_login():
    d        = request.get_json() or {}
    username = (d.get('username') or '').strip()
    password = d.get('password', '')
    if not username or not password:
        return fail('账号和密码不能为空', 400)
    _rl_key = _login_rl_key('admin', username)
    if _login_is_locked(_rl_key):
        return fail('登录失败次数过多，请稍后再试', 429)
    row = db_query_one(
        "SELECT id, username, real_name, org_id, org_level, user_type, password_hash "
        "FROM admin_user WHERE username=? AND COALESCE(is_active,1)=1",
        (username,)
    )
    if not row or not _verify_pw(password, row.get('password_hash') or ''):
        _login_record_fail(_rl_key)
        return fail('账号或密码错误', 401)
    _login_clear(_rl_key)
    # 如果是旧 SHA-256 哈希，自动升级为 bcrypt
    if not (row['password_hash'].startswith('$2b$') or row['password_hash'].startswith('$2a$')):
        db_exec("UPDATE admin_user SET password_hash=? WHERE username=?",
                (_hash_pw(password), row['username']))
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    db_exec("UPDATE admin_user SET last_login=? WHERE id=?", (now, row['id']))
    token = _make_admin_token(row['id'])
    add_op_log('管理员登录', f'{row["username"]} 登录')
    # 下发该账号的可见菜单权限：超管或未配置=全部菜单；否则=已配置的菜单 keys
    _is_super = _admin_is_super(row)
    if _is_super:
        _menus = _ALL_MENU_KEYS
    else:
        _mk = _get_account_menu_keys(row['id'])
        _menus = _ALL_MENU_KEYS if _mk is None else _mk
    return ok({
        'token':    token,
        'userId':   row['id'],
        'username': row['username'],
        'realName': row.get('real_name'),
        'orgId':    row.get('org_id') or 1,
        'orgLevel': row.get('org_level') or 1,
        'userType': row.get('user_type') or 9,
        'isSuper':  _is_super,
        'menuKeys': _menus,
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
# _PORTAL_SECRET/_make_token/_verify_token/_get_portal_customer 已抽至
# core/security.py(见文件顶部 import)。


# ── 客户门户：API ──────────────────────────────────────────────────────────────

@app.post('/api/customer/login')
def portal_login():
    d          = request.get_json() or {}
    login_name = (d.get('login_name') or '').strip()
    password   = d.get('password', '')
    if not login_name or not password:
        return fail('账号和密码不能为空', 400)
    _rl_key = _login_rl_key('portal', login_name)
    if _login_is_locked(_rl_key):
        return fail('登录失败次数过多，请稍后再试', 429)
    row = db_query_one(
        "SELECT id, name, login_name, password_hash FROM customer WHERE login_name=? AND status='活跃'",
        (login_name,)
    )
    if not row or not _verify_pw(password, row.get('password_hash') or ''):
        _login_record_fail(_rl_key)
        return fail('账号或密码错误', 401)
    _login_clear(_rl_key)
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
        f"SELECT d.phone, d.name, d.last_lat, d.last_lng, d.last_speed, "
        f"d.last_location_time, d.status, d.last_battery, d.last_battery_time, "
        f"d.terminal_id, d.imei, d.terminal_model, c.name AS customer_name "
        f"FROM device d LEFT JOIN customer c ON d.customer_id = c.id "
        f"WHERE d.customer_id IN ({cid_ph})",
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
    # status 过滤：与管理端 list_alarms 一致(0未处理/1已处理)。之前漏过滤导致客户端红点
    # 恒等于该客户全部报警数——管理员处理后 status 已置 1，客户端仍把已处理的计入"未处理"。
    # 列名统一带 alarm_record. 前缀：records 查询 JOIN device 后 phone/status 两列都会歧义
    conds  = [f"alarm_record.phone IN ({ph})"]
    params = list(phones)
    status = request.args.get('status')
    if status is not None and status != '':
        _st = _num_or_none(status, int)
        if _st is None:
            return fail('status 参数无效', 400)
        conds.append("alarm_record.status=?"); params.append(_st)
    where  = "WHERE " + " AND ".join(conds)
    total   = db_scalar(f"SELECT COUNT(*) FROM alarm_record {where}", params)
    # JOIN device 带出终端ID/IMEI(供前端"设备号"列显示 terminal_id||phone、IMEI列显示真实imei)
    records = db_query(
        f"SELECT alarm_record.*, d.terminal_id, d.imei FROM alarm_record "
        f"LEFT JOIN device d ON alarm_record.phone = d.phone {where} "
        f"ORDER BY alarm_record.alarm_time DESC LIMIT ? OFFSET ?",
        params + [size, offset])
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
        "SELECT h.*, d.name as device_name, d.terminal_id, d.imei, c.name as account "
        + base + where +
        " ORDER BY h.record_time DESC LIMIT ? OFFSET ?",
        params + [size, offset])
    return ok({'records': records, 'total': total, 'page': page})


def _portal_device_owned(cid, phone):
    """校验 phone 设备是否属于当前客户(含下级子账号)。返回 device 行或 None。"""
    all_cids = _get_all_descendant_cids(cid)
    cid_ph   = ','.join('?' * len(all_cids))
    return db_query_one(
        f"SELECT id, name FROM device WHERE phone=? AND customer_id IN ({cid_ph})",
        [phone] + all_cids)


@app.post('/api/customer/commands/zhiling')
def portal_zhiling_command():
    """客户向自己名下的天禧设备下发智令指令。逻辑同管理端 zhiling_command,
    但设备归属校验改为「属于当前客户子树」。短连接设备离线时入待发队列,上线补发。"""
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    data  = request.get_json() or {}
    phone = (data.get('phone') or '').strip()
    cmd   = (data.get('cmd')   or '').strip()
    if not phone or not cmd:
        return fail('phone 和 cmd 不能为空')
    dev = _portal_device_owned(cid, phone)
    if not dev:
        return fail('设备不存在或无权限', 403)
    spec = zl.AVAILABLE_COMMANDS.get(cmd)
    if not spec:
        return fail(f'不支持的天禧指令: {cmd}')
    try:
        args = []
        for pname in spec['params']:
            if pname not in data:
                return fail(f'缺少参数: {pname}（命令 {cmd} 需要 {spec["params"]}）')
            args.append(data[pname])
        if cmd == 'set_sos_numbers' and len(args) == 1:
            nums = args[0] if isinstance(args[0], (list, tuple)) else [args[0]]
            cmd_str = spec['func'](*nums)
        else:
            cmd_str = spec['func'](*args)
    except Exception as e:
        return fail(f'构造指令失败: {e}')
    try:
        frame = build_zhiling_frame(phone, cmd_str)
    except Exception as e:
        return fail(f'构造指令帧失败: {e}')
    with sessions_lock:
        conn = sessions.get(phone)
    if not conn:
        db_exec("INSERT INTO pending_command (phone,cmd,payload_hex,status) VALUES (?,?,?,?)",
                (phone, cmd, frame.hex(), 'pending'))
        db_exec("INSERT INTO command_history (phone,device_name,command,result,response) VALUES (?,?,?,?,?)",
                (phone, dev['name'] or phone, f'{cmd} {cmd_str}', 'queued', '设备离线，已入待发队列，上线后自动下发'))
        return ok({'cmd': cmd, 'phone': phone, 'cmd_str': cmd_str, 'queued': True,
                   'message': '设备当前离线，指令已排队，设备下次上线时自动下发'})
    try:
        conn.sendall(frame)
        db_exec("INSERT INTO command_history (phone,device_name,command,result,response) VALUES (?,?,?,?,?)",
                (phone, dev['name'] or phone, f'{cmd} {cmd_str}', 'success', ''))
        return ok({'cmd': cmd, 'phone': phone, 'cmd_str': cmd_str, 'queued': False})
    except Exception as e:
        db_exec("INSERT INTO pending_command (phone,cmd,payload_hex,status) VALUES (?,?,?,?)",
                (phone, cmd, frame.hex(), 'pending'))
        db_exec("INSERT INTO command_history (phone,device_name,command,result,response) VALUES (?,?,?,?,?)",
                (phone, dev['name'] or phone, f'{cmd} {cmd_str}', 'queued', f'实时下发失败已转待发队列: {e}'))
        return ok({'cmd': cmd, 'phone': phone, 'cmd_str': cmd_str, 'queued': True,
                   'message': '实时下发失败，已转入待发队列，设备下次上线时自动下发'})


@app.post('/api/customer/commands/g618g')
def portal_g618g_command():
    """客户向自己名下的 G618G 设备下发指令。逻辑同管理端 g618g_command,
    设备归属校验改为属于当前客户子树。G618G 短连接,离线入队、上线补发。"""
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    data  = request.get_json() or {}
    phone = (data.get('phone') or '').strip()
    cmd   = (data.get('cmd')   or '').strip()
    if not phone or not cmd:
        return fail('phone 和 cmd 不能为空')
    dev = _portal_device_owned(cid, phone)
    if not dev:
        return fail('设备不存在或无权限', 403)
    builder = _G618G_CMD_MAP.get(cmd)
    if not builder:
        return fail(f'不支持的 G618G 指令: {cmd}')
    if cmd == 'set_server_ip':
        ip = data.get('ip', '')
        try:
            port = int(data.get('port', 0))
        except (ValueError, TypeError):
            port = 0
        if not _re.match(r'^(\d{1,3}\.){3}\d{1,3}$', ip) or not (1 <= port <= 65535):
            return fail('IP 地址或端口格式错误')
    try:
        payload = builder(data)
    except Exception as e:
        return fail(f'指令参数错误: {e}')
    with sessions_lock:
        conn = sessions.get(phone)
    if not conn:
        db_exec("INSERT INTO pending_command (phone,cmd,payload_hex,status) VALUES (?,?,?,?)",
                (phone, cmd, payload.hex(), 'pending'))
        db_exec("INSERT INTO command_history (phone,device_name,command,result,response) VALUES (?,?,?,?,?)",
                (phone, dev['name'] or ('G618G-'+phone[-6:]), cmd, 'queued', '设备离线，已加入待发队列，上线后自动下发'))
        return ok({'cmd': cmd, 'phone': phone, 'queued': True,
                   'message': '设备当前离线，指令已排队，设备下次上线时自动下发'})
    try:
        conn.sendall(payload)
        import time as _t; _t.sleep(0.01)
        conn.sendall(payload)
        db_exec("INSERT INTO command_history (phone,device_name,command,result,response) VALUES (?,?,?,?,?)",
                (phone, dev['name'] or ('G618G-'+phone[-6:]), cmd, 'success', ''))
        return ok({'cmd': cmd, 'phone': phone, 'queued': False})
    except Exception as e:
        db_exec("INSERT INTO pending_command (phone,cmd,payload_hex,status) VALUES (?,?,?,?)",
                (phone, cmd, payload.hex(), 'pending'))
        db_exec("INSERT INTO command_history (phone,device_name,command,result,response) VALUES (?,?,?,?,?)",
                (phone, dev['name'] or ('G618G-'+phone[-6:]), cmd, 'queued', f'实时下发失败已转待发队列: {e}'))
        return ok({'cmd': cmd, 'phone': phone, 'queued': True,
                   'message': '实时下发失败，已转入待发队列，设备下次上线时自动下发'})


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


@app.post('/api/customer/commands/batch_text')
def portal_batch_send_text():
    """客户批量向自己子树内的设备下发文本指令。越权设备静默跳过,仅对在线设备下发。"""
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    data   = request.get_json() or {}
    phones = data.get('phones', [])
    text   = (data.get('text') or '').strip()
    if not phones:
        return fail('phones 不能为空', 400)
    if len(phones) > 500:
        return fail('单次批量下发不超过 500 台设备', 400)
    if not text:
        return fail('指令内容不能为空', 400)
    # 限定在客户子树内的设备,越权 phone 静默跳过
    my_phones = set(_get_subtree_phones(cid))
    sent, offline, denied = 0, 0, 0
    for phone in phones:
        if phone not in my_phones:
            denied += 1
            continue
        with sessions_lock:
            conn = sessions.get(phone)
        if not conn:
            offline += 1
            continue
        try:
            body = bytes([0x01]) + text.encode('gbk', errors='replace')
            conn.sendall(p.encode_message(0x8300, phone, next_serial(), body))
            db_exec("INSERT INTO command_history (phone,device_name,command,result) VALUES (?,?,?,?)",
                    (phone, phone, text, '已发送'))
            sent += 1
        except Exception:
            offline += 1
    return ok({'sent': sent, 'offline': offline, 'denied': denied})


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
    # 关机报警驱动：直接读 status 字段，与管理端一致
    alarm   = db_scalar(f"SELECT COUNT(*) FROM device WHERE phone IN ({ph}) AND status=2", phones)
    online  = db_scalar(f"SELECT COUNT(*) FROM device WHERE phone IN ({ph}) AND status=1", phones)
    offline = total - online - alarm
    return ok({'total': total, 'online': online, 'offline': offline, 'alarm': alarm})


# ── 客户门户：报表统计（照搬管理端 report_summary，过滤范围换成客户子树） ──────
@app.get('/api/customer/report/summary')
def portal_report_summary():
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)

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

    # 客户子树：cid 及所有后代客户
    all_cids = _get_all_descendant_cids(cid)
    phones   = _get_subtree_phones(cid)
    cid_ph   = ','.join('?' * len(all_cids)) if all_cids else '0'
    ph       = ','.join('?' * len(phones)) if phones else '0'

    # 设备统计：按 customer_id 过滤到客户子树
    device_total    = db_scalar(f"SELECT COUNT(*) FROM device WHERE customer_id IN ({cid_ph})", all_cids)
    device_online   = db_scalar(f"SELECT COUNT(*) FROM device WHERE customer_id IN ({cid_ph}) AND status=1", all_cids)
    device_alarm    = db_scalar(f"SELECT COUNT(*) FROM device WHERE customer_id IN ({cid_ph}) AND status=2", all_cids)
    device_active   = db_scalar(f"SELECT COUNT(*) FROM device WHERE customer_id IN ({cid_ph}) AND lifecycle=1", all_cids)
    device_inactive = db_scalar(f"SELECT COUNT(*) FROM device WHERE customer_id IN ({cid_ph}) AND lifecycle=0", all_cids)
    device_disabled = db_scalar(f"SELECT COUNT(*) FROM device WHERE customer_id IN ({cid_ph}) AND lifecycle IN (2,3)", all_cids)

    # 报警统计：alarm_record 按 phone 关联子树设备
    if phones:
        alarm_total     = db_scalar(f"SELECT COUNT(*) FROM alarm_record WHERE phone IN ({ph})", phones)
        alarm_unhandled = db_scalar(f"SELECT COUNT(*) FROM alarm_record WHERE phone IN ({ph}) AND status=0", phones)
    else:
        alarm_total = alarm_unhandled = 0
    # 使用参数化查询，防止日期字段 SQL 注入
    if not phones:
        alarm_period = 0
    elif start_raw:
        alarm_period = db_scalar(
            f"SELECT COUNT(*) FROM alarm_record WHERE phone IN ({ph}) AND date(alarm_time) BETWEEN ? AND ?",
            list(phones) + [start_raw, end_raw]
        )
    else:
        from datetime import timedelta
        _start_date = (datetime.now() - timedelta(days=days - 1)).strftime('%Y-%m-%d')
        alarm_period = db_scalar(
            f"SELECT COUNT(*) FROM alarm_record WHERE phone IN ({ph}) AND date(alarm_time) >= ?",
            list(phones) + [_start_date]
        )

    # SIM 卡：sim_card 按 device_phone 关联子树设备（列名是 device_phone，非 phone）
    if phones:
        sim_total   = db_scalar(f"SELECT COUNT(*) FROM sim_card WHERE device_phone IN ({ph})", phones)
        sim_normal  = db_scalar(f"SELECT COUNT(*) FROM sim_card WHERE device_phone IN ({ph}) AND status='正常'", phones)
        sim_exp7    = db_scalar(f"SELECT COUNT(*) FROM sim_card WHERE device_phone IN ({ph}) AND expire_date IS NOT NULL "
                                f"AND expire_date <= date('now','+7 days') AND expire_date >= date('now')", phones)
        sim_exp30   = db_scalar(f"SELECT COUNT(*) FROM sim_card WHERE device_phone IN ({ph}) AND expire_date IS NOT NULL "
                                f"AND expire_date <= date('now','+30 days') AND expire_date >= date('now')", phones)
        sim_expired = db_scalar(f"SELECT COUNT(*) FROM sim_card WHERE device_phone IN ({ph}) AND expire_date IS NOT NULL "
                                f"AND expire_date < date('now')", phones)
    else:
        sim_total = sim_normal = sim_exp7 = sim_exp30 = sim_expired = 0

    # 客户统计：子树内的客户
    customer_total = db_scalar(f"SELECT COUNT(*) FROM customer WHERE id IN ({cid_ph})", all_cids)
    if phones:
        loc_total = db_scalar(f"SELECT COUNT(*) FROM location_record WHERE phone IN ({ph})", phones)
    else:
        loc_total = 0
    # 充值：recharge 通过 sim_id 关联，先取子树设备名下 SIM 的 id 列表
    sim_ids = [r['id'] for r in db_query(f"SELECT id FROM sim_card WHERE device_phone IN ({ph})", phones)] if phones else []
    if sim_ids:
        sp = ','.join('?' * len(sim_ids))
        recharge_total = db_scalar(f"SELECT COALESCE(SUM(amount),0) FROM recharge WHERE sim_id IN ({sp})", sim_ids)
        if start_raw:
            recharge_period = db_scalar(
                f"SELECT COALESCE(SUM(amount),0) FROM recharge WHERE sim_id IN ({sp}) AND date(created_at) BETWEEN ? AND ?",
                sim_ids + [start_raw, end_raw]
            )
        else:
            from datetime import timedelta as _td
            _rstart_date = (datetime.now() - _td(days=days - 1)).strftime('%Y-%m-%d')
            recharge_period = db_scalar(
                f"SELECT COALESCE(SUM(amount),0) FROM recharge WHERE sim_id IN ({sp}) AND date(created_at) >= ?",
                sim_ids + [_rstart_date]
            )
    else:
        recharge_total = recharge_period = 0

    # 趋势：按实际天数
    trend_days = min(days, 30)
    from datetime import timedelta as _trd
    _trend_date = (datetime.now() - _trd(days=trend_days - 1)).strftime('%Y-%m-%d')
    if phones:
        alarm_trend = db_query(
            f"SELECT date(alarm_time) as day, COUNT(*) as cnt FROM alarm_record "
            f"WHERE phone IN ({ph}) AND date(alarm_time) >= ? GROUP BY day ORDER BY day",
            list(phones) + [_trend_date]
        )
        alarm_types = db_query(
            f"SELECT alarm_desc, COUNT(*) as cnt FROM alarm_record WHERE phone IN ({ph}) "
            f"GROUP BY alarm_desc ORDER BY cnt DESC LIMIT 6",
            phones
        )
        loc_trend = db_query(
            f"SELECT date(gps_time) as day, COUNT(*) as cnt FROM location_record "
            f"WHERE phone IN ({ph}) AND date(gps_time) >= ? GROUP BY day ORDER BY day",
            list(phones) + [_trend_date]
        )
    else:
        alarm_trend = alarm_types = loc_trend = []

    # 客户排名（按名下设备数）：限定子树内客户
    customer_rank = db_query(
        f"SELECT c.name, COUNT(d.id) as device_count "
        f"FROM customer c LEFT JOIN device d ON d.customer_id=c.id "
        f"WHERE c.id IN ({cid_ph}) "
        f"GROUP BY c.id ORDER BY device_count DESC LIMIT 10",
        all_cids
    )

    # 本月新增设备 / 新增客户（限定子树）
    new_devices   = db_scalar(f"SELECT COUNT(*) FROM device WHERE customer_id IN ({cid_ph}) "
                              f"AND date(created_at) >= date('now','start of month')", all_cids)
    new_customers = db_scalar(f"SELECT COUNT(*) FROM customer WHERE id IN ({cid_ph}) "
                              f"AND date(created_at) >= date('now','start of month')", all_cids)

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


# ── 客户门户：设备列表（带分页 / 搜索） ──────────────────────────────────────

@app.get('/api/customer/device_list')
def portal_device_list():
    """带分页+关键词的设备列表，供前端表格使用"""
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    page, size = _page_params(20)
    keyword = request.args.get('keyword', '').strip()
    model   = request.args.get('terminal_model', '').strip()
    imei    = request.args.get('imei', '').strip()
    sub_cid = request.args.get('customer_id', '').strip()
    offset  = (page - 1) * size
    base_cols = ("SELECT device.id, device.phone, device.name, device.plate_no, "
                 "device.manufacturer, device.terminal_model, device.terminal_id, device.imei, "
                 "device.last_lat, device.last_lng, "
                 "device.last_speed, device.last_location_time, device.online_time, device.status, device.customer_id, "
                 "device.last_battery, device.last_battery_time, "
                 "device.last_seen, device.last_signal, device.last_loc_type, device.last_address, "
                 "device.presence_state, device.offline_reason, "
                 "c.name AS customer_name, c.login_name AS account, "
                 "c.contact AS real_name, c.phone AS contact_phone, c.avatar AS avatar, c.address AS address, "
                 "r.name AS role_name, r.color AS role_color, r.icon_type AS role_icon, "
                 "r.customer_id AS role_cust "
                 "FROM device LEFT JOIN device_role r ON device.role_id = r.id "
                 "LEFT JOIN customer c ON device.customer_id = c.id")
    all_cids = _get_all_descendant_cids(cid)
    # 账号筛选：若指定了子客户，须落在本客户子树内(防越权)，否则忽略该条件
    if sub_cid:
        try:
            _sub = int(sub_cid)
            if _sub in all_cids:
                scope_cids = _get_all_descendant_cids(_sub)
            else:
                scope_cids = all_cids
        except ValueError:
            scope_cids = all_cids
    else:
        scope_cids = all_cids
    cid_ph = ','.join('?' * len(scope_cids))
    conds  = [f"device.customer_id IN ({cid_ph})"]
    params = list(scope_cids)
    if keyword:
        conds.append("(device.name LIKE ? OR device.phone LIKE ?)")
        params += [f'%{keyword}%', f'%{keyword}%']
    if model:
        conds.append("device.terminal_model LIKE ?")
        params.append(f'%{model}%')
    if imei:
        # 客户端「设备IMEI」框：按 IMEI 或 设备号/主键 模糊匹配
        conds.append("(device.imei LIKE ? OR device.phone LIKE ? OR device.terminal_id LIKE ?)")
        params += [f'%{imei}%', f'%{imei}%', f'%{imei}%']
    where = "WHERE " + " AND ".join(conds)
    total   = db_scalar(f"SELECT COUNT(*) FROM device LEFT JOIN customer c ON device.customer_id = c.id {where}", params)
    records = db_query(f"{base_cols} {where} ORDER BY device.id LIMIT ? OFFSET ?", params + [size, offset])
    # 在线状态直接用 status 字段(关机报警驱动)，不再按时间窗口覆盖。
    # 角色隔离：设备若挂着不属于本客户子树的角色(如管理员建的全局角色)，
    # 在客户端不显示该角色，避免出现「客户角色列表里没有、设备却挂着」的错位。
    _scope = set(all_cids)
    for _rec in records:
        _rc = _rec.pop('role_cust', None)
        if _rc not in _scope:
            _rec['role_id']    = None
            _rec['role_name']  = None
            _rec['role_color'] = None
            _rec['role_icon']  = None
    return ok({'records': records, 'total': total, 'page': page})


# ── 客户门户：批量导入设备（自动归到当前客户名下） ───────────────────────────

@app.post('/api/customer/devices/import')
def portal_import_devices():
    """客户端批量导入设备。规则同管理端 import_devices,但强制把新建设备
    customer_id 设为当前登录客户(防越权把设备建到别人名下)。
    设备号(deviceNo)与 IMEI 至少填一个;已存在或本批重复的跳过。"""
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    data = request.get_json() or {}
    rows = data.get('rows')
    if not isinstance(rows, list) or not rows:
        return fail('导入数据为空')
    if len(rows) > 5000:
        return fail('单次导入不能超过 5000 条,请分批导入')
    # 客户所属组织(建档时 org_id 跟随客户)
    _cust = db_query_one("SELECT org_id FROM customer WHERE id=?", (cid,))
    org_id = (_cust.get('org_id') if _cust else 1) or 1
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    created = skipped = failed = 0
    details = []
    seen_in_batch = set()
    for idx, r in enumerate(rows):
        rownum = idx + 1
        if not isinstance(r, dict):
            failed += 1
            details.append({'row': rownum, 'phone': '', 'status': 'failed', 'reason': '行格式错误'})
            continue
        dev_no = (str(r.get('deviceNo') or r.get('terminalId') or '')).strip()
        imei   = (str(r.get('imei') or '')).strip()
        if not dev_no and not imei:
            _legacy = (str(r.get('phone') or '')).strip()
            if _legacy:
                dev_no = _legacy
        phone = dev_no or imei
        if not phone:
            failed += 1
            details.append({'row': rownum, 'phone': '', 'status': 'failed', 'reason': '设备号和 IMEI 至少填一个'})
            continue
        if phone in seen_in_batch:
            skipped += 1
            details.append({'row': rownum, 'phone': phone, 'status': 'skipped', 'reason': '文件内重复'})
            continue
        seen_in_batch.add(phone)
        if db_query_one("SELECT id FROM device WHERE phone=?", (phone,)):
            skipped += 1
            details.append({'row': rownum, 'phone': phone, 'status': 'skipped', 'reason': '设备号/IMEI 已存在'})
            continue
        try:
            db_exec(
                "INSERT INTO device (phone,name,plate_no,manufacturer,terminal_model,"
                "terminal_id,imei,customer_id,plate_color,auth_code,status,org_id,lifecycle,remark,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,1,'DEFAULT',0,?,0,?,?,?)",
                (phone, str(r.get('name', '') or ''), str(r.get('plateNo', '') or ''),
                 str(r.get('manufacturer', '') or ''), str(r.get('terminalModel', '') or ''),
                 dev_no, imei, cid, org_id, str(r.get('remark', '') or ''), now, now)
            )
            created += 1
            details.append({'row': rownum, 'phone': phone, 'status': 'created', 'reason': ''})
        except Exception as e:
            failed += 1
            details.append({'row': rownum, 'phone': phone, 'status': 'failed', 'reason': str(e)[:120]})
    add_op_log('客户批量导入设备', f'客户#{cid} 导入 {created} 台,跳过 {skipped} 台,失败 {failed} 台')
    return ok({'created': created, 'skipped': skipped, 'failed': failed, 'details': details})


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
    # 只能操作「父客户直属」或「该子客户直属」的设备（allowed 集合）。
    # allowed 之外的设备（如该子客户自己的下级持有的设备）绝不动，避免误伤更深层级。
    allowed = {r['phone'] for r in db_query(
        "SELECT phone FROM device WHERE customer_id=? OR customer_id=?", (cid, sid)
    )}
    target = [p for p in phones if p in allowed]           # 本次要归属到 sid 的设备
    target_set = set(target)
    # 全量设置语义，但严格限制在 allowed 内：
    # 1) 回收：仅把「当前属于 sid 且本次未勾选」的设备收回父客户（不碰 sid 下级的设备）
    reclaim = [p for p in allowed
               if p not in target_set
               and db_query_one("SELECT 1 FROM device WHERE phone=? AND customer_id=?", (p, sid))]
    for phone in reclaim:
        db_exec("UPDATE device SET customer_id=? WHERE phone=?", (cid, phone))
    # 2) 分配：把本次勾选的设备归属到 sid
    for phone in target:
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
    devices_str = d.get('devices','') or ''
    fence_type  = d.get('fence_type', 'circle')
    color       = d.get('color', '#409EFF')
    # 按围栏类型分别落库(与管理端 create_fence 一致)：之前不分类型、直接塞 coordinates 原始
    # 数组且不存 adcode，导致客户建多边形(coordinates 未 JSON 化插入报错)、行政区(缺 adcode)失败。
    import json as _json
    if fence_type == 'circle':
        lat = _num_or_none(d.get('lat'), float, lo=-90,  hi=90)
        lng = _num_or_none(d.get('lng'), float, lo=-180, hi=180)
        radius = _num_or_none(d.get('radius', 2000), int, lo=1)
        if lat is None or lng is None:
            return fail('圆形围栏需要合法的 lat(-90~90)/lng(-180~180)', 400)
        if radius is None:
            return fail('radius 需为正整数', 400)
        ins_sql = ("INSERT INTO geo_fence (name,fence_type,lat,lng,radius,color,devices,customer_id,created_at) "
                   "VALUES (?,?,?,?,?,?,?,?,?)")
        ins_params = (name, 'circle', lat, lng, radius, color, devices_str, cid, now)
    elif fence_type == 'polygon':
        coords = d.get('coordinates')
        if not coords:
            return fail('多边形围栏需要 coordinates', 400)
        ins_sql = ("INSERT INTO geo_fence (name,fence_type,lat,lng,coordinates,color,devices,customer_id,created_at) "
                   "VALUES (?,?,?,?,?,?,?,?,?)")
        ins_params = (name, 'polygon', 0.0, 0.0, _json.dumps(coords), color, devices_str, cid, now)
    elif fence_type == 'administrative':
        if not d.get('adcode'):
            return fail('行政区围栏需要 adcode', 400)
        ins_sql = ("INSERT INTO geo_fence (name,fence_type,lat,lng,adcode,coordinates,color,devices,customer_id,created_at) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?)")
        ins_params = (name, 'administrative', 0.0, 0.0, str(d['adcode']),
                      _json.dumps(d.get('coordinates', [])), color, devices_str, cid, now)
    else:
        return fail('未知围栏类型', 400)
    with _db_lock:
        conn = get_db()
        try:
            new_id = conn.execute(ins_sql, ins_params).lastrowid
            _sync_fence_devices(new_id, devices_str, conn=conn)   # 双写：同 conn 同步关联表
            conn.commit()
            return ok({'id': new_id})
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
    fence_type = d.get('fence_type', 'circle')
    color      = d.get('color', '#409EFF')
    # 按类型更新：多边形/行政区的 coordinates 需 JSON 化，行政区还要写 adcode(与创建一致)
    import json as _json
    if fence_type == 'polygon':
        db_exec("UPDATE geo_fence SET name=?,fence_type=?,lat=0,lng=0,coordinates=?,color=? WHERE id=?",
                (d.get('name'), 'polygon', _json.dumps(d.get('coordinates') or []), color, fid))
    elif fence_type == 'administrative':
        db_exec("UPDATE geo_fence SET name=?,fence_type=?,lat=0,lng=0,adcode=?,coordinates=?,color=? WHERE id=?",
                (d.get('name'), 'administrative', str(d.get('adcode') or ''),
                 _json.dumps(d.get('coordinates') or []), color, fid))
    else:
        db_exec("UPDATE geo_fence SET name=?,fence_type=?,lat=?,lng=?,radius=?,color=? WHERE id=?",
                (d.get('name'), 'circle', d.get('lat'), d.get('lng'),
                 d.get('radius', 2000), color, fid))
    return ok()


@app.delete('/api/customer/fences/<int:fid>')
def portal_delete_fence(fid):
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    if not db_query_one("SELECT id FROM geo_fence WHERE id=? AND customer_id=?", (fid, cid)):
        return fail('无权限或不存在', 404)
    db_exec("DELETE FROM geo_fence WHERE id=?", (fid,))
    _sync_fence_devices(fid, '')   # 双写：清理该围栏在加速表中的关联
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
    devices_str = ','.join(phones)
    db_exec("UPDATE geo_fence SET devices=? WHERE id=?", (devices_str, fid))
    _sync_fence_devices(fid, devices_str)   # 双写：全量替换该围栏的关联设备
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


# ── 客户门户：设备角色/分组（每客户独立，按 customer_id 隔离） ──────────────────
# 客户只看/管 device_role.customer_id = 自己的角色；分配设备限定在客户子树内。

@app.get('/api/customer/roles')
def portal_list_roles():
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    records = db_query(
        "SELECT r.*, "
        "(SELECT COUNT(*) FROM device d WHERE d.role_id = r.id) as device_count "
        "FROM device_role r WHERE r.customer_id=? ORDER BY r.created_at ASC", (cid,))
    return ok({'records': records, 'total': len(records)})


@app.post('/api/customer/roles')
def portal_create_role():
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    d = request.get_json() or {}
    name = (d.get('name') or '').strip()
    if not name:
        return fail('角色名称不能为空', 400)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    db_exec(
        "INSERT INTO device_role (name,color,icon_type,description,customer_id,created_at) "
        "VALUES (?,?,?,?,?,?)",
        (name, d.get('color', '#409EFF'), d.get('icon_type', '圆形'),
         d.get('description', ''), cid, now))
    return ok()


def _portal_role_or_none(rid, cid):
    """取该客户拥有的角色(customer_id=cid)，无权/不存在返回 None，防跨客户改他人角色。"""
    return db_query_one("SELECT id FROM device_role WHERE id=? AND customer_id=?", (rid, cid))


@app.put('/api/customer/roles/<int:rid>')
def portal_update_role(rid):
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    if not _portal_role_or_none(rid, cid):
        return fail('角色不存在或无权限', 404)
    d = request.get_json() or {}
    name = (d.get('name') or '').strip()
    if not name:
        return fail('角色名称不能为空', 400)
    db_exec("UPDATE device_role SET name=?,color=?,icon_type=?,description=? WHERE id=?",
            (name, d.get('color', '#409EFF'), d.get('icon_type', '圆形'),
             d.get('description', ''), rid))
    return ok()


@app.delete('/api/customer/roles/<int:rid>')
def portal_delete_role(rid):
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    if not _portal_role_or_none(rid, cid):
        return fail('角色不存在或无权限', 404)
    db_exec("UPDATE device SET role_id=NULL WHERE role_id=?", (rid,))
    db_exec("DELETE FROM device_role WHERE id=?", (rid,))
    return ok()


@app.put('/api/customer/roles/<int:rid>/assign')
def portal_assign_role_devices(rid):
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    if not _portal_role_or_none(rid, cid):
        return fail('角色不存在或无权限', 404)
    d = request.get_json() or {}
    phones = d.get('phones', [])
    # 只允许分配客户子树内的设备，越权 phone 静默忽略
    my_phones = set(_get_subtree_phones(cid))
    phones = [p for p in phones if p in my_phones]
    db_exec("UPDATE device SET role_id=NULL WHERE role_id=?", (rid,))
    if phones:
        ph = ','.join('?' * len(phones))
        db_exec(f"UPDATE device SET role_id=? WHERE phone IN ({ph})", [rid] + list(phones))
    return ok()


# ── 客户门户：标注点 / 风险点（每客户独立，按 customer_id 隔离） ──────────────────

@app.get('/api/customer/mark_points')
def portal_list_mark_points():
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    name = request.args.get('name', '').strip()
    conds, args = ["customer_id=?"], [cid]
    if name:
        conds.append("name LIKE ?"); args.append(f'%{name}%')
    sql = "SELECT * FROM mark_point WHERE " + " AND ".join(conds) + " ORDER BY created_at DESC"
    return ok(db_query(sql, tuple(args)))


@app.post('/api/customer/mark_points')
def portal_create_mark_point():
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    d = request.get_json() or {}
    name = (d.get('name') or '').strip()
    if not name or d.get('lat') is None or d.get('lng') is None:
        return fail('name/lat/lng 不能为空', 400)
    lat = float(d['lat']); lng = float(d['lng'])
    if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        return fail('坐标超出有效范围', 400)
    db_exec("INSERT INTO mark_point (name,lat,lng,remark,customer_id) VALUES (?,?,?,?,?)",
            (name, lat, lng, d.get('remark', ''), cid))
    return ok()


@app.delete('/api/customer/mark_points/<int:mid>')
def portal_delete_mark_point(mid):
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    if not db_query_one("SELECT id FROM mark_point WHERE id=? AND customer_id=?", (mid, cid)):
        return fail('无权限或不存在', 404)
    db_exec("DELETE FROM mark_point WHERE id=?", (mid,))
    return ok()


@app.get('/api/customer/risk_points')
def portal_list_risk_points():
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    sql = "SELECT * FROM risk_point WHERE customer_id=? ORDER BY created_at DESC"
    return ok(db_query(sql, (cid,)))


@app.post('/api/customer/risk_points')
def portal_create_risk_point():
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    d = request.get_json() or {}
    name = (d.get('name') or '').strip()
    if not name or d.get('lat') is None or d.get('lng') is None:
        return fail('name/lat/lng 不能为空', 400)
    lat = float(d['lat']); lng = float(d['lng'])
    if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        return fail('坐标超出有效范围', 400)
    db_exec("INSERT INTO risk_point (name,lat,lng,level,remark,customer_id) VALUES (?,?,?,?,?,?)",
            (name, lat, lng, d.get('level', 'medium'), d.get('remark', ''), cid))
    return ok()


@app.delete('/api/customer/risk_points/<int:rid>')
def portal_delete_risk_point(rid):
    cid = _get_portal_customer()
    if not cid:
        return fail('未授权', 401)
    if not db_query_one("SELECT id FROM risk_point WHERE id=? AND customer_id=?", (rid, cid)):
        return fail('无权限或不存在', 404)
    db_exec("DELETE FROM risk_point WHERE id=?", (rid,))
    return ok()


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
# _current_admin/_scope_path/_orgs_in_scope/_org_in_scope 已随鉴权组抽至
# core/security.py(见文件顶部 import)。


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

# MQTT 接入(_mqtt_on_message/start_mqtt_subscriber + MQTT_* 常量)已抽至
# core/ingest.py;下面 import 保持不变。start_mqtt_subscriber 供 gunicorn
# post_fork 通过 app re-export 调用。
from core.ingest import start_mqtt_subscriber


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


# ── 账号级功能权限（菜单可见 + 接口校验双重管控）────────────────────────────────
# 需求：给每个管理账号(admin_user)单独勾选它能访问的功能菜单。未配置=拥有全部(向后兼容)。
# 权限项 key 用前端路由 path(与菜单一一对应)。存 account_permission 表，登录时下发。
# 超级管理员(user_type>=9 或 username=admin)不受限，始终拥有全部权限。

# 全部可授权的功能菜单(key=前端路由path, name=显示名, group=分组)
MENU_PERMISSIONS = [
    ('bigscreen',        '大屏展示',   '监控'),
    ('dashboard',        '系统概览',   '监控'),
    ('map',              '实时地图',   '监控'),
    ('track',            '轨迹回放',   '监控'),
    ('fence',            '电子围栏',   '监控'),
    ('health',           '健康数据',   '监控'),
    ('query',            '设备查询',   '监控'),
    ('device-info',      '设备信息',   '管理'),
    ('device-settings',  '设备设置',   '管理'),
    ('role-settings',    '角色设置',   '管理'),
    ('sims',             'SIM卡管理',  '管理'),
    ('customers',        '客户管理',   '管理'),
    ('org',              '组织管理',   '系统'),
    ('module-auth',      '模块授权',   '系统'),
    ('platform-setting', '平台设置',   '系统'),
    ('alarms',           '报警管理',   '运营'),
    ('alarm-setting',    '报警设置',   '运营'),
    ('attendance',       '考勤统计',   '运营'),
    ('recharges',        '充值管理',   '运营'),
    ('reports',          '报表统计',   '运营'),
]
_ALL_MENU_KEYS = [m[0] for m in MENU_PERMISSIONS]

# 菜单 key → 保护该菜单的接口路径前缀(接口层校验用)。只列各菜单独有的写/读接口前缀；
# 通用接口(如 /api/devices/summary、/api/auth/*)不纳入，避免误伤跨页面公共调用。
_MENU_API_PREFIX = {
    'customers':        ['/api/customers'],
    'org':              ['/api/orgs', '/api/organizations'],
    'module-auth':      ['/api/modules'],
    'platform-setting': ['/api/platform-setting'],
    'role-settings':    ['/api/roles'],
    'sims':             ['/api/sims', '/api/recharges'],
    'reports':          ['/api/reports'],
    'attendance':       ['/api/attendance'],
    'alarm-setting':    ['/api/alarm-rules', '/api/alarm-setting'],
    'fence':            ['/api/fences'],
    'track':            ['/api/track'],
}

def _ensure_account_permission_table():
    """建账号权限表(幂等)。account_id 对应 admin_user.id；menu_keys 逗号分隔。"""
    try:
        db_exec(
            "CREATE TABLE IF NOT EXISTS account_permission ("
            " account_id INTEGER PRIMARY KEY,"
            " menu_keys  TEXT DEFAULT '',"
            " updated_at TEXT )")
    except Exception as e:
        log.warning("[账号权限] 建表失败: %s", e)

def _get_account_menu_keys(admin_id):
    """返回该账号的可见菜单 key 列表。未配置(无记录)返回 None(表示全部可见,向后兼容)。"""
    _ensure_account_permission_table()
    try:
        row = db_query_one("SELECT menu_keys FROM account_permission WHERE account_id=?", (admin_id,))
    except Exception:
        return None
    if not row:
        return None
    raw = (row.get('menu_keys') or '').strip()
    if raw == '':
        return []            # 显式配置为"无任何权限"
    return [k for k in raw.split(',') if k]

def _admin_is_super(admin_row):
    """超级管理员判定:username=admin 或 user_type>=9,不受菜单权限限制。"""
    if not admin_row:
        return False
    return (admin_row.get('username') == 'admin') or ((admin_row.get('user_type') or 0) >= 9)

@app.get('/api/account-permissions/accounts')
def list_admin_accounts():
    """列出所有管理账号(供账号权限页选择账号)。返回 id/username/realName/isSuper。"""
    if not _verify_admin_token(request.headers.get('X-Admin-Token', '')):
        return fail('未授权', 401)
    rows = db_query("SELECT id, username, real_name, user_type, COALESCE(is_active,1) AS is_active "
                    "FROM admin_user ORDER BY id")
    out = []
    for r in rows:
        out.append({'id': r['id'], 'username': r['username'],
                    'realName': r.get('real_name') or '',
                    'isSuper': _admin_is_super(r),
                    'isActive': bool(r.get('is_active', 1))})
    return ok(out)

@app.get('/api/account-permissions/menus')
def list_menu_permissions():
    """返回全部可授权菜单项(供权限配置界面渲染勾选树)。"""
    if not _verify_admin_token(request.headers.get('X-Admin-Token', '')):
        return fail('未授权', 401)
    return ok([{'key': k, 'name': n, 'group': g} for (k, n, g) in MENU_PERMISSIONS])

@app.get('/api/account-permissions/<int:account_id>')
def get_account_permission(account_id):
    """查某账号的已配置菜单权限。返回 {account_id, menu_keys, is_all}。"""
    if not _verify_admin_token(request.headers.get('X-Admin-Token', '')):
        return fail('未授权', 401)
    keys = _get_account_menu_keys(account_id)
    return ok({'account_id': account_id,
               'menu_keys': keys if keys is not None else _ALL_MENU_KEYS,
               'is_all': keys is None})

@app.put('/api/account-permissions/<int:account_id>')
def set_account_permission(account_id):
    """设置某账号的菜单权限。Body: {menu_keys: [...]}。仅超管可配。"""
    tok = request.headers.get('X-Admin-Token', '')
    admin_id = _verify_admin_token(tok)
    if not admin_id:
        return fail('未授权', 401)
    me = db_query_one("SELECT username, user_type FROM admin_user WHERE id=?", (admin_id,))
    if not _admin_is_super(me):
        return fail('仅超级管理员可配置账号权限', 403)
    d = request.get_json() or {}
    keys = d.get('menu_keys', [])
    if not isinstance(keys, list):
        return fail('menu_keys 必须是数组', 400)
    # 只保留合法 key,去重保序
    valid = [k for k in _ALL_MENU_KEYS if k in set(keys)]
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    _ensure_account_permission_table()
    exists = db_query_one("SELECT account_id FROM account_permission WHERE account_id=?", (account_id,))
    if exists:
        db_exec("UPDATE account_permission SET menu_keys=?, updated_at=? WHERE account_id=?",
                (','.join(valid), now, account_id))
    else:
        db_exec("INSERT INTO account_permission (account_id, menu_keys, updated_at) VALUES (?,?,?)",
                (account_id, ','.join(valid), now))
    add_op_log('配置账号权限', f'account_id={account_id} menus={",".join(valid)}')
    return ok({'account_id': account_id, 'menu_keys': valid})


# ── 全局异常/错误处理 ──────────────────────────────────────────────────────────
# 统一以 fail() 相同的 {code,msg} JSON 返回，记录日志但不向客户端泄露堆栈。
# 仅在 _DEBUG_MODE 时附带异常细节，便于本地排障。

@app.errorhandler(Exception)
def _handle_uncaught(e):
    # HTTPException（含 404/405/400 等）交给下面专用/默认处理，避免把 4xx 变成 500
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    log.exception('未处理异常: %s', e)
    msg = '服务器内部错误'
    if _DEBUG_MODE:
        msg = f'服务器内部错误: {e}'
    return fail(msg, 500)


@app.errorhandler(404)
def _handle_404(e):
    # 仅对 API 路径返回 JSON；其余路径已被 SPA 兜底路由接管，不会到这里
    if request.path.startswith('/api/'):
        return fail('接口不存在', 404)
    return e


@app.errorhandler(405)
def _handle_405(e):
    if request.path.startswith('/api/'):
        return fail('请求方法不被允许', 405)
    return e


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
