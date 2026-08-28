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
timeout = 120          # 超过此秒无响应则 worker 被 kill 并重启
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
    from app import init_db, start_batch_writer, start_tcp_server, start_mqtt_subscriber

    init_db()
    start_batch_writer()

    threading.Thread(target=start_tcp_server,      daemon=True, name='tcp-808').start()
    threading.Thread(target=start_mqtt_subscriber, daemon=True, name='mqtt-sub').start()
