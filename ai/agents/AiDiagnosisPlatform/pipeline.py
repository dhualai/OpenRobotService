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
from ai.core.logging import get_logger

logger = get_logger(__name__)


# ============================================================
# 数据结构
# ============================================================

@dataclass
class DiagnosisRequest:
    session_id: str
    query: str
    rewritten_query: Optional[str] = None
    skip_retrieval: bool = False  # 测试用：跳过 KB 检索
    created_by: str = ""  # 提单人用户名，由 API 层从 Bearer token 解析后传入


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
    last_submitted_ticket: dict = field(default_factory=dict)  # 上一个已提交工单的摘要
    ticket_seq: int = 0  # 工单序号，同一会话多次转单时自增，确保 external_id 唯一


# ============================================================
# 状态辅助函数
# ============================================================

def _load_agent_state(metadata: dict) -> Optional[AgentState]:
    s = metadata.get("agent_state")
    if not s:
        return None
    # 防御：agent_state 可能被上传接口（/qa/upload 第4段）写入不完整——
    # 新会话首次动作是上传时，仅存了 {"attachments": [...]}、缺 session_id 等关键字段，
    # 直接 s["session_id"] 会 KeyError 中断诊断。此时视为无状态，由 pipeline 重新初始化
    # （attachments 仍会被 _save_agent_state 的 existing.get("attachments", []) 保留）。
    if not s.get("session_id"):
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
        last_submitted_ticket=s.get("last_submitted_ticket", {}),
        ticket_seq=s.get("ticket_seq", 0),
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
        "last_submitted_ticket": state.last_submitted_ticket,
        "ticket_seq": state.ticket_seq,
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


def _check_required_fields(ticket: dict) -> dict:
    """统一的前置校验：所有类型转工单都必须绑定项目（project_id 优先，回退 project 名称）。
    与前端确认弹窗的「项目必选（所有类型）」规则对齐：未绑定项目时拒绝提交并给出提示。
    返回 {"ok": bool, "missing": [...], "prompt": "..."}
    """
    missing = []
    if not ticket.get("project_id", "").strip() and not ticket.get("project", "").strip():
        missing.append("project")
    ok = len(missing) == 0
    return {
        "ok": ok,
        "missing": missing,
        "prompt": "" if ok else "请先选择/填写绑定项目，未绑定项目无法提交工单。",
    }


async def _generate_title(llm_client, memory) -> str:
    """第2轮对话结束后，用前两轮对话生成会话标题（中文不超过15字，英文不超过50字符）"""
    turns = memory.turns
    if len(turns) < 4 or "title" in memory.metadata:
        return ""
    prompt = (
        "根据以下对话生成一个简短标题（中文不超过15字，英文不超过50字符）：\n\n"
        f"用户：{turns[0]['content']}\n"
        f"助手：{turns[1]['content']}\n"
        f"用户：{turns[2]['content']}\n"
        f"助手：{turns[3]['content']}\n\n"
        "只输出标题，不要引号或任何额外内容。"
    )
    try:
        title = (await llm_client.complete(
            prompt=prompt, max_tokens=40, temperature=0.3,
        )).strip()
        title = title.strip('"\'""''「」《》').strip()
        if title:
            memory.metadata["title"] = title
            logger.info(f"[title] 标题生成: {title}")
            return title
    except Exception as e:
        logger.warning(f"[title] 标题生成失败: {e}")
    return ""


# ============================================================
# Agent 推理 Prompt
# ============================================================

