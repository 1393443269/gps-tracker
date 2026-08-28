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

from core.db import db_query_one, db_exec, get_db, _db_lock, DB_BACKEND
import core.db as _dbmod
from core.state import (
    _loc_queue, _dev_latest, _dev_latest_lk,
    _devid_cache, _DEVID_CACHE_TTL,
)

log = logging.getLogger(__name__)

# PG 专用(批量写用 execute_values);sqlite 后端为 None,与原 getattr 兜底一致
_pg_extras = getattr(_dbmod, '_pg_extras', None)


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

def _batch_writer_loop():
    """后台线程:每 0.5 秒把队列里的位置记录 + 设备最新状态批量写入。"""
    while True:
        _time_mod.sleep(0.5)
        # 取出本轮位置记录,单批上限 2000 行,超出的留到下轮,避免单次事务过大
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
            log.critical("[批量写] 连续 3 次落库失败,%d 条位置记录已丢弃!请检查数据库连接。", len(rows))

_batch_writer_started = False
_batch_writer_start_lock = threading.Lock()

def start_batch_writer():
    """启动批量写线程,加锁防止多 worker 并发调用时双启。"""
    global _batch_writer_started
    with _batch_writer_start_lock:
        if _batch_writer_started:
            return
        _batch_writer_started = True
    threading.Thread(target=_batch_writer_loop, daemon=True).start()
    log.info("[批量写] 位置异步批量落库线程已启动")


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
