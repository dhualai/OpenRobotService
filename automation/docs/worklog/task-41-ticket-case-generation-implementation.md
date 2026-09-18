# Task 41：工单生成候选用例模块

> 日期：2026-09-15
> 状态：已实现，工单 837 真实生成完成，待人工 review

## 目标

实现工单驱动流水线第二模块：读取只读扫描得到的候选工单，生成候选功能测试用例和 review 文档。

## 实现内容

- 新增工单模式生成器：
  - `automation/ci_ai_gen/ticket_pipeline.py`
- 新增提示词：
  - `ticket_analysis.md`
  - `ticket_case_gen.md`
- 新增 CLI：
  - `automation/scripts/cli-generate-ticket-cases.py`
- LLM 客户端支持通用 OpenAI 兼容配置：
  - `LLM_API_KEY`
  - `LLM_BASE_URL`
  - `LLM_MODEL`
  - DeepSeek 变量作为兼容回退
- 产物目录：
  - `automation/references/generated-cases/tickets/{ticket_id}/`
- 产物：
  - `analysis.md`
  - `cases.json`
  - `cases.md`
  - `manifest.json`
- manifest 状态固定为 `pending_review`，不自动归档、不自动执行。

## 验证

- 使用 fake LLM 的单元测试验证生成流程：

```text
2 passed in 0.67s
```

- AI 客户端回归测试：

```text
13 passed in 18.13s
```

- dry-run 用工单 837 验证通过。
- OpenCode Go 真实生成成功：

```text
Generated 24 candidate case(s) for ticket 837
```

- 生成目录：

```text
automation/references/generated-cases/tickets/837/
├── analysis.md
├── cases.json
├── cases.md
└── manifest.json
```

manifest 状态为 `pending_review`，未自动归档、未自动执行。

## 下一步

1. 人工 review `cases.md` / `cases.json`。
2. 确认接口路径、权限代码和展示形态等“待确认”项。
3. review 通过后归档为正式功能用例。
4. 继续实现 `push test` 用例选择和执行。

## 可用 Provider

已核对 OpenCode 官方文档：

- OpenCode Go 是 $10/月订阅，不是免费套餐。
- Go API Base URL：`https://opencode.ai/zen/go/v1`
- OpenAI 兼容接口：`/chat/completions`
- 本次使用模型：`deepseek-v4-flash`
- 必须发送稳定的 `x-opencode-session`，并使用自定义 `User-Agent`
- 客户端已支持：
  - `LLM_API_KEY`
  - `LLM_BASE_URL`
  - `LLM_MODEL`
  - `LLM_SESSION_ID`
  - `LLM_USER_AGENT`
  - `LLM_EXTRA_HEADERS_JSON`

安全提示：本次 API Key 曾通过对话传递，使用完成后应立即在 OpenCode 控制台轮换。
