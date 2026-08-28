"""
鉴权与权限:token 签发/校验、密钥、组织 scope 权限、密码哈希。
从 app.py 原样抽出,函数/常量名与行为保持完全一致。

设计:整组一起搬——_org_scope_ids 前向调用 _current_admin/_scope_path/
_orgs_in_scope,拆散会 NameError,故必须同模块。本模块单向依赖 core.db,
仅 _get_portal_customer 用 flask.request(import flask 的全局代理,不 import app,
避免与 app import security 成环),与 core.db / common.geometry 的单向范式一致。

留在 app.py 的是依赖 app 实例的部分:@app.before_request 拦截器、
@app.route 登录/登出/改密接口——它们从本模块 import 所需函数。
"""
import os
import base64
import hashlib
import logging
import time as _time_mod

from flask import request
from core.db import db_query, db_query_one

log = logging.getLogger(__name__)


# ── 密码哈希 ───────────────────────────────────────────────────────────────────
def _hash_pw(pwd: str) -> str:
    """用 bcrypt 哈希密码(cost=12)。bcrypt 只处理前 72 字节。"""
    import bcrypt as _bcrypt
    return _bcrypt.hashpw(pwd.encode('utf-8')[:72], _bcrypt.gensalt(rounds=12)).decode('utf-8')


def _verify_pw(plain: str, stored: str) -> bool:
    """验证密码,兼容旧 SHA-256 哈希(自动升级)。异常一律返回 False。"""
    import bcrypt as _bcrypt
    try:
        if stored and (stored.startswith('$2b$') or stored.startswith('$2a$')):
            return _bcrypt.checkpw(plain.encode('utf-8')[:72], stored.encode('utf-8'))
        # 旧 SHA-256 路径(兼容历史数据)
        return hashlib.sha256(plain.encode('utf-8')).hexdigest() == stored
    except Exception:
        return False


# ── 密钥(fail-closed:缺环境变量则拒绝启动)────────────────────────────────────
def _require_secret(env_name: str) -> str:
    """读取签名密钥。生产必须通过环境变量注入;缺失则拒绝启动(fail-closed),
    杜绝用硬编码默认密钥继续运行导致 token 可被离线伪造。
    仅当显式设置 ALLOW_DEV_SECRET=1(本地开发)时,才允许回退到临时开发密钥。"""
    val = os.environ.get(env_name, '').strip()
    if val:
        return val
    if os.environ.get('ALLOW_DEV_SECRET') == '1':
        log.warning("[安全] %s 未设置,正在使用开发临时密钥(仅限本地!生产务必注入 %s)", env_name, env_name)
        return f'DEV_ONLY_{env_name}_do_not_use_in_prod'
    raise RuntimeError(
        f"[安全] 环境变量 {env_name} 未设置,拒绝启动。请注入强随机密钥,"
        f"或本地开发时设置 ALLOW_DEV_SECRET=1。")


# ── 管理员 token ───────────────────────────────────────────────────────────────
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
        if not _hmac.compare_digest(sig, expected):   # 常量时间比较,防时序攻击
            return None
        if _time_mod.time() - int(ts_s) > 30 * 24 * 3600:
            return None
        aid = int(aid_s)
        # 检查 admin 账号是否仍然活跃;DB 查询失败时不阻断(避免 DB 抖动误登出)
        try:
            row = db_query_one("SELECT is_active FROM admin_user WHERE id=?", (aid,))
            if row is not None and not row.get('is_active', 1):
                return None  # 明确禁用才拒绝
        except Exception:
            pass  # DB 异常不影响已签发的有效 token
        return aid
    except Exception:
        return None


# ── 客户门户 token ─────────────────────────────────────────────────────────────
_PORTAL_SECRET = _require_secret('PORTAL_SECRET')

def _make_token(customer_id: int) -> str:
    import hmac as _hmac
    ts  = int(_time_mod.time())
    raw = f"{customer_id}:{ts}"
    sig = _hmac.new(_PORTAL_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return base64.b64encode(f"{raw}:{sig}".encode()).decode()

def _verify_token(token: str):
    """返回 customer_id(int) 或 None(无效/过期/已禁用)"""
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
        # 检查客户账号是否仍然活跃;DB 查询失败时不阻断(避免 DB 抖动误登出)
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
    """从请求头或 query 里拿 token,返回 customer_id 或 None"""
    token = request.headers.get('X-Customer-Token') or request.args.get('token', '')
    return _verify_token(token) if token else None


# ── 组织 scope 权限 ─────────────────────────────────────────────────────────────
def _current_admin(req):
    """从请求 token 取出当前管理员行(含 org_id / user_type)"""
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
    None  → 无限制(根超管)
    str   → 只能看到该前缀下的组织(含自身)
    注意:不可返回 ''(空字符串),空串会匹配所有 org_path LIKE '%',等同于无限制。
    """
    if not admin:
        return '__NONE__'   # 没有 admin 信息,用不可能存在的前缀实现最严格限制
    # 超级管理员 且 属于根组织 → 无限制
    if admin.get('user_type') == 9 and (admin.get('org_id') or 1) == 1:
        return None
    org_id = admin.get('org_id') or 1
    org = db_query_one("SELECT org_path FROM sys_org WHERE id=?", (org_id,))
    return (org.get('org_path') or f'/{org_id}/') if org else f'/{org_id}/'


def _orgs_in_scope(scope):
    """
    查出当前管理员范围内的所有组织行。
    scope=None → 全部;scope=str → 用 org_path LIKE 过滤
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


def _org_scope_ids(request_obj):
    """返回当前管理员可见的 org_id 列表;None 表示无限制(超管)"""
    admin = _current_admin(request_obj)
    scope = _scope_path(admin)
    if scope is None:
        return None
    return [o['id'] for o in _orgs_in_scope(scope)]


def _org_where(scope_ids, existing_conds=None, existing_params=None, col='org_id'):
    """
    根据 scope_ids 生成 WHERE 子句片段和参数列表。
    scope_ids=None → 无额外过滤;
    scope_ids=[]   → 空集(WHERE 1=0)。
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
