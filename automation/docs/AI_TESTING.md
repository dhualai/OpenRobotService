# AI 质量评估模块设计（P2）

> 状态：设计稿（待人工确认） | 作者：automation 测试架构 | 日期：2026-08-06

---

## 1. 背景与目标

### 1.1 现状缺口

| 现有体系 | 测什么 | 测不了什么 |
|---------|--------|-----------|
| `automation/tests/` API 用例（92 条） | 接口通断、状态码、字段断言 | LLM 输出**质量**（非确定，无法硬断言） |
| `ai/tests/` 脚本测试 | 人工连通性验证 | 回归门禁、质量量化、报告 |
| MockBackend AI 域 | 接口 mock 通断 | 真实推理质量 |

### 1.2 目标

- 建立 **golden 数据集 + 三层评估** 的 AI 质量回归体系，落地 `automation/` 内
- 每次修改 prompt / 知识库 / 检索阈值后，一键运行即可判断"质量是否下降"
- 幂等可重复、可进 CI、输出 Allure 报告（符合 AGENTS.md 硬性要求）

### 1.3 范围

- 覆盖三个 Agent：`AiDiagnosisPlatform`（诊断/提单）、`AiTaskPlatform`（方案草稿）、`AiDataAnalysisPlatform`（数据分析）
- 覆盖 5 路 RAG 检索质量
- **不修改** `ai/` `backend/` `frontend/` 任何业务代码（只读阅读契约）

---

## 2. 总体设计：三层评估金字塔

```
┌─────────────────────────────────────────────────┐
│  L3  LLM-as-Judge（rubric 打分 1-5）             │  ← 贵，-m slow 单独跑
│     诊断质量 / 工单字段 / 派单 reasoning 质量      │
├─────────────────────────────────────────────────┤
│  L2  指标层（faithfulness / retrieval recall@k）  │  ← 半价，核心回归门
│     回答是否基于检索文档（防幻觉）                 │
├─────────────────────────────────────────────────┤
│  L1  确定性断言层（JSON schema / 关键词 / 状态机） │  ← 免费，CI 必跑
│     action / phase / decision_type / 字段完整     │
└─────────────────────────────────────────────────┘
```

**分层原因**：LLM 输出非确定 → 用"打分"代替"断言"；确定性断言成本为零，优先跑；LLM-judge 最贵，用阈值 + 采样控制预算。

### 2.1 执行模式

| 模式 | 调用方式 | 测什么 | 幂等性 |
|------|---------|--------|--------|
| A. 隔离推理 | `POST /api/ai/qa/ask` + `skip_retrieval=true` | Agent 推理/状态机/JSON 结构（不依赖知识库数据） | 高 |
| B. 全链路 | 真实服务 + 真实 Qdrant（知识库已入库） | 检索质量 + 回答忠实度 | 中（依赖库内容） |
| C. 纯 mock | 现有 MockBackend AI 域 | 接口通断、降级路径 | 高（已有） |

- 默认跑 **A + B**（A 走 L1，B 走 L1+L2）
- C 已有 API 用例覆盖，不重复建设

---

## 3. 目录结构与文件清单

```
automation/
├── src/ai_metrics/          # ★ 新增：指标层（纯 Python，零新依赖）
│   ├── __init__.py
│   ├── schema_validity.py              # L1: JSON 字段完整/类型/枚举
│   ├── keyword_hit.py                  # L1: 关键信息点命中率
│   ├── retrieval_recall.py             # L2: 5 路检索 recall@k（对 golden 期望命中）
│   ├── faithfulness.py                 # L2: 回答是否基于 reference_docs（LLM 判定）
│   └── llm_judge.py                    # L3: rubric 打分（复用 ai.core LLMClient，只读导入）
├── src/ai_metrics/tests/    # ★ 新增：指标自测（Fast Lane）
│   └── test_schema_validity.py
├── testdata/fixtures/ai/               # ★ 新增：golden 数据集（JSON）
│   ├── diagnosis.json                  # 诊断 Agent：多轮场景
│   ├── assigner.json                   # 智能派单：期望 decision_type/置信度区间
│   ├── rag_retrieval.json              # 5 路检索：期望命中 collection
│   ├── data_analysis.json              # 数据分析：期望摘要/洞察关键词
│   └── task_agent.json                 # 任务 Agent：期望 SolutionDraft 字段
├── tests/ai/                           # ★ 新增：评估用例
│   ├── conftest.py                     # ai_client fixture（httpx → 真实 8401）
│   ├── runner.py                       # load_ai_cases + run_ai_case（仿 common/test_runner.py）
│   ├── test_diagnosis.py
│   ├── test_assigner.py
│   ├── test_rag.py
│   ├── test_data_analysis.py
│   └── test_task_agent.py
├── docs/AI_TESTING.md                  # 本文档
└── pyproject.toml                      # 变更：新增 marker "judge"
```

