"""
框架单例:Flask app、SocketIO、CORS。
从 app.py 抽出,作为 app.py 与后台接入层(core/ingest 等)共同依赖的中立源头,
用于打破「后台线程要用 socketio ↔ socketio 定义在 app.py」的循环 import。

⚠️ gevent monkey patch 必须在任何 socket/ssl 相关 import 之前执行。
本模块极可能是最早被导入的模块之一(app.py 顶部即 import 它),故在此处
再做一次 patch_all(幂等,重复调用无害),确保无论导入顺序如何,patch 都最先完成。
app.py 顶部保留其自身的 patch 作为双保险。
"""
# ── gevent 猴子补丁(必须在所有其他 import 之前执行)────────────────────────────
try:
    from gevent import monkey as _gmonkey
    if not _gmonkey.is_module_patched('socket'):
        _gmonkey.patch_all(thread=True, socket=True, ssl=True)
    _GEVENT_AVAILABLE = True
except ImportError:
    _GEVENT_AVAILABLE = False

from flask import Flask
from flask_socketio import SocketIO
from flask_cors import CORS

# ── 应用初始化 ─────────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)
_SIO_MODE = 'gevent' if _GEVENT_AVAILABLE else 'threading'
socketio = SocketIO(app, cors_allowed_origins='*', async_mode=_SIO_MODE,
                    logger=False, engineio_logger=False)
