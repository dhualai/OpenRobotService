"""
统一智能诊断 Agent（纯 Agent 架构）

所有消息统一走 Agent 路径。
Agent 自主决策：检索知识库 → 初步引导 → 追问 → 给出方案 → 转工单。
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import httpx
from typing import Optional, Dict, List
from dataclasses import dataclass, field

from ai.config import get_ai_config
from ai.exceptions import AITimeoutError, LowConfidenceError, ServiceUnavailableError, RetrieveEmptyError
from ai.core import get_llm_client, get_retrieval_service, get_memory_manager


# ============================================================
# 数据结构
# ============================================================

@dataclass
class DiagnosisRequest:
    session_id: str
    query: str
    rewritten_query: Optional[str] = None
    skip_retrieval: bool = False  # 测试用：跳过 KB 检索


@dataclass
class AgentState:
    """Agent 对问题的持续理解——每轮诊断更新"""
    session_id: str
    problem_summary: str = ""
    ruled_out: List[str] = field(default_factory=list)
    hypotheses: List[str] = field(default_factory=list)
    collected_info: Dict[str, str] = field(default_factory=dict)
    diagnosis_rounds: int = 0
    phase: str = "idle"           # idle | diagnosing | escalated | resolved
    original_query: str = ""


# ============================================================
# 状态辅助函数
# ============================================================

def _load_agent_state(metadata: dict) -> Optional[AgentState]:
    s = metadata.get("agent_state")
    if not s:
        return None
    return AgentState(
        session_id=s["session_id"],
        problem_summary=s.get("problem_summary", ""),
        ruled_out=s.get("ruled_out", []),
        hypotheses=s.get("hypotheses", []),
        collected_info=s.get("collected_info", {}),
        diagnosis_rounds=s.get("diagnosis_rounds", 0),
        phase=s.get("phase", "idle"),
        original_query=s.get("original_query", ""),
    )


def _save_agent_state(memory, state: AgentState) -> None:
    existing = memory.metadata.get("agent_state", {})
    memory.metadata["agent_state"] = {
        "session_id": state.session_id,
        "problem_summary": state.problem_summary,
        "ruled_out": state.ruled_out,
        "hypotheses": state.hypotheses,
        "collected_info": state.collected_info,
        "diagnosis_rounds": state.diagnosis_rounds,
        "phase": state.phase,
        "original_query": state.original_query,
        "attachments": existing.get("attachments", []),  # 保留上传的附件
    }


def _agent_state_summary(state: AgentState) -> dict:
    return {
        "phase": state.phase,
        "problem_summary": state.problem_summary[:100] if state.problem_summary else "",
        "diagnosis_rounds": state.diagnosis_rounds,
        "hypotheses": state.hypotheses,
        "collected_fields": list(state.collected_info.keys()),
    }


# ============================================================
# Agent 推理 Prompt
# ============================================================

DIAGNOSIS_PROMPT = """你是工业移动机器人（AGV/AMR）领域的技术支持专家。
所服务的产品是 USP（Universal Scheduling Platform）大调度系统，用于 AGV/AMR 的调度管理、车辆管理、设备管理、地图编辑与监控运维。
USP 是网页端系统（PC浏览器访问），没有移动端APP。严禁提及"手机""移动端""APP"等移动端概念。
严禁给出手机、电脑等消费电子产品的通用回答，严禁超出 AGV/AMR 领域。

## 知识库使用优先级（极其重要）
知识库中有五类 chunk，按以下优先级使用：

1. **FAQ（标题含「FAQ」）**：用户问的具体问题如果在 FAQ 中有直接匹配（如错误码含义、常见问题），**优先直接回答**，不追问不绕弯。
2. **🚗 车端错误码（标题含「车端错误码」）**：用户提到车载/车端/AGV本体上的错误码或报警时，直接匹配错误码给出原因和方案。
   ⚠️ **铁律**：如果车端错误码 section 显示「未找到匹配项」，该错误码**确实不在系统收录范围内**。
   你**必须**在回复中明确告知用户"该错误码未收录"，**绝对禁止**根据其他知识库内容、翻译表或自身知识编造该错误码的含义。
   用户问的是具体数字错误码，不等于问"车端有什么常见报警"。
3. **🌐 翻译表（标题含「翻译表」）**：用户问某个字段/标签/错误码的中英文含义时，从翻译表查找。也可辅助理解车端错误码的英文描述。
4. **🔍 故障排查树**：用户描述了故障现象，以上来源没有直接覆盖时，用排查树按步骤引导。
5. **知识库（操作手册）**：howto 类操作问题走这里，按前提→操作→预期结果给出步骤。

⚠️ **关键**：各知识源不互斥！先看 FAQ/车端错误码有没有现成答案，有就直接用；都没有的故障才走排查树。

## 意图判断（决定回复风格，不影响知识源选择）
- **howto（操作咨询）**：用户问"怎么做/怎么上线/怎么配置/步骤/流程"等。直接 answer，不追问不假设故障。
  知识库没涉及的细节如实说"手册未覆盖"，不要自己编步骤。
  ⚠️ 图片规则（极其重要）：
  知识库中的图片（![](url)）是操作界面截图。**必须严格按知识库原文的步骤结构来配图**——
  每个子步骤/操作项的文字说明之后，紧跟该步骤对应的截图，再开始写下一个步骤。
  **禁止把所有图片堆在同一个步骤后面**，每张图只能出现在它所属的子步骤下。