**不改动**：`src/runner/`、`backend_mock.py`（AI 域 mock 已有）、`testdata/cases/`。

---

## 4. Golden 数据集设计（`testdata/fixtures/ai/`）

### 4.1 通用字段

| 字段 | 说明 | 示例 |
|------|------|------|
| `id` | `DIAG-001` 等 | `DIAG-001` |
| `mode` | `skip_retrieval` / `full`（对应执行模式 A/B） | `skip_retrieval` |
| `session_id` | 多轮会话分组（同组共享） | `eval-diag-001` |
| `turns` | 多轮输入（每轮一个 query） | `["车不动了，任务一直调度中"]` |
| `expect` | 期望行为（见下） | — |
| `note` | 覆盖类型：正常/异常/AI/边界 | — |

### 4.2 `expect` 结构（L1/L2/L3 分层断言）

```json
{
  "expect": {
    "l1": {
      "actions": ["ask", "answer", "submit"],
      "phase": "diagnosing",
      "key_terms": ["路径规划", "定位"],
      "schema": {"message": "str", "agent_state.hypotheses": "list"}
    },
    "l2": {
      "expect_hits": {"retrieve": ["操作手册"], "retrieve_faq": ["FAQ"]}
    },
    "l3": {
      "rubric": "回答基于检索内容且不编造检查项",
      "min_score": 4
    }
  }
}
```

### 4.3 示例用例（真实场景，源自 docs/ 知识库源文件）

```json
{
  "id": "DIAG-001",
  "mode": "full",
  "turns": ["车子不动了，任务状态一直显示调度中"],
  "expect": {
    "l1": {
      "actions": ["ask"],
      "phase": "diagnosing",
      "key_terms": ["调度", "定位", "排查"],
      "schema": {"type": "str", "action": "str", "agent_state.phase": "str"}
    },
    "l2": {"expect_hits": {"retrieve": []}},
    "l3": {"rubric": "回复必须引导确认具体现象，不得直接给出最终结论", "min_score": 4}
  }
}
```

```json
{
  "id": "ASSIGN-001",
  "mode": "skip_retrieval",
  "turns": ["潜伏车无法移动，MQTT 连接正常但调度下发任务无响应"],
  "expect": {
    "l1": {
      "decision_type": ["auto", "recommend"],
      "confidence_min": 0.5,
      "schema": {"assignee": "str", "reasoning": "str"}
    }
  }
}
```

### 4.4 数据来源

- 场景取自 `ai/docs/` 知识库源文件（FAQ、问题排查树_v1.json、操作手册）中真实高频问题
- 期望值依据 `AI_Service_Description.md` 中的行为契约（action/phase/decision_type 定义）
- 首批规模：诊断 10 条 / 派单 6 条 / RAG 8 条 / 数据分析 4 条 / 任务 Agent 4 条，共 **32 条**

---

## 5. 指标设计

| 指标 | 层级 | 实现 | 判定 |
|------|------|------|------|
| `schema_validity` | L1 | 递归检查字段存在/类型/枚举 | 缺失即失败 |
| `keyword_hit` | L1 | 期望关键词命中率 | ≥ 阈值（默认 0.8） |
| `retrieval_recall` | L2 | golden 期望命中文档是否出现在 top-k 结果 | recall@k ≥ 阈值 |
| `faithfulness` | L2 | LLM 判定回答是否被 reference_docs 支撑（复用 `ai.core.llm`，只读 import） | score ≥ 阈值 |
| `llm_judge` | L3 | rubric 提示词打分 1-5 | ≥ min_score |

**成本控制**：
- L1 全跑（免费）；L2 每用例 1 次 judge 调用；L3 仅 `-m judge` 或 `-m slow` 跑
- judge 模型用 `ai/.env` 现有 DeepSeek 配置，不新增厂商

---

## 6. 测试用例组织（`tests/ai/`）

沿用 `test_call.py` 数据驱动模式：

```python
CASES = load_ai_cases("diagnosis")

@pytest.mark.ai
class TestDiagnosisEval:
    @pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
    async def test_eval(self, ai_client, case):
        result = await run_ai_case(ai_client, case)
        assert result.passed, result.report
```

- `conftest.py` 提供 `ai_client` fixture：`httpx.AsyncClient(base_url=http://localhost:8401)`，服务未启动时 `pytest.skip`（不阻塞本地无服务场景）
- 多轮会话按 `session_id` 分组串行执行，单轮并行
- Allure 附件：每用例附 request/response/score 明细
- `pyproject.toml` 新增 marker `judge`

### 运行命令

```powershell
cd automation; pytest tests/ai/ -m "not judge" -v        # L1+L2 快速回归
cd automation; pytest tests/ai/ -v                        # 全部（含 judge）
cd automation; pytest src/ai_metrics/tests/ -v # 指标自测
```

---

