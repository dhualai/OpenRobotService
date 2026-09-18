# 智能派单功能分支日常流程

你改代码的地方：本地 `ai-feature-dispatch`  
推到哪里：远程 `origin/ai-feature-dispatch`  
最后合进：`test`（PR 可让 Agent 按规范起草；合入选 merge commit）

旧分支名 `feature/smart-dispatch` 已弃用，新工作请用本分支。

## 命名

| 用途 | 格式 | 例 |
|------|------|-----|
| 分支 | `模块-feature/fix/chore-英文内容` | `ai-feature-dispatch` |
| PR 标题 | `feature/fix/chore（ORS-工单号）：中文内容` | `feature（ORS-785）：完善派单口径` |

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

只推 `ai-feature-dispatch`，不会推 `test`。

## 一条命令：先拉 test 再推远程

```powershell
.\.agents\skills\smart-dispatch-git\scripts\sync.ps1
```

也可以在 Cursor 里说「同步分支」或「拉 test 并推送」，Agent 会跑同一套脚本。

## 提 PR 合进 test

1. 先 push 功能分支  
2. 对 Agent 说「提 PR」或「写合并 PR」，并给 **ORS 工单号**  
3. 标题形如：`feature（ORS-xxx）：中文说明`  
4. GitHub 合入选 **Create a merge commit**，不要 squash  

Agent 可代跑 `gh pr create --base test --head ai-feature-dispatch`（需你明确说创建）。

## 不要做

| 不要 | 原因 |
|------|------|
| squash 合进 test 再把 test merge 回来 | 提交重复、容易冲突 |
| rebase 已推送的功能分支再强推 | 历史被改写 |
| 在功能分支上 `git pull origin test` | 容易合错当前分支 |
| 继续在 `feature/smart-dispatch` 上堆新需求 | 已改用 `ai-feature-dispatch` |

有冲突时脚本会停。解完 `git add` 再 `git commit` 完成合并，然后重新跑 `-Action push`。
