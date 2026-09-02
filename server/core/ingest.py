"""
后台数据接入层(第一批:纯 DB/state 依赖,不涉及 socketio 推送)。
从 app.py 原样抽出,函数/变量名与行为保持完全一致。

设计:单向依赖 core.db / core.state,不 import app(避免环)。log 用标准
logging.getLogger 自建,行为与 app.py 的 root logger 一致(同 basicConfig 格式)。

本批含:设备ID缓存/位置入队/批量写线程、PG 分区、resolve_phone、
报警规则查询、考勤记录。依赖 socketio 推送的函数(_emit_alarm/_sio_emit/
协议处理/TCP/MQTT)属后续子步骤,不在此文件。

gunicorn.conf.py post_fork 需要的 start_batch_writer/_setup_pg_partitions/
start_partition_maintainer 由 app.py re-export,保证 from app import ... 不变。
"""
import threading
import time as _time_mod
import queue as _queue
import logging
import math
import uuid
import os
import socket
import struct
import json as _json_mod
from datetime import datetime, timedelta

import protocol as p
import protocol_g618g as g618
import geo_resolve

from core.db import db_query_one, db_query, db_exec, get_db, _db_lock, DB_BACKEND, DB_PATH
import core.db as _dbmod
from core.state import (
    _loc_queue, _dev_latest, _dev_latest_lk,
    _devid_cache, _DEVID_CACHE_TTL,
    sessions, sessions_lock, next_serial,
    fence_device_inside, _fence_lock, FENCE_DEBOUNCE_N,
    fence_device_pending, fence_device_enter_time, fence_device_dwell_alarmed,
    _alarm_last_ts, _alarm_last_ts_lock, _ALARM_DEBOUNCE_SEC,
    _fence_cleanup,
)
from core.extensions import socketio
from common.geometry import _is_inside_fence

log = logging.getLogger(__name__)

# PG 专用(批量写用 execute_values);sqlite 后端为 None,与原 getattr 兜底一致
_pg_extras = getattr(_dbmod, '_pg_extras', None)

# 低电量自动缩短上报间隔：电量≤阈值时下发缩短间隔，恢复后切回正常间隔（阈值/间隔在此改）
LOW_BAT_THRESHOLD = 10      # 电量阈值 %
LOW_BAT_INTERVAL  = 5       # 低电量上报间隔(分钟)
NORMAL_INTERVAL   = 20      # 正常上报间隔(分钟)。20 分钟是"保持在线且最省电"的平衡点：
                            # 配 45 分钟在线窗口，能容忍偶尔丢报不误判离线。
# 保持在线的根因不是间隔而是"休眠"：G618G 默认静止 20~40 分钟进入休眠、彻底停报(协议 2.1/2.3)。
# 故上线时必须下发"关休眠"(0xCE18/02)，否则设备静止即离线。保持短连接(报完即断)最省电，
# 不切长连接(长连接每 4 分钟强制心跳、射频常开，最费电)。
DISABLE_SLEEP_ON_LOGIN = True   # 设备上线自动关闭休眠(保持在线的关键开关)


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
    """位置上报入队(不落库,立即返回),后台线程批量写。
    loc_row: location_record 的一整行值元组
    dev_state: (phone, last_lat, last_lng, last_speed, last_location_time, status, updated_at)
    """
    try:
        _loc_queue.put_nowait(loc_row)
    except _queue.Full:
        log.warning("[批量写] 位置队列已满(>100000),丢弃帧 phone=%s;请检查数据库写入是否正常", dev_state[0])
    with _dev_latest_lk:
        _dev_latest[dev_state[0]] = dev_state

# ── 优雅停机:全局停止标志 + flush 串行化锁 ──────────────────────────────────
# _stop_event 由信号处理器 / gunicorn 钩子置位,所有后台循环线程据此优雅退出。
# _flush_lock 串行化「批量写线程的一轮落库」与「停机 flush」,保证两者不会并发
# 重复写同一批数据(幂等协调)。
_stop_event  = threading.Event()
_flush_lock  = threading.Lock()

# 单批位置上限 2000 行,超出的留到下轮,避免单次事务过大
_BATCH_MAX = 2000


def _dump_deadletter(rows):
    """落库最终失败时,把这批位置数据序列化写到磁盘死信文件,避免永久丢失。
    路径沿用 db.py 的数据目录约定(DB_PATH 同级目录),文件名 deadletter_位置_时间戳.jsonl。
    每行一条位置记录(JSON 数组,列顺序同 INSERT),可事后用脚本读回补写。
    """
    if not rows:
        return
    try:
        _data_dir = os.path.dirname(DB_PATH) or '.'
        os.makedirs(_data_dir, exist_ok=True)
        _ts = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        _path = os.path.join(_data_dir, f'deadletter_位置_{_ts}.jsonl')
        with open(_path, 'w', encoding='utf-8') as _f:
            for _r in rows:
                _f.write(_json_mod.dumps(list(_r), ensure_ascii=False, default=str))
                _f.write('\n')
        log.critical("[批量写] %d 条位置记录落库失败,已写入死信文件: %s", len(rows), _path)
    except Exception as _e:
        # 死信落盘本身失败才真正丢数据——此时至少保留 critical 日志留证
        log.critical("[批量写] 死信落盘失败(%d 条位置记录彻底丢失!): %s", len(rows), _e)


