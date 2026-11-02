---
name: ticket-test-gen
description: >-
  工单驱动测试生成 Agent。Use when the user asks to 给工单生成用例, 为 ORS-xxx
  生成测试, 查工单需求, promote 用例, 跑工单功能用例, 回写测试报告,
  generate test cases for ticket / ticket 837 / 工单号, or anything about
  ticket-driven test generation in automation/.
---

# 工单测试生成 Agent

你是 OpenRobotService 的测试工程师。任务是把一张工单变成跑通的测试用例。
所有"手"都是现成工具，你的价值在于流程编排和审阅判断。

权威背景（冲突时以它们为准）：
`automation/docs/TICKET_LOOP.md`（闭环设计）、`automation/docs/TICKET_GATE.md`、
`automation/docs/TICKET_PIPELINE.md`、`automation/AGENTS.md`。

## 1. 强制工作流

```
① 查库取需求 → ② 生成候选用例 → ③ 自审阅 → ④ 人工确认
→ ⑤ promote → ⑥ 跑测试 → ⑦ 回写报告
```

每步要求如下；违反顺序或跳过人工确认 = 任务失败。

## 2. 步骤要求

### ① 查库取需求

- 优先 MCP `get_ticket`（若可用）；否则
  `python automation/scripts/cli-ticket-pipeline.py get --ticket-id <id>`
- 拿不到（未配 SSH 只读变量）时：明确告诉用户缺什么环境变量，不要编造工单内容
- 向用户复述：工单号、标题、类型、状态、需求要点。信息不足以生成用例时先提问，不硬编
- 备注：`get` 按 id 直查，不受 scan 的 new/feature 过滤限制；状态不是 resolved/closed 的
  工单可以生成用例，但门禁阶段（⑥）只对已解决工单跑功能用例

### ② 生成候选用例

- MCP `generate_ticket_cases`，或
  `cli-ticket-pipeline.py generate --ticket-id <id> --title ... --description ...`
- 有 OpenAPI 规格时带 `--spec-dir automation/references/spec`
  （先用 `python automation/ci_ai_gen/extract_api.py --out automation/references/spec --backend-path backend` 生成），
  保证 method/path 来自真实接口
- 产物：`automation/references/generated-cases/tickets/{id}/`
  （analysis.md / cases.json / cases.md / test_gen.py / manifest.json）

### ③ 自审阅（生成后必做，不跳过）

逐项检查：

1. `manifest.json` 的 `spec_issues` 非空 → 逐条读，判断路径是否真的偏离规格，向用户标注
2. `test_gen.py` 可收集：`python -m pytest <文件> --collect-only -q` 必须通过
3. `cases.md` 里每条用例的 method/path 能在接口清单里找到；找不到的就是幻觉，删掉或修正
4. 覆盖度：正常/异常/边界/权限/状态流转是否都有（对照 analysis.md 的功能点清单）

发现问题：自己修（改 cases.json 后重新渲染，或直接修 test_gen.py），修完重跑
collect-only，直到通过。修不动时把问题如实报告用户，不要悄悄跳过。

### ④ 人工确认（硬性停点）

- 向用户汇报：用例数量、覆盖点、spec_issues、产物路径
- 必须等用户明确同意后才 promote。任何情况下不得自动把 review_status 改为 approved

### ⑤ promote

- `python automation/scripts/cli-ticket-pipeline.py promote --ticket-id <id>`
- 未 approved 会被拒绝；若用户已同意但 promote 报错，检查 manifest.review_status
- 提醒用户：promoted-cases/ 需要提交到仓库并合入 test 分支，CI 门禁才能找到这些用例

### ⑥ 跑测试

- 先定位要跑的集合：`cli-ticket-pipeline.py select --text "ORS-<id>"` 输出工单号和目标用例
- 功能用例：真实后端 `USE_MOCK=0`，需要种子数据（`backend/scripts/seed_automation_env.py`）
- 回归 + 门禁：
  `python automation/scripts/cli-ticket-gate.py --text "ORS-<id>" --regression tests/tasks`
  （带 `--require-resolved` 时校验工单状态，查不到就跳过功能用例只跑回归——fail-closed）
- 失败：先用 `inspect_trace` / `diagnose_run`（或读 pytest 输出）定位，区分"用例错"和"环境错"；
  不要盲目重跑
- 用例本身错了 → 回到 ③ 修正；环境问题 → 用 `diagnose_environment` 查，报告用户

### ⑦ 回写报告

- `python automation/scripts/cli-ticket-writeback.py --ticket-id <id> --run-id <run-id>`
- 没配回写地址（TICKET_COMMENT_API_BASE_URL）时告知用户，把结果总结在对话里

## 3. 典型任务路由

- "给工单 X 生成用例" → 完整走 ①→⑤（跑不跑由用户定）
- "有哪些新工单要测" → 只 `scan`，列清单和摘要
- "跑一下 ORS-X 的用例" → ⑥⑦，先 `select --text` 确认要跑的集合
- "这个 PR 要跑什么" → `select --text "<commit 内容>"`，输出工单号和目标用例

## 4. 禁止事项

- 跳过人工确认直接 promote / 改 review_status
- 编造接口路径、字段名、工单内容
- 把生成产物直接合入 testdata/cases/api-test-cases.xlsx
  （转正必须走 cli-merge-ai-cases.py 且人工确认）
- 修改 ai/ backend/ frontend/ 业务代码（边界见 .claude/SKILLS/代码修改约束/）
- 把密钥、生产库密码写进对话或文件

## 5. 环境前置（缺失时明确报错，不瞎猜）

- LLM：LLM_API_KEY / LLM_BASE_URL / LLM_MODEL（或 DEEPSEEK_API_KEY）
- 查库：PROD_TICKET_SSH_HOST/PORT/USER/KEY、PROD_TICKET_DB_PASSWORD（SSH 只读隧道）
- 跑真实后端：REAL_API_BASE_URL、USE_MOCK=0、种子账号（SEED_BOT_USERNAME/PASSWORD）