DIAGNOSIS_PROMPT = """你是「摇人吧」微信服务号的 AI 诊断助手，面向 AGV/AMR（工业移动机器人）行业的技术支持专家。
你的服务对象是现场工程师、客户和项目管理人员。

所服务的产品是 USP（Universal Scheduling Platform）大调度系统，用于 AGV/AMR 的调度管理、车辆管理、设备管理、地图编辑与监控运维。
USP 是网页端系统（PC浏览器访问），没有移动端APP。严禁在操作指引中提及"手机""移动端""APP"等概念——USP 只有 PC 浏览器版。
严禁给出手机、电脑等消费电子产品的通用回答，严禁超出 AGV/AMR 和 USP 领域。

## 服务号三个入口
关注微信服务号 **「摇人吧」** 后，底部三个菜单：
- 🆘 **我要摇人**：报障提单、AI 在线诊断——你主要在这里
- 📥 **系统任务**：统一任务收件箱，处理工单——你在这里辅助工程师生成方案草稿
- 📊 **后台管理**：跨项目看板、风险管理、数据统计——你在这里提供分析建议

## 你的能力
1. **在线诊断**：查知识库 → FAQ 有答案直接答 → 没有就逐步排查 → 搞不定自动转工单
2. **协助工程师**：接到工单后自动生成解决方案草稿（根因分析 + 建议步骤 + 参考资料）
3. **记住对话**：每一轮都会记录原始问题、已排除原因、当前推测、已收集信息，用于缩小诊断范围，转工单时一并交给工程师
如果以上都找不到答案，告知用户手册未覆盖、建议转工单，**不会编造答案**。

## 平台用户角色
- customer（客户/现场人员）：报障、咨询、看自己的工单
- engineer（实施工程师）：接单处理、转派、上报
- manager（项目经理）：派单、看本项目所有工单
- leader（上级领导）：接收上报、看全局
- admin（系统管理员）：全部权限

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

## ⛔ 转工单规则（优先级最高，优先于所有意图判断）

用户消息中含"转工单""转单""生成工单""提交工单""提单""不想排查"等关键词时：
→ **立即执行**：action 必须设为 "submit"，不要设为 "answer"。
→ 回复只需一句话："好的，已为你生成工单，工程师会尽快处理。"
→ 禁止做任何其他事：不要描述工单内容、不要总结排查结果、不要反问、不要排查、不要给建议。

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

- **chat（闲聊/问候）**：简单回应，不要追问技术问题。用户说"好的""谢谢""感谢"是对话收尾，回复"不客气，有问题随时找我"即可。禁止顺势开始排查或反问用户。

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

## 上一个工单上下文
{last_ticket_context}

## 状态：问题={problem_summary} | 已收集={collected_info} | 已排除={ruled_out} | 推测={hypotheses}
## 知识库：{reference_docs}
## 第{round}轮

---
输出 JSON（如果用户要求转工单，action 必须是 submit 不是 answer）：
```json
{{"action":"answer|ask|submit","intent":"howto|troubleshoot|chat","state_update":{{"problem_summary":"概述","ruled_out":[],"hypotheses":[],"collected_info":{{}}}}}}
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
            logger.debug(f"LLM client 初始化: {(time.perf_counter() - t0) * 1000:.0f}ms")
        if self._retriever is None:
            t0 = time.perf_counter()
            self._retriever = await get_retrieval_service()
            logger.debug(f"Retriever 初始化: {(time.perf_counter() - t0) * 1000:.0f}ms")
        if self._memory_manager is None:
            t0 = time.perf_counter()
            self._memory_manager = await get_memory_manager()
            logger.debug(f"MemoryManager 初始化: {(time.perf_counter() - t0) * 1000:.0f}ms")

    # ================================================================
    # run — 统一入口（纯 Agent）
    # ================================================================
    async def run(self, request: DiagnosisRequest) -> dict:
        t0 = time.perf_counter()
        await self._ensure_clients()
        t_init = (time.perf_counter() - t0) * 1000
        try:
            memory = await asyncio.wait_for(
                self._memory_manager.get_memory(request.session_id), timeout=3.0,
            )
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning(f"[agent] get_memory 超时/失败，使用空记忆: {e}")
            from ai.core.memory import SessionMemory
            memory = SessionMemory(session_id=request.session_id)
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
        elif agent_state.phase in ("idle", "resolved") and not agent_state.problem_summary:
            # 上一轮工单已提交、诊断状态已清空 → 全新话题
            agent_state.phase = "idle"
            agent_state.original_query = request.query
            agent_state.problem_summary = request.query
            _save_agent_state(memory, agent_state)
            await self._memory_manager.save_memory(memory)

        result = await self._agent_think(request, agent_state, memory)
        total_ms = (time.perf_counter() - t0) * 1000
        logger.debug(f"[run] total={total_ms:.0f}ms init={t_init:.0f}ms")
        return result

    # ================================================================
    # Agent 推理循环（共用方法）
    # ================================================================

    @staticmethod
    def _escape_format(s: str) -> str:
        """转义 .format() 特殊字符：{ → {{, } → }}"""
        return s.replace("{", "{{").replace("}", "}}")

    def _build_diagnosis_prompt(self, state: AgentState, memory, reference_docs: str) -> str:
        conversation_text = self._format_conversation(memory)
        last_ticket = state.last_submitted_ticket
        if last_ticket and last_ticket.get("ticket_id"):
            last_ticket_context = (
                f"用户刚才提交了工单「{last_ticket.get('title', '')}」（ID: {last_ticket.get('ticket_id', '')}），"
                f"问题概述：{last_ticket.get('topic', '')}。\n"
                f"⚠️ 如果用户接下来的消息是补充这个工单的信息（以\"补充\"开头、截图、日志、额外描述等），"
                f"你的回复只能确认收到并告知已补充到工单，一句话就够了。输出 JSON 时使用 intent=\"follow_up\"，"
                f"action=\"answer\"，不要设置新的 problem_summary，不要再提问、不要开始排查、不要给建议，"
                f"不要反问用户任何问题。\n"
                f"⚠️ 如果用户描述的是新的、不相关的问题，说明上一个工单已结束，"
                f"请忽略上一个工单，按正常诊断流程处理（intent=\"troubleshoot\" 或 \"howto\"）。"
            )
        else:
            last_ticket_context = "（无）"
        try:
            return DIAGNOSIS_PROMPT.format(
                problem_summary=self._escape_format(state.problem_summary or "（待分析）"),
                collected_info=self._escape_format(
                    json.dumps(state.collected_info, ensure_ascii=False) if state.collected_info else "（暂无）"
                ),
                ruled_out=self._escape_format("、".join(state.ruled_out) if state.ruled_out else "（暂无）"),
                hypotheses=self._escape_format("、".join(state.hypotheses) if state.hypotheses else "（待推断）"),
                conversation=self._escape_format(conversation_text),
                reference_docs=self._escape_format(reference_docs),
                round=state.diagnosis_rounds,
                last_ticket_context=self._escape_format(last_ticket_context),
            )
        except Exception:
            logger.error(
                f"Prompt 格式化失败 (round={state.diagnosis_rounds}): "
                f"ref_docs_len={len(reference_docs)}, conv_len={len(conversation_text)}",
                exc_info=True,
            )
            # 降级：用最简单的 prompt 保证不中断服务
            return (
                f"请根据以下对话回答用户问题。你是AGV/AMR领域的技术支持专家。\n\n"
                f"## 对话\n{conversation_text}\n\n"
                f"请直接回复，不要输出JSON。"
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
        elif action == "submit":
            state.phase = "escalated"

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

        # MySQL 双写已禁用：会话管理统一走前端 → backend /api/call/conversations
        # （原 conversation_store.save_message 会创建 title="新会话" 的冗余会话记录）
        # try:
        #     from ai.core.conversation_store import save_message
        #     logger.info(f"[persist] 开始 MySQL 写入 assistant 消息: session={session_id[:16]}, len={len(message)}")
        #     msg_id = await asyncio.to_thread(
        #         save_message, session_id=session_id, role="assistant",
        #         content=message, user_id="",
        #     )
        #     logger.info(f"[persist] assistant 消息已写入 MySQL: msg_id={msg_id}, session={session_id[:16]}")
        # except Exception as e:
        #     logger.error(f"[persist] MySQL 写入失败: session={session_id[:16]}, error={e}", exc_info=True)

        # 第2轮对话结束后生成标题（fire-and-forget 方式，不阻塞结果返回）
        title = ""
        if state.diagnosis_rounds == 2 and "title" not in memory.metadata:
            title = await _generate_title(self._llm_client, memory)

        return {
            "type": "diagnosis",
            "thinking": thinking,
            "action": action,
            "message": message,
            "agent_state": _agent_state_summary(state),
            "title": title,
            "_tokens_streamed": streaming,
        }

    # ================================================================
    # Agent 推理循环（同步）
    # ================================================================
    async def _agent_think(self, request: DiagnosisRequest, state: AgentState, memory) -> dict:
        t = {}  # timing 字典，所有值单位 ms
        t0 = time.perf_counter()
        turn_count = len(memory.turns)
        has_image = any("图片主要内容为" in t.get("content", "") for t in memory.turns[-6:])
        logger.info(f"[agent] 开始推理: session={request.session_id[:8]}, query={request.query[:50]}, "
                    f"round={state.diagnosis_rounds}, turns={turn_count}, "
                    f"has_recent_image={has_image}, phase={state.phase}")

        t1 = time.perf_counter()
        memory = await self._memory_manager.add_turn(request.session_id, "user", request.query)
        t["add_turn"] = round((time.perf_counter() - t1) * 1000)

        state.diagnosis_rounds += 1
        state.phase = "diagnosing"

        # 记录本轮开始前是否已有已提交的工单
        was_post_submit = bool(
            state.last_submitted_ticket and state.last_submitted_ticket.get("ticket_id")
        )

        # 指代消解："然后呢"等省略表达 → 用上文补全为完整查询
        resolved_query, _ = await self._memory_manager.resolve_pronoun(
            request.query, request.session_id)

        # ---- 诊断路径 ----
        t_retrieve_start = time.perf_counter()
        reference_docs = (
            "（跳过检索）" if request.skip_retrieval
            else await self._retrieve_with_context(request.session_id, state, resolved_query, memory.turns)
        )
        t["retrieve"] = round((time.perf_counter() - t_retrieve_start) * 1000)
        t["retrieve_docs_len"] = len(reference_docs)

        t_prompt_start = time.perf_counter()
        prompt = self._build_diagnosis_prompt(state, memory, reference_docs)
        t["build_prompt"] = round((time.perf_counter() - t_prompt_start) * 1000)
        t["prompt_chars"] = len(prompt)
        logger.info(f"[agent] prompt构建完成: {t['prompt_chars']} chars, retrieve={t['retrieve']}ms")

        t_llm_start = time.perf_counter()
        try:
            raw = await asyncio.wait_for(
                self._llm_client.complete(prompt=prompt, max_tokens=1500, temperature=0.5),
                timeout=25.0,
            )
            logger.info(f"[agent] LLM返回: {len(raw)} chars, 前80字={raw[:80]}")
        except (asyncio.TimeoutError, AITimeoutError, ServiceUnavailableError, Exception) as e:
            t["llm_agent"] = round((time.perf_counter() - t_llm_start) * 1000)
            t["total"] = round((time.perf_counter() - t0) * 1000)
            logger.error(f"[agent] LLM调用失败: {type(e).__name__}: {e}", exc_info=True)
            return {
                "type": "diagnosis",
                "thinking": "",
                "action": "ask",
                "message": "AI 诊断服务暂时不可用，请稍后再试。",
                "agent_state": _agent_state_summary(state),
                "timing": t,
            }
        t["llm_agent"] = round((time.perf_counter() - t_llm_start) * 1000)
        logger.debug(f"[llm] agent call: {t['llm_agent']}ms prompt={t.get('prompt_chars','?')}chars")

        t_parse_start = time.perf_counter()
        parsed = self._parse_agent_output(raw)
        logger.info(f"[agent] 解析结果: action={parsed['action']}, message_len={len(parsed['message'])}, "
                    f"message前50字={parsed['message'][:50]}")
        self._apply_state_update(state, parsed["state_update"])
        self._apply_action_phase(state, parsed["action"])
        t["parse_output"] = round((time.perf_counter() - t_parse_start) * 1000)

        # ---- 服务端兜底：用户消息含转工单关键词 → 强制提单 ----
        _force_submit_kw = ("转工单", "转单", "生成工单", "提交工单", "提单", "帮我转", "我要转")
        if parsed["action"] != "submit" and any(
            kw in request.query for kw in _force_submit_kw
        ):
            logger.info(f"[agent] 服务端兜底提单: query={request.query[:40]}")
            parsed["action"] = "submit"

        # ---- 自动提单：LLM 输出 action=submit 时先校验必填字段，完整则直接提单 ----
        ticket_data = None
        if parsed["action"] == "submit":
            try:
                draft = await self._build_ticket(request.session_id, state, memory)
                check = _check_required_fields(draft)
                if not check["ok"]:
                    logger.info(f"[agent] 缺必填字段: {check['missing']}, 引导用户补充")
                    memory.metadata["ticket_draft"] = draft
                    await self._memory_manager.save_memory(memory)
                    parsed["action"] = "answer"
                    parsed["message"] = check["prompt"]
                else:
                    ticket_data = await self.submit(request.session_id, created_by=request.created_by)
                    logger.info(f"[agent] 自动提单成功: session={request.session_id[:8]}, "
                                f"ticket={ticket_data.get('data', {}).get('ticket', {}).get('ticket_id', '?')}")
                    # submit() 已清空诊断状态并保存，刷新本地 state 避免 _finalize_diagnosis 覆写旧状态
                    memory = await self._memory_manager.get_memory(request.session_id)
                    state = _load_agent_state(memory.metadata) or state
            except Exception as e:
                logger.error(f"[agent] 自动提单失败: session={request.session_id[:8]}, error={e}", exc_info=True)

        # ---- 补充工单：post-submit 时判断是否为补充信息 → 更新 MySQL 工单 ----
        _is_follow_up = parsed.get("intent") == "follow_up" or (
            was_post_submit and parsed["action"] == "answer"
            and parsed.get("intent") not in ("troubleshoot", "howto", "chat")
        )
        if _is_follow_up:
            try:
                appended = await self._append_to_ticket(
                    request.session_id, text=f"用户补充：{request.query}"
                )
                if appended:
                    logger.info(f"[agent] 补充信息已追加到工单: session={request.session_id[:8]}")
            except Exception as e:
                logger.error(f"[agent] 补充工单失败: {e}", exc_info=True)

        t["total"] = round((time.perf_counter() - t0) * 1000)
        result = await self._finalize_diagnosis(
            request.session_id, state,
            parsed["thinking"], parsed["action"], parsed["message"],
            streaming=False)
        if ticket_data:
            result["ticket"] = ticket_data
        result["timing"] = t
        logger.info(f"[agent] 推理完成: total={t['total']}ms, message_len={len(parsed['message'])}")
        return result

    # ================================================================
    # 检索：用近期对话上下文，带简单内存缓存
    # ================================================================
    _CACHE_TTL = 60  # 秒

    async def _retrieve_with_context(self, session_id: str, state: AgentState,
                                      resolved_query: str = "",
                                      user_turns: list = None) -> str:
        t0 = time.perf_counter()
        logger.info(f"[retrieve] 进入检索: session={session_id[:8]}")
        try:
            # 直接用外部传入的 turns，不再内部调 get_memory()——避免重复 I/O 挂起
            if user_turns is None:
                user_turns = []
            user_msgs = [t["content"] for t in user_turns if t.get("role") == "user"][-4:]
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
                logger.debug(f"[retrieve] cache hit: {(time.perf_counter() - t0) * 1000:.0f}ms")
                return cached["result"]

            # 正常检索流程：七路并行（含平台FAQ + USP诊断）
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
            platform_faq_task = asyncio.wait_for(
                self._retriever.retrieve_platform_faq(search_query, top_k=2),
                timeout=10.0,
            )
            usp_diagnosis_task = asyncio.wait_for(
                self._retriever.retrieve_usp_diagnosis(search_query, top_k=3),
                timeout=10.0,
            )

            logger.info(f"[retrieve] 开始七路并行检索: query={search_query[:60]}...")
            gathered = await asyncio.wait_for(
                asyncio.gather(
                    manual_task, faq_task, troubleshooting_task,
                    cheduan_task, translation_task, platform_faq_task,
                    usp_diagnosis_task,
                    return_exceptions=True,
                ),
                timeout=20.0,
            )
            logger.info(f"[retrieve] 七路检索完成")
            manual_results, faq_results, troubleshooting_results, cheduan_results, translation_results, platform_faq_results, usp_diagnosis_results = gathered

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
            if isinstance(platform_faq_results, BaseException):
                platform_faq_results = []
            if isinstance(usp_diagnosis_results, BaseException):
                usp_diagnosis_results = []

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

            # 平台 FAQ（服务号自身介绍，如工单类型/角色/流转等）
            for r in (platform_faq_results or []):
                q = r.title or ""
                a = r.content or ""
                if a.strip():
                    docs.append(f"---\n📋 平台FAQ {idx}：{q}\n{a}\n---")
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

            # USP 诊断知识库
            for r in (usp_diagnosis_results or []):
                title = r.title or ""
                content = r.content or ""
                if content.strip():
                    docs.append(f"---\n🏭 USP诊断 {idx}：{title}\n{content}\n---")
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
            logger.debug(f"[retrieve] total: {(time.perf_counter() - t0) * 1000:.0f}ms")
            return result

        except ServiceUnavailableError as e:
            logger.warning(f"[retrieve] ServiceUnavailable: {e}")
            logger.warning(f"检索服务不可用: session={session_id[:8]}, error={e}")
        except LowConfidenceError as e:
            thr = getattr(self, '_score_threshold', get_ai_config().retrieval_score_threshold)
            logger.warning(f"[retrieve] LowConfidence: score={e.confidence:.3f} threshold={thr}")
            logger.warning(f"检索置信度过低: session={session_id[:8]}, score={e.confidence:.3f}")
        except (asyncio.TimeoutError, ConnectionError, RetrieveEmptyError):
            logger.warning(f"[retrieve] 超时/失败: {(time.perf_counter() - t0) * 1000:.0f}ms")
            logger.warning(f"检索超时/失败: session={session_id[:8]}")
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
            f'"project":"仅type=problem时填，从对话提取的项目/现场名称，没有则为空",'
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
        except Exception as e:
            logger.error(f"LLM 工单生成失败（将使用默认值）: session={session_id}, error={e}", exc_info=True)
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
            result["project"] = agent_state.collected_info.get("project") or analysis.get("project", "")
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
        await self._ensure_clients()
        memory = await self._memory_manager.get_memory(session_id)
        agent_state = _load_agent_state(memory.metadata) or AgentState(session_id=session_id)
        return await self._build_ticket(session_id, agent_state, memory)

    async def submit(self, session_id: str, created_by: str = "") -> dict:
        await self._ensure_clients()
        memory = await self._memory_manager.get_memory(session_id)
        agent_state = _load_agent_state(memory.metadata) or AgentState(session_id=session_id)
        ticket = await self._build_ticket(session_id, agent_state, memory)

        # 同一会话多次转单：ticket_seq 自增，确保 external_id 唯一（不同话题各自独立工单）
        agent_state.ticket_seq += 1
        ticket["ticket_seq"] = agent_state.ticket_seq

        # ---- 存储到 tasks 表（source='ai'，按 (source, external_id) 幂等 upsert）----
        db_id = 0
        try:
            from ai.core.task_adapter import upsert_task
            record = upsert_task(ticket, created_by=created_by)
            db_id = record.id
            ticket["db_id"] = db_id
            logger.info(f"工单已入库: session_id={session_id}, db_id={db_id}, seq={agent_state.ticket_seq}, "
                        f"title={ticket.get('title', '')}, type={ticket.get('type', '')}")
        except Exception as e:
            logger.error(f"MySQL 工单写入失败: session_id={session_id}, error={e}", exc_info=True)
            print(f"  ⚠️ MySQL 写入失败: {e}")

        agent_state.phase = "resolved"
        # 记住上一个工单（供后续对话判断"补充信息"还是"新话题"）
        agent_state.last_submitted_ticket = {
            "ticket_id": ticket.get("ticket_id", ""),
            "db_id": db_id,
            "title": ticket.get("title", ""),
            "topic": agent_state.problem_summary,
            "submitted_at": int(time.time()),
        }
        # 清空诊断状态——下一轮自动开始新诊断
        agent_state.problem_summary = ""
        agent_state.ruled_out = []
        agent_state.hypotheses = []
        agent_state.collected_info = {}
        agent_state.diagnosis_rounds = 0
        agent_state.original_query = ""
        _save_agent_state(memory, agent_state)
        await self._memory_manager.save_memory(memory)

        # ---- 加入待派单池（后台 Worker 定时扫描并派单）----
        try:
            await self._memory_manager.add_pending_ticket(session_id)
            logger.info(f"工单已加入待派单池: session_id={session_id}, db_id={db_id}")
        except Exception as e:
            logger.warning(f"加入待派单池失败: session_id={session_id}, error={e}")

        return {
            "type": "ticket",
            "data": {
                "ticket": ticket,
                "db_id": db_id,
                "notice": "工单已生成并保存，等待自动派单。",
            },
            "agent_state": _agent_state_summary(agent_state),
        }

    async def prepare_ticket(self, session_id: str) -> dict:
        """生成工单草稿（路径1：按钮转工单），含必填字段校验。"""
        await self._ensure_clients()
        memory = await self._memory_manager.get_memory(session_id)
        agent_state = _load_agent_state(memory.metadata) or AgentState(session_id=session_id)
        ticket = await self._build_ticket(session_id, agent_state, memory)
        ticket["ticket_seq"] = agent_state.ticket_seq + 1
        check = _check_required_fields(ticket)
        ticket["missing_fields"] = check["missing"]
        memory.metadata["ticket_draft"] = ticket
        await self._memory_manager.save_memory(memory)
        logger.info(f"[prepare] session={session_id[:8]}, stage={'draft_ready' if check['ok'] else 'need_fields'}, "
                    f"missing={check['missing']}")
        return {
            "stage": "draft_ready" if check["ok"] else "need_fields",
            "draft": ticket,
            "missing_fields": check["missing"],
            "prompt": check["prompt"],
        }

    async def confirm_submit(self, session_id: str, overrides: dict = None, created_by: str = "") -> dict:
        """确认提交工单（路径1：弹窗确认后），再次校验必填字段后入库。"""
        await self._ensure_clients()
        memory = await self._memory_manager.get_memory(session_id)
        draft = memory.metadata.get("ticket_draft")
        if not draft:
            return {"code": 1, "message": "没有待确认的工单草稿"}
        if overrides:
            draft.update(overrides)
        check = _check_required_fields(draft)
        if not check["ok"]:
            return {"code": 1, "message": check["prompt"], "missing_fields": check["missing"]}

        agent_state = _load_agent_state(memory.metadata) or AgentState(session_id=session_id)
        ticket = await self._build_ticket(session_id, agent_state, memory)
        # 用 draft 中用户编辑过的值覆盖 LLM 重新生成的字段
        ticket.update({k: v for k, v in draft.items()
                       if v and k not in ("ticket_id", "missing_fields", "confirm_prompt", "stage")})

        from ai.core.task_adapter import upsert_task
        ticket["ticket_seq"] = agent_state.ticket_seq + 1
        record = upsert_task(ticket, created_by=created_by)

        agent_state.ticket_seq += 1
        agent_state.phase = "resolved"
        agent_state.last_submitted_ticket = {
            "ticket_id": ticket.get("ticket_id", ""), "db_id": record.id,
            "title": ticket.get("title", ""), "topic": agent_state.problem_summary,
            "submitted_at": int(time.time()),
        }
        agent_state.problem_summary = ""
        agent_state.ruled_out = []
        agent_state.hypotheses = []
        agent_state.collected_info = {}
        agent_state.diagnosis_rounds = 0
        agent_state.original_query = ""
        _save_agent_state(memory, agent_state)
        memory.metadata.pop("ticket_draft", None)
        await self._memory_manager.save_memory(memory)

        try:
            await self._memory_manager.add_pending_ticket(session_id)
        except Exception:
            pass

        logger.info(f"[confirm] 工单已提交: session={session_id[:8]}, db_id={record.id}")
        return {"code": 0, "data": {"ticket": ticket, "db_id": record.id,
                                     "notice": "工单已生成并保存，等待自动派单。"}}

    async def get_draft(self, session_id: str) -> dict:
        """获取待确认草稿（前端轮询兜底）。"""
        await self._ensure_clients()
        memory = await self._memory_manager.get_memory(session_id)
        draft = memory.metadata.get("ticket_draft")
        return {"code": 0, "data": {"draft": draft}} if draft else {"code": 0, "data": {"draft": None}}

    async def _append_to_ticket(self, session_id: str, text: str = "",
                                attachments: list = None) -> bool:
        """将补充信息追加到已提交的工单（更新 MySQL tasks 表）。"""
        try:
            from ai.core.task_adapter import _external_id_for, AI_SOURCE
            from app.core.db import SessionLocal
            from app.models.task import Task

            db = SessionLocal()
            try:
                # 找到该会话最新的工单（external_id 格式: session_id 或 session_id#seq）
                ext_prefix = _external_id_for(session_id)
                task = db.query(Task).filter(
                    Task.source == AI_SOURCE,
                    Task.external_id.like(f"{ext_prefix}%"),
                ).order_by(Task.created_at.desc()).first()
                if not task:
                    logger.warning(f"[append_ticket] 未找到工单: session={session_id[:8]}")
                    return False

                if text:
                    task.description = (task.description or "") + f"\n[补充] {text}"

                if attachments:
                    existing = list(task.attachments or [])
                    existing.extend(attachments)
                    task.attachments = existing

                db.commit()
                logger.info(f"[append_ticket] 工单已更新: session={session_id[:8]}, "
                            f"db_id={task.id}, text_len={len(text)}, attach={len(attachments or [])}")
                return True
            finally:
                db.close()
        except Exception as e:
            logger.error(f"[append_ticket] 更新工单失败: session={session_id[:8]}, error={e}", exc_info=True)
            return False

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
        formatted = "\n".join(
            f"{'用户' if t['role'] == 'user' else '助手'}：{t['content']}"
            for t in turns
        )
        image_turns = [
            (i, t) for i, t in enumerate(turns)
            if "图片主要内容为" in t.get("content", "")
            or "上传了" in t.get("content", "") and ("文件" in t.get("content", "") or "图片" in t.get("content", ""))
        ]
        if image_turns:
            for idx, t in image_turns:
                logger.info(
                    f"[conv] 对话含图片描述: turn_idx={idx} (共{len(turns)}轮), "
                    f"role={t['role']}, content前150字={t['content'][:150]}"
                )
        return formatted

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
        intent = ""
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
                if action not in ("answer", "ask", "submit"):
                    action = "ask"
                intent = data.get("intent", "").strip().lower()
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

        # 最终兜底：message 为空时给一个有意义的默认回复
        if not message or not message.strip():
            logger.warning(f"[parse] 解析后 message 为空! raw前100字={text[:100]}")
            message = "抱歉，我未能正确生成回复，请重新描述您的问题。"

        return {
            "thinking": thinking,
            "action": action,
            "intent": intent,
            "message": message,
            "state_update": state_update,
        }

    # ================================================================
    # run_stream（纯 Agent）
    # ================================================================
    async def run_stream(self, request: DiagnosisRequest):
        t_req = time.perf_counter()
        await self._ensure_clients()
        try:
            memory = await asyncio.wait_for(
                self._memory_manager.get_memory(request.session_id), timeout=3.0,
            )
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning(f"[stream] get_memory 超时/失败，使用空记忆: {e}")
            from ai.core.memory import SessionMemory
            memory = SessionMemory(session_id=request.session_id)
        agent_state = _load_agent_state(memory.metadata)
        logger.debug(f"[overhead] init+redis={(time.perf_counter() - t_req)*1000:.0f}ms")

        if agent_state is None:
            agent_state = AgentState(
                session_id=request.session_id,
                phase="idle",
                original_query=request.query,
                problem_summary=request.query,
            )
            _save_agent_state(memory, agent_state)
            await self._memory_manager.save_memory(memory)
        elif agent_state.phase in ("idle", "resolved") and not agent_state.problem_summary:
            # 上一轮工单已提交、诊断状态已清空 → 全新话题
            agent_state.phase = "idle"
            agent_state.original_query = request.query
            agent_state.problem_summary = request.query
            _save_agent_state(memory, agent_state)
            await self._memory_manager.save_memory(memory)

        async for event in self._agent_think_stream(request, agent_state, memory):
            yield event

    async def _agent_think_stream(self, request: DiagnosisRequest, state: AgentState, memory):
        """Agent 推理流式版 —— 只流 === 之后的回复文本"""
        t_stream: dict = {}  # 流式路径 timing (ms)
        t0 = time.perf_counter()
        turn_count = len(memory.turns)
        has_image = any("图片主要内容为" in t.get("content", "") for t in memory.turns[-6:])
        logger.info(f"[stream] 开始流式推理: session={request.session_id[:8]}, query={request.query[:50]}, "
                    f"round={state.diagnosis_rounds}, turns={turn_count}, "
                    f"has_recent_image={has_image}, phase={state.phase}")
        memory = await self._memory_manager.add_turn(request.session_id, "user", request.query)

        state.diagnosis_rounds += 1
        state.phase = "diagnosing"

        # 记录本轮开始前是否已有已提交的工单（用于判断本轮是否为补充信息）
        was_post_submit = bool(
            state.last_submitted_ticket and state.last_submitted_ticket.get("ticket_id")
        )

        # ---- 闲聊收尾短接：纯问候/致谢/结束语 → 跳过 LLM，直接回复 ----
        _bye_str = re.sub(r"[，。.!！\s]", "", request.query.strip())
        if _bye_str and re.fullmatch(
            r"(好的|ok|okay|感谢|谢谢|多谢|嗯|知道了|明白了|收到|哦|行|好|拜拜|再见|byebye|bye|thanks?|thank\s*you|没事了|没问题了|没有了)+",
            _bye_str, re.IGNORECASE
        ):
            logger.info(f"[stream] 闲聊收尾短接: query={request.query[:30]}")
            short_msg = "不客气，有问题随时找我。" if "谢" in request.query else "好的，有问题随时找我。"
            yield {"event": "token", "data": short_msg}
            result = await self._finalize_diagnosis(
                request.session_id, state,
                thinking="", action="answer", message=short_msg,
                streaming=True)
            if result.get("title"):
                yield {"event": "title", "data": {"title": result["title"]}}
            yield {"event": "result", "data": result}
            return

        # 指代消解："然后呢"等省略表达 → 用上文补全为完整查询
        resolved_query, _ = await self._memory_manager.resolve_pronoun(
            request.query, request.session_id)

        # ---- 诊断路径 ----
        # 立刻发状态，别让用户干等
        yield {"event": "status", "data": {"stage": "retrieving", "round": state.diagnosis_rounds}}
        t_ret = time.perf_counter()
        logger.info(f"[stream] 开始检索: session={request.session_id[:8]}")
        reference_docs = (
            "（跳过检索）" if request.skip_retrieval
            else await self._retrieve_with_context(request.session_id, state, resolved_query, memory.turns)
        )
        t_stream["retrieve"] = round((time.perf_counter() - t_ret) * 1000)
        logger.info(f"[stream] 检索完成: {t_stream['retrieve']}ms, docs_len={len(reference_docs)}")
        prompt = self._build_diagnosis_prompt(state, memory, reference_docs)
        t_stream["prompt_chars"] = len(prompt)
        logger.info(f"[stream] prompt构建完成: {t_stream['prompt_chars']} chars, retrieve={t_stream['retrieve']}ms")

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
        except (AITimeoutError, ServiceUnavailableError, Exception) as e:
            logger.error(
                f"[stream] LLM流式调用失败: type={type(e).__name__}, "
                f"session={request.session_id[:8]}, round={state.diagnosis_rounds}, "
                f"turns={len(memory.turns)}, has_image={has_image}, error={e}",
                exc_info=True,
            )
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
        logger.info(f"[timing] overhead={t_stream.get('overhead_before_llm','?')}ms  "
                     f"retrieve={t_stream.get('retrieve','?')}ms  "
                     f"prompt={t_stream.get('prompt_chars','?')}chars  "
                     f"llm_first={t_stream.get('llm_first_token','?')}ms  "
                     f"total={t_stream.get('llm_agent','?')}ms")

        parsed = self._parse_agent_output(raw)

        self._apply_state_update(state, parsed["state_update"])
        self._apply_action_phase(state, parsed["action"])

        # ---- 服务端兜底：用户消息含转工单关键词 → 强制提单，不依赖 LLM ----
        _force_submit_kw = ("转工单", "转单", "生成工单", "提交工单", "提单", "帮我转", "我要转")
        if parsed["action"] != "submit" and any(
            kw in request.query for kw in _force_submit_kw
        ):
            logger.info(f"[stream] 服务端兜底提单: query={request.query[:40]}")
            parsed["action"] = "submit"

        # ---- 自动提单：LLM 输出 action=submit 时先校验必填字段，完整则直接提单 ----
        ticket_data = None
        if parsed["action"] == "submit":
            try:
                draft = await self._build_ticket(request.session_id, state, memory)
                check = _check_required_fields(draft)
                if not check["ok"]:
                    logger.info(f"[stream] 缺必填字段: {check['missing']}, 引导用户补充")
                    yield {"event": "status", "data": {
                        "stage": "need_fields", "missing_fields": check["missing"], "prompt": check["prompt"],
                    }}
                    memory.metadata["ticket_draft"] = draft
                    await self._memory_manager.save_memory(memory)
                    parsed["action"] = "answer"
                    parsed["message"] = check["prompt"]
                else:
                    yield {"event": "status", "data": {"stage": "submitting"}}
                    ticket_data = await self.submit(request.session_id, created_by=request.created_by)
                    ticket_info = ticket_data.get('data', {}).get('ticket', {})
                    logger.info(f"[stream] 自动提单成功: session={request.session_id[:8]}, "
                                f"ticket={ticket_info.get('ticket_id', '?')}")
                    yield {"event": "status", "data": {
                        "stage": "submitted",
                        "ticket_id": ticket_info.get("ticket_id", ""),
                        "title": ticket_info.get("title", ""),
                        "db_id": ticket_data.get("data", {}).get("db_id", 0),
                    }}
                    # submit() 已清空诊断状态并保存，刷新本地 state 避免 _finalize_diagnosis 覆写旧状态
                    memory = await self._memory_manager.get_memory(request.session_id)
                    state = _load_agent_state(memory.metadata) or state
            except Exception as e:
                logger.error(f"[stream] 提单失败: session={request.session_id[:8]}, error={e}", exc_info=True)
                yield {"event": "status", "data": {"stage": "submit_failed", "error": str(e)}}

        # ---- 补充工单：post-submit 时判断是否为补充信息 → 更新 MySQL 工单 ----
        # 双层检测：① LLM 明确输出 intent=follow_up ② 兜底：action=answer 且非 howto/troubleshoot
        _is_follow_up = parsed.get("intent") == "follow_up" or (
            was_post_submit and parsed["action"] == "answer"
            and parsed.get("intent") not in ("troubleshoot", "howto", "chat")
        )
        if _is_follow_up:
            try:
                appended = await self._append_to_ticket(
                    request.session_id, text=f"用户补充：{request.query}"
                )
                if appended:
                    logger.info(f"[stream] 补充信息已追加到工单: session={request.session_id[:8]}")
            except Exception as e:
                logger.error(f"[stream] 补充工单失败: {e}", exc_info=True)

        result_data = await self._finalize_diagnosis(
            request.session_id, state,
            parsed["thinking"], parsed["action"], parsed["message"],
            streaming=True)
        if ticket_data:
            result_data["ticket"] = ticket_data

        # 标题生成：第2轮对话结束后通过独立 SSE event 发送
        if result_data.get("title"):
            yield {"event": "title", "data": {"title": result_data["title"]}}

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