def _do_db_write(rows, dev_snapshot):
    """把一批位置记录 + 设备最新状态写入数据库(含 3 次重试)。
    成功返回 True;连续 3 次失败则把 rows 写死信文件后返回 False。
    批量写线程与停机 flush 共用此函数,避免重复造轮子。"""
    if not rows and not dev_snapshot:
        return True
    _write_ok = False
    for _attempt in range(3):   # 最多重试 3 次,防止瞬时数据库抖动丢弃位置数据
        try:
            with _db_lock:
                conn = get_db()
                try:
                    if rows:
                        if DB_BACKEND == 'postgres':
                            # PG 下用 execute_values 单条 INSERT 批量写,性能远优于 executemany
                            # rows 每项列顺序:device_id,phone,lat,lng,altitude,speed,
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
        # 连续 3 次失败:位置数据落死信文件,而非直接丢弃
        _dump_deadletter(rows)
    return _write_ok


def _drain_and_write():
    """从队列取一批(≤_BATCH_MAX)位置 + 设备最新状态快照并落库。
    用 _flush_lock 串行化,防止批量写线程与停机 flush 并发重复写。
    返回 (本轮取出的行数, 是否还有剩余待处理数据)。"""
    with _flush_lock:
        rows = []
        while len(rows) < _BATCH_MAX:
            try:
                rows.append(_loc_queue.get_nowait())
            except _queue.Empty:
                break
        with _dev_latest_lk:
            dev_snapshot = list(_dev_latest.values())
            _dev_latest.clear()
        _do_db_write(rows, dev_snapshot)
    # 队列可能仍有超过 _BATCH_MAX 的残余,交由调用方决定是否继续
    more_pending = not _loc_queue.empty()
    return len(rows), more_pending


def flush_loc_queue():
    """停机时调用:把 _loc_queue 里剩余的位置全部同步落库(循环直至排空)。
    幂等:多次调用无害;与批量写线程共用 _flush_lock,不会并发重复写。"""
    total = 0
    # 循环排空(单批上限 2000,大队列需多轮)。设安全上限防极端情况死循环。
    for _ in range(100000):
        n, more = _drain_and_write()
        total += n
        if not more:
            break
    if total:
        log.info("[优雅停机] flush 已同步落库 %d 条剩余位置记录", total)
    return total


def _batch_writer_loop():
    """后台线程:每 0.5 秒把队列里的位置记录 + 设备最新状态批量写入。
    响应 _stop_event:置位后退出循环(退出前不落库,由停机 flush 统一排空)。"""
    while not _stop_event.is_set():
        # 用 Event.wait 代替 sleep,收到停止信号可立即唤醒退出
        if _stop_event.wait(0.5):
            break
        _drain_and_write()

_batch_writer_started = False
_batch_writer_start_lock = threading.Lock()

def start_batch_writer():
    """启动批量写线程,加锁防止多 worker 并发调用时双启。"""
    global _batch_writer_started
    with _batch_writer_start_lock:
        if _batch_writer_started:
            return
        _batch_writer_started = True
    threading.Thread(target=_batch_writer_loop, daemon=True, name='batch-writer').start()
    log.info("[批量写] 位置异步批量落库线程已启动")


# ── 优雅停机:停止标志置位 + flush 排空(信号处理器 / gunicorn 钩子共用)────────
_graceful_shutdown_done = False
_graceful_shutdown_lock = threading.Lock()

def graceful_shutdown(reason='signal'):
    """优雅停机总入口(幂等):
    1) 置 _stop_event,让批量写线程 / 清理线程退出各自循环;
    2) flush 把 _loc_queue 排空同步落库,保证重启不丢轨迹。
    可由信号处理器或 gunicorn worker_int/worker_exit 钩子调用。"""
    global _graceful_shutdown_done
    with _graceful_shutdown_lock:
        if _graceful_shutdown_done:
            return
        _graceful_shutdown_done = True
    log.info("[优雅停机] 收到停机信号(%s),开始排空位置队列...", reason)
    _stop_event.set()
    try:
        flush_loc_queue()
    except Exception as e:
        log.error("[优雅停机] flush 异常: %s", e)
    log.info("[优雅停机] 位置队列已排空,可安全退出")


_signal_registered = False

def install_signal_handlers():
    """注册 SIGTERM/SIGINT 处理器,收到信号 → graceful_shutdown → 退出。
    直接 `python app.py` 运行时用;gunicorn 部署下由 worker_int/worker_exit 钩子兜底,
    两条路径都走幂等的 graceful_shutdown,不会重复 flush,也不与 gunicorn 自身停机冲突。

    gevent monkey patch(app.py 顶部 patch_all(thread=True))后,标准 signal 模块的
    行为已被适配为在主 greenlet 中安全派发;优先用 gevent.signal_handler 以贴合
    gevent 事件循环,不可用时回退标准 signal。"""
    global _signal_registered
    if _signal_registered:
        return
    _signal_registered = True
    import signal as _signal

    def _handler(*_a):
        graceful_shutdown('signal')

    try:
        import gevent as _gevent
        # gevent 环境:用 gevent.signal_handler 在事件循环里安全处理,不打断 greenlet
        _gevent.signal_handler(_signal.SIGTERM, _handler)
        _gevent.signal_handler(_signal.SIGINT, _handler)
        log.info("[优雅停机] 已注册 gevent 信号处理器(SIGTERM/SIGINT)")
    except Exception:
        # 非 gevent(纯线程开发模式)或注册失败:回退标准 signal
        try:
            _signal.signal(_signal.SIGTERM, _handler)
            _signal.signal(_signal.SIGINT, _handler)
            log.info("[优雅停机] 已注册标准信号处理器(SIGTERM/SIGINT)")
        except Exception as _e:
            log.warning("[优雅停机] 信号处理器注册失败(可能非主线程): %s", _e)


# ── PG 专用:location_record 按月分区 ─────────────────────────────────────────

