"""
数据库后端抽象层(SQLite / PostgreSQL 双后端)。
从 app.py 原样抽出,函数/类/全局符号名与行为保持完全一致。

设计:本模块零依赖 app.py(单向依赖),自行读取环境变量与探测 gevent,
逻辑与 app.py 原实现完全相同。所有 SQL 仍用 ? 占位符;PG 后端自动转 %s。

导出供业务层使用的符号:
  DB_BACKEND, _db_lock, get_db, db_exec, db_query, db_query_one, db_scalar,
  _to_pg, _pg_dialect, _split_sql, _ConnWrapper
PG 专用(批量写线程用):_pg_extras, _get_pg_pool
"""
import os
import sqlite3
import threading
import re as _re

# gevent 是否可用:与 app.py 顶部 monkey patch 的判定保持一致。
# 生产由 gunicorn gevent worker 起,app.py 已在最顶部 patch_all;此处仅探测标志,不重复 patch。
try:
    import gevent as _gevent_probe  # noqa: F401
    _GEVENT_AVAILABLE = True
except ImportError:
    _GEVENT_AVAILABLE = False

# 数据目录/DB 路径:与 app.py 原逻辑一致(BASE_DIR 取本文件上级 = server/)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH  = os.environ.get('DB_PATH', os.path.join(BASE_DIR, 'tracker.db'))
_db_dir = os.path.dirname(DB_PATH)
if _db_dir:
    os.makedirs(_db_dir, exist_ok=True)

# ── 数据库后端抽象层 ─────────────────────────────────────────────────────────
# 通过环境变量 DB_BACKEND 切换:'sqlite'(默认)或 'postgres'。
# 业务代码统一用 ? 占位符;postgres 后端会自动把 ? 转成 %s。
DB_BACKEND = os.environ.get('DB_BACKEND', 'sqlite').lower()

if DB_BACKEND == 'postgres':
    import psycopg2 as _pg
    import psycopg2.extras as _pg_extras
    _PG_DSN = os.environ.get('DATABASE_URL',
                             'postgresql://postgres:postgres@127.0.0.1:5432/gps')

    # 幂等建表/加列时 PG 会抛"已存在"类错误。这些错误有稳定的 SQLSTATE(与 locale 无关),
    # 用于 executescript 逐条执行时忽略"已存在",而不依赖被本地化的英文错误消息。
    #   42P07 duplicate_table       表已存在(CREATE TABLE / CREATE INDEX 索引已存在)
    #   42701 duplicate_column      列已存在(ALTER TABLE ... ADD COLUMN)
    #   42P06 duplicate_schema      schema 已存在(CREATE SCHEMA)
    #   42710 duplicate_object      对象已存在(如约束/触发器/序列等)
    #   42P16 invalid_table_definition 建表定义相关重复(如重复主键定义)
    #   42723 duplicate_function    函数已存在
    #   42P05 duplicate_prepared_statement 预备语句已存在
    _PG_DUPLICATE_SQLSTATES = frozenset({
        '42P07', '42701', '42P06', '42710', '42P16', '42723', '42P05',
    })

    # ── PG 连接池:防止每请求新建/关闭连接,在高并发下耗尽 max_connections ─────
    from psycopg2.pool import ThreadedConnectionPool as _PgPool
    _pg_pool      = None
    _pg_pool_lock = threading.Lock()

    def _get_pg_pool():
        global _pg_pool
        if _pg_pool is None:
            with _pg_pool_lock:
                if _pg_pool is None:
                    # psycogreen:令 psycopg2 的 I/O 等待协作式让出 gevent 事件循环
                    if _GEVENT_AVAILABLE:
                        try:
                            import psycogreen.gevent as _pcg
                            _pcg.patch_psycopg()
                        except ImportError:
                            pass   # psycogreen 可选;不影响连接池防耗尽功能
                    # maxconn 通过环境变量 PG_POOL_MAX 配置(默认 20)。
                    # 1000 台设备接入时建议提到 40~50,并同步调大 PG 的 max_connections(默认 100)。
                    _pg_pool = _PgPool(
                        minconn=2,
                        maxconn=int(os.environ.get('PG_POOL_MAX', '20')),
                        dsn=_PG_DSN,
                        cursor_factory=_pg_extras.RealDictCursor,
                    )
        return _pg_pool