## 7. 实现步骤（按顺序，一次一个模块）

| 步骤 | 内容 | 验收 |
|------|------|------|
| 1 | 本设计文档确认 | 人工确认 |
| 2 | `src/ai_metrics/`（schema_validity + keyword_hit + 自测） | `pytest src/ai_metrics/tests/ -v` 通过 |
| 3 | `testdata/fixtures/ai/diagnosis.json` + `tests/ai/conftest.py + runner.py + test_diagnosis.py` | L1 全绿 |
| 4 | `rag_retrieval.json` + `retrieval_recall.py` + `test_rag.py` | recall 指标可算 |
| 5 | `assigner.json` + `test_assigner.py` | 派单 decision_type 断言 |
| 6 | `faithfulness.py` + 接入 L2 | faithfulness 可算 |
| 7 | `llm_judge.py` + `-m judge` + `data_analysis.json` + `task_agent.json` | L3 全链路 |
| 8 | Allure 报告验证 + worklog + CI 脚本（fast-lane 加 ai 模块） | 报告生成成功 |

> 步骤 3-7 每步完成即运行测试并更新 worklog，符合"设计→实现→测试→文档"循环。

---

## 8. 风险分析

| 风险 | 等级 | 缓解 |
|------|------|------|
| LLM 输出非确定导致偶发失败 | 高 | 分层阈值 + `-m slow` 隔离 + 重试 1 次 |
| 真实服务未启动 | 中 | `ai_client` fixture 检测不可达即 skip，不误报 |
| judge 调用成本 | 中 | 默认不跑 L3，采样控制；小数据集起步 |
| 知识库内容变更导致 golden 失效 | 中 | golden 数据与 `ai/docs/` 源文件版本关联，worklog 记录 |
| 检索期望命中不准确（文档切块差异） | 中 | 期望只到 collection 级别，不断言具体 chunk id |
| `ai/` 目录变更导致只读导入失效 | 低 | 仅 import `ai.core.llm` 稳定接口，变更时 review |

---

## 9. 确认决策（2026-08-06）

| # | 问题 | 决策 |
|---|------|------|
| 1 | 评估通道 | ✅ 走真实 AI 服务 HTTP（8401），服务不可达自动 skip |
| 2 | Judge 模型 | ✅ 复用现有 DeepSeek 配置（`ai/.env`），零新增依赖 |
| 3 | Golden 数据源 | ✅ 从 `ai/docs/` 知识库高频问题抽取 |
| 4 | CI 集成 | ✅ 本期不加，仅本地运行，下一期再议 |

---

## 10. 实现记录与偏差（2026-08-06 首期交付）

首期已实现：`ai_metrics/`（schema/keyword/recall/faithfulness/rubric + LLMJudgeClient）、4 个 golden 数据集（diagnosis 10 / assigner 6 / rag 8 / data_analysis 4）、4 个测试文件。**42 个指标自测通过，28 个 AI 用例在无服务/无依赖环境优雅跳过**。

| 原设计 | 实际实现 | 原因 |
|--------|---------|------|
| L2 用真实检索文档判 faithfulness | golden 用例**内嵌参考文档**（`expect.l2.reference_docs`），HTTP 响应不含检索文档 | AI 服务无检索结果暴露端点，且不得改 `ai/` 代码 |
| judge 复用 `ai.core` LLMClient | `LLMJudgeClient` 基于 automation 自带 **openai SDK** 独立实现（读 ai/.env 的 DEEPSEEK 配置） | 本环境无 AI 运行时依赖（tenacity 等） |
| RAG/派单走 HTTP | `run_rag_case` / `run_assigner_case` 惰性导入 `ai.core`（需 AI 环境），不可用时跳过 | 无检索/派单 HTTP 端点；`/api/ai/ticketReferee` 文档存在但路由未实现 |
| 任务 Agent 数据集 | **延期至下一期** | `/api/ai/task/analyze` 依赖 backend 任务上下文 + 认证，属重依赖 |
| golden 数据源 | 知识库源文件（ai/docs/）不在仓库，基于接口契约 + 文档示例构造 | 场景后续可按真实入库内容替换 |

### 运行命令

```powershell
cd automation; pytest src/ai_metrics/tests/ -v        # 指标自测（Fast Lane）
cd automation; pytest tests/ai/ -m "not judge" -v                # L1+L2（服务可用时）
cd automation; pytest tests/ai/ -v                               # 全部（含 judge 调用）
```

### 环境要求

| 评估 | 需要 |
|------|------|
| 诊断 / 数据分析（HTTP） | AI 服务运行于 localhost:8401（`AI_EVAL_BASE_URL` 可覆盖） |
| RAG recall / 派单 | AI 运行时依赖（在 ai 环境安装 automation 后运行） |
| L2 faithfulness / L3 judge | `DEEPSEEK_API_KEY` 等（ai/.env） |