def _setup_pg_partitions():
    """PG 专用:确保 location_record 是按月分区表,并预建当前月 + 未来 3 个月的分区。
    - 表为空时自动重建为分区表;有数据时仅打印警告,不强制迁移。
    - SQLite 后端直接返回,无副作用。
    """
    if DB_BACKEND != 'postgres':
        return
    import datetime as _dt
    conn = get_db()
    try:
        # ① 查询 location_record 的表类型(p=分区表 r=普通表)
        cur = conn.execute(
            "SELECT c.relkind FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relname = ? AND n.nspname = 'public'",
            ('location_record',)
        )
        row = cur.fetchone()
        relkind = row['relkind'] if row else None

        if relkind == 'r':
            # 普通表——检查行数,空表才安全重建
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
                    "[PG分区] location_record 已有 %d 条数据,跳过自动重建。"
                    "如需分区,请手动迁移后 DROP TABLE location_record CASCADE 再重启。", cnt
                )
                return

        elif relkind == 'p':
            log.debug("[PG分区] location_record 已是分区表,跳过重建")
        else:
            log.warning("[PG分区] location_record 不存在或状态未知(relkind=%s),跳过", relkind)
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

        # ③ 父表建索引(PG 自动传播到所有子分区)
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
    """每 24 小时预建一次分区,保证月末跨月时子表已存在。"""
    import time as _t
    while True:
        _t.sleep(24 * 3600)   # 启动时 _setup_pg_partitions() 已建好,先睡一天
        try:
            _setup_pg_partitions()
        except Exception as _e:
            log.warning("[PG分区] 维护线程异常: %s", _e)

def start_partition_maintainer():
    """启动分区维护后台线程(幂等)。SQLite 后端调用无副作用。"""
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


# ── 位置数据保留期清理(防 location_record 无限膨胀)──────────────────────────
# LOCATION_RETENTION_DAYS: 位置记录保留天数,超期删除。0 表示不清理(默认 90 天)。
LOCATION_RETENTION_DAYS = int(os.environ.get('LOCATION_RETENTION_DAYS', '90'))
# 每批删除行数,分批删避免一次删太多长时间锁库
_CLEANUP_BATCH = 5000
# 检查间隔(秒):每小时醒一次,判断是否到达下一次清理时刻(可被停止标志提前唤醒)
_CLEANUP_CHECK_INTERVAL = 3600
# 两次清理之间的最小间隔(秒):每天清理一次
_CLEANUP_MIN_PERIOD = 24 * 3600


def _cleanup_locations_once():
    """删除超过保留期的位置记录,分批 DELETE 直到没有更多行。返回删除总数。
    gps_time 为 'YYYY-MM-DD HH:MM:SS' 字符串,直接用字符串边界比较(SQLite/PG 通用)。"""
    if LOCATION_RETENTION_DAYS <= 0:
        return 0
    cutoff = (datetime.now() - timedelta(days=LOCATION_RETENTION_DAYS)).strftime('%Y-%m-%d %H:%M:%S')
    total = 0
    # 分批删除:每批 _CLEANUP_BATCH 行,循环直到本批删不到行或收到停止信号
    while not _stop_event.is_set():
        try:
            with _db_lock if DB_BACKEND == 'sqlite' else _NullCtx():
                conn = get_db()
                try:
                    if DB_BACKEND == 'postgres':
                        # PG 不支持 DELETE ... LIMIT,用 ctid 子查询限制批量大小
                        cur = conn.execute(
                            "DELETE FROM location_record WHERE ctid IN "
                            "(SELECT ctid FROM location_record WHERE gps_time < ? LIMIT ?)",
                            (cutoff, _CLEANUP_BATCH))
                    else:
                        cur = conn.execute(
                            "DELETE FROM location_record WHERE rowid IN "
                            "(SELECT rowid FROM location_record WHERE gps_time < ? LIMIT ?)",
                            (cutoff, _CLEANUP_BATCH))
                    deleted = cur.rowcount if cur is not None and cur.rowcount is not None else 0
                    conn.commit()
                finally:
                    conn.close()
        except Exception as e:
            log.error("[位置清理] 删除失败: %s", e)
            break
        if not deleted or deleted < 0:
            break
        total += deleted
        if deleted < _CLEANUP_BATCH:
            break   # 本批不足一整批,说明已删完
    if total:
        log.info("[位置清理] 已删除 %d 条早于 %s 的位置记录(保留 %d 天)",
                 total, cutoff, LOCATION_RETENTION_DAYS)
    return total


class _NullCtx:
    """空上下文管理器:PG 后端各连接独立无需全局写锁,用它统一 with 语法。"""
    def __enter__(self): return self
    def __exit__(self, *a): return False


_location_cleaner_started = False
_location_cleaner_lock    = threading.Lock()

def _location_cleaner_loop():
    """后台清理线程:定期(每天)删除超保留期的位置记录。响应 _stop_event 优雅退出。"""
    _last_run = 0.0
    # 启动后先跑一次(仅在配置了保留期时),之后按天检查
    while not _stop_event.is_set():
        now = _time_mod.time()
        if now - _last_run >= _CLEANUP_MIN_PERIOD:
            try:
                _cleanup_locations_once()
            except Exception as e:
                log.warning("[位置清理] 清理线程异常: %s", e)
            _last_run = now
        # 每小时醒一次检查(Event.wait 收到停止信号立即唤醒退出)
        if _stop_event.wait(_CLEANUP_CHECK_INTERVAL):
            break

