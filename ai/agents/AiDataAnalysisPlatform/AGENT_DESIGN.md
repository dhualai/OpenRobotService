# AiDataAnalysisPlatform · Agent 节点与 Prompt 版本管理设计

> 本文档定义 `ai/agents/AiDataAnalysisPlatform` 的 Agent 节点拆分、Prompt 版本管理、流程编排与全节点暴露方案。
> 目标：让数据分析 Agent 从"黑盒调用"变成"可观测、可回滚、可插拔"的流水线。

---

## 1. 设计目标

| 目标 | 说明 |
|------|------|
| **节点全暴露** | 把一次 `analyze` / `chat` 调用拆分为独立节点，每个节点均可观测、可替换、可复用。 |
| **Prompt 版本管理** | 系统 Prompt、用户 Prompt、输出格式要求支持版本化注册、选择、回滚与 A/B 对比。 |
| **流程可编排** | 用显式 Pipeline 取代硬编码顺序，支持同步 / 流式两条路径共用同一节点定义。 |
| **Trace 可观测** | 每个 API 响应附带 `_trace` 数组，记录节点耗时、输入输出快照、异常与版本信息。 |

---

## 2. 当前代码结构

| Phase | 内容 | 文件 |
|:------|:-----|:-----|
| 1 | 骨架搭建 | `__init__.py` / `schemas.py` / `config.py` / `prompts.py` |
| 2 | 分析引擎 | `analyzer.py`（数据预处理 + LLM 调用 + 结果解析） |
| 3 | LLM 客户端 | `llm_client.py`（HTTP 调用 `ai/api/router.py`） |
| 4 | Agent 门面 | `agent.py`（`analyze` / `analyze_stream` / `chat` / `health_check`） |
| 5 | API 路由 | `router.py`（`/health` `/analyze` `/chat` `/types`） |

> 当前 `analyzer.py` 内部把"预处理 → 构建 Prompt → 调用 LLM → 解析"耦合在一起，尚未显式拆分为可观测节点。

---

## 3. Agent 节点拆分（全节点暴露）

一次完整的数据分析请求 `POST /api/ai/analysis/analyze` 将拆分为以下节点：

| 节点 | 职责 | 当前位置 | 未来独立模块 |
|:-----|:-----|:---------|:-------------|
| `validate` | 校验请求参数、数据非空、枚举合法 | `router.py` / `analyzer.preprocess_data` | `nodes/validate.py` |
| `preprocess` | 数据格式化：JSON 美化 / CSV 转 Markdown / 文本原样 | `analyzer.preprocess_data` | `nodes/preprocess.py` |
| `load_context` | 加载补充上下文（预留：任务、工单、会话历史） | `agent.analyze` 参数透传 | `nodes/context.py` |
| `select_prompt_version` | 根据 `analysis_type` + `prompt_version` 选择 Prompt 模板 | `prompts.build_system_prompt` | `prompts/registry.py` |
| `build_prompt` | 组装 system / user prompt | `prompts.build_system_prompt` / `build_user_prompt` | `nodes/build_prompt.py` |
| `call_llm` | 调用大模型（同步或流式） | `llm_client.chat` / `chat_stream` | `nodes/call_llm.py` |
| `parse_result` | 将 Markdown 回复解析为结构化 `AnalysisResult` | `analyzer._parse_result` | `nodes/parse_result.py` |
| `format_output` | 按 schema 填充 summary / insights / recommendations | `analyzer.analyze` | `nodes/format_output.py` |
| `record_trace` | 收集各节点耗时、版本、异常 | 暂无 | `pipeline.py` |

### 3.1 节点暴露形式

每个节点统一实现为可调用对象：

```python
class PipelineNode(Protocol):
    name: str
    async def __call__(self, ctx: AnalysisContext) -> AnalysisContext: ...
```

- 节点通过 `AnalysisContext` 共享状态，避免过长的参数列表。
- 节点可单独测试、Mock、替换。
- Pipeline 支持在中间件层记录 `_trace`。

---

## 4. Prompt 版本管理

### 4.1 当前现状

