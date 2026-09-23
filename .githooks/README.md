# 防屎山提交前检查(git hooks)

这个目录放的是**自动检查铁律违规**的 git 钩子,配合根目录《防屎山开发规矩》使用——把"必须记得做的事"从人的记忆里挪进机制。

## 装一次就行

clone 仓库后,每人跑一次:

```bash
bash .githooks/install-hooks.sh
```

它把 git 的钩子目录指向本目录(`git config core.hooksPath .githooks`)。之后每次 `git commit` 会自动检查。

## 它检查什么

只扫**本次提交新增的行**(存量代码不报,避免"一提交就报错、大家索性关掉"):

- SQL 方言炸弹:`ROUND` 没带 `CAST`、SQL 里写 `strftime('...','now')`、`INSERT OR IGNORE`、`AUTOINCREMENT`、`IFNULL`
- 资金:直接写 `UPDATE sim_card SET balance`(必须走唯一资金入口)
- 横切:裸写 `FROM device`(可能漏软删除过滤,应走 `_scope_where`)
- 前端:组件里裸读写 `localStorage` 的 token/role

## 误报了怎么办

- 确为误报/有正当理由:在该行末尾加注释 `# noqa: shitpile`,该行跳过检查。
- 应急整体跳过(不鼓励):`git commit --no-verify`。

## 想加新规则

编辑 `.githooks/check_conventions.py` 的 `RULES` 列表,加一条 `dict(exts=..., pattern=..., name=..., hint=...)` 即可。改完建议先本地验证正则不误伤。

## 注意

- 钩子只在**本地 commit 时**生效,不是服务器强制。它是"帮你别犯错",不是"防坏人"。真要强制,得在 CI(如 GitHub Actions)里再跑一遍同一个脚本。
- 检查脚本自身出错时**不拦截提交**(避免把人堵死),只在 stderr 提示。