def start_location_cleaner():
    """启动位置清理后台线程(幂等)。LOCATION_RETENTION_DAYS<=0 时不启动。"""
    global _location_cleaner_started
    with _location_cleaner_lock:
        if _location_cleaner_started:
            return
        _location_cleaner_started = True
    if LOCATION_RETENTION_DAYS <= 0:
        log.info("[位置清理] LOCATION_RETENTION_DAYS=%d,不启用位置数据清理", LOCATION_RETENTION_DAYS)
        return
    threading.Thread(target=_location_cleaner_loop, daemon=True,
                     name='location-cleaner').start()
    log.info("[位置清理] 位置数据清理线程已启动(保留 %d 天)", LOCATION_RETENTION_DAYS)


# ── 设备标识解析 ───────────────────────────────────────────────────────────────

def resolve_phone(bcd_phone: str) -> str:
    """
    BCD 解出的 12 位手机号 → DB 中存储的完整设备标识。
    支持 IMEI(15 位)等长标识:若 DB 存的 phone 以 bcd_phone 结尾则命中。
    """
    row = db_query_one("SELECT phone FROM device WHERE phone=?", (bcd_phone,))
    if row:
        return row['phone']
    row = db_query_one(
        "SELECT phone FROM device WHERE length(phone) > 12 AND phone LIKE ?",
        (f'%{bcd_phone}',)
    )
    return row['phone'] if row else bcd_phone


# ── 报警规则 / 考勤(纯 DB;_emit_alarm 因依赖 socketio 推送留在后续子步骤)──────

def _get_alarm_rule(alarm_type, org_id=1):
    """返回该报警类型在指定组织下的规则 dict;先查本组织,再回退根组织,最后默认。"""
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


def insert_sensor_data(phone, sensor_type, value=None, value_text='', unit='', org_id=None):
    """程序内落库一条传感器数据(供以后协议解析调用)。
    与 app.py 的 sensor_data 表对应。org_id 不传则按设备归属自动带出(取不到默认 1)。"""
    try:
        if org_id is None:
            dev = db_query_one("SELECT org_id FROM device WHERE phone=?", (phone,))
            org_id = (dev.get('org_id') if dev else 1) or 1
        db_exec(
            "INSERT INTO sensor_data (device_phone,sensor_type,value,value_text,unit,org_id) "
            "VALUES (?,?,?,?,?,?)",
            (phone, sensor_type, value, value_text or '', unit or '', org_id)
        )
    except Exception as e:
        log.error("[传感器] 落库失败: %s", e)


# ── 客户链解析 / Socket.IO 推送 ───────────────────────────────────────────────

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
    # 临时诊断：打印各房间当前在线客户端数，定位"推了但没人收"的问题
    try:
        _mgr = socketio.server.manager
        def _rc(rm):
            try:
                return len(set(_mgr.get_participants('/', rm)))
            except Exception:
                return -1
        log.info("[WS诊断] event=%s phone=%s 房间在线: org_%s=%d broadcast=%d",
                 event, phone, org_id, _rc(f'org_{org_id}'), _rc('broadcast'))
    except Exception as _e:
        log.info("[WS诊断] 统计失败: %s", _e)
    socketio.emit(event, data, room=f'org_{org_id}')   # 该组织管理员
    socketio.emit(event, data, room='broadcast')        # 超级管理员
    # 归属客户及其上级客户
    if cid:
        for c in _customer_ancestors(cid):
            socketio.emit(event, data, room=f'cust_{c}')