`prompts.py` 中 `_BASE_SYSTEM_PROMPT` 和 `_TYPE_INSTRUCTIONS` 为硬编码字符串，所有用户共用同一套 Prompt，无法：

- 回滚到历史 Prompt；
- 针对特定业务场景切换 Prompt；
- 做 A/B 实验对比效果。

### 4.2 版本化设计

```text
prompts/
├── __init__.py
├── base.py                 # PromptVersion 数据类
├── registry.py             # PromptRegistry 注册表
├── versions/
│   ├── v1.0.0_base.py      # 初始版本（当前 prompts.py 内容）
│   ├── v1.1.0_fault.py     # 故障分析加强版
│   └── v1.2.0_summary.py   # 摘要格式优化版
```

### 4.3 Prompt 注册表示例

```python
# prompts/registry.py
class PromptRegistry:
    def __init__(self):
        self._versions: dict[str, PromptTemplate] = {}

    def register(self, template: PromptTemplate) -> None:
        self._versions[template.version] = template

    def get(self, version: str | None, analysis_type: AnalysisType) -> PromptTemplate:
        version = version or self.default_version
        if version not in self._versions:
            raise ValueError(f"未知 Prompt 版本: {version}")
        return self._versions[version]
```

### 4.4 版本选择策略

| 维度 | 说明 |
|------|------|
| **默认版本** | 从环境变量 `ANALYSIS_PROMPT_VERSION` 读取，缺省为 `v1.0.0`。 |
| **请求级覆盖** | `AnalysisRequest` 增加 `prompt_version: str | None` 字段，单次请求可指定版本。 |
| **按类型绑定** | 注册表允许同一版本内为不同 `AnalysisType` 定义不同指令。 |
| **灰度发布** | 通过配置中心按 `user_id` / `tenant` 分配不同版本（预留扩展点）。 |

### 4.5 版本内容规范

每个 Prompt 版本必须包含：

```python
@dataclass
class PromptTemplate:
    version: str
    base_system_prompt: str
    type_instructions: dict[AnalysisType, str]
    output_format: str
    metadata: dict[str, Any]  # author, changelog, model_hint 等
```

---

## 5. 流程管理（Pipeline）

### 5.1 Pipeline 结构

引入 `pipeline.py` 作为流程编排器，取代 `analyzer.py` 中的硬编码顺序。

```python
# pipeline.py
class AnalysisPipeline:
    def __init__(self, nodes: list[PipelineNode], trace_enabled: bool = True):
        self.nodes = nodes
        self.trace_enabled = trace_enabled

    async def run(self, ctx: AnalysisContext) -> AnalysisContext:
        for node in self.nodes:
            start = time.monotonic()
            try:
                ctx = await node(ctx)
            except Exception as exc:
                ctx.add_trace(node.name, elapsed_ms=..., status="error", error=str(exc))
                raise
            else:
                ctx.add_trace(node.name, elapsed_ms=..., status="ok")
        return ctx
```

### 5.2 默认流程定义

```python
# 同步分析流程
DEFAULT_ANALYSIS_PIPELINE = [
    ValidateNode(),
    PreprocessNode(),
    LoadContextNode(),
    SelectPromptVersionNode(),
    BuildPromptNode(),
    CallLLMNode(stream=False),
    ParseResultNode(),
    FormatOutputNode(),
]

# 流式分析流程：只到 call_llm 节点，后续不解析
DEFAULT_STREAM_PIPELINE = [
    ValidateNode(),
    PreprocessNode(),
    LoadContextNode(),
    SelectPromptVersionNode(),
    BuildPromptNode(),
    CallLLMNode(stream=True),
]
```

### 5.3 与现有代码的关系

| 现有文件 | 改造后 |
|:---------|:-------|
| `analyzer.py` | 保留 `DataAnalyzer` 作为高层门面，内部改为组合 `AnalysisPipeline`。 |
| `agent.py` | 不变，继续对外暴露 `analyze` / `analyze_stream` / `chat`。 |
| `router.py` | 响应中增加 `_trace` 字段。 |
| `prompts.py` | 拆分为 `prompts/` 包，原有函数保留为 `v1.0.0` 的兼容入口。 |

---

