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
    """设备下线时清理四个围栏状态字典,防止内存泄漏和 phone 复用时的状态污染。"""
    with _fence_lock:
        fence_device_inside.pop(phone, None)
        fence_device_pending.pop(phone, None)
        fence_device_enter_time.pop(phone, None)
        fence_device_dwell_alarmed.pop(phone, None)
    # 顺带清理设备ID缓存与报警去重时间戳,防止长期运行内存无界增长
    _devid_cache.pop(phone, None)
    with _alarm_last_ts_lock:
        for _k in [k for k in _alarm_last_ts if k[0] == phone]:
            _alarm_last_ts.pop(_k, None)


def next_serial():
    with _serial_lock:
        _serial[0] = (_serial[0] + 1) & 0xFFFF
        return _serial[0]