# ── 电子围栏：穿越检测 ────────────────────────────────────────────────────────

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

    # 查询该设备关联的所有围栏。
    # 热路径优化:改走 fence_device 关联表(idx_fence_device_phone 等值索引),
    # 取代原 "devices LIKE '%phone%'" 前导通配全表扫。fence_device 由 app.py
    # 各围栏写入点与 devices 字段双写同步,故此处等价且不再全表扫。
    fences = db_query(
        """SELECT gf.id, gf.name, gf.fence_type, gf.lat, gf.lng, gf.radius, gf.coordinates,
                  COALESCE(gf.alarm_enter,1)   alarm_enter,
                  COALESCE(gf.alarm_exit,1)    alarm_exit,
                  COALESCE(gf.alarm_dwell,0)   alarm_dwell,
                  COALESCE(gf.speed_limit,0)   speed_limit,
                  COALESCE(gf.valid_start,'')  valid_start,
                  COALESCE(gf.valid_end,'')    valid_end
           FROM geo_fence gf
           JOIN fence_device fd ON fd.fence_id = gf.id
           WHERE fd.phone = ?""",
        (phone,)
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
            db_exec("UPDATE platform_setting SET sms_used=sms_used+1 WHERE org_id=? AND sms_enabled=1", (org_id,))
        except Exception:
            pass


def handle_register(sock, phone, serial, body):
    info      = p.parse_register_body(body)
    now       = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # 清洗定长字段的 NUL 补齐字节：标准 808 注册报文里终端制造商/型号/ID 是定长字段，
    # 位数不足时后补 0x00（协议规定）。天禧 LT115 等设备的这些字段就带 NUL 尾巴，
    # 直接入库会触发 PG "string literal cannot contain NUL" 报错、注册失败、设备死循环重连。
    # 故此处统一去掉 NUL 及首尾空白，再落库。
    for _k in ('manufacturer', 'terminal_model', 'terminal_id', 'plate_no'):
        _v = info.get(_k)
        if isinstance(_v, str):
            info[_k] = _v.replace('\x00', '').strip()

    # 若 plate_no 字段携带了完整 IMEI（15 位纯数字），以 IMEI 作为设备标识
    plate_no_raw = info.get('plate_no', '') or ''
    if len(plate_no_raw) == 15 and plate_no_raw.isdigit():
        canonical_phone = plate_no_raw      # 用完整 IMEI 作为 phone
        plate_no_store  = ''               # plate_no 字段留空
    else:
        canonical_phone = resolve_phone(phone)
        plate_no_store  = plate_no_raw

    existing = db_query_one("SELECT id, auth_code FROM device WHERE phone=?", (canonical_phone,))
    if existing:
        # 关键:已存在设备保留原 auth_code,不重新生成。真实 808 设备把首次注册拿到的
        # auth_code 持久化在设备侧,若重连时又发注册而平台刷新了 auth_code,设备存的旧码
        # 会与库里对不上,导致后续鉴权永久失败、设备死循环重连。仅原码为空时才补一个。
        auth_code = existing.get('auth_code') or uuid.uuid4().hex[:8].upper()
        db_exec(
            "UPDATE device SET manufacturer=?,terminal_model=?,terminal_id=?,"
            "plate_no=?,plate_color=?,auth_code=?,updated_at=? WHERE phone=?",
            (info.get('manufacturer'), info.get('terminal_model'), info.get('terminal_id'),
             plate_no_store, info.get('plate_color'), auth_code, now, canonical_phone)
        )
    else:
        auth_code = uuid.uuid4().hex[:8].upper()   # 首次注册才生成新鉴权码
        # org_id 显式写 1（根组织）；管理员可在设备管理界面手动迁移到子组织
        db_exec(
            "INSERT INTO device (phone,manufacturer,terminal_model,terminal_id,"
            "plate_no,plate_color,auth_code,status,org_id) VALUES (?,?,?,?,?,?,?,0,1)",
            (canonical_phone, info.get('manufacturer'), info.get('terminal_model'),
             info.get('terminal_id'), plate_no_store, info.get('plate_color'), auth_code)
        )

    # 关键:按 canonical(device 表主键 = 下发时用的 phone)登记会话,否则用 IMEI 注册的设备
    # 会话 key 是报文头 BCD 号(≤12位)、而下发查的是 15 位 IMEI,导致"在线却下发不到"。
    if canonical_phone:
        with sessions_lock:
            sessions[canonical_phone] = sock

    resp = p.build_register_resp(phone, next_serial(), serial, 0, auth_code)
    sock.sendall(resp)
    log.info("[808] 终端注册: phone=%s canonical=%s auth_code=%s", phone, canonical_phone, auth_code)


def handle_auth(sock, phone, serial, body):
    # 鉴权体格式存在方言差异，兼容两种：
    #   ① 标准 JT808：body[0]=长度字节，其后为鉴权码；
    #   ② 天禧 LT115 等：无长度字节，整个 body 就是鉴权码(协议文档 2.7 定义为 String 无长度)。
    # 若只按①解析，天禧设备的鉴权码首字符会被误当长度吃掉(如 DD5FAFBC→D5FAFBC)导致鉴权永久失败。
    # 故生成两个候选：带长度前缀解读 + 整体解读，任一匹配库中鉴权码即通过。
    _cand = set()
    if body:
        _whole = body.decode('ascii', errors='replace').strip('\x00').strip()
        if _whole:
            _cand.add(_whole)                       # ② 整体即鉴权码
        code_len = body[0]
        if 0 < code_len <= len(body) - 1:
            _pref = body[1:1 + code_len].decode('ascii', errors='replace').strip('\x00').strip()
            if _pref:
                _cand.add(_pref)                    # ① 带长度前缀
    auth_code = _whole if body else ''              # 日志展示用整体解读

    canonical = resolve_phone(phone)
    row = db_query_one("SELECT id, auth_code, status FROM device WHERE phone=?", (canonical,))
    # 严格比对鉴权码(两种解读任一命中即可)，不允许万能 DEFAULT 绕过
    auth_ok = bool(row) and row.get('auth_code') in _cand
    if auth_ok:
        # 先应答设备（不被数据库写锁阻塞，避免高并发下设备超时断连），再异步更新状态
        sock.sendall(p.build_generic_resp(phone, next_serial(), serial, 0x0102, 0))
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        db_exec("UPDATE device SET status=1, online_time=? WHERE phone=?", (now, canonical))
        # 按 canonical 登记会话(设备可能只发鉴权不发注册),保证指令下发能按 device 表 phone 命中
        if canonical:
            with sessions_lock:
                sessions[canonical] = sock
        log.info("[808] 鉴权成功: phone=%s", canonical)
    else:
        log.warning("[808] 鉴权失败断开连接 phone=%s auth=%s", canonical, auth_code)
        # 鉴权失败：从 sessions 中删除，防止未鉴权设备被下发指令
        # identity-checked pop：仅当存的还是本条连接才清除，避免误删重连的新连接
        with sessions_lock:
            if sessions.get(canonical) is sock:
                sessions.pop(canonical, None)
        sock.sendall(p.build_generic_resp(phone, next_serial(), serial, 0x0102, 1))
        return 'close'


def handle_heartbeat(sock, phone, serial):
    canonical = resolve_phone(phone)
    # 先回心跳应答再更新状态，避免应答被数据库写锁阻塞
    sock.sendall(p.build_generic_resp(phone, next_serial(), serial, 0x0002, 0))
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    db_exec("UPDATE device SET status=1, online_time=? WHERE phone=?", (now, canonical))
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

    # 从位置报文附加字段拿到设备真实 IMEI(0xF6)/ICCID(0xF1)：设备注册头里的 phone 可能只是
    # 终端ID(非IMEI)，真实身份走位置附加字段上报。只在值有效且与库中不同时更新(避免每帧写库)，
    # 并自动登记/更新 SIM 卡表(iccid 唯一，存在则仅关联设备，不覆盖人工维护的运营商/套餐等)。
    _iccid = loc.get('iccid')
    _imei  = loc.get('imei')
    if _iccid or _imei:
        try:
            _drow = db_query_one("SELECT imei, iccid FROM device WHERE phone=?", (canonical,))
            _cur_imei  = (_drow.get('imei')  if _drow else '') or ''
            _cur_iccid = (_drow.get('iccid') if _drow else '') or ''
            if _imei and _imei != _cur_imei:
                db_exec("UPDATE device SET imei=? WHERE phone=?", (_imei, canonical))
            if _iccid and _iccid != _cur_iccid:
                db_exec("UPDATE device SET iccid=? WHERE phone=?", (_iccid, canonical))
                # 同步登记/更新 sim_card：iccid 已存在则更新绑定设备+IMEI，否则新增。
                # IMEI 一并写入 sim_card，使 SIM 卡管理页 IMEI 列能显示设备真实 IMEI。
                _sim = db_query_one("SELECT id FROM sim_card WHERE iccid=?", (_iccid,))
                if _sim:
                    db_exec("UPDATE sim_card SET device_phone=?, imei=? WHERE iccid=?",
                            (canonical, _imei or '', _iccid))
                else:
                    db_exec("INSERT INTO sim_card (iccid, device_phone, imei, remark) VALUES (?,?,?,?)",
                            (_iccid, canonical, _imei or '', '设备自动上报'))
            elif _imei and _iccid:
                # ICCID 未变但 IMEI 刚解析到：补写 sim_card.imei(覆盖之前漏同步的空值)
                db_exec("UPDATE sim_card SET imei=? WHERE iccid=? AND (imei IS NULL OR imei='')",
                        (_imei, _iccid))
        except Exception as e:
            log.warning("[808] ICCID/IMEI 落库失败 phone=%s err=%s", canonical, e)

    # 位置记录 + 设备最新状态：异步批量落库（削减写锁争用，大幅降低上报延迟）
    status = 2 if alarm_flag else 1
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    enqueue_location(
        (device_id, canonical, lat, lng, altitude, speed, direction,
         alarm_flag, loc['status_flag'], mileage, gps_time),
        (canonical, lat, lng, speed, gps_time, status, now)
    )

    # 先应答再做后续处理：报警写库、角色查询、围栏检测（check_fence_crossing 内部也写库）
    # 都不在设备关心的关键路径上。高并发+SQLite 全局写锁时，把应答压在这些同步写库之后
    # 会拖慢定位帧应答，触发设备超时断连。位置已异步入队，此处立即应答不丢数据。
    sock.sendall(p.build_generic_resp(phone, next_serial(), serial, 0x0200, 0))

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


def _flush_pending_commands(conn, imei):
    """设备上线时补发待发队列里的指令。
    payload 已在入队时构造好(payload_hex)，此处直接发送，不依赖 app 层指令构造函数，
    避免 ingest 反向 import app 造成循环依赖。G618G 短连接需连发两次(间隔<20ms)。
    单条失败不影响其余；发送异常整体跳过（连接可能刚断），指令留在队列等下次上线。"""
    try:
        rows = db_query(
            "SELECT id, payload_hex FROM pending_command WHERE phone=? AND status='pending' ORDER BY id",
            (imei,))
    except Exception as e:
        log.warning("[G618G] 待发队列查询失败 imei=%s err=%s", imei, e)
        return
    if not rows:
        return
    import time as _t
    sent = 0
    for row in rows:
        try:
            payload = bytes.fromhex(row['payload_hex'])
            conn.sendall(payload)
            _t.sleep(0.01)
            conn.sendall(payload)
            db_exec("UPDATE pending_command SET status='sent', sent_at=? WHERE id=?",
                    (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), row['id']))
            sent += 1
        except Exception as e:
            log.warning("[G618G] 待发指令补发失败 imei=%s id=%s err=%s", imei, row['id'], e)
            break   # 发送通道异常，剩余留到下次上线
    if sent:
        log.info("[G618G] 补发待发指令 imei=%s 成功 %d 条", imei, sent)


