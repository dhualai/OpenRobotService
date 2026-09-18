---
name: smart-dispatch-git
description: >-
  Sync origin/test into local ai-feature-dispatch, push that branch, and help draft
  or create the merge PR into test. Use when the user mentions 同步分支, 拉 test,
  推送功能分支, ai-feature-dispatch, 智能派单 git, 提 PR, 合并 PR, or daily merge of test.
---

# 智能派单功能分支 Git

长期功能分支：`ai-feature-dispatch`（旧名 `feature/smart-dispatch` 已弃用）。  
合入目标：`test`。合入方式：**Create a merge commit**，不要 squash。

## 命名规范（团队）

分支：

```text
[模块]-[feature|fix|chore|…]-修改内容（英文）
```

例：`ai-feature-dispatch`、`call-fix-ticket-count-sort`

PR / merge 标题：

```text
[feature|fix|chore|…]（ORS-工单号）：修改内容（中文）
```

例：`feature（ORS-785）：完善智能派单 Step1–3 产品口径`

## 同步 / 推送做什么

1. `git fetch origin`
2. 工作区干净时切到 `ai-feature-dispatch`
3. `git merge origin/test`（禁止 rebase）
4. `git push origin ai-feature-dispatch`（禁止 `--force` / `--force-with-lease`）

## 禁止（同步脚本）

- 不推 `test` / `main`
- 不强推
- 不 `git pull origin test`（会把当前分支搞混）
- 工作区有未提交改动时：先停下来告诉用户，不要 stash、不要擅自提交

## 怎么跑同步

在仓库根执行（PowerShell）：

```powershell
# 拉 test 到本地功能分支，并推到远程功能分支
.\.agents\skills\smart-dispatch-git\scripts\sync.ps1

# 只拉 test
.\.agents\skills\smart-dispatch-git\scripts\sync.ps1 -Action sync

# 只推远程功能分支
.\.agents\skills\smart-dispatch-git\scripts\sync.ps1 -Action push
```

合并冲突：停，把冲突文件列给用户，等他解完再继续。

若用户说已经 **squash** 合进 test：不要把 test merge 回旧历史上继续堆，告诉他把功能分支重置到最新 `origin/test` 再开新提交。

## 合并 PR（用户明确要求时）

用户说「提 PR / 写 PR / 合并到 test」时再做；同步脚本默认不提 PR。

### 准备

1. 确认当前在 `ai-feature-dispatch`，已 push，相对 `origin/test` 有可合提交
2. 向用户确认 **ORS 工单号**（没有则标题里先写 `ORS-xxxx` 占位并标明待补）
3. 用 `git log origin/test..HEAD` 与 `git diff origin/test...HEAD` 归纳改动，写中文摘要

### 标题

严格按规范：

```text
feature（ORS-1234）：一句话中文说明
```

类型按改动选 `feature` / `fix` / `chore` 等，不要英文长标题。

### 正文模板

```markdown
## Summary
- …

## Test plan
- [ ] …
```

### 创建

仅在用户明确要求创建时：

```powershell
git push -u origin HEAD
gh pr create --base test --head ai-feature-dispatch --title "feature（ORS-xxx）：…" --body "…"
```

创建后把 PR URL 回给用户，并提醒 GitHub 上合入选 **Create a merge commit**。

人手清单见 [README.md](README.md)。