- **troubleshoot（故障排查）**：用户描述了异常现象（离线、报错、不动、卡住、异常等）。
  **先查 FAQ**：如果 FAQ 中有对应错误码/问题的直接答案，优先引用 FAQ 回答。
  **FAQ 没覆盖 + 有排查树**：严格按排查树逐步骤引导（见下方详细规则）。
  **都没有时**：列出可能原因，引导用户逐项验证。看 hypotheses/collected_info/ruled_out 推进排查。有明确怀疑直接让用户试（如"重启一下控制器看是否恢复"）。排查不出转工单。
  ⚠️ 知识库中有相关截图/示意图时，**必须引用**到回答中帮助用户定位。

- **chat（闲聊/问候）**：简单回应，引导用户描述具体问题。

- **转工单**：用户说"转工单""转单""生成工单""提交工单"或类似表述时，action 设为 answer，直接告知用户工单将基于当前诊断信息生成，不要开始新的排查。不要引用 prompt 示例中的任何内容。

## 重要规则
- 知识库每个 chunk 以 `---` 分隔，标题在 `知识库 N（标题）：`、`FAQ N：`、`🔍 故障排查树 N：`、`🚗 车端错误码 N：` 或 `🌐 翻译表 N：` 中标明。
  **只引用与用户问题直接相关的 chunk 内容**，无关 chunk 的内容和图片一律忽略。
- **禁止在回复中暴露知识来源**：不要说"根据排查树""根据知识库""检索结果显示"等话术。
  直接给出步骤/答案，用户不需要知道你查了什么。

## 🔍 故障排查树使用规则（极其重要）
- 排查树用于 FAQ 无法直接覆盖的故障场景。如果用户问题在 FAQ 中已有现成答案（如错误码解释），直接用 FAQ，不走排查树。

⚠️ **铁律（违反将导致错误诊断）**：
  - **绝对禁止**自己编造排查树中没有的检查项、原因或方案
  - **绝对禁止**跳过排查树的步骤顺序，或合并多步为一步
  - 你只能问排查树中明确列出的分支选项，不能自己添加选项
  - 每一轮排查你只能说排查树中**当前这一步**的内容，不能提前透露后续步骤

1. **分流判断**（命中多个排查树 / 用户描述模糊时）：
   - **不要**直接开始走某一棵树的步骤！
   - 把命中的故障场景列出来让用户确认是哪一个，如：
     "🔍 排查树匹配到以下几种情况，请确认你的具体现象：
     1. <场景A的症状名称>
     2. <场景B的症状名称>
     3. <场景C的症状名称>
     请问你的任务界面显示的是哪种状态？"
   - **用户确认后再**进入该树的逐步骤排查。

2. **逐步骤排查**（用户确认 / 只有一棵树命中时）：
   - 严格按树的顺序，**一次只问一步**，把该步的分支选项列出来让用户选
   - 根据用户回答跳转到对应分支（结论或下一步）
   - 到达「【结论】」时输出原因+方案
   - 如果某步有「以上都不是」分支但用户描述不在选项中，走「以上都不是」继续

3. **排查树中的结论引用**：
   - 排查树里的「原因」和「方案」是经验总结，输出时引用它们
   - 如果【结论】涉及操作步骤（如"急停→推车→RESET"），展开为可执行的步骤
   - 排查树某一步走完后，下一轮继续按树往下走，不要回到自由发挥模式

## 对话
{conversation}

## 状态：问题={problem_summary} | 已收集={collected_info} | 已排除={ruled_out} | 推测={hypotheses}
## 知识库：{reference_docs}
## 第{round}轮