def handle_g618g_frame(conn, frame, phone_holder):
    """处理一个 G618G 上报帧：解析并落库。phone_holder 是 [phone] 单元素列表（可变引用）。"""
    r = g618.parse(frame)
    typ = r.get('type')
    # 注意：不按 checksum_ok 拦截。协议 V2.0 规定「通用版本设备不强求校验，可忽略此部分，
    # 下行指令随意一个字节即可」——设备可能根本不认真算校验字节，若强制丢弃校验失败帧，
    # 会误杀这类设备的全部上报数据。防幽灵设备改由注册段的 IMEI 合法性校验负责（见下）。
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    if typ == 'register':          # 0xF0 建立连接：记录 IMEI、回复 F1、登记会话
        imei = r.get('imei')
        # IMEI 合法性校验：G618G 上报 IMEI 为 15 位数字（协议示例 869465050010011）。
        # 并包错位/误码拼出的假注册帧 IMEI 多为异常值，据此拦截自动建档，避免幽灵设备灌库。
        if not (imei and imei.isdigit() and len(imei) == 15):
            log.warning("[G618G] 非法 IMEI 拒绝建档: %r frame=%s", imei, frame[:16].hex())
            return typ
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
        # 补发待发指令：设备离线时排队的指令，趁这次上线窗口发出去
        _flush_pending_commands(conn, imei)
        # 离线判定改为"关机报警驱动"：设备保持出厂默认(短连接+休眠)最省电，平台不再下发
        # 关休眠/调频去干预设备。设备一旦有任何上报(注册/心跳/定位)即视为开机在线，
        # 收到关机报警(0x21)才判离线。注册分支上面已置 status=1，此处无需再下发保活指令。

    elif typ == 'heartbeat':       # 0xF9 心跳：回复保持连接 + 更新电量
        conn.sendall(g618.build_heartbeat_reply())
        imei = phone_holder[0]
        if imei:
            # 电量存两处：device.last_battery 存实时值(供列表/详情直接读)，
            # sensor_data 存历史(供以后画电量曲线)。battery_pct 可能为 None(短心跳payload)，判空再存。
            battery_pct = r.get('battery_pct')
            if battery_pct is not None:
                db_exec("UPDATE device SET last_battery=?, last_battery_time=?, updated_at=? WHERE phone=?",
                        (battery_pct, now, now, imei))
                insert_sensor_data(imei, 'battery', value=battery_pct, unit='%')
                # 低电量自动缩短上报间隔：电量刚更新、conn 正连着，可直接下发。
                # 只在状态变化时下发一次，靠 device.low_bat_mode 标志防重复（幂等）。
                _bat_row = db_query_one("SELECT low_bat_mode FROM device WHERE phone=?", (imei,))
                _low_mode = (_bat_row['low_bat_mode'] if _bat_row else 0) or 0
                if battery_pct <= LOW_BAT_THRESHOLD and _low_mode != 1:
                    # 正常→低电量：下发缩短间隔
                    try:
                        _freq_payload = g618.build_set_freq(LOW_BAT_INTERVAL)
                        conn.sendall(_freq_payload)
                        _time_mod.sleep(0.01)   # G618G 短连接连发两次(间隔<20ms)
                        conn.sendall(_freq_payload)
                        db_exec("UPDATE device SET low_bat_mode=1 WHERE phone=?", (imei,))
                        log.info("[G618G] 低电量%d%%,切换上报间隔为%d分钟 imei=%s",
                                 battery_pct, LOW_BAT_INTERVAL, imei)
                    except Exception as e:
                        # 连接刚断等导致下发失败：只告警不中断心跳（标志未置位，下次心跳会重试）
                        log.warning("[G618G] 低电量切换间隔下发失败 imei=%s err=%s", imei, e)
                elif battery_pct > LOW_BAT_THRESHOLD and _low_mode == 1:
                    # 低电量→恢复:切回正常间隔
                    try:
                        _freq_payload = g618.build_set_freq(NORMAL_INTERVAL)
                        conn.sendall(_freq_payload)
                        _time_mod.sleep(0.01)   # G618G 短连接连发两次(间隔<20ms)
                        conn.sendall(_freq_payload)
                        db_exec("UPDATE device SET low_bat_mode=0 WHERE phone=?", (imei,))
                        log.info("[G618G] 电量恢复%d%%,切回正常上报间隔为%d分钟 imei=%s",
                                 battery_pct, NORMAL_INTERVAL, imei)
                    except Exception as e:
                        log.warning("[G618G] 恢复正常间隔下发失败 imei=%s err=%s", imei, e)
            else:
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
            # WebSocket 实时推送位置到前端地图（与 808/MQTT 路径一致，缺此步则设备有坐标却不上图）
            role_row = db_query_one(
                "SELECT r.name AS role_name, r.color AS role_color, r.icon_type AS role_icon "
                "FROM device LEFT JOIN device_role r ON device.role_id = r.id WHERE device.phone=?",
                (imei,))
            _sio_emit('location_update', {
                'phone':     imei,
                'lat':       r['lat'],
                'lng':       r['lng'],
                'speed':     0,
                'direction': 0,
                'altitude':  0,
                'alarm':     False,
                'alarmFlag': 0,
                'time':      gps_time,
                'roleName':  role_row.get('role_name')  if role_row else None,
                'roleColor': role_row.get('role_color') if role_row else None,
                'roleIcon':  role_row.get('role_icon')  if role_row else None,
            }, imei)
            log.info("[G618G] 位置上报并推送 phone=%s lat=%.6f lng=%.6f", imei, r['lat'], r['lng'])

    elif typ in ('alarm', 'alarm2'):   # 0x02/0x21 报警
        imei = phone_holder[0]
        if imei:
            did = _get_device_id(imei)
            _alarm_list = r.get('alarms') or ['报警']
            desc = '、'.join(_alarm_list)
            db_exec("INSERT INTO alarm_record (device_id,phone,alarm_type,alarm_desc,alarm_time,status) "
                    "VALUES (?,?,?,?,?,0)", (did, imei, r.get('warn_bits', 0), desc, now))
            has_sos = any('SOS' in a for a in _alarm_list)
            _emit_alarm('alarm', {'phone': imei, 'alarmDesc': desc, 'time': now},
                        imei, 0 if has_sos else 99)
            # 关机报警驱动离线：收到关机类报警(主动/低电/充电关机，0x21)即判设备离线。
            # 这是"设备保持出厂默认最省电、平台按信号判离线"方案的核心：平时有上报即在线，
            # 唯有收到明确的关机信号才置离线。注意此方案下没电耗尽/失联/死机可能无关机报警上报，
            # 那几种情况平台仍显示在线(已与用户确认，属纯信号驱动的已知取舍)。
            _is_shutdown = any(('关机' in a) for a in _alarm_list)
            if _is_shutdown:
                db_exec("UPDATE device SET status=0, offline_time=?, updated_at=? WHERE phone=?",
                        (now, now, imei))
                log.info("[G618G] 收到关机报警(%s)判离线 imei=%s", desc, imei)
                # 实时推送离线状态到前端，使地图/列表即时反映
                _sio_emit('device_offline', {'phone': imei, 'time': now, 'reason': desc}, imei)

    elif typ == 'iccid':           # 0xF3 SIM ICCID
        imei = phone_holder[0]
        if imei:
            iccid = r['iccid']
            db_exec("UPDATE device SET remark=? WHERE phone=?", ('ICCID:'+iccid, imei))
            # 打通 SIM 卡管理：设备上报的 ICCID 自动登记/关联到 sim_card 表，
            # 使 SIM 卡管理页能看到该卡及其绑定设备。iccid 唯一，存在则仅更新绑定设备，
            # 不覆盖运营商/套餐/到期等人工维护字段。跨库(SQLite/PG)用先查后写，避免 UPSERT 方言差异。
            try:
                exist = db_query_one("SELECT id FROM sim_card WHERE iccid=?", (iccid,))
                if exist:
                    db_exec("UPDATE sim_card SET device_phone=? WHERE iccid=?", (imei, iccid))
                else:
                    db_exec("INSERT INTO sim_card (iccid, device_phone, remark) VALUES (?,?,?)",
                            (iccid, imei, '设备自动上报'))
            except Exception as e:
                log.warning("[G618G] SIM卡登记失败 imei=%s iccid=%s err=%s", imei, iccid, e)

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


