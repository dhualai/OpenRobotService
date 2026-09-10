# 功能分支日常流程

你改代码的地方：本地 `feature/smart-dispatch`  
推到哪里：远程 `origin/feature/smart-dispatch`  
最后合进：`test`（PR 你自己提）

## 每天开工（拉最新 test）

工作区先干净（该提交的提交，不该提交的还原）。

```powershell
cd D:\CodeHub\AI\OpenRobotService
.\.agents\skills\smart-dispatch-git\scripts\sync.ps1 -Action sync
```

这会：`fetch` → 切到功能分支 → `merge origin/test`。

## 改完要备份到远程

```powershell
.\.agents\skills\smart-dispatch-git\scripts\sync.ps1 -Action push
```

只推 `feature/smart-dispatch`，不会推 `test`。

## 一条命令：先拉 test 再推远程

```powershell
.\.agents\skills\smart-dispatch-git\scripts\sync.ps1
```

也可以在 Cursor 里说「同步分支」或「拉 test 并推送」，Agent 会跑同一套脚本。

## 提 PR（你自己做）

GitHub：`feature/smart-dispatch` → `test`  
合入方式选 **Create a merge commit**，不要 squash。

## 不要做

| 不要 | 原因 |
|------|------|
| squash 合进 test 再把 test merge 回来 | 提交重复、容易冲突 |
| rebase 已推送的功能分支再强推 | 历史被改写 |
| 在功能分支上 `git pull origin test` | 容易合错当前分支 |

有冲突时脚本会停。解完 `git add` 再 `git commit` 完成合并，然后重新跑 `-Action push`。
