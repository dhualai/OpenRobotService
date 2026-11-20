# AiDiagnosisPlatform 诊断 Agent 完整流程与提示词

> 代码路径：`ai/agents/AiDiagnosisPlatform/pipeline.py`（1744 行）
> API 路由：`ai/api/router.py` — `POST /api/ai/qa/ask` / `POST /api/ai/qa/ask/stream`

---

## 一、整体架构

```
用户消息 → run() / run_stream()
           │
           ├── 加载会话记忆 (Redis)
           ├── 加载/初始化 AgentState
           └── _agent_think() / _agent_think_stream()
                  │
                  ├── 节点 1: 转工单关键词短路 [无 LLM]
                  ├── 节点 2: pending_submit 自动提单 [无 LLM]
                  ├── 节点 3: 闲聊收尾短路 [仅流式]
                  ├── 节点 4: 指代消解 + 三路检索
                  ├── 节点 5: 构建 Prompt → LLM 推理
                  ├── 节点 6: 解析 LLM 输出 (JSON + 文本)
                  ├── 节点 7: 闭环保护 / pending_submit 自动提单 / 兜底提单
                  ├── 节点 8: 必填字段校验 → submit()
                  └── 节点 9: _finalize_diagnosis() 写回记忆
```

---

## 二、AgentState 状态机

| 字段 | 类型 | 说明 |
|------|------|------|
| `session_id` | str | 会话 ID |
| `phase` | str | `idle` → `diagnosing` → `resolved` / `escalated` |
| `problem_summary` | str | LLM 提炼的问题概述 |
| `hypotheses` | List[str] | 当前推测 |
| `ruled_out` | List[str] | 已排除原因 |
| `collected_info` | Dict[str,str] | 已收集信息（project、型号、版本等） |
| `diagnosis_rounds` | int | 本轮轮数 |
| `original_query` | str | 用户原始问题 |
| `last_submitted_ticket` | dict | 上一张工单摘要（ticket_id, title, topic） |
| `ticket_seq` | int | 工单序号（同会话多次提单自增） |
| `pending_submit` | bool | 用户说了转工单但缺项目，等用户补完触发 |

**状态流转：**
```
idle → (用户消息) → diagnosing → LLM action=answer → resolved
                               → LLM action=submit → escalated → submit() → resolved (清空诊断状态)
```

---

## 三、所有决策节点

### 节点 1：转工单关键词短路（`_short_kw`）

**触发条件**：用户消息含 `转工单|转单|生成工单|提交工单|提单|提个工单|提工单|帮我转|我要转|帮我提单`

```
  → _can_submit() 失败？ → 直接拒绝（"工单已提交处理中..."）
  → 缺 project？        → pending_submit=True，回复"请给出工单关联的项目名称"
  → 条件满足           → 直接 submit()（不调 LLM）
```

### 节点 2：pending_submit 自动提单

**触发条件**：`state.pending_submit == True`（上一轮缺项目被拦截）

```
  → 用户输入当作项目名 → _resolve_project() → submit()
```

### 节点 3：闲聊收尾短路（仅流式）

**触发条件**：消息匹配正则 `好的|ok|感谢|谢谢|拜拜|再见|bye|没事了|...`

```
  → 跳过 LLM，直接回复"好的/不客气，有问题随时找我"
```

### 节点 4：闭环保护（重复提单拦截）

**触发条件**：`_can_submit()` 返回 False

```python
def _can_submit(state):
    if phase in ("resolved","escalated") and not problem_summary:
        return False, "工单已提交处理中.../当前没有待处理的故障..."
    return True, ""
```

### 节点 5：LLM 输出解析（`_parse_agent_output`）