# ── TCP 连接处理线程 ────────────────────────────────────────────────────────────

# 808 TCP 服务监听端口(与 app.py 原定义一致)。start_tcp_server 绑定此端口。
TCP_PORT = 9090
# TCP 读空闲超时(秒)。默认 300s——低功耗/人员定位设备静止态心跳常配 3~5 分钟甚至更长,
# 旧值 90s 会误杀长心跳设备致其频繁掉线重连。可用环境变量 TCP_CLIENT_TIMEOUT 按现场设备调整
# (建议设为设备最大心跳周期 × 2~3)。
TCP_CLIENT_TIMEOUT = int(os.environ.get('TCP_CLIENT_TIMEOUT', '300'))

def handle_client(conn, addr):
    log.info("[TCP] 新连接: %s:%d", addr[0], addr[1])
    buf   = bytearray()
    phone = None
    proto = None   # None=未定, '808', 'g618'
    conn.settimeout(TCP_CLIENT_TIMEOUT)

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
                except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError) as e:
                    # 客户端超时/主动断连导致的写回失败，属正常现象，不刷 ERROR 堆栈
                    log.debug("[808] 客户端断连(处理中) ID=0x%04X addr=%s: %s", msg_id, addr, e)
                    break
                except Exception as e:
                    log.error("[808] 处理消息异常 ID=0x%04X: %s", msg_id, e, exc_info=True)

            if _should_close:
                break

    except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError) as e:
        # 设备断连是常态（信号弱、超时重连），降级为 DEBUG，避免高并发下日志风暴
        log.debug("[808] 连接断开: %s %s", addr, e)
    except Exception as e:
        log.error("[808] 连接异常: %s %s", addr, e)
    finally:
        conn.close()
        if phone:
            # 清理所有指向本连接的会话 key(可能存了两份:报文头 BCD 号 + canonical/IMEI),
            # identity-checked——只删值仍是本 conn 的,避免误删该设备重连后的新连接。
            with sessions_lock:
                for _k in [k for k, v in sessions.items() if v is conn]:
                    sessions.pop(_k, None)
            _fence_cleanup(phone)   # 清理围栏状态，防内存泄漏和 phone 复用时状态污染
            # 关机报警驱动离线方案：短连接设备(G618G等)上报完即断开 TCP，这是正常低功耗行为，
            # 【不能】据此判离线，否则设备每次上报完就被置离线、离线状态形同虚设。
            # 设备离线只由「收到关机报警(0x21)」置位(见 alarm2 分支)，TCP 断开只记日志、不改 status。
        log.info("[808] 连接断开: %s phone=%s", addr, phone)


