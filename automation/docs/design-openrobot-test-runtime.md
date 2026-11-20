# OpenRobot 测试运行时设计（Phase 0）

> 日期：2026-09-17
> 状态：Phase 0/1/2/3/4 已实现（运行时、MCP、契约、真实环境、UI、外部 AI 评测、工单闭环、环境调度与 gate）
> 目标：为 pytest、CI、AI IDE 和后续 MCP 提供统一的 run_id / trace_id / RunClient 底座

## 1. 背景

当前自动化能力已经包含 API Mock、业务链路、真实后端冒烟、AI 评测和工单生成。
缺少的是统一运行上下文与可检查的 Trace，导致：

- pytest、AI 评测和真实链路各有一套运行记录格式；
- AI IDE 无法直接提交场景和查看执行轨迹；
- 后续 MCP Server 没有稳定的底层 SDK。

Phase 0 不改变现有 pytest 用例，只在其上增加运行时基础设施。

## 2. 总体结构

```text
pytest / CI / AI IDE / CLI
             |
             v
   OpenRobotTestClient
             |
             v
        RunManager
             |
    +--------+---------+
    |                  |
RunContext         TraceStore
                       |
          output/runs/{run_id}/
            run.json
            status.json
            steps.jsonl
            artifacts/
```

## 3. 核心模块

| 文件 | 职责 |
|---|---|
| `automation/src/runtime/context.py` | `RunContext`，保存 run_id、trace_id、scenario、profile、env |
| `automation/src/runtime/trace_store.py` | 文件型 Trace：run.json、status.json、steps.jsonl、artifacts |
| `automation/src/runtime/manager.py` | 创建、查询和结束运行 |
| `automation/src/client/openrobot_client.py` | 薄客户端：health、submit、run、status、stop |
| `automation/scripts/cli-openrobot.py` | 命令行入口 |
| `automation/conftest.py` | pytest 生命周期接入，自动记录运行状态 |
| `automation/rules.md` | AI/API/UI/工单测试行为规范 |

## 4. 场景注册表

Phase 0 内置场景：

| 场景 | 目标 | 默认 profile |
|---|---|---|
| `framework` | `src`、`config`、`ci_ai_gen` | fast |
| `api_mock` | Mock API + 业务链路 | fast |
| `business_chain` | `tests/business_chain` | fast / pro |
| `real_smoke` | `tests/real -m smoke` | pro |
| `real_lifecycle` | 真实工单生命周期 | pro |
| `infrastructure` | MySQL / Redis / Qdrant 契约 | pro |
| `ai_eval` | AI L1/L2/L3 评测 | pro / nightly |

## 5. CLI 用法

```powershell
cd automation

python scripts/cli-openrobot.py health
python scripts/cli-openrobot.py scenarios
python scripts/cli-openrobot.py run --scenario business_chain --profile fast
python scripts/cli-openrobot.py run --scenario real_smoke --profile pro --env test
python scripts/cli-openrobot.py status --run-id run-20260917-...
python scripts/cli-openrobot.py trace --run-id run-20260917-...
python scripts/cli-openrobot.py list --limit 20
```

## 6. pytest 接入

任意 pytest 运行都会自动写入 Trace：

```powershell
pytest tests/business_chain -q
pytest tests/real -m smoke --run-scenario real_smoke --run-profile pro --run-env test
pytest tests/ai -m ai --run-scenario ai_eval --run-profile pro
```

如果由 `OpenRobotTestClient` 启动 pytest，父子进程共享同一个
`OPENROBOT_RUN_ID` 和 `OPENROBOT_TRACE_ID`。

## 7. 运行产物

```text
automation/output/runs/{run_id}/
├── run.json       # 场景、profile、env、command、pid、variables
├── status.json    # running / passed / failed / stopped / error
├── steps.jsonl    # 结构化步骤
└── artifacts/     # pytest.log、截图、trace.zip、数据库快照等
```

## 8. AI/MCP 后续接口

Phase 1 已在此底座上增加 MCP 工具：

- `run_scenario`
- `get_run_status`
- `inspect_trace`
- `diagnose_environment`
- `run_ai_eval`
- `generate_ticket_cases`

MCP Server 不直接执行测试逻辑，只调用 `OpenRobotTestClient`。

## 9. 验收标准

- 任意 pytest 运行都有 run_id/trace_id。
- `run.json`、`status.json`、`steps.jsonl` 可读取。
- CLI 能列出场景、启动运行、查询状态和读取 Trace。
- 原有 API、框架、业务链路测试不回归。
- Trace 写入失败不能阻断测试执行。
- 不在日志中写入密钥、Token 或生产数据。

## 10. 后续阶段

| 阶段 | 内容 |
|---|---|
| Phase 1 | MCP Server、后台任务状态、通知、Trace 展示 |
| Phase 2 | Schemathesis 契约测试、真实环境 SSH 适配、DB/Redis/Qdrant 断言 |
| Phase 3 | Playwright UI、DeepEval/Ragas、工单驱动闭环 |
| Phase 4 | 多环境池、并发调度、报告回写、失败自诊断 |