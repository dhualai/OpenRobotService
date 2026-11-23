# AI 检视使用说明（ORS-1149）

## 触发方式速查

| 场景 | 操作 | 执行者 |
|---|---|---|
| 同仓 PR 深度检视 | PR 页面右侧 Labels 勾选 `ai-review` | 任意有写权限成员 |
| 带标签 PR 的迭代重检 | 无需操作——每次 push 自动升级深度检视（标签=开关） | — |
| fork PR 深度检视 | 方式一/二（见下） | 任意有写权限成员 |
| 移除检视开关 | PR 页面摘掉 `ai-review` 标签 | 任意有写权限成员 |

> fork PR 的 labeled 事件 GitHub 不派发给 base 仓库 workflow（实测 4 次全零），
> 故 fork 场景用哨兵；fork PR 的快筛仍会自动跑（结果在 Checks，无评论能力）。

## 方式一：网页提交哨兵（零命令行）

1. 仓库 Code 页 → 分支下拉切到 `ai-review-go`；
2. 编辑固定文件 `.trigger/latest`（内容随意，或写 PR 号）→ Commit changes；
3. **Commit message 写 `ai-review PR#495`**（点名要检视的 PR）；
4. 提交后 PR 评论区出现 ⏳ 进度，约 1~2 分钟替换为检视报告。

## 方式二：哨兵脚本（一行命令）

前置：本机装有 GitHub CLI 且已 `gh auth login`（无需本地仓库工作区）。

```powershell
.\ai-review-go.ps1 -Pr 495
```

## 报告解读

- 🔴 **必须采纳**：合入前应处理的问题（高危/涉库/密钥/部署链路）；
- 💡 建议折叠在每条下方，含影响、改法与收益；
- 报告**不阻塞合并**，是否采纳由人工判断；
- `🔄 较上次检视：新增 X 条，已消除 Y 条` 为与上次报告的增量对比。

## 已知限制

- fork PR 的深度检视需主仓成员代触发（fork 无 secrets，LLM 调用不可用）；
- fork PR 的快筛高危仅体现在 Checks 日志（无评论能力），哨兵深度检视可补全；
- 同 label 快速摘/打可能被 GitHub 事件去重（重试间隔建议 > 1 分钟）。