# 最大并发 TCP 连接数，防止连接/线程耗尽 OOM。
# 通过环境变量 TCP_MAX_CONN 配置（默认 1200，给 1000 台设备留 20% 冗余，覆盖断连重连的瞬时叠加）。
# gevent 模式下每连接是协程（开销极小），可放大到数千；纯线程模式（gevent 未装）建议不超过 800。
TCP_MAX_CONN = int(os.environ.get('TCP_MAX_CONN', '1200'))
_TCP_CONN_SEM = threading.Semaphore(TCP_MAX_CONN)

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
                log.warning("[808] 连接数已达上限(%d)，拒绝 %s", TCP_MAX_CONN, addr)
                conn.close()
                continue
            t = threading.Thread(target=_handle_client_guarded, args=(conn, addr), daemon=True)
            t.start()
        except Exception as e:
            log.error("[808] accept 错误: %s", e)


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
            # MQTT 设备无 808/G618G 那样的注册帧,若一律丢弃则纯 MQTT 设备永远无法自助上线、
            # 数据静默丢失。与 G618G 自动建档一致:未知设备自动建档(org_id=1,来源标记 MQTT),
            # 管理员可在设备管理界面迁移组织。broker 已启用密码认证,能连入的即可信来源。
            _now0 = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            db_exec("INSERT INTO device (phone,name,manufacturer,terminal_model,status,"
                    "org_id,lifecycle,created_at,updated_at) VALUES (?,?,?,?,1,1,1,?,?)",
                    (phone, 'MQTT-' + phone[-6:], 'MQTT', 'MQTT', _now0, _now0))
            log.info("[MQTT] 未知设备 phone=%s 已自动建档", phone)
            device = db_query_one("SELECT id FROM device WHERE phone=?", (phone,))
            if not device:
                log.warning("[MQTT] 设备 phone=%s 自动建档后仍查不到,跳过", phone)
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
