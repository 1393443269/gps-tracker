#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
防屎山提交前检查(git pre-commit 调用)。

核心原则:只检查【本次暂存改动新增的行】(git diff --cached 里以 + 开头的行),
存量代码一律不碰——避免"存量欠账导致每次提交都报错、团队索性关掉钩子"。

对应《防屎山开发规矩》的三条铁律里可自动化的部分。检出违规时:
  - 默认【拦截提交】(exit 1),并打印哪一行违反哪条规矩、怎么改。
  - 确实是误报/有正当理由,可在该行加注释  # noqa: shitpile  跳过该行检查,
    或提交时用  git commit --no-verify  整体跳过(不鼓励,留作应急)。

只依赖 Python 标准库,无第三方依赖。
"""
import subprocess
import sys
import re

SKIP_TOKEN = 'noqa: shitpile'   # 行内豁免标记

# ── 规则定义:(适用文件后缀, 正则, 规矩说明, 建议) ──────────────────────────────
RULES = [
    # 铁律2:SQL 只说 PostgreSQL 能听懂的话
    dict(exts=('.py',),
         pattern=re.compile(r"ROUND\s*\(\s*(?!CAST)[^,)]*,\s*\d+\s*\)"),
         name="SQL方言: ROUND 未带 CAST",
         hint="PostgreSQL 无 ROUND(double,int)。改成 ROUND(CAST(x AS numeric), 2)。已踩过:确认到账 500。"),
    dict(exts=('.py',),
         pattern=re.compile(r"strftime\s*\(\s*['\"][^'\"]*['\"]\s*,\s*['\"]now['\"]"),
         name="SQL方言: SQL 里用了 strftime('...','now')",
         hint="PG 翻译层只认有限枚举,换个写法会静默漏翻。当前时间请用 Python datetime.now() 传参。"),
    dict(exts=('.py',),
         pattern=re.compile(r"\bINSERT\s+OR\s+IGNORE\b", re.IGNORECASE),
         name="SQL方言: INSERT OR IGNORE(SQLite 特有)",
         hint="PG 用 INSERT ... ON CONFLICT DO NOTHING。"),
    dict(exts=('.py',),
         pattern=re.compile(r"\bAUTOINCREMENT\b", re.IGNORECASE),
         name="SQL方言: AUTOINCREMENT(SQLite 特有)",
         hint="PG 用 SERIAL / GENERATED ... AS IDENTITY。"),
    dict(exts=('.py',),
         pattern=re.compile(r"\bIFNULL\s*\(", re.IGNORECASE),
         name="SQL方言: IFNULL(SQLite 特有)",
         hint="改用 COALESCE(...),两库通用。"),

    # 铁律1:横切过滤零手写
    dict(exts=('.py',),
         pattern=re.compile(r"UPDATE\s+sim_card\s+SET\s+balance\s*="),
         name="资金: 直接写 UPDATE sim_card SET balance",
         hint="余额变动必须走唯一的资金入口函数,禁止复制这段 SQL(现已复制 3 处,别加第 4 处)。"),

    # 铁律3 / 前端:角色分流与 localStorage 裸读
    dict(exts=('.vue', '.js'),
         pattern=re.compile(r"localStorage\.(get|set|remove)Item\s*\(\s*['\"](customer_token|admin_token|user_role|is_super|menu_keys)"),
         name="前端: 直接读写 localStorage 的 token/role",
         hint="token/role 请收进统一的 auth store,别在组件里裸读 localStorage(key 拼错不报错、只读到 null)。"),
]

# device 软删除:单独处理(需要跨"是否已用 _scope_where"判断,简单版按行匹配)
DEVICE_RULE = dict(
    exts=('.py',),
    # 匹配 FROM device(可带别名),但排除 information_schema / 注释行 / 已带 deleted 的同行
    pattern=re.compile(r"\bFROM\s+device\b", re.IGNORECASE),
    name="横切: 裸写 FROM device(可能漏软删除过滤)",
    hint="设备查询请走 _scope_where('device');若确为写操作/已在别处过滤,同行加 # noqa: shitpile。")


def staged_added_lines():
    """返回 [(file, lineno_in_new_file, line_text), ...],仅暂存改动里【新增的行】。"""
    # 拿到暂存的 diff(unified,含文件名与行号)
    out = subprocess.run(
        ['git', 'diff', '--cached', '--unified=0', '--no-color'],
        capture_output=True, text=True, encoding='utf-8', errors='replace').stdout
    results = []
    cur_file = None
    new_ln = 0
    for raw in out.splitlines():
        if raw.startswith('+++ b/'):
            cur_file = raw[6:]
            continue
        if raw.startswith('@@'):
            # @@ -a,b +c,d @@  取 c 作为新文件起始行
            m = re.search(r"\+(\d+)", raw)
            new_ln = int(m.group(1)) if m else 0
            continue
        if raw.startswith('+') and not raw.startswith('+++'):
            results.append((cur_file, new_ln, raw[1:]))
            new_ln += 1
        elif not raw.startswith('-'):
            # 上下文行(unified=0 下基本没有),推进行号
            new_ln += 1
    return results


def main():
    added = staged_added_lines()
    if not added:
        return 0

    violations = []
    for fpath, lineno, text in added:
        if fpath is None:
            continue
        # 跳过检查脚本自身所在目录:规则的说明文字里天然含这些关键词,否则会自我误报
        if fpath.startswith('.githooks/'):
            continue
        if SKIP_TOKEN in text:
            continue
        # 普通规则
        for rule in RULES:
            if not fpath.endswith(rule['exts']):
                continue
            if rule['pattern'].search(text):
                violations.append((fpath, lineno, rule['name'], rule['hint'], text.strip()))
        # device 软删除规则:排除已带 deleted 或 information_schema 的行
        if fpath.endswith(DEVICE_RULE['exts']):
            if DEVICE_RULE['pattern'].search(text):
                low = text.lower()
                if 'deleted' not in low and 'information_schema' not in low \
                        and '_scope_where' not in low and 'device_role' not in low \
                        and 'device_id' not in low:
                    violations.append((fpath, lineno, DEVICE_RULE['name'],
                                       DEVICE_RULE['hint'], text.strip()))

    if not violations:
        return 0

    print("\n\033[31m✗ 防屎山检查未通过:本次改动引入了以下违规(仅检查新增行)\033[0m\n")
    for fpath, lineno, name, hint, text in violations:
        print(f"  \033[33m{fpath}:{lineno}\033[0m  {name}")
        print(f"    代码: {text[:100]}")
        print(f"    规矩: {hint}")
        print()
    print("处理方式:按上面提示修正;确为误报请在该行加  # noqa: shitpile ;")
    print("应急整体跳过(不鼓励):git commit --no-verify\n")
    return 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        # 检查脚本自身出错时【不拦截提交】(避免钩子把人堵死),只提示
        print(f"[防屎山检查] 脚本异常,已跳过检查(不拦截): {e}", file=sys.stderr)
        sys.exit(0)
