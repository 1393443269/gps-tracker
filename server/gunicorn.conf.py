"""Gunicorn 生产配置 - Flask-SocketIO + gevent WebSocket

用法：
  gunicorn --config gunicorn.conf.py app:app
  （Dockerfile CMD 已配好，docker-compose up 直接生效）

架构说明：
  - workers=1：808 TCP 服务是进程单例（端口只能绑定一次），必须单 worker
  - GeventWebSocketWorker：支持 nginx → backend 的 WebSocket 升级（Socket.IO 需要）
  - worker_connections=1000：gevent 协程池，每个连接一个协程，无线程开销
"""

# ── Worker ───────────────────────────────────────────────────────────────────
workers = 1
worker_class = 'geventwebsocket.gunicorn.workers.GeventWebSocketWorker'
worker_connections = 1000
bind = '0.0.0.0:8080'
timeout = 600          # Socket.IO 长连接场景；gevent worker 不依赖此值做心跳
keepalive = 5          # HTTP keep-alive 秒数（与 Nginx upstream keepalive 配合）

# ── 日志 ─────────────────────────────────────────────────────────────────────
loglevel = 'info'
accesslog = '-'        # stdout → docker logs 可见
errorlog  = '-'        # stderr

# ── 启动钩子 ──────────────────────────────────────────────────────────────────
def post_fork(server, worker):
    """在 Gunicorn fork 出 worker 进程后初始化辅助服务。

    必须在 post_fork（而非模块级）启动，原因：
    - fork 前启动 TCP socket 会导致父子进程共享同一文件描述符，出现竞争
    - 父进程只加载 app 模块做预热，不开任何 I/O 线程
    """
    import threading
    from app import (init_db, start_batch_writer, start_tcp_server, start_mqtt_subscriber,
                     _setup_pg_partitions, start_partition_maintainer)
    from core.ingest import start_location_cleaner

    init_db()
    _setup_pg_partitions()       # PG：location_record 按月分区初始化（SQLite 跳过）
    start_partition_maintainer() # PG：每日预建分区的维护线程
    start_batch_writer()
    start_location_cleaner()     # 位置数据保留期清理线程（LOCATION_RETENTION_DAYS，默认 90 天）

    threading.Thread(target=start_tcp_server,      daemon=True, name='tcp-808').start()
    threading.Thread(target=start_mqtt_subscriber, daemon=True, name='mqtt-sub').start()


# ── 停机钩子：优雅停机，flush 未落库的位置队列，重启不丢轨迹 ──────────────────
# 选型说明：gunicorn 用 workers=1，master 收到 SIGTERM/SIGINT 后转发给 worker，
# gevent worker 在真正退出前会触发 worker_int。此处调用 ingest.graceful_shutdown
# (幂等) 置停止标志并把 _loc_queue 排空同步落库。走 gunicorn 钩子而非在 worker 里
# 自行抢占信号，可避免与 gunicorn 自身的优雅停机流程冲突。worker_exit 兜底，确保
# 任意退出路径都排空队列;两处都幂等，不会重复 flush。
def worker_int(worker):
    """worker 收到 INT/TERM（优雅停机）时：排空位置队列后再退出。"""
    try:
        from core.ingest import graceful_shutdown
        graceful_shutdown('gunicorn.worker_int')
    except Exception as e:
        worker.log.error("[优雅停机] worker_int flush 失败: %s", e)


def worker_exit(server, worker):
    """worker 退出时兜底 flush（幂等，若 worker_int 已 flush 则空转）。"""
    try:
        from core.ingest import graceful_shutdown
        graceful_shutdown('gunicorn.worker_exit')
    except Exception as e:
        worker.log.error("[优雅停机] worker_exit flush 失败: %s", e)