支持四种 JSON 格式：
- A) `` ```json {...} === 回复文本 ``（标准）
- B) `` ```json {...} ``` 回复文本 ``
- C) `{...} 回复文本`（裸 JSON）
- D) `{...}`（纯 JSON）

解析出 `{action, intent, thinking, state_update}` 后，提取 JSON 之后的文本作为 message。

### 节点 6：服务端兜底提单

**触发条件**：LLM 没设 `action=submit` 但：
- 消息里写了"已生成/提交/创建工单"（LLM 嘴嗨）
- 用户消息匹配工单意图正则

```python
_llm_claimed_submit = re.search(r'已(生成|提交|创建)|工单已|已为你', _msg)
_user_wants_submit = re.search(r'(提|转|生成|提交|下|创建|开|帮我|给我).{0,4}(工单|单子)|...')
```

→ 强制 `parsed["action"] = "submit"`

### 节点 7：必填字段校验（`_check_required_fields`）

提交前校验 `project` 不为空。缺字段 → 返回提示引导用户补充。

### 节点 8：补充工单（follow_up）

**触发条件**：上一轮刚提交工单，本轮 LLM `intent=follow_up` 或 `action=answer` 且非 howto/troubleshoot

→ `_append_to_ticket()` 把用户消息追加到工单描述

---

## 四、检索流程（`_retrieve_with_context`）

```
search_query = 最近4轮用户消息 + resolved_query + hypotheses + problem_summary
              ↓
         缓存命中？→ 直接返回
              ↓
     三路并行域检索（asyncio.gather）:
     ┌────────────┼────────────┐
     team/6       company/4    industry/3
     └────────────┼────────────┘
              ↓
     合并 → 按 sub_domain 贴标签 → 转文本
              ↓
     错误码提取 → 检查 cheduan 命中 → 未命中则插入提示
              ↓
     错误码精确检索 (retrieve_cheduan): 排最前
              ↓
     _rewrite_images() 转换图片路径
```

**sub_domain 标签映射：**
| sub_domain | 标签 |
|------------|------|
| faq, usp_faq | 📋 FAQ |
| cheduan_errors, cheduan_implementation | 🚗 车端 |
| translation | 🌐 翻译 |
| usp_manual, usp_product | 📖 手册 |
| product_catalog, vda5050_protocol | 🏢 产品 |
| 其他 | 📄 |

---

## 五、完整 DIAGNOSIS_PROMPT

```
你是 U老师，是「摇人吧」微信服务号的 AI 诊断助手，面向 AGV/AMR（工业移动机器人）行业的技术支持专家。
你的服务对象是现场工程师、客户和项目管理人员。

你的名字是"U老师"，严禁自称其他名字（如"小U""AI助手""智能助手"等）。
只在用户问"你是谁"或首次对话打招呼时才说"我是U老师"，其他情况不要重复自我介绍。

所服务的产品是 USP（Universal Scheduling Platform）大调度系统，用于 AGV/AMR 的调度管理、车辆管理、设备管理、地图编辑与监控运维。
USP 是网页端系统（PC浏览器访问），没有移动端APP。严禁在操作指引中提及"手机""移动端""APP"等概念——USP 只有 PC 浏览器版。
严禁给出手机、电脑等消费电子产品的通用回答，严禁超出 AGV/AMR 和 USP 领域。

## 服务号三个入口
- 🆘 我要摇人：报障提单、AI 在线诊断——你主要在这里
- 📥 系统任务：统一任务收件箱，处理工单——你在这里辅助工程师生成方案草稿
- 📊 后台管理：跨项目看板、风险管理、数据统计——你在这里提供分析建议

## 你的能力
1. 在线诊断：查知识库 → FAQ 有答案直接答 → 没有就逐步排查 → 搞不定自动转工单
2. 协助工程师：接到工单后自动生成解决方案草稿
3. 记住对话：记录原始问题、已排除原因、当前推测、已收集信息
如果以上都找不到答案，告知用户手册未覆盖、建议转工单，不会编造答案。

## 知识库使用优先级（极其重要）
1. FAQ：用户问的具体问题如果在 FAQ 中有直接匹配，优先直接回答
2. 🚗 车端错误码：用户提到车载/车端/AGV本体上的错误码时，直接匹配错误码给出原因和方案。
   ⚠️ 铁律：如果车端错误码显示「未找到匹配项」，该错误码确实不在系统收录范围内。
   你必须明确告知用户"该错误码未收录"，绝对禁止根据其他知识库内容编造。
3. 🌐 翻译表：用户问某个字段/标签/错误码的中英文含义时，从翻译表查找。
4. 知识库（操作手册）：howto 类操作问题走这里。

## ⛔ 转工单规则（优先级最高）
用户表示要创建/提交工单时 → action 设为 "submit"
用户表示不想继续排查时（如"不想排查""算了"）→ action 设为 "answer"，不要追问

## 🧑‍💼 转人工规则
含"转人工" → 告知没有在线人工客服，action="answer"，不追问项目信息。

## 意图判断
- howto（操作咨询）：直接 answer，知识库没涉及的如实说"手册未覆盖"
  ⚠️ 图片规则：严格按知识库原文的步骤结构配图，禁止把所有图片堆在一起
- troubleshoot（故障排查）：先查 FAQ → 没覆盖时列出可能原因 → 引导排除 → 搞不定转工单
- chat（闲聊/问候）：简单回应，不要追问技术问题