## 6. API 响应中的 `_trace`

### 6.1 Trace 节点定义

参考图片中的"全节点埋点"设计，数据分析接口返回的 `_trace` 包含 9 个节点：

```json
{
  "analysis_type": "fault",
  "summary": "...",
  "insights": [],
  "recommendations": [],
  "_trace": [
    {"node": "validate",       "status": "ok",   "elapsed_ms": 0.2,  "version": null},
    {"node": "preprocess",     "status": "ok",   "elapsed_ms": 3.5,  "output_len": 2048},
    {"node": "load_context",   "status": "ok",   "elapsed_ms": 1.0,  "context_len": 0},
    {"node": "select_prompt",  "status": "ok",   "elapsed_ms": 0.1,  "prompt_version": "v1.0.0"},
    {"node": "build_prompt",   "status": "ok",   "elapsed_ms": 0.3,  "system_len": 512, "user_len": 2100},
    {"node": "call_llm",       "status": "ok",   "elapsed_ms": 3200, "model": "deepseek-chat"},
    {"node": "parse_result",   "status": "ok",   "elapsed_ms": 5.0,  "insights_count": 3},
    {"node": "format_output",  "status": "ok",   "elapsed_ms": 0.5,  "output_schema": "AnalysisResult"},
    {"node": "record_trace",   "status": "ok",   "elapsed_ms": 0.1,  "trace_count": 9}
  ]
}
```

### 6.2 流式接口的 Trace

流式响应通过 SSE 最后一条事件返回 `_trace`：

```text
data: {"content": "..."}
...
data: {"trace": [...]}
```

---

## 7. 数据模型扩展

### 7.1 `AnalysisRequest` 新增字段

```python
class AnalysisRequest(BaseModel):
    # ... 原有字段 ...
    prompt_version: str | None = Field(
        default=None, description="指定 Prompt 版本，缺省使用全局默认版本"
    )
    enable_trace: bool = Field(
        default=True, description="是否在响应中返回节点级 trace"
    )
```

### 7.2 `AnalysisResult` 新增字段

```python
class AnalysisResult(BaseModel):
    # ... 原有字段 ...
    prompt_version: str | None = Field(
        default=None, description="实际使用的 Prompt 版本"
    )
    trace: list[dict[str, Any]] | None = Field(
        default=None, alias="_trace", description="节点执行轨迹"
    )
```

---

## 8. 实现路线图

| Phase | 内容 | 文件 |
|:------|:-----|:-----|
| 1 | 创建 Pipeline 骨架与 Context | `pipeline.py` / `context.py` |
| 2 | 拆分 Analyzer 为独立节点 | `nodes/*.py` |
| 3 | Prompt 版本化改造 | `prompts/` 包 + `registry.py` |
| 4 | 接入 Trace 收集 | `pipeline.py` + `schemas.py` |
| 5 | 更新 API 响应 | `router.py` |
| 6 | 单元测试与回归验证 | `tests/` |

---

## 9. 接口契约（不变）

改造后对外 HTTP 接口保持兼容：

| 端点 | 方法 | 说明 |
|:-----|:-----|:-----|
| `/api/ai/analysis/health` | GET | 健康检查 |
| `/api/ai/analysis/analyze` | POST | 非流式 / 流式数据分析 |
| `/api/ai/analysis/chat` | POST | 快速对话 |
| `/api/ai/analysis/types` | GET | 分析类型与数据源枚举 |

新增字段 `prompt_version` / `enable_trace` 均为可选，旧客户端无需改动。

---

## 10. 关键设计原则

1. **向后兼容**：现有 `analyze` / `chat` 接口的字段与行为不变。  
2. **显式优于隐式**：Pipeline 节点、Prompt 版本、Trace 信息全部显式注册与返回。  
3. **可测试性**：每个节点可独立实例化与 Mock，Pipeline 可注入自定义节点序列。  
4. **可观测性**：任何节点失败时，`_trace` 中保留已执行节点的快照，便于定位。  

---

*文档版本：v1.0*  
*所属模块：`ai/agents/AiDataAnalysisPlatform`*  
*维护者：OpenRobotService AI 服务团队*
