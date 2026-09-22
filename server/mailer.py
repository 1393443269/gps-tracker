# -*- coding: utf-8 -*-
"""
mailer.py — SMTP 邮件发送模块(GPS 追踪平台主动通知用)

╔══════════════════════════════════════════════════════════════════════════╗
║  安全红线(必读)                                                            ║
║  1. SMTP 授权码/密码【只从环境变量读取】,绝不写死进代码、绝不进 git。       ║
║  2. 本模块不打印/不返回任何密钥;排查配置只暴露"缺哪个变量名",不暴露值。    ║
║  3. 发信失败一律 try/except 兜底,返回 (False, 错误信息),绝不向上抛异常     ║
║     中断调用方(扫描线程/接口),更不能影响主服务。                          ║
╚══════════════════════════════════════════════════════════════════════════╝

只用 Python 标准库 smtplib + email,不依赖任何第三方库。

环境变量:
  SMTP_HOST       SMTP 服务器地址(如 smtp.qq.com);未配则视为未启用
  SMTP_PORT       端口,默认 465(SSL)。starttls 常用 587
  SMTP_USER       发件邮箱账号(登录名),同时作默认发件人地址
  SMTP_PASSWORD   授权码/密码(【机密】只放环境变量)
  SMTP_FROM_NAME  发件人显示名,默认 "GPS追踪平台"
  SMTP_USE_SSL    是否用 SSL(SMTP_SSL);默认 "1"(启用)。设为 "0" 则用
                  普通 SMTP + starttls 升级
"""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from email.utils import formataddr

# 发信网络超时(秒):防止 SMTP 服务器无响应把扫描线程挂死
_SMTP_TIMEOUT = 15


def _env(key, default=''):
    """读环境变量并去除首尾空白;缺失返回 default。"""
    v = os.environ.get(key, default)
    return v.strip() if isinstance(v, str) else v


def _use_ssl():
    """SMTP_USE_SSL 默认启用;'0'/'false'/'no' 视为关闭。"""
    v = _env('SMTP_USE_SSL', '1').lower()
    return v not in ('0', 'false', 'no', '')


def missing_config():
    """返回缺失的必需配置项名列表(仅暴露变量名,不暴露值),便于排查。
    必需项:SMTP_HOST / SMTP_USER / SMTP_PASSWORD。"""
    missing = []
    for key in ('SMTP_HOST', 'SMTP_USER', 'SMTP_PASSWORD'):
        if not _env(key):
            missing.append(key)
    return missing


def smtp_configured():
    """SMTP 三要素(HOST/USER/PASSWORD)是否都配齐。"""
    return len(missing_config()) == 0


def _normalize_recipients(to_addrs):
    """把收件人参数统一成去重、非空的地址列表。支持单个字符串或可迭代。"""
    if to_addrs is None:
        return []
    if isinstance(to_addrs, str):
        candidates = [to_addrs]
    else:
        try:
            candidates = list(to_addrs)
        except TypeError:
            candidates = [to_addrs]
    result = []
    seen = set()
    for a in candidates:
        if not a:
            continue
        addr = str(a).strip()
        if not addr or addr in seen:
            continue
        seen.add(addr)
        result.append(addr)
    return result


def send_mail(to_addrs, subject, body_text, body_html=None):
    """发送一封邮件。

    参数:
      to_addrs   收件人;单个字符串或字符串列表
      subject    主题(中文安全,内部按 UTF-8 编码)
      body_text  纯文本正文(必填)
      body_html  HTML 正文(可选;提供则作为 multipart/alternative 的 HTML 部分)

    返回:
      (ok: bool, msg: str)
      成功 -> (True, "已发送至 xxx")
      失败 -> (False, "错误描述")  ← 任何异常都在此消化,不向上抛。
    """
    try:
        recipients = _normalize_recipients(to_addrs)
        if not recipients:
            return (False, '收件人为空')

        miss = missing_config()
        if miss:
            return (False, 'SMTP 未配置齐全,缺少: ' + ','.join(miss))

        host = _env('SMTP_HOST')
        user = _env('SMTP_USER')
        password = _env('SMTP_PASSWORD')
        from_name = _env('SMTP_FROM_NAME', 'GPS追踪平台') or 'GPS追踪平台'
        use_ssl = _use_ssl()
        try:
            port = int(_env('SMTP_PORT', '465') or '465')
        except (ValueError, TypeError):
            port = 465

        # 组装邮件:有 HTML 则用 multipart/alternative(text 兜底 + html)
        if body_html:
            msg = MIMEMultipart('alternative')
            msg.attach(MIMEText(body_text or '', 'plain', 'utf-8'))
            msg.attach(MIMEText(body_html, 'html', 'utf-8'))
        else:
            msg = MIMEText(body_text or '', 'plain', 'utf-8')

        msg['Subject'] = Header(subject or '', 'utf-8')
        msg['From'] = formataddr((str(Header(from_name, 'utf-8')), user))
        msg['To'] = ', '.join(recipients)

        # 连接 + 登录 + 发送
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=_SMTP_TIMEOUT)
        else:
            server = smtplib.SMTP(host, port, timeout=_SMTP_TIMEOUT)
        try:
            if not use_ssl:
                # 普通 SMTP 先升级到 TLS 再登录,避免明文传密码
                try:
                    server.starttls()
                except smtplib.SMTPException:
                    pass  # 服务器不支持则退化(部分内网 relay);登录仍尝试
            server.login(user, password)
            server.sendmail(user, recipients, msg.as_string())
        finally:
            try:
                server.quit()
            except Exception:
                pass

        return (True, '已发送至 ' + ', '.join(recipients))

    except smtplib.SMTPAuthenticationError as e:
        return (False, 'SMTP 认证失败(账号或授权码错误): %s' % (e,))
    except smtplib.SMTPException as e:
        return (False, 'SMTP 错误: %s' % (e,))
    except (OSError, TimeoutError) as e:
        return (False, '网络/连接错误: %s' % (e,))
    except Exception as e:  # noqa: BLE001 兜底,绝不向上抛
        return (False, '发送异常: %s' % (e,))
