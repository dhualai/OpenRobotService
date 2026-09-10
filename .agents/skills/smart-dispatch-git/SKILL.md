---
name: smart-dispatch-git
description: >-
  Sync origin/test into local feature/smart-dispatch and push that branch to origin.
  Use when the user mentions 同步分支, 拉 test, 推送功能分支, feature/smart-dispatch git
  workflow, or daily merge of test. Does not create pull requests.
---

# 智能派单功能分支 Git

长期分支：`feature/smart-dispatch`。合入目标：`test`。PR 由用户自己提。

## 做什么

1. `git fetch origin`
2. 在干净工作区切到 `feature/smart-dispatch`
3. `git merge origin/test`（禁止 rebase）
4. `git push origin feature/smart-dispatch`（禁止 `--force` / `--force-with-lease`）

## 禁止

- 不提 PR、不 `gh pr create`
- 不推 `test` / `main`
- 不强推
- 不 `git pull origin test`（会把当前分支搞混）
- 工作区有未提交改动时：先停下来告诉用户，不要 stash、不要帮他提交

## 怎么跑

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

PR 合进 `test` 必须用 **merge commit**，不要 squash。若用户说已经 squash 合进去了：不要把 test merge 回旧历史上继续堆，告诉他把功能分支重置到最新 `origin/test`。

人手清单见 [README.md](README.md)。