---
输出 JSON：
```json
{{"action":"answer|ask","intent":"howto|troubleshoot|chat","state_update":{{"problem_summary":"概述","ruled_out":[],"hypotheses":[],"collected_info":{{}}}}}}
```
JSON 之后直接写回复。语气像工程师。引用图片时用 ![说明](url) 格式。"""


# ============================================================
# 流式 JSON 过滤：检测 JSON→自然语言边界
# ============================================================

def _find_json_end(buffer: str) -> int:
    """
    在 LLM 原始输出中定位 JSON 结束、自然语言开始的位置。

    支持三种格式：
      ```json\\n{...}\\n```\\n\\n<message>
      ```json\\n{...}\\n```<message>
      {\\n...}\\n\\n<message>

    Returns: message 起始位置，-1 表示 JSON 尚未结束。
    """
    if not buffer:
        return -1

    # Case 1: Fenced JSON — 找闭合的 ``` 出现在 } 之后
    m = re.search(r'\}\s*```\s*', buffer)
    if m:
        return m.end()

    # Case 2: Bare JSON — 跟踪括号深度，找顶层 }
    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(buffer):
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                # 顶层 JSON 已闭合，跳过后续空白，找消息起点
                rest = buffer[i + 1:]
                m2 = re.match(r'\s*', rest)
                # JSON 已完成：即使后面暂时只有空白，也标记边界
                # （后续 token 到达时 _json_done=True 会直接流式输出）
                if m2:
                    return i + 1 + m2.end()
                return i + 1

    return -1


# ============================================================
# ============================================================
# 智能诊断 Agent
# ============================================================

class AiDiagnosisPlatform:
    """纯 Agent 流水线：所有消息统一走 Agent 推理"""

    def __init__(self):
        self.config = get_ai_config()
        self._llm_client = None
        self._retriever = None
        self._memory_manager = None
        self._retrieval_cache: dict = {}  # 实例级缓存，不跨 session 串味

    async def _ensure_clients(self):
        if self._llm_client is None:
            t0 = time.perf_counter()
            self._llm_client = await get_llm_client()
            print(f"  [T]  [init] LLM client: {(time.perf_counter() - t0) * 1000:.0f}ms")
        if self._retriever is None:
            t0 = time.perf_counter()
            self._retriever = await get_retrieval_service()
            print(f"  [T]  [init] Retriever: {(time.perf_counter() - t0) * 1000:.0f}ms")
        if self._memory_manager is None:
            t0 = time.perf_counter()
            self._memory_manager = await get_memory_manager()
            print(f"  [T]  [init] MemoryManager: {(time.perf_counter() - t0) * 1000:.0f}ms")

    # ================================================================
    # run — 统一入口（纯 Agent）
    # ================================================================
    async def run(self, request: DiagnosisRequest) -> dict:
        t0 = time.perf_counter()
        await self._ensure_clients()
        t_init = (time.perf_counter() - t0) * 1000
        memory = await self._memory_manager.get_memory(request.session_id)
        agent_state = _load_agent_state(memory.metadata)

        if agent_state is None:
            agent_state = AgentState(
                session_id=request.session_id,
                phase="idle",
                original_query=request.query,
                problem_summary=request.query,
            )
            _save_agent_state(memory, agent_state)
            await self._memory_manager.save_memory(memory)

        result = await self._agent_think(request, agent_state, memory)
        total_ms = (time.perf_counter() - t0) * 1000
        print(f"  [T]  [run] total={total_ms:.0f}ms (init={t_init:.0f}ms)")
        return result

    # ================================================================
    # Agent 推理循环（共用方法）
    # ================================================================

    def _build_diagnosis_prompt(self, state: AgentState, memory, reference_docs: str) -> str:
        conversation_text = self._format_conversation(memory)
        return DIAGNOSIS_PROMPT.format(
            problem_summary=state.problem_summary or "（待分析）",
            collected_info=json.dumps(state.collected_info, ensure_ascii=False) if state.collected_info else "（暂无）",
            ruled_out="、".join(state.ruled_out) if state.ruled_out else "（暂无）",
            hypotheses="、".join(state.hypotheses) if state.hypotheses else "（待推断）",
            conversation=conversation_text,
            reference_docs=reference_docs,
            round=state.diagnosis_rounds,
        )

    def _apply_state_update(self, state: AgentState, state_update: dict) -> None:
        if not state_update:
            return
        if "problem_summary" in state_update:
            state.problem_summary = state_update["problem_summary"]
        if "ruled_out" in state_update:
            state.ruled_out = state_update["ruled_out"]
        if "hypotheses" in state_update:
            state.hypotheses = state_update["hypotheses"]
        if "collected_info" in state_update:
            # 合并新字段，空值/无 视为清除
            for k, v in state_update["collected_info"].items():
                if v is None:
                    state.collected_info.pop(k, None)
                    continue
                if isinstance(v, (dict, list)):
                    v = json.dumps(v, ensure_ascii=False)
                v = str(v).strip()
                if v and v not in ("无", "无无", "不清楚", "不知道", "暂无", "未知"):
                    state.collected_info[k] = v
                else:
                    state.collected_info.pop(k, None)

    def _apply_action_phase(self, state: AgentState, action: str) -> None:
        if action == "answer":
            state.phase = "resolved"

    async def _finalize_diagnosis(self, session_id: str, state: AgentState,
                                    thinking: str, action: str, message: str,
                                    streaming: bool = False) -> dict:
        # 手动添加 turn + 更新 agent_state，一次 save_memory 完成
        memory = await self._memory_manager.get_memory(session_id)
        memory.turns.append({"role": "assistant", "content": message})
        if len(memory.turns) > self._memory_manager.max_turns:
            memory.turns = memory.turns[-self._memory_manager.max_turns:]
        _save_agent_state(memory, state)
        await self._memory_manager.save_memory(memory)

        return {
            "type": "diagnosis",
            "thinking": thinking,
            "action": action,
            "message": message,
            "agent_state": _agent_state_summary(state),
            "_tokens_streamed": streaming,
        }

    # ================================================================
    # Agent 推理循环（同步）
    # ================================================================
    async def _agent_think(self, request: DiagnosisRequest, state: AgentState, memory) -> dict:
        t = {}  # timing 字典，所有值单位 ms
        t0 = time.perf_counter()

        t1 = time.perf_counter()
        memory = await self._memory_manager.add_turn(request.session_id, "user", request.query)
        t["add_turn"] = round((time.perf_counter() - t1) * 1000)

        state.diagnosis_rounds += 1
        state.phase = "diagnosing"

        # 指代消解："然后呢"等省略表达 → 用上文补全为完整查询
        resolved_query, _ = await self._memory_manager.resolve_pronoun(
            request.query, request.session_id)

        # ---- 诊断路径 ----
        t_retrieve_start = time.perf_counter()
        reference_docs = (
            "（跳过检索）" if request.skip_retrieval
            else await self._retrieve_with_context(request.session_id, state, resolved_query)
        )
        t["retrieve"] = round((time.perf_counter() - t_retrieve_start) * 1000)
        t["retrieve_docs_len"] = len(reference_docs)

        t_prompt_start = time.perf_counter()
        prompt = self._build_diagnosis_prompt(state, memory, reference_docs)
        t["build_prompt"] = round((time.perf_counter() - t_prompt_start) * 1000)
        t["prompt_chars"] = len(prompt)

        t_llm_start = time.perf_counter()
        try:
            raw = await asyncio.wait_for(
                self._llm_client.complete(prompt=prompt, max_tokens=1500, temperature=0.5),
                timeout=25.0,
            )
        except (asyncio.TimeoutError, AITimeoutError, ServiceUnavailableError, Exception):
            t["llm_agent"] = round((time.perf_counter() - t_llm_start) * 1000)
            t["total"] = round((time.perf_counter() - t0) * 1000)
            return {
                "type": "diagnosis",
                "thinking": "",
                "action": "ask",
                "message": "AI 诊断服务暂时不可用，请稍后再试。",
                "agent_state": _agent_state_summary(state),
                "timing": t,
            }
        t["llm_agent"] = round((time.perf_counter() - t_llm_start) * 1000)
        print(f"  [T]  [llm] agent call: {t['llm_agent']}ms (prompt {t.get('prompt_chars', '?')} chars, max_tokens=1500)")

        t_parse_start = time.perf_counter()
        parsed = self._parse_agent_output(raw)
        self._apply_state_update(state, parsed["state_update"])
        self._apply_action_phase(state, parsed["action"])
        t["parse_output"] = round((time.perf_counter() - t_parse_start) * 1000)

        t["total"] = round((time.perf_counter() - t0) * 1000)
        result = await self._finalize_diagnosis(
            request.session_id, state,
            parsed["thinking"], parsed["action"], parsed["message"],
            streaming=False)
        result["timing"] = t
        return result

    # ================================================================
    # 检索：用近期对话上下文，带简单内存缓存
    # ================================================================
    _CACHE_TTL = 60  # 秒

    async def _retrieve_with_context(self, session_id: str, state: AgentState,
                                      resolved_query: str = "") -> str:
        t0 = time.perf_counter()
        try:
            memory = await self._memory_manager.get_memory(session_id)
            user_msgs = [t["content"] for t in memory.turns if t["role"] == "user"][-4:]
            search_query = " ".join(user_msgs) if user_msgs else state.original_query
            # 指代消解后的完整查询优先，语义更明确
            if resolved_query:
                search_query = resolved_query + " " + search_query
            # 加入当前推测提升检索精度
            if state.hypotheses:
                search_query = search_query + " " + " ".join(state.hypotheses)
            if state.problem_summary:
                search_query = search_query + " " + state.problem_summary

            # 缓存命中：同一查询 60 秒内复用结果
            cache_key = search_query[:200]
            cached = self._retrieval_cache.get(cache_key)
            if cached and time.time() - cached["ts"] < self._CACHE_TTL:
                print(f"  [T]  [retrieve] cache hit, total: {(time.perf_counter() - t0) * 1000:.0f}ms")
                return cached["result"]

            # 正常检索流程：五路并行
            config = get_ai_config()
            manual_task = asyncio.wait_for(
                self._retriever.retrieve(search_query, top_k=config.retrieval_top_k),
                timeout=15.0,
            )
            faq_task = asyncio.wait_for(
                self._retriever.retrieve_faq(search_query, top_k=max(2, config.retrieval_top_k - 1)),
                timeout=10.0,
            )
            troubleshooting_task = asyncio.wait_for(
                self._retriever.retrieve_troubleshooting(search_query, top_k=3),
                timeout=10.0,
            )
            cheduan_task = asyncio.wait_for(
                self._retriever.retrieve_cheduan(search_query, top_k=3),
                timeout=10.0,
            )
            translation_task = asyncio.wait_for(
                self._retriever.retrieve_translation(search_query, top_k=2),
                timeout=10.0,
            )

            gathered = await asyncio.gather(
                manual_task, faq_task, troubleshooting_task,
                cheduan_task, translation_task,
                return_exceptions=True,
            )
            manual_results, faq_results, troubleshooting_results, cheduan_results, translation_results = gathered

            # 单路失败不拖垮全部：只丢弃异常的那一路
            if isinstance(manual_results, BaseException):
                manual_results = ([], 0.0)
            if isinstance(faq_results, BaseException):
                faq_results = []
            if isinstance(troubleshooting_results, BaseException):
                troubleshooting_results = []
            if isinstance(cheduan_results, BaseException):
                cheduan_results = []
            if isinstance(translation_results, BaseException):
                translation_results = []

            results, _ = manual_results  # unpack (results, top1_score)

            docs = []
            idx = 1

            # FAQ 最优先（直接答案）
            for r in (faq_results or []):
                q = r.title or ""
                a = r.content or ""
                if a.strip():
                    docs.append(f"---\nFAQ {idx}：{q}\n{a}\n---")
                    idx += 1

            # 车端错误码
            cheduan_found = False
            for r in (cheduan_results or []):
                if r.content.strip():
                    cheduan_found = True
                    docs.append(f"---\n🚗 车端错误码 {idx}：{r.title}\n{r.content}\n---")
                    idx += 1

            # 关键：用户明确问了错误码，但车端知识库没匹配到 → 显式告知 LLM
            # 防止 LLM 根据其他渠道（FAQ/排查树）的无关内容编造答案
            _query_codes = self._retriever._extract_error_codes(search_query)
            if _query_codes and not cheduan_found:
                codes_str = "、".join(_query_codes)
                docs.insert(0, f"---\n🚗 车端错误码（重要）：用户查询的错误码 [{codes_str}] 在车端知识库中**未找到匹配项**。"
                                 f"这意味着该错误码不在系统收录范围内。你必须在回复中明确告知用户该错误码未收录，"
                                 f"**绝对禁止**根据其他知识库内容或自身知识编造该错误码的含义。\n---")

            # 翻译表
            for r in (translation_results or []):
                if r.content.strip():
                    docs.append(f"---\n🌐 翻译表 {idx}：{r.title}\n{r.content}\n---")
                    idx += 1

            # 排查树
            for r in (troubleshooting_results or []):
                symptom = r.title or ""
                tree_text = r.content or ""
                if tree_text.strip():
                    docs.append(f"---\n🔍 故障排查树 {idx}：{symptom}\n{tree_text}\n---")
                    idx += 1

            # 操作手册结果
            for r in (results or []):
                title = f"（{r.title}）" if r.title else ""
                media_url = f"{config.media_url_prefix}/operation_doc"
                raw = re.sub(
                    r'!\[([^\]]*)\]\((?:\./)?media/([^)]+)\)',
                    rf'![\1]({media_url}/\2)',
                    r.content,
                )
                # 保序压缩：每张图保留前文 200 字上下文，确保每个步骤都有文字
                img_sep = re.compile(r'(!\[[^\]]*\]\([^)]+\))')
                parts = img_sep.split(raw)
                compact: list[str] = []
                ctx_budget = 200  # 每张图前面的文本预算
                for i, part in enumerate(parts):
                    if i % 2 == 0:  # 文本段 → 保留尾部（紧贴下一张图的部分）
                        if len(part) > ctx_budget:
                            part = "…" + part[-ctx_budget:]
                        compact.append(part)
                    else:  # 图片，原位置保留
                        compact.append(part)
                # 最后一段纯文本（尾部没有图片的），只截开头
                if len(parts) % 2 == 1 and len(parts[-1]) > ctx_budget:
                    compact[-1] = parts[-1][:ctx_budget] + "…"
                # payload images 兜底：追回 content 中漏掉的
                content = "".join(compact)
                if r.images:
                    extra = []
                    for img in r.images:
                        img_name = img.replace("media/", "")
                        if img_name not in content:
                            extra.append(f"![示意图]({media_url}/{img_name})")
                    if extra:
                        content += "\n\n" + "\n".join(extra)
                docs.append(f"---\n知识库 {idx}{title}：\n{content}\n---")
                idx += 1

            result = "\n".join(docs) if docs else "（知识库暂无匹配文档，请告知用户当前手册未覆盖此问题，建议转工单处理，不要自己编造答案。）"

            self._retrieval_cache[cache_key] = {"result": result, "ts": time.time()}
            # 防止缓存无限增长
            if len(self._retrieval_cache) > 200:
                oldest = min(self._retrieval_cache, key=lambda k: self._retrieval_cache[k]["ts"])
                del self._retrieval_cache[oldest]
            print(f"  [T]  [retrieve] total: {(time.perf_counter() - t0) * 1000:.0f}ms")
            return result

        except ServiceUnavailableError as e:
            print(f"  [T]  [retrieve] ServiceUnavailable: {e}, total: {(time.perf_counter() - t0) * 1000:.0f}ms")
        except LowConfidenceError as e:
            print(f"  [T]  [retrieve] LowConfidence: score={e.confidence:.3f} threshold={self.config.retrieval_score_threshold}, total: {(time.perf_counter() - t0) * 1000:.0f}ms")
        except (asyncio.TimeoutError, ConnectionError, RetrieveEmptyError):
            print(f"  [T]  [retrieve] failed/timed-out, total: {(time.perf_counter() - t0) * 1000:.0f}ms")
        return "（知识库检索失败，请告知用户当前系统检索异常、建议稍后重试或转工单处理，不要自己编造答案。）"

    # ================================================================
    # 工单生成
    # ================================================================
    async def _build_ticket(self, session_id: str, agent_state: AgentState, memory) -> dict:
        conversation_text = self._format_conversation(memory)
        reasoning = (
            f"问题概述：{agent_state.problem_summary}\n"
            f"推测原因：{'、'.join(agent_state.hypotheses) if agent_state.hypotheses else '无'}\n"
            f"已排除：{'、'.join(agent_state.ruled_out) if agent_state.ruled_out else '无'}\n"
            f"已收集信息：{json.dumps(agent_state.collected_info, ensure_ascii=False)}\n"
            f"诊断轮数：{agent_state.diagnosis_rounds}"
        )

        prompt = (
            f"请根据以下对话和诊断过程，生成结构化工单。\n\n"
            f"## 对话记录\n{conversation_text}\n\n"
            f"## Agent 推理链\n{reasoning}\n\n"
            f"请先判断工单类型（problem=报障/bug=缺陷/feature=功能需求/support=支持请求/other=其他），"
            f"然后以 JSON 格式返回：\n"
            f'{{"type":"problem|bug|feature|support|other","title":"≤20字","description":"≤150字，含排查过程",'
            f'"priority":"紧急|高|中|低","contact":"从对话提取的联系人，没有则为空",'
            f'"location":"仅type=problem时填，现场位置","robot_type":"仅type=problem时填，机器人型号/编号",'
            f'"fault_code":"仅type=problem时填，故障码","special_notes":"仅type=problem时填，特殊说明",'
            f'"steps_to_reproduce":"仅type=bug时填","expected_result":"仅type=bug时填",'
            f'"actual_result":"仅type=bug时填","severity":"仅type=bug时填:阻塞/主要/次要/轻微",'
            f'"version":"仅type=bug时填","scenario":"仅type=feature时填，需求场景",'
            f'"expected_effect":"仅type=feature时填","source":"仅type=feature时填:客户提出/内部发现/竞品对标",'
            f'"support_type":"仅type=support时填","preferred_response":"仅type=support时填:电话/现场/线上"}}'
        )

        try:
            raw = await self._llm_client.complete(prompt=prompt, max_tokens=600, temperature=0.2)
            clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
            analysis = json.loads(clean)
        except Exception:
            analysis = {}

        ticket_type = analysis.get("type", "other")

        # 通用字段
        result = {
            "ticket_id": f"AI-{session_id[-6:]}-{int(time.time()) % 100000}",
            "session_id": session_id,
            "type": ticket_type,
            "title": analysis.get("title", agent_state.original_query[:20]),
            "description": analysis.get("description", agent_state.problem_summary[:150]),
            "priority": analysis.get("priority", "中"),
            "status": "pending",
            "contact": analysis.get("contact", ""),
            "diagnosis": {
                "problem_summary": agent_state.problem_summary,
                "hypotheses": agent_state.hypotheses,
                "ruled_out": agent_state.ruled_out,
                "collected_info": agent_state.collected_info,
                "rounds": agent_state.diagnosis_rounds,
            },
            "created_at": int(time.time()),
            "source": "ai_agent",
            "attachments": memory.metadata.get("agent_state", {}).get("attachments", []),
        }

        # 类型专属字段
        if ticket_type == "problem":
            result["location"] = analysis.get("location", "")
            result["robot_type"] = analysis.get("robot_type", "")
            result["fault_code"] = analysis.get("fault_code", "")
            result["special_notes"] = analysis.get("special_notes", "")
        elif ticket_type == "bug":
            result["steps_to_reproduce"] = analysis.get("steps_to_reproduce", "")
            result["expected_result"] = analysis.get("expected_result", "")
            result["actual_result"] = analysis.get("actual_result", "")
            result["severity"] = analysis.get("severity", "")
            result["version"] = analysis.get("version", "")
        elif ticket_type == "feature":
            result["scenario"] = analysis.get("scenario", "")
            result["expected_effect"] = analysis.get("expected_effect", "")
            result["source"] = analysis.get("source", "")
        elif ticket_type == "support":
            result["support_type"] = analysis.get("support_type", "")
            result["preferred_response"] = analysis.get("preferred_response", "")

        return result

    async def get_ticket(self, session_id: str) -> dict:
        """只读获取工单数据，不改变状态"""
        memory = await self._memory_manager.get_memory(session_id)
        agent_state = _load_agent_state(memory.metadata) or AgentState(session_id=session_id)
        return await self._build_ticket(session_id, agent_state, memory)

    async def submit(self, session_id: str, created_by: str = "") -> dict:
        await self._ensure_clients()
        memory = await self._memory_manager.get_memory(session_id)
        agent_state = _load_agent_state(memory.metadata) or AgentState(session_id=session_id)
        ticket = await self._build_ticket(session_id, agent_state, memory)

        # ---- 存储到 tasks 表（source='ai'，按 session_id 幂等 upsert）----
        db_id = 0
        try:
            from ai.core.task_adapter import upsert_task
            record = upsert_task(ticket, created_by=created_by)
            db_id = record.id
            ticket["db_id"] = db_id
        except Exception as e:
            print(f"  ⚠️ MySQL 写入失败: {e}")

        agent_state.phase = "resolved"
        _save_agent_state(memory, agent_state)
        await self._memory_manager.save_memory(memory)

        try:
            await self._memory_manager.add_pending_ticket(session_id)
        except Exception:
            pass

        # ---- 智能派单推荐 ----
        try:
            from ai.agents.AiDiagnosisPlatform.assigner import assign_ticket
            print(f"\n{'='*50}")
            print(f"[派单] 工单「{ticket.get('title', '')}」自动派单中...")
            _result = await assign_ticket(
                ticket_id=str(db_id or ticket["ticket_id"]),
                title=ticket.get("title", ""),
                problem_description=ticket.get("description", ""),
                status="pending_dispatch",
                priority=ticket.get("priority", "中"),
                ticket_type=ticket.get("type", "other"),
                session_id=session_id,
                source="ai_agent",
                location=ticket.get("location", ""),
                robot_type=ticket.get("robot_type", ""),
                fault_code=ticket.get("fault_code", ""),
                special_notes=ticket.get("special_notes", ""),
                diagnosis_hypotheses=agent_state.hypotheses,
                diagnosis_ruled_out=agent_state.ruled_out,
                diagnosis_collected_info=agent_state.collected_info,
                diagnosis_rounds=agent_state.diagnosis_rounds,
                contact=ticket.get("contact", ""),
            )
            ticket["assignee"] = _result.engineer_name
            ticket["assignee_id"] = _result.engineer_id
            ticket["assign_confidence"] = _result.confidence_score
            ticket["assign_reasoning"] = _result.reasoning
            ticket["assign_decision_type"] = _result.decision_type
            print(f"[派单] ✅ 已自动分派 → {_result.engineer_name} (ID:{_result.engineer_id})")
            print(f"[派单]    置信度: {_result.confidence_score:.0%} | 决策: {_result.decision_type}")
            print(f"[派单]    理由: {_result.reasoning[:120]}")
            print(f"{'='*50}\n")
        except Exception as _e:
            print(f"  ⚠️ 智能派单失败（不阻塞工单生成）: {_e}")

        return {
            "type": "ticket",
            "data": {
                "ticket": ticket,
                "db_id": db_id,
                "notice": "工单已生成并保存。",
            },
            "agent_state": _agent_state_summary(agent_state),
        }

    async def _push_to_dispatch(self, ticket: dict) -> bool:
        dispatch_url = getattr(self.config, 'dispatch_api_url', '')
        if not dispatch_url:
            return False
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            try:
                resp = await client.post(dispatch_url, json=ticket)
                if resp.status_code == 200:
                    await self._memory_manager.remove_pending_ticket(ticket["session_id"])
                    return True
                return False
            except Exception:
                return False

    # ================================================================
    # 工具方法
    # ================================================================

    def _format_conversation(self, memory, max_turns: int = 8) -> str:
        """只取最近 N 条，避免长对话撑大 prompt"""
        turns = memory.turns[-max_turns:] if len(memory.turns) > max_turns else memory.turns
        return "\n".join(
            f"{'用户' if t['role'] == 'user' else '助手'}：{t['content']}"
            for t in turns
        )

    def _parse_agent_output(self, raw: str) -> dict:
        """
        解析 Agent 输出，支持三种格式：
        A) ```json {...} ``` === 回复文本     ← 标准格式
        B) ```json {...} ``` 回复文本         ← 缺 ===
        C) {...} 回复文本                     ← 裸 JSON（无 ``` 包裹）
        D) {...}                              ← 只有 JSON
        """
        text = raw.strip()
        thinking = ""
        action = "ask"
        state_update = {}
        message = text
        json_end = 0  # JSON 区域结束位置

        # ---- 尝试匹配带 ``` 包裹的 JSON ----
        m_fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
        # ---- 尝试匹配裸 JSON（以 { 开头，含 "thinking" 字段）----
        m_bare = re.match(r"(\{[\s\S]*?\"action\"[\s\S]*?\})\s*", text) if not m_fenced else None

        json_str = None
        if m_fenced:
            json_str = m_fenced.group(1).strip()
            json_end = m_fenced.end()
        elif m_bare:
            json_str = m_bare.group(1).strip()
            json_end = m_bare.end()

        if json_str:
            try:
                data = json.loads(json_str)
                thinking = data.get("thinking", "")
                action = data.get("action", "ask").strip().lower()
                if action not in ("answer", "ask"):
                    action = "ask"
                state_update = data.get("state_update", {})
            except (json.JSONDecodeError, Exception):
                pass

        # ---- 提取 JSON 之后的文本 ----
        after_json = text[json_end:] if json_end else text

        if json_str:
            after = re.sub(r"^```\s*", "", after_json).strip()
            if after:
                message = after

        message = message.lstrip("\n\r ")

        # 兜底：如果 message 仍然以 JSON 开头（无 === 且无后续文本），
        # 尝试剥掉裸 JSON 对象
        if message and (message.startswith("{") or message.startswith("```")):
            # 先试带 ``` 包裹的
            cleaned = re.sub(r'```(?:json)?\s*\{[\s\S]*?\}\s*```', '', message).strip()
            # 再试裸 JSON
            if not cleaned or cleaned.startswith("{"):
                cleaned = re.sub(r'^\s*\{[\s\S]*?"action"[\s\S]*?\}\s*', '', message, count=1).strip()
            if cleaned:
                message = cleaned
            else:
                message = "抱歉，我未能正确生成回复，请重新描述您的问题。"

        return {
            "thinking": thinking,
            "action": action,
            "message": message,
            "state_update": state_update,
        }

    # ================================================================
    # run_stream（纯 Agent）
    # ================================================================
    async def run_stream(self, request: DiagnosisRequest):
        t_req = time.perf_counter()
        await self._ensure_clients()
        memory = await self._memory_manager.get_memory(request.session_id)
        agent_state = _load_agent_state(memory.metadata)
        print(f"  [T]  [overhead] init+redis={(time.perf_counter() - t_req)*1000:.0f}ms")

        if agent_state is None:
            agent_state = AgentState(
                session_id=request.session_id,
                phase="idle",
                original_query=request.query,
                problem_summary=request.query,
            )
            _save_agent_state(memory, agent_state)
            await self._memory_manager.save_memory(memory)

        async for event in self._agent_think_stream(request, agent_state, memory):
            yield event

    async def _agent_think_stream(self, request: DiagnosisRequest, state: AgentState, memory):
        """Agent 推理流式版 —— 只流 === 之后的回复文本"""
        t_stream: dict = {}  # 流式路径 timing (ms)
        t0 = time.perf_counter()
        memory = await self._memory_manager.add_turn(request.session_id, "user", request.query)

        state.diagnosis_rounds += 1
        state.phase = "diagnosing"

        # 指代消解："然后呢"等省略表达 → 用上文补全为完整查询
        resolved_query, _ = await self._memory_manager.resolve_pronoun(
            request.query, request.session_id)

        # ---- 诊断路径 ----
        # 立刻发状态，别让用户干等
        yield {"event": "status", "data": {"stage": "retrieving", "round": state.diagnosis_rounds}}
        t_ret = time.perf_counter()
        reference_docs = (
            "（跳过检索）" if request.skip_retrieval
            else await self._retrieve_with_context(request.session_id, state, resolved_query)
        )
        t_stream["retrieve"] = round((time.perf_counter() - t_ret) * 1000)
        prompt = self._build_diagnosis_prompt(state, memory, reference_docs)
        t_stream["prompt_chars"] = len(prompt)

        yield {"event": "status", "data": {"stage": "analyzing", "round": state.diagnosis_rounds}}

        t_llm = time.perf_counter()
        t_stream["overhead_before_llm"] = round((t_llm - t0) * 1000)
        raw_tokens: list[str] = []
        t_first_llm = None
        _buf = ""          # 累积缓冲区，用于检测 JSON→消息边界
        _json_done = False # True 表示已越过 JSON 区域
        try:
            async for token in self._llm_client.stream(prompt=prompt, max_tokens=1500, temperature=0.5):
                raw_tokens.append(token)

                if not _json_done:
                    _buf += token
                    msg_start = _find_json_end(_buf)
                    if msg_start >= 0:
                        _json_done = True
                        # 把缓冲区中属于消息的尾部发出去
                        tail = _buf[msg_start:]
                        if tail:
                            if t_first_llm is None:
                                t_first_llm = time.perf_counter()
                                t_stream["llm_first_token"] = round((t_first_llm - t_llm) * 1000)
                            yield {"event": "token", "data": tail}
                else:
                    if t_first_llm is None:
                        t_first_llm = time.perf_counter()
                        t_stream["llm_first_token"] = round((t_first_llm - t_llm) * 1000)
                    yield {"event": "token", "data": token}
        except (AITimeoutError, ServiceUnavailableError, Exception):
            msg = "AI 诊断服务暂时不可用，请稍后再试。"
            for ch in msg:
                yield {"event": "token", "data": ch}
            yield {"event": "result", "data": {
                "type": "diagnosis", "thinking": "", "action": "ask",
                "message": msg, "agent_state": _agent_state_summary(state),
                "_tokens_streamed": True,
            }}
            return

        raw = "".join(raw_tokens)
        t_stream["llm_agent"] = round((time.perf_counter() - t_llm) * 1000)
        print(f"  [T]  [timing] overhead={t_stream.get('overhead_before_llm','?')}ms  "
              f"retrieve={t_stream.get('retrieve','?')}ms  "
              f"prompt={t_stream.get('prompt_chars','?')}chars  "
              f"llm_first={t_stream.get('llm_first_token','?')}ms  "
              f"total={t_stream.get('llm_agent','?')}ms")

        parsed = self._parse_agent_output(raw)

        self._apply_state_update(state, parsed["state_update"])
        self._apply_action_phase(state, parsed["action"])

        result_data = await self._finalize_diagnosis(
            request.session_id, state,
            parsed["thinking"], parsed["action"], parsed["message"],
            streaming=True)

        # 兜底：如果 LLM 只输出了 JSON 没有消息正文，前面的流式 yield 不会触发任何 token。
        # 此时把解析出来的 message 作为一次性 token 发出去，确保前端有内容展示。
        if t_first_llm is None and parsed["message"]:
            for ch in parsed["message"]:
                yield {"event": "token", "data": ch}

        # 嵌入流式路径 timing
        t_stream["total"] = round((time.perf_counter() - t0) * 1000)
        result_data["timing"] = t_stream

        yield {"event": "result", "data": result_data}

    async def run_with_timeout(self, request: DiagnosisRequest, timeout: float = 30.0) -> dict:
        """超时保护在 LLM 调用层（asyncio.wait_for），这里只透传"""
        try:
            return await self.run(request)
        except Exception:
            return {
                "type": "diagnosis",
                "thinking": "",
                "action": "ask",
                "message": "AI 诊断服务暂时不可用，请稍后再试。",
                "agent_state": {},
            }


# ============================================================
# 全局单例
# ============================================================

_pipeline: Optional[AiDiagnosisPlatform] = None
_pipeline_lock = asyncio.Lock()


async def get_diagnosis_platform() -> AiDiagnosisPlatform:
    global _pipeline
    if _pipeline is None:
        async with _pipeline_lock:
            if _pipeline is None:
                _pipeline = AiDiagnosisPlatform()
    return _pipeline
