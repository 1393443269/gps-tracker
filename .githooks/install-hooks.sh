#!/usr/bin/env bash
# 一次性安装防屎山钩子:把 git 的钩子目录指向版本库里的 .githooks/。
# 团队每个人 clone 后跑一次即可(钩子本身已在版本库,不用各自复制)。
set -e
cd "$(git rev-parse --show-toplevel)"
git config core.hooksPath .githooks
chmod +x .githooks/pre-commit .githooks/check_conventions.py 2>/dev/null || true
echo "✓ 防屎山钩子已安装(core.hooksPath -> .githooks)"
echo "  以后每次 git commit 会自动检查本次改动是否违反铁律。"
echo "  误报:该行加 # noqa: shitpile ;应急跳过:git commit --no-verify"
