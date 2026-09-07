"""
进程内共享状态:会话表、流水号、围栏状态字典、位置队列与缓存。
从 app.py 原样抽出,变量/函数名与行为保持完全一致。

设计:本模块是最底层的中立容器,只依赖标准库(threading/queue),
零依赖 app.py 与 core.db,专门用于打破「TCP 线程 ↔ REST 路由」对
sessions / next_serial 等共享数据的双向耦合——两侧都单向 import 本模块。

注意:重业务函数(check_fence_crossing / _get_device_id / enqueue_location /
_batch_writer_loop 等)仍留在 app.py,它们通过 import 访问这里的容器。
"""
import threading
import queue as _queue

# ── 高频位置写入的异步批量落库(削减 SQLite 全局写锁争用)────────────────────────
_loc_queue     = _queue.Queue(maxsize=100000)  # 有界队列防 OOM;满时丢弃最新帧并告警
_dev_latest    = {}                 # phone -> 设备最新状态(多次上报只落最后一次)
_dev_latest_lk = threading.Lock()
_devid_cache: dict = {}            # phone → (device_id, expire_ts);10 分钟 TTL
_DEVID_CACHE_TTL = 600
_alarm_last_ts: dict = {}          # (phone, alarm_type) → last_alarm_unix_ts
_alarm_last_ts_lock = threading.Lock()
_ALARM_DEBOUNCE_SEC = 60           # 同类型报警至少间隔 60 秒

# ── 会话管理 ───────────────────────────────────────────────────────────────────
sessions      = {}        # phone → socket
sessions_lock = threading.Lock()
_serial       = [0]
_serial_lock  = threading.Lock()

# 围栏状态:记录每台设备当前"在哪些围栏内",用于检测穿越
# phone → set of fence_id
fence_device_inside: dict = {}
_fence_lock = threading.Lock()   # 保护下面四个围栏状态字典的并发读写

# ── P0: 防抖 ─────────────────────────────────────────────────────────────────
# 连续读数一致 FENCE_DEBOUNCE_N 次才确认状态切换,避免边界抖动重复告警
FENCE_DEBOUNCE_N = 3
fence_device_pending: dict = {}        # phone → {fence_id: (last_state:bool|None, count:int)}

# ── P1: 停留超时 ──────────────────────────────────────────────────────────────
fence_device_enter_time:    dict = {}  # phone → {fence_id: datetime} 进入时刻
fence_device_dwell_alarmed: dict = {}  # phone → set of fence_id(已触发滞留告警,离开时清除)


def _fence_cleanup(phone):
    """连接断开时的轻量清理。

    重要:绝不清围栏进出状态(fence_device_inside / enter_time / dwell / 报警去重时间戳)。
    这些是【跨连接的业务状态】,必须保留——否则短连接设备(报完即断、几十秒一轮,如
    LT115/G618)每次重连都会丢失"已在围栏内"的记忆,把静止设备反复当成"新进入",
    每轮刷一条进入报警(这是之前刷屏的真因);同时报警60秒去重也会被清而失效。

    连接级临时状态很小,保留不会造成实际内存压力;真正的内存回收由离线扫描/TTL 负责。
    仅清理 fence_device_pending(防抖计数,连接内累积的中间态,清掉不影响进出判定的正确性)。
    """
    with _fence_lock:
        fence_device_pending.pop(phone, None)


def next_serial():
    with _serial_lock:
        _serial[0] = (_serial[0] + 1) & 0xFFFF
        return _serial[0]
