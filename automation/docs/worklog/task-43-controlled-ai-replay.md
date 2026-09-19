# Task 43：可控 AI Replay 基础模块

> 日期：2026-09-19
> 状态：阶段 1 实现完成，等待阶段 2 确认

## 目标

为微信 H5 UI 回归提供独立、确定性的自动化 AI 支持：

- Replay LLM 服务运行在 `9410`。
- API-only AI 服务运行在 `9411`。
- 不加载 `ai.run` 的 lifespan Worker。
- 固定问题返回固定模型协议和工单草稿。
- 非固定请求明确失败，不回退真实模型。

## 阅读内容

- `automation/docs/testing/analysis/analysis-ui-regression-business-chain.md`
- `automation/docs/design-ui-regression-business-chain.md`
- `ai/run.py`
- `ai/config.py`
- `ai/core/llm.py`
- `ai/agents/AiDiagnosisPlatform/pipeline.py`
- `ai/agents/AiTaskPlatform/services/diagnosis_worker.py`
- `ai/agents/AiDiagnosisPlatform/assigner/pipeline/worker.py`

## 修改文件

- `automation/pyproject.toml`
- `automation/src/controlled_ai/__init__.py`
- `automation/src/controlled_ai/config.py`
- `automation/src/controlled_ai/replay.py`
- `automation/src/controlled_ai/llm_server.py`
- `automation/src/controlled_ai/app.py`
- `automation/src/controlled_ai/run_services.py`
- `automation/src/controlled_ai/tests/__init__.py`
- `automation/src/controlled_ai/tests/test_replay.py`
- `automation/testdata/controlled_ai/call_ticket_close.json`

## 实现内容

1. 新增 Replay 配置加载，支持动态 `run_id`。
2. 新增固定场景匹配规则，非固定问题抛出明确错误。
3. 新增 OpenAI 兼容接口：
   - `POST /v1/chat/completions`
   - `POST /v1/responses`
4. 支持非流式和 SSE 流式响应。
5. 新增 API-only AI 应用：
   - 只挂载 AI Router。
   - 不导入 `ai.run`。
   - 不启动诊断、派单、解决方式和知识沉淀 Worker。
6. 强制 `9411` 使用 Replay LLM 配置，避免误连真实模型。
7. 新增单进程启动入口，同时启动：
   - Replay LLM `9410`
   - API-only AI `9411`

## 验证结果

```text
6 passed in 0.78s
```

覆盖：

- 意图分类和查询改写响应。
- 字段规划和工单草稿响应。
- 主模型 JSON + 中文消息协议。
- 动态 `run_id` 替换。
- 非固定问题拒绝。
- OpenAI 兼容非流式和 SSE 流式接口。
- API-only 应用构建，不导入 `ai.run`。

## 环境说明

- 项目 `.venv` 已补充 `fastapi` 和 `uvicorn`。
- Replay 和 API 应用工厂已通过本机单测。
- 测试服务器 `test-ai` conda 环境已具备完整 AI 运行依赖。

## 测试环境部署

- 服务器：`125.122.97.107:8802`
- 项目目录：`/data/apps/TestOpenRobotService`
- Python：`/data/home/usp-a/miniconda3/envs/test-ai/bin/python`
- Replay LLM：`127.0.0.1:9410`
- API-only AI：`127.0.0.1:9411`
- 用户服务：`openrobot-controlled-ai.service`
- 已启用用户级 systemd 和 `linger`
- 现有 Supervisor 服务 `test-open-robot-ai`（9401）未修改

健康检查结果：

```json
{"status":"ok","service":"controlled-ai-api","mode":"api-only","background_workers":0,"llm_backend":"relay","relay_base_url":"http://127.0.0.1:9410/v1","relay_model":"automation-fixed","intent_llm_backend":"relay","intent_model":"automation-fixed","plan_execute":"0","ticket_tool_loop":"0"}
```

真实 AI Pipeline 联调结果：

- 意图分类命中 Replay。
- 固定问题直接进入 `generating_ticket`。
- 返回 `stage=review`。
- 草稿项目为 `Leo_test / 摇人吧服务号-测试`。
- 处理人为“自动化处理人”。
- 未调用确认提交，未创建测试工单。
- 4 个验证会话已通过 `MemoryManager.clear()` 清理。

## 风险

- Replay 响应协议需要在真实 AI Pipeline 上做首次联调校准。
- 如果 AI Pipeline 后续改变模型调用次数或 Prompt 判定标记，需要同步更新 Replay。
- 当前只覆盖一条固定问题，不处理多轮补充。
- 本次联调未创建测试工单，但仍需在 UI 场景实现时验证确认提单和清理链路。

## 下一步

1. 人工确认阶段 1。
2. 进入阶段 2，实现本地 UI 回归 Gateway。
3. 将 SSH 隧道端口 `19400/19411` 和本地 Gateway 串通。