def _pg_dialect(sql):
    """把 SQLite 方言 SQL 改写成 PostgreSQL 兼容写法(仅处理本项目实际用到的差异)。
    覆盖:自增主键、建表时间默认值、INSERT OR IGNORE、now/date 时间运算。
    注意:Python 侧的 datetime.now().strftime() 是 Python 代码不经过此函数,无需处理。"""
    s = sql
    # 1) 自增主键:INTEGER PRIMARY KEY AUTOINCREMENT -> SERIAL PRIMARY KEY
    s = _re.sub(r'INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT', 'SERIAL PRIMARY KEY', s, flags=_re.I)
    # 2) 建表默认值:DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime')) -> DEFAULT (to_char(now(),'YYYY-MM-DD HH24:MI:SS'))
    s = s.replace("strftime('%Y-%m-%d %H:%M:%S','now','localtime')",
                  "to_char(now(),'YYYY-MM-DD HH24:MI:SS')")
    s = s.replace("strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')",
                  "to_char(now(),'YYYY-MM-DD HH24:MI:SS')")
    # 3) INSERT OR IGNORE -> INSERT ... ON CONFLICT DO NOTHING(PG 需显式 ON CONFLICT)
    #    本项目 OR IGNORE 用于避免重复插入,DO NOTHING 语义等价
    s = _re.sub(r'INSERT\s+OR\s+IGNORE\s+INTO', 'INSERT INTO', s, flags=_re.I)
    _or_ignore = _re.search(r'INSERT\s+OR\s+IGNORE', sql, flags=_re.I)
    if _or_ignore and 'ON CONFLICT' not in s.upper():
        s = s.rstrip().rstrip(';') + ' ON CONFLICT DO NOTHING'
    # 4) date('now') / date('now','+N days') -> PG 的日期运算
    s = s.replace("date('now','start of month')", "date_trunc('month', now())::date")
    s = _re.sub(r"date\('now',\s*'([+-]\d+) days'\)",
                lambda m: f"(now()::date + interval '{m.group(1)} day')::date", s)
    s = s.replace("date('now')", "now()::date")
    # 5) strftime 裸用(查询里对 now 取字符串,若有)
    s = s.replace("strftime('%Y-%m-%d %H:%M:%S','now')", "to_char(now(),'YYYY-MM-DD HH24:MI:SS')")
    return s


def _split_sql(script):
    """把多语句 SQL 脚本按分号拆成单条,忽略 -- 注释,避开字符串字面量内的分号。"""
    out, buf, in_str = [], [], False
    # 先逐行去掉 -- 行注释
    lines = []
    for ln in script.split('\n'):
        # 去掉行内 -- 注释(不在字符串里时)
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
    """PG 适配:先做方言改写;再把 SQL 里的字面 % 转义成 %%(psycopg2 用 % 做
    参数占位,字面 % 不转义会被当成占位符解析而报 IndexError,如 LIKE '%'||x||'%');
    最后把 ? 占位符转成 %s。转义务必在 ?→%s 之前,否则新引入的 %s 会被误转义。"""
    s = _pg_dialect(sql)
    s = s.replace('%', '%%')      # 先转义所有字面 %
    s = s.replace('?', '%s')      # 再把占位符 ? 变成 %s(此时不会被上一步影响)
    return s


class _ConnWrapper:
    """统一 sqlite3 / psycopg2 的接口差异:
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
        # SQLite 有原生 executescript;postgres 先做方言转换,再按分号逐条独立执行
        if self._backend == 'sqlite':
            self._raw.executescript(script)
        else:
            # 逐条执行:一条失败(如索引/表已存在)不影响其余,配合 autocommit 各自独立提交
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
                    # 幂等建表/加列场景下的"已存在"类错误忽略,其余抛出。
                    # 用 SQLSTATE(pgcode)判定而非英文错误消息子串:PG 服务器在
                    # 非英文 locale(如中文)下错误消息本地化,消息子串判定会误判
                    # (误抛中断 init_db,或误吞无关错误);SQLSTATE 由标准固定,与 locale 无关。
                    if getattr(_e, 'pgcode', None) not in _PG_DUPLICATE_SQLSTATES:
                        raise
        return self
    def cursor(self):  return self._raw.cursor()
    def commit(self):  self._raw.commit()
    def close(self):
        if self._pool is not None:
            try:
                self._pool.putconn(self._raw)   # 归还连接到池,供后续请求复用
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
            # 关键:开 autocommit,对齐 SQLite 的自动提交语义。
            # 否则 psycopg2 默认把多条语句包进一个事务,某条(如 ALTER 列已存在被 try/except 忽略)
            # 失败会把事务打成中止态,后续全部报 InFailedSqlTransaction,真正的首错被掩盖。
            raw.autocommit = True
            return _ConnWrapper(raw, 'postgres', pool=pool)  # close() 时归还连接池
        except Exception:
            pool.putconn(raw)   # autocommit 设置异常时归还连接,防止泄漏
            raise
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # 提升并发写性能
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")  # 锁竞争时最多等 5 秒而非立即报 database is locked
    return _ConnWrapper(conn, 'sqlite')

_db_lock = threading.Lock()

def db_exec(sql, params=()):
    """写操作:SQLite 用全局锁防并发写冲突;PG 连接池各连接独立,无需全局锁。"""
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
        # sqlite3.Row 支持 [0];psycopg2 RealDictCursor 返回 dict,取第一个值
        return list(r.values())[0] if isinstance(r, dict) else r[0]
    finally:
        conn.close()
