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

from app.ai import SYSTEM_PROMPT
from app.ai.config import get_ai_config
from app.ai.exceptions import AITimeoutError, LowConfidenceError, ServiceUnavailableError, RetrieveEmptyError
from app.ai.core import get_llm_client, get_retrieval_service, get_memory_manager


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

DIAGNOSIS_PROMPT = """你是 AGV/AMR 技术支持 Agent。

## 先判断用户意图
- **howto（操作咨询）**：用户问"怎么做/怎么上线/怎么配置/步骤/流程"等，想了解操作方法。
  从知识库找答案，按前提→操作→预期结果的顺序给出步骤。直接 answer，不追问不假设故障。
  知识库没涉及的细节如实说"手册未覆盖"，不要自己编步骤。
  ⚠️ 图片规则（极其重要）：
  知识库中的图片（![](url)）是操作界面截图。**必须严格按知识库原文的步骤结构来配图**——
  每个子步骤/操作项的文字说明之后，紧跟该步骤对应的截图，再开始写下一个步骤。
  **禁止把所有图片堆在同一个步骤后面**，每张图只能出现在它所属的子步骤下。

- **troubleshoot（故障排查）**：用户描述了异常现象（离线、报错、不动、卡住、异常等）。
  列出可能原因，引导用户逐项验证。看 hypotheses/collected_info/ruled_out 推进排查。
  如果你有明确怀疑，让用户直接试（如"重启一下控制器看是否恢复"）。排查不出时建议转工单。
  ⚠️ 知识库中有相关截图/示意图时，**必须引用**到回答中帮助用户定位。

- **chat（闲聊/问候）**：简单回应，引导用户描述具体问题。

## 重要规则
- 知识库每个 chunk 以 `---` 分隔，标题在 `知识库 N（标题）：` 或 `🔍 故障排查树 N：` 中标明。
  **只引用与用户问题直接相关的 chunk 内容**，无关 chunk（如用户问自研车，但检索到了科钛车/库位前置点）的内容和图片一律忽略。

## 🔍 故障排查树使用规则（极其重要）
- 如果知识库中有「🔍 故障排查树」（结构化步骤引导），你应该用它来排查问题。
  **但只有用户明确在排查故障（intent=troubleshoot）时才使用排查树**，
  操作咨询（howto）走「知识库」chunk，不走排查树。

1. **分流判断**（命中多个排查树 / 用户描述模糊时）：
   - **不要**直接开始走某一棵树的步骤！
   - 把命中的故障场景列出来让用户确认是哪一个，如：
     "🔍 排查树匹配到以下几种情况，请确认你的具体现象：
     1. 车待命，任务状态一直显示调度中，机器人编号为-
     2. 车不动了，原子任务状态路径规划中
     3. 车不动了，原子任务状态已下发
     4. 车不动了，原子任务状态执行中
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
            print(f"  ⏱  [init] LLM client: {(time.perf_counter() - t0) * 1000:.0f}ms")
        if self._retriever is None:
            t0 = time.perf_counter()
            self._retriever = await get_retrieval_service()
            print(f"  ⏱  [init] Retriever: {(time.perf_counter() - t0) * 1000:.0f}ms")
        if self._memory_manager is None:
            t0 = time.perf_counter()
            self._memory_manager = await get_memory_manager()
            print(f"  ⏱  [init] MemoryManager: {(time.perf_counter() - t0) * 1000:.0f}ms")

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
        print(f"  ⏱  [run] total={total_ms:.0f}ms (init={t_init:.0f}ms)")
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
        await self._memory_manager.add_turn(session_id, "assistant", message)
        fresh_memory = await self._memory_manager.get_memory(session_id)
        _save_agent_state(fresh_memory, state)
        await self._memory_manager.save_memory(fresh_memory)

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
                self._llm_client.complete(prompt=prompt, system_prompt=SYSTEM_PROMPT,
                                           max_tokens=1500, temperature=0.5),
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
        print(f"  ⏱  [llm] agent call: {t['llm_agent']}ms (prompt {t.get('prompt_chars', '?')} chars, max_tokens=1500)")

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
                print(f"  ⏱  [retrieve] cache hit, total: {(time.perf_counter() - t0) * 1000:.0f}ms")
                return cached["result"]

            # 正常检索流程：三路并行（排查树 + 操作手册 + FAQ）
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

            manual_results, faq_results, troubleshooting_results = await asyncio.gather(
                manual_task, faq_task, troubleshooting_task,
            )
            results, _ = manual_results  # unpack (results, top1_score)

            docs = []
            idx = 1

            # 排查树结果最优先（结构化步骤引导最精准）
            for r in (troubleshooting_results or []):
                symptom = r.title or ""
                tree_text = r.content or ""
                if tree_text.strip():
                    docs.append(f"---\n🔍 故障排查树 {idx}：{symptom}\n{tree_text}\n---")
                    idx += 1

            # FAQ 结果（直接答案）
            for r in (faq_results or []):
                q = r.title or ""
                a = r.content or ""
                if a.strip():
                    docs.append(f"---\nFAQ {idx}：{q}\n{a}\n---")
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

            result = "\n".join(docs) if docs else "（知识库暂无匹配文档，请基于机器人/工业自动化行业知识进行诊断。）"

            self._retrieval_cache[cache_key] = {"result": result, "ts": time.time()}
            # 防止缓存无限增长
            if len(self._retrieval_cache) > 200:
                oldest = min(self._retrieval_cache, key=lambda k: self._retrieval_cache[k]["ts"])
                del self._retrieval_cache[oldest]
            print(f"  ⏱  [retrieve] total: {(time.perf_counter() - t0) * 1000:.0f}ms")
            return result

        except ServiceUnavailableError as e:
            print(f"  ⏱  [retrieve] ServiceUnavailable: {e}, total: {(time.perf_counter() - t0) * 1000:.0f}ms")
        except LowConfidenceError as e:
            print(f"  ⏱  [retrieve] LowConfidence: score={e.confidence:.3f} threshold={self.config.retrieval_score_threshold}, total: {(time.perf_counter() - t0) * 1000:.0f}ms")
        except (asyncio.TimeoutError, ConnectionError, RetrieveEmptyError):
            print(f"  ⏱  [retrieve] failed/timed-out, total: {(time.perf_counter() - t0) * 1000:.0f}ms")
        return "（知识库暂无匹配文档，请基于机器人/工业自动化行业知识进行诊断。）"

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
            "status": "pending_dispatch",
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

    async def submit(self, session_id: str) -> dict:
        await self._ensure_clients()
        memory = await self._memory_manager.get_memory(session_id)
        agent_state = _load_agent_state(memory.metadata) or AgentState(session_id=session_id)
        ticket = await self._build_ticket(session_id, agent_state, memory)

        # ---- 存储到 MySQL ----
        db_id = 0
        try:
            from app.models.ticket import Ticket
            from app.core.database import SessionLocal
            db = SessionLocal()
            record = Ticket(
                session_id=session_id,
                ticket_ai_id=ticket.get("ticket_id", ""),
                title=ticket.get("title", ""),
                description=ticket.get("description", ""),
                type=ticket.get("type", "other"),
                priority=ticket.get("priority", "中"),
                contact=ticket.get("contact", ""),
                diagnosis=ticket.get("diagnosis", {}),
                attachments=ticket.get("attachments", []),
                location=ticket.get("location", ""),
                robot_type=ticket.get("robot_type", ""),
                fault_code=ticket.get("fault_code", ""),
                special_notes=ticket.get("special_notes", ""),
                steps_to_reproduce=ticket.get("steps_to_reproduce", ""),
                expected_result=ticket.get("expected_result", ""),
                actual_result=ticket.get("actual_result", ""),
                severity=ticket.get("severity", ""),
                version=ticket.get("version", ""),
                scenario=ticket.get("scenario", ""),
                expected_effect=ticket.get("expected_effect", ""),
                source=ticket.get("source", ""),
                support_type=ticket.get("support_type", ""),
                preferred_response=ticket.get("preferred_response", ""),
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            db_id = record.id
            ticket["db_id"] = db_id
            db.close()
        except Exception as e:
            print(f"  ⚠️ MySQL 写入失败: {e}")

        agent_state.phase = "resolved"
        _save_agent_state(memory, agent_state)
        await self._memory_manager.save_memory(memory)

        try:
            await self._memory_manager.add_pending_ticket(session_id)
        except Exception:
            pass

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
        print(f"  ⏱  [overhead] init+redis={(time.perf_counter() - t_req)*1000:.0f}ms")

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
            async for token in self._llm_client.stream(prompt=prompt, system_prompt=SYSTEM_PROMPT,
                                                         max_tokens=1500, temperature=0.5):
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
        print(f"  ⏱  [timing] overhead={t_stream.get('overhead_before_llm','?')}ms  "
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