## 重要规则
- 只引用与用户问题直接相关的 chunk 内容
- 禁止在回复中暴露知识来源（不说"根据知识库"）
- 产品/车型介绍时，若有图片必须用 ![说明](url) 格式引用

## 对话
{conversation}

## 上一个工单上下文
{last_ticket_context}

## 状态：问题={problem_summary} | 已收集={collected_info} | 已排除={ruled_out} | 推测={hypotheses}
## 知识库：{reference_docs}
## 第{round}轮

---
输出 JSON：
{"action":"answer|ask|submit","intent":"howto|troubleshoot|chat","state_update":{...}}
JSON 之后直接写回复。
```

---

## 六、LLM 输出格式

```json
{
  "action": "answer | ask | submit",
  "intent": "howto | troubleshoot | chat",
  "thinking": "推理过程（内部用）",
  "state_update": {
    "problem_summary": "概述",
    "ruled_out": [],
    "hypotheses": [],
    "collected_info": {
      "project": "不可由 LLM 设置——被_apply_state_update 拦截"
    }
  }
}
```

---

## 七、_apply_state_update 规则

```python
def _apply_state_update(state, state_update):
    # project 由用户显式输入经 _resolve_project 设置，LLM 无权改动
    for k, v in state_update["collected_info"].items():
        if k == "project":
            continue                               # ← 拦截
        if v in ("无", "无无", "不清楚", "不知道", "暂无", "未知"):
            state.collected_info.pop(k, None)       # ← 无效值清除
        else:
            state.collected_info[k] = v             # ← 合并
```

---

## 八、submit() 提交流程

```
_build_ticket() → LLM 生成工单 JSON（type/title/description/priority 等）
       ↓
  ticket_seq += 1
       ↓
  upsert_task() → MySQL tasks 表（幂等 upsert by source+external_id）
       ↓
  清空诊断状态：phase=resolved, problem_summary="", hypotheses=[]
       ↓
  add_pending_ticket() → Redis 待派单池
```

---

## 九、项目匹配（`_resolve_project`）

```
用户输入 → ProjectMatcher.get_candidates(min_score=0.7)
          │
          ├── 0 候选 → 返回原始输入
          ├── 1 候选 → 直接返回该项目名
          └── 多个候选 → LLM 裁决（输出数字序号）
```

---

## 十、流式 SSE 事件类型

| event | data | 说明 |
|-------|------|------|
| `status` | `{stage: "retrieving"/"analyzing"/"submitting"/"submitted"/"submit_failed"/"need_fields"}` | 阶段状态 |
| `token` | `"文"` | 流式文字（逐字） |
| `title` | `{title: "..."}` | 第2轮后生成的会话标题 |
| `result` | `{type, action, message, agent_state, ticket?}` | 最终结果 |

---

## 十一、完整 API 接口

### POST /api/ai/qa/ask
```json
// Request
{"session_id": "xxx", "query": "错误码402什么意思", "skip_retrieval": false}

// Response
{
  "type": "diagnosis",
  "thinking": "",
  "action": "answer",
  "message": "我是U老师。错误码402是...",
  "agent_state": {"phase": "resolved", "problem_summary": "...", ...},
  "title": "错误码402查询"
}
```

### POST /api/ai/qa/ask/stream
同上，响应为 SSE 流：`{event: "token", data: "文"}` → `{event: "result", data: {...}}`

### POST /api/ai/qa/upload
上传图片/附件 → VLM 分析 → 写入对话历史。附带 `message` 参数时会走诊断 pipeline。

### GET /api/ai/qa/health
健康检查。

---

## 十二、关键配置项

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `RETRIEVAL_TOP_K` | 3 | 检索返回数 |
| `RETRIEVAL_SCORE_THRESHOLD` | 0.65 | 置信度阈值 |
| `REDIS_MAX_CONTEXT_TURNS` | 3 | 历史轮数 |
| `AI_CHAIN_TIMEOUT` | 2.5s | LLM 单轮超时 |
| `LLM_READ_TIMEOUT` | 30s | LLM 读超时 |
| `_CACHE_TTL` | 60s | 检索缓存 |

---

## 十三、流式 JSON 边界检测（`_find_json_end`）

流式场景下 LLM 逐 token 输出。`_find_json_end()` 实时跟踪缓冲区，检测 JSON 结束、自然语言开始的边界：
- 检测 fenced JSON 的 `}```  闭合
- 跟踪裸 JSON 的括号深度（支持字符串内转义）
- JSON 区域结束前的 token 丢弃，之后的 token 实时 yield 给前端
