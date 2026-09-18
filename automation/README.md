# automation/ - 自动化测试平台

OpenRobotService 的 pytest 自动化测试框架：**代码驱动用例 + MockBackend + Allure 报告**。
接口测试按业务模块编写 `tests/{module}/test_{module}_code.py`，默认走内存 MockBackend，设置 `USE_MOCK=0` 后可切换真实后端。

## 目录结构

```
automation/
├── src/                       # 框架库（import 调用）
│   ├── clients/               # ApiClient / MySQL / Redis / Qdrant
│   ├── assertions/            # 断言工具（含报告聚合）
│   ├── fixtures/              # pytest 夹具
│   ├── logger/                # 日志（控制台/文件/Allure）
│   ├── mocks/                 # MockBackend（httpx.MockTransport）
│   ├── ai_metrics/            # AI 评估指标（schema / recall / faithfulness / veto）
│   ├── reporting/             # Allure 元数据
│   └── utils/                 # retry / timer / helpers
├── config/                    # 配置（local / sit / uat，各含 config.yaml）
├── tests/                     # 测试用例（按业务模块）
│   ├── call/ tasks/ admin/ auth/ wechat/ integrations/
│   ├── ai/                    # AI 质量评估（L1/L2/L3）
│   ├── real/                  # 真实后端冒烟（USE_MOCK=0）
│   └── infrastructure/        # MySQL / Redis / Qdrant 契约检查
├── ci_ai_gen/                 # AI 测试生成流水线（analyze → cases → script → gate）
├── scripts/                   # CLI 工具
├── testdata/fixtures/         # 静态测试数据（含 AI golden 数据集）
├── references/                # PRD / 接口文档 / 归档用例
├── docs/                      # 方案、规范、工作记录
├── output/                    # 测试产出（gitignored）
├── conftest.py                # Allure 报告自动生成钩子
├── pyproject.toml             # pytest 配置与依赖
└── AGENTS.md                  # AI 工作规范
```

## 快速开始

```powershell
cd automation
pip install -e .

# 框架库 + 配置 + CI 生成器（Fast Lane，不连外部服务）
pytest src config ci_ai_gen mcp_server -v

# API Mock 测试
pytest tests/ -m api -v

# 指定模块
pytest tests/tasks -v
```

### 统一测试运行时

```powershell
python scripts/cli-openrobot.py health
python scripts/cli-openrobot.py scenarios
python scripts/cli-openrobot.py run --scenario business_chain --profile fast
python scripts/cli-openrobot.py status --run-id <run-id>
python scripts/cli-openrobot.py trace --run-id <run-id>
```

每次 pytest 或 CLI 运行都会在 `output/runs/{run_id}/` 下生成：

- `run.json`：场景、profile、环境、命令
- `status.json`：running / passed / failed / stopped
- `steps.jsonl`：结构化步骤
- `artifacts/`：日志、截图、trace 等

运行规则见 `automation/rules.md`，设计见 `automation/docs/design-openrobot-test-runtime.md`。

### 多环境调度与 Ticket Gate

```powershell
# 环境池与并发调度
python -c "from automation.src.runtime import RunScheduler, ScheduledTask; print(RunScheduler().run_many([ScheduledTask('api_mock', tags=('fast',))]))"

# 报告回写工单
python scripts/cli-ticket-writeback.py --ticket-id 837 --run-id <run-id>

# test 分支 ticket gate
python scripts/cli-ticket-gate.py --message-file commit-message.txt `
  --regression tests/business_chain --env local
```

细节见 `automation/docs/ENVIRONMENT_SCHEDULING.md`、`automation/docs/TICKET_GATE.md`。

### 外部 AI 评测与工单闭环

```powershell
# DeepEval / Ragas 可选依赖
pip install -e "automation/[external-eval]"

# 工单扫描、生成、promotion、按工单选回归用例
python scripts/cli-ticket-pipeline.py scan --limit 20
python scripts/cli-ticket-pipeline.py run --limit 20
python scripts/cli-ticket-pipeline.py select --text "fix ORS-837" --regression tests/business_chain
```

细节见 `automation/docs/AI_EXTERNAL_EVAL.md` 和 `automation/docs/TICKET_PIPELINE.md`。

### 契约测试与 UI Smoke

```powershell
# OpenAPI 契约冒烟
$env:CONTRACT_TEST_ENABLED = "1"
pytest tests/contract -m contract -v

# UI Smoke
$env:PLAYWRIGHT_BASE_URL = "http://127.0.0.1:5173"
pytest tests/ui -m ui -v
```

契约细节见 `automation/docs/CONTRACT_TESTING.md`，UI 细节见 `automation/docs/UI_TESTING.md`，
真实环境 SSH 见 `automation/docs/REMOTE_TESTING.md`。

### MCP Server

```powershell
cd D:\WorkCode\OpenRobotService
pip install -e automation
python -m automation.mcp_server
```

MCP 工具：

- `run_scenario`
- `get_run_status`
- `inspect_trace`
- `diagnose_environment`
- `run_ai_eval`
- `generate_ticket_cases`

配置和使用见 `automation/mcp_server/README.md`。

### AI 评估测试

AI 测试需要真实 AI 服务与 AI 运行时依赖：

```powershell
pip install -r ../ai/requirements.txt
AI_EVAL_BASE_URL=http://localhost:8401 pytest tests/ai -v
```

AI 服务未启动或 judge 未配置时，用例会明确 `skip`，不会伪装成通过。

### 真实后端冒烟

```powershell
USE_MOCK=0 REAL_API_BASE_URL=http://localhost:8400 pytest tests/real -m smoke -v
```

默认管理员账号可通过 `REAL_ADMIN_USERNAME` / `REAL_ADMIN_PASSWORD` 覆盖。

## 添加测试用例

1. 在 `tests/{module}/test_{module}_code.py` 添加 `test_*` 自由函数，用 `@pytest.mark.api` 标记。
2. 请求统一通过测试模块内的 `_api()` helper，自动生成 Allure step、请求/响应附件和断言信息。
3. 若接口在真实后端已存在，先确认 `src/mocks/backend_mock.py` 支持该路由；再用 `USE_MOCK=0` 验证真实契约。

## 常用脚本

| 脚本 | 用途 |
|------|------|
| `scripts/cli-merge-ai-cases.py` | 合并 AI 生成用例（`--dry-run` 预览） |
| `scripts/cli-compare-ai-runs.py` | 对比 AI 评估运行记录 |
| `scripts/cli-update-bad-cases.py` | 更新 AI bad-case 台账 |
| `scripts/cli-gen-case-inventory.py` | 生成用例清单 |
| `scripts/cli-gen-scenario-coverage.py` | 生成场景覆盖报告 |

详细规范见 `AGENTS.md` 和 `docs/`。