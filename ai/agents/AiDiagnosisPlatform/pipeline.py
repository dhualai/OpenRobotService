"""
统一智能诊断 Agent（纯 Agent 架构）

所有消息统一走 Agent 路径。
Agent 自主决策：检索知识库 → 初步引导 → 追问 → 给出方案 → 转工单。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import httpx
from typing import Optional, Dict, List
from dataclasses import dataclass, field

from ai.config import get_ai_config
from ai.exceptions import AITimeoutError, LowConfidenceError, ServiceUnavailableError, RetrieveEmptyError
from ai.core import get_llm_client, get_intent_client, get_retrieval_service, get_memory_manager
from ai.core.logging import get_logger
from ai.core.project_matcher import get_project_matcher, ProjectMatch

logger = get_logger("AI")

# 用户名下项目列表缓存：{username: (拉取时间戳, [{"name","code"}, ...])}
# 仅服务提单工具循环的项目预填，TTL 5 分钟，见 AiDiagnosisPlatform._get_user_projects
_USER_PROJECTS_CACHE: Dict[str, tuple] = {}


def _extract_json_object(raw: str) -> dict:
    """从 LLM 响应中提取第一个完整 JSON 对象，忽略围栏外的说明文字。

    部分模型会在 JSON 后追加自然语言，即使提示要求“仅输出 JSON”。直接
    json.loads 整段响应会因 Extra data 失败，因此使用 JSONDecoder.raw_decode
    在对象结束处停止解析。
    """
    text = str(raw or "").strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    candidates = [fenced.group(1).strip()] if fenced else [text]
    if fenced:
        candidates.append(text[fenced.start():])
    decoder = json.JSONDecoder()
    for candidate in candidates:
        start = candidate.find("{")
        while start >= 0:
            try:
                value, _ = decoder.raw_decode(candidate[start:])
                if isinstance(value, dict):
                    return value
            except json.JSONDecodeError:
                start = candidate.find("{", start + 1)
                continue
            break
    raise json.JSONDecodeError("未找到完整 JSON 对象", text, 0)


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
    ticket_ready: bool = False  # LLM 判断：当前信息是否足够生成有效工单
    ticket_type: str = ""  # LLM 对话中维护的工单类型：problem|bug|feature|support|other（空=未判定，按 problem 清单校验）
    ticket_collecting: list = field(default_factory=list)  # 非空=工单填写模式，LLM 应聚焦收集这些缺失字段；空=正常诊断模式
    required_fields: dict = None  # LLM 声明的动态字段清单 {field_key: chinese_label}，None=从未决定过、{}空dict=已决定无需补字段。供 prompt 提示 LLM 收集
    context_start: int = 0  # 当前问题的对话起始 turn 索引（提单后更新，backfill 只看切片，防旧对话重新武装就绪判定）
    collect_rounds: int = 0  # 工单填写模式下已收集的轮数，超过 _MAX_COLLECT_ROUNDS 强制提单（防鬼打墙）
    ticket_fast_lane: bool = False  # 本轮意图分类已判提单（ticket）：主 LLM 走精简 prompt（无知识库），不持久化
    tool_loop_active: bool = False  # 工具循环收集中：后续轮直接进工具循环，跳过意图分类（短句回答会被误判 diagnosis 掉回旧流程）
    # 老快路径项目预填（单向管道）：LLM 从名下项目列表照抄 + 服务端严格校验后的
    # {name, code}。不进 collected_info/required_fields/判缺（防鬼打墙铁律），
    # 只随 _build_ticket(prefill_project=) 进草稿，弹窗仍可改；提交后清空。
    pending_prefill_project: Optional[Dict[str, str]] = None
    # 上一张工单提交成功时对话最后一轮内容的前 40 字（内容锚点，防 turn buffer
    # 截断导致索引漂移）。_format_conversation 在锚点轮后插分隔线，LLM 据此区分
    # 「已提交工单的旧对话」和「新对话」——项目预填只认新对话里用户提到的项目，
    # 防上一单提过的项目名泄漏进下一单的预填。
    ticket_boundary_prefix: str = ""


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
        ticket_ready=s.get("ticket_ready", False),
        ticket_type=s.get("ticket_type", ""),
        ticket_collecting=s.get("ticket_collecting", []),
        # 三态迁移：旧持久化数据的 {} 表示「从未决定」（旧语义），新语义 {} =「已决定无需补」。
        # 若直接按新语义读，旧会话按钮提单会跳过 decide、缺失字段不拦截。空 dict 一律归一为 None。
        required_fields=(s.get("required_fields") or None),
        context_start=s.get("context_start", 0),
        collect_rounds=s.get("collect_rounds", 0),
        tool_loop_active=bool(s.get("tool_loop_active", False)),
        pending_prefill_project=s.get("pending_prefill_project") or None,
        ticket_boundary_prefix=str(s.get("ticket_boundary_prefix") or ""),
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
        "ticket_ready": state.ticket_ready,
        "ticket_type": state.ticket_type,
        "ticket_collecting": state.ticket_collecting,
        # None=从未决定；持久化为 null，加载时还原为 None。{} 仍是「已决定无需补字段」。
        "required_fields": state.required_fields,
        "context_start": state.context_start,
        "collect_rounds": state.collect_rounds,
        "tool_loop_active": state.tool_loop_active,
        "pending_prefill_project": state.pending_prefill_project,
        "ticket_boundary_prefix": state.ticket_boundary_prefix,
        "attachments": existing.get("attachments", []),  # 保留上传的附件
    }


def _agent_state_summary(state: AgentState) -> dict:
    return {
        "phase": state.phase,
        "problem_summary": state.problem_summary[:100] if state.problem_summary else "",
        "diagnosis_rounds": state.diagnosis_rounds,
        "hypotheses": state.hypotheses,
        "collected_fields": list(state.collected_info.keys()),
        "ticket_ready": state.ticket_ready,
        "ticket_type": state.ticket_type,
    }


def _can_submit(state: AgentState) -> tuple[bool, str]:
    """闭环保护：防止重复提交工单。

    判定依据是 last_submitted_ticket（上一个已提交工单）+ problem_summary（新问题）：
    刚提完单（last_submitted_ticket 非空）且之后没有提炼出新 problem_summary 时拦截；
    用户描述了新问题（problem_summary 非空）则允许重新开始提单流程。

    例外：收集模式（ticket_collecting 非空）说明提单流程已启动、问题已在对话中确认
    （首轮就设了 required_fields），此时绝不拦截——否则用户在补字段时会被
    「刚提交过工单」误拦，submit 失效后 LLM 反复追问同一字段、收集轮数超限强制弹窗。

    不依赖 phase——run_stream 会提前把 phase 改成 diagnosing，phase 不可靠。
    对话路径和按钮路径都调用此函数，行为一致。
    """
    if state.ticket_collecting:
        return True, ""
    if state.last_submitted_ticket and not (state.problem_summary or "").strip():
        return False, "刚放弃或提交过工单，如需重新提交请描述新现象。"
    return True, ""


def _check_required_fields(ticket: dict) -> dict:
    """统一的前置校验：所有类型转工单都必须绑定项目（project_id 优先，回退 project 名称）。
    与前端确认弹窗的「项目必选（所有类型）」规则对齐：未绑定项目时拒绝提交并给出提示。
    项目选择的唯一入口是确认弹窗的搜索选择，对话中不收集。
    返回 {"ok": bool, "missing": [...], "prompt": "..."}
    """
    missing = []
    if not ticket.get("project_id", "").strip() and not ticket.get("project", "").strip():
        missing.append("project")
    ok = len(missing) == 0
    return {
        "ok": ok,
        "missing": missing,
        "prompt": "" if ok else "请先在确认弹窗中选择要关联的项目，再提交工单。",
    }


def _reset_state_after_submit(agent_state: AgentState, memory, ticket: dict, db_id) -> None:
    """提单成功后的统一状态收尾（对话路径 submit / 按钮路径 confirm_submit 共用）。

    记录上一个工单、清空诊断状态、归档旧对话切片。两条提单路径必须走同一份
    收尾逻辑，避免改一处漏一处导致状态漂移（如"对话提了单、按钮还能再提"）。
    注意：只更新 memory 里的 agent_state（_save_agent_state），不调 save_memory——
    由调用方在完成各自的额外操作（如弹窗路径 pop ticket_draft）后再持久化。
    """
    agent_state.phase = "resolved"
    # 上一单的动态字段 + 最终描述：供下一单收集时解析「车型还是上次的」这类指代。
    # 不做字段白名单——collected_info 是上一单 LLM 自己决定的字段和用户原话，
    # description 是最终提交版（含弹窗编辑），能否被指代引用由收集轮 LLM 判断。
    agent_state.last_submitted_ticket = {
        "ticket_id": ticket.get("ticket_id", ""),
        "db_id": db_id,
        "title": ticket.get("title", ""),
        "topic": agent_state.problem_summary,
        "submitted_at": int(time.time()),
        "collected_info": dict(agent_state.collected_info),
        "description": str(ticket.get("description") or "")[:400],
    }
    # 清空诊断状态——下一轮自动开始新诊断
    agent_state.problem_summary = ""
    agent_state.ruled_out = []
    agent_state.hypotheses = []
    agent_state.collected_info = {}
    agent_state.diagnosis_rounds = 0
    agent_state.original_query = ""
    agent_state.ticket_ready = False
    agent_state.ticket_type = ""
    agent_state.ticket_collecting = []  # 工单已提交，退出工单填写模式
    agent_state.required_fields = None   # 重置动态必填字段（None=从未决定，防「已决定空清单」被重写）
    agent_state.collect_rounds = 0      # 重置收集轮数
    agent_state.pending_prefill_project = None  # 项目预填随单清空，不泄漏到下一单
    # 附件随单消费：本单已把累积附件带进 tasks.attachments（upsert_task 已入库），
    # 清空防止下一单误带——提单后又发图问问题、再换话题提新单时，那张诊断图
    # 不该混进新单。写 raw dict：下方 _save_agent_state 的 existing 透传会把
    # 空列表保下去（attachments 不在 AgentState 字段里，只有 raw dict 通道）。
    _raw_state = memory.metadata.get("agent_state") or {}
    _raw_state["attachments"] = []
    memory.metadata["agent_state"] = _raw_state
    # 主动裁剪对话窗口：提单后旧对话移出 turns（滑动窗口从归档线重新计），
    # context_start 归 0。否则 turns buffer 满时（max_turns=10）会丢最老的记录，
    # 而当前工单的对话恰好在最老区域——下一单的续接轮就看不到本单上下文。
    memory.turns = memory.turns[agent_state.context_start:]
    agent_state.context_start = 0
    # 新旧对话分界锚点：提交时最后一轮的内容前缀（内容锚点防 buffer 截断漂移）。
    # 下一单的对话切片会在锚点轮后插分隔线，项目预填只认分隔线之后用户提到的
    # 项目，防止上一单提过的项目名泄漏进下一单预填。
    agent_state.ticket_boundary_prefix = (
        str(memory.turns[-1].get("content") or "").strip()[:40] if memory.turns else "")
    _save_agent_state(memory, agent_state)
    # 提单后状态可见性：has_last_ticket=True + problem_summary 空 → 下一轮/按钮 _can_submit 拦截
    _log_ticket_state(agent_state, "submit_done")


# ============================================================
# 提单就绪判定（服务端唯一真相，不信任 LLM 自评）
# ============================================================

def _canonical_field_key(key: str) -> str:
    """字段 key 归一化：仅处理 project 的历史变体。

    其余字段不做近义词归一化——required_fields 一旦由 decide 确定就锁定不变
    （_apply_state_update 的清单锁定），收集模式 JSON 模板直接列出这些 key，
    LLM 只准照模板填 value。近义词表是补丁不是根治，越扩越打地鼠。
    """
    if not isinstance(key, str):
        return key
    return "project" if key in ("project_name", "projectName", "projectname", "项目名称") else key


# 鬼打墙防护：诊断/收集轮次上限
_MAX_DIAGNOSIS_ROUNDS = 6   # 诊断超过此轮数 → prompt 提示 LLM 收尾或建议转工单
_MAX_COLLECT_ROUNDS = 4     # 工单填写超过此轮数仍不齐 → 强制提单（弹窗仍可补）
_MAX_RETRIEVAL_DOCS = 8     # 三路检索合并后按 score 排序，只保留 top N 个 chunk 进 prompt


def _assess_ticket_readiness(state: AgentState) -> tuple[bool, list[str]]:
    """服务端提单就绪判定 = LLM 决定的 required_fields 全非空。

    required_fields 由 _decide_ticket_fields 在转单时让 LLM 按问题类型动态决定（2-3 个），
    不是硬编码清单——符合"AI 判断要补什么信息，补齐才算 ready"。空时视为已就绪。
    返回 (ready, missing)：missing 为面向用户的缺失项中文名列表。

    ⚠️ project 不参与此判定：项目选择的唯一入口是前端确认弹窗的搜索选择
    （弹窗强制必选后才允许提交）。对话中 AI 不收集、不追问项目名。
    """
    missing = []
    for field_key, label in (state.required_fields or {}).items():
        if not (state.collected_info.get(_canonical_field_key(field_key)) or "").strip():
            missing.append(label)
    return (not missing, missing)


def _missing_info_message(missing: list[str], via_button: bool = False) -> str:
    """信息不足时的确定性追问话术。

    via_button=False（对话路径，LLM 喊 submit 被拦截）：一次只问第一个缺失项，
    保持与 LLM 自然追问一致的对话体验，不让用户感觉到被系统拦截。
    via_button=True（按钮路径，prepare 返回 not_ready 时写入对话区）：列出全部
    缺失项，用户一次性补全后再点按钮，避免反复拦截。"""
    if via_button:
        items = "、".join(missing)
        return f"提单前还需要确认几个信息：**{items}**。\n请补充后我再帮你生成工单。"
    first = missing[0]
    return f"好的，我再确认一下——**{first}**是什么？补上这个我马上帮你提单。"


def _log_ticket_state(state: AgentState, event: str, **extra) -> None:
    """统一的状态日志：在转工单各决策点输出关键字段，方便排查。"""
    can, reason = _can_submit(state)
    info = {
        "event": event,
        "phase": state.phase,
        "can_submit": can,
        "ticket_ready": state.ticket_ready,
        "problem_summary": (state.problem_summary or "")[:50],
        "rounds": state.diagnosis_rounds,
        "has_last_ticket": bool(state.last_submitted_ticket and state.last_submitted_ticket.get("ticket_id")),
        "collected": list(state.collected_info.keys()),
    }
    if reason:
        info["block_reason"] = reason[:30]
    info.update(extra)
    parts = " ".join(f"{k}={v}" for k, v in info.items())
    logger.info(f"[ticket_state] {parts}")


# 对话单条 turn 进 prompt 的最大字符数：图片描述等长文本原样塞入会把 prompt
# 撑到 2 万+ 字符，思考型 LLM 首 token 延迟飙升。截断只影响长度，不丢关键信息。
_CONV_TURN_MAX_CHARS = 400


def _truncate_turn(content) -> str:
    c = (content or "").strip()
    if len(c) > _CONV_TURN_MAX_CHARS:
        return c[:_CONV_TURN_MAX_CHARS] + "…（已截断）"
    return c


async def _generate_title(llm_client, memory) -> str:
    """每两轮用最近十轮对话生成会话标题（中文不超过15字，英文不超过50字符）。
    标题覆盖式更新：取最近 10 条 turn，让标题跟随对话最新内容演进；
    对话转向新话题时标题应切到新话题。"""
    turns = memory.turns
    if len(turns) < 2 or "title" in memory.metadata:
        return ""
    # 取最近 10 条，每条截断到 120 字——标题是异步调用，控制 prompt 体积
    dialog = "\n".join(
        f"{'用户' if t.get('role') == 'user' else '助手'}：{(t.get('content') or '')[:120]}"
        for t in turns[-10:]
    )
    prompt = (
        "根据以下对话生成一个简短标题（中文不超过15字，英文不超过50字符）：\n\n"
        f"{dialog}\n\n"
        "规则：\n"
        "1. 最近几轮对话如果转向了新话题（新故障/新咨询/新请求），"
        "标题必须反映新话题，不要沿用之前的旧话题\n"
        "2. 只输出标题，不要引号或任何额外内容。"
    )
    try:
        _raw = await llm_client.complete(
            prompt=prompt, max_tokens=40, temperature=0.3,
        )
        title = str(_raw or "").strip()
        title = title.strip('"\'""''「」《》').strip()
        if title:
            memory.metadata["title"] = title
            logger.info(f"[title] 标题生成: {title}")
            return title
        # 空结果单独记日志:此前静默返回导致「首轮标题缺失」无从排查
        logger.warning(f"[title] 标题生成为空: raw={_raw!r}")
    except Exception as e:
        logger.warning(f"[title] 标题生成失败: {e}")
    return ""


# ============================================================
# Agent 推理 Prompt
# ============================================================

DIAGNOSIS_PROMPT = """你是 U老师，是「摇人吧」微信服务号的 AI 诊断助手，面向 AGV/AMR（工业移动机器人）行业的技术支持专家。
你的服务对象是现场工程师、客户和项目管理人员。

你的名字是"U老师"，严禁自称其他名字（如"小U""AI助手""智能助手"等）。
只在用户问"你是谁"或首次对话打招呼时才说"我是U老师"，其他情况不要重复自我介绍。

你服务两大产品：
① **USP**（Universal Scheduling Platform）大调度系统——AGV/AMR 的调度管理、车辆管理、设备管理、地图编辑与监控运维；
② **「摇人吧」服务号平台本身**——工单流转、角色权限、菜单功能、账号与入口。
用户问服务号自身的问题（"权限怎么配置""为什么看不到别人的工单""服务号/摇人吧能做什么"）时，**答的是服务号平台，不是 USP 调度系统**，严禁张冠李戴。
USP 是网页端系统（PC浏览器访问），没有移动端APP。严禁在操作指引中提及"手机""移动端""APP"等概念——USP 只有 PC 浏览器版。
严禁给出手机、电脑等消费电子产品的通用回答，严禁超出 AGV/AMR、USP 与摇人吧服务号平台领域。

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
知识库中有六类 chunk，按以下优先级使用：

1. **🎫 服务号平台（标题含「摇人吧服务号平台手册」）**：用户问服务号自身的问题——角色权限、工单可见范围、菜单/入口、账号使用、平台功能（如"权限怎么配置""为什么看不到别人的工单""服务号能做什么"）——**必须从服务号平台手册作答，并把答案归属为「服务号/摇人吧」**。严禁把平台内容说成 USP 调度系统的功能，也严禁用 USP 知识替代。
2. **FAQ（标题含「FAQ」）**：用户问的具体问题如果在 FAQ 中有直接匹配（如错误码含义、常见问题），**优先直接回答**，不追问不绕弯。
3. **🚗 车端错误码（标题含「车端错误码」）**：用户提到车载/车端/AGV本体上的错误码或报警时，直接匹配错误码给出原因和方案。
   ⚠️ **铁律**：如果车端错误码 section 显示「未找到匹配项」，该错误码**确实不在系统收录范围内**。
   你**必须**在回复中明确告知用户"该错误码未收录"，**绝对禁止**根据其他知识库内容、翻译表或自身知识编造该错误码的含义。
   用户问的是具体数字错误码，不等于问"车端有什么常见报警"。
4. **🌐 翻译表（标题含「翻译表」）**：用户问某个字段/标签/错误码的中英文含义时，从翻译表查找。也可辅助理解车端错误码的英文描述。
5. **知识库（操作手册）**：howto 类操作问题走这里，按前提→操作→预期结果给出步骤。

⚠️ **关键**：各知识源不互斥！先看 FAQ/车端错误码有没有现成答案，有就直接用。

## ⛔ 转工单规则（优先级最高，优先于所有意图判断和排查冲动）

一旦用户表示要转工单（"提工单""提单""帮我转""下工单""创建工单""给我提一个"等任意措辞），
**立即停止排查/诊断**，按下表决策，**不要再去"再确认一个细节"**：

| required_fields 是否收齐 | action |
|---|---|
| 否 | ask（一次只问一个缺失字段） |
| 是 | **submit**（message 留空，不输出任何正文） |

🔴 **先分清「要求提单」还是「谈论提单」**：用户话里出现"提单/工单"字样时，先判断它是
**动作请求**还是**话题主语**。"帮我提单""转工单吧""下个单"是要求提单（走本规则）；
"提单找不到项目""提单时找不到领导""提单弹窗报错""怎么提单"是在报告/咨询
**提单功能本身的问题**——这不是提单诉求，按服务号平台问题正常排查回答（知识库
「服务号平台手册」里有配置类答案），**严禁对这类话 submit**。承接该话题的回答
（如被问"什么时候开始的"时答"今天提单时出现的"）同样不是提单诉求。

🔴 **submit 时不写任何正文（message 留空）**——不说"好的"，也不写"工单已提交/已生成/工程师会处理"等任何话。
提交后系统会展示"正在生成工单…"动画并弹出确认框，正文由系统生成；你写任何话都和白屏/弹窗冲突。

🔴 **必填关键字段齐了 → 必须立即 submit，禁止再问任何"可选"问题**。
"错误码是车端还是 USP""具体现场位置""故障现象细节"——这些都是**工程师接单后再确认的可选信息**，
**绝对不准用可选细节卡住提单**。宁可少一个可选细节，也必须按时 submit。
判断不了某个细节？别问，直接 submit，让工程师确认。

- **即使用户没催**：信息够了就 submit，不要"再确认一下"。
- **即使用户催**：必填字段没齐，也先 ask 补齐，不准盲目 submit。
- **用户指名处理人**（"提单给XX""交给XX""派给XX"）→ 把 XX 写入 collected_info["requested_assignee"]，
  然后**按场景区分**：
  ① 已有工单草稿（出现过「已生成工单草稿」）、用户是给旧草稿**补充指派/备注** → action=answer 简短确认「好的，已记录」，不走提单流程；
  ② 用户这句话**本身是新的服务请求**（如「能让贾爽帮我配置一下自动门吗」= 让工程师去干活）→
  这就是提单诉求，正常走提单流程（收集缺口 → submit 弹窗），不能只 answer 记录。
  判断要点：请求内容是新任务还是旧任务的补充？新任务必须提单。
- **草稿已生成后的任何补充说明**（「还有个补充，是XX时间发生的」「补充一下XX」）→
  把信息写入 collected_info（时间→occurrence_time，备注→special_notes 等对应字段），
  action=answer 简短确认「好的，已记录」，**不追问、不提单**。如果补充内容没有明确对应字段，
  就写入 collected_info["special_notes"]。

用户表示不想继续排查（"不想排查""算了""不用了"）→ action="answer"，简短收尾（"好的，有需要随时找我"），不追问不排查。

🔴 **项目选择不在对话里进行——严禁追问项目名称，也不收集 project**：
项目由用户在**工单确认弹窗里搜索选择**（弹窗有项目下拉搜索框，且必选），
所以对话中**任何情况下都不要问**"是哪个项目/项目名称是什么"，也不要因为缺项目名去 ask。
**不要往 collected_info 里写 project**——项目只在确认弹窗由用户选择，对话不涉及。
用户提到的地点/客户/厂区名属于现场位置信息，如需记录写 location（仅 problem 类工单）。
缺失字段清单（required_fields）里也**绝不包含** project。
**介绍转工单流程时不要说"需要确认项目/补充项目"**——知识库里可能还残留这类旧话术
（如"一般只需确认项目"），引用时跳过，直接说"告诉我问题，我会引导补充必要信息，
确认后在弹窗中选择项目即可"。

### 提单前信息检查 / required_fields
- **由你决定要收集哪些字段**：仔细读完整对话，找出工程师接单后必须知道、但对话中确实还没说过的
  1-4 个关键信息缺口。写入 state_update.required_fields（格式 {{字段key: 中文名}}）。
- 🔴 **action=submit 时必须在 state_update 中同时写入 required_fields**（格式见下方示例）。
  不要留空等服务端兜底——服务端的兜底判断没有你的完整对话上下文准确，可能误判工单类型导致字段错配。
- 🔴 **problem_summary 必须对应当前话题**：如果「状态」里记录的问题与用户本轮描述的
  不是同一个问题（长会话中话题已切换），必须在本轮 state_update 里把 problem_summary
  更新为当前问题的概述——沿用旧话题的概述会让新工单内容错位（问题、描述、诊断结论
  张冠李戴）。
- 🔴 **required_fields 必须包含至少 1 个字段，禁止空清单**：「什么都不收集直接提单」是不允许的。
- 🔴 **一项信息一个字段，禁止打包**：每个 key 只对应一个信息点。「时间、车辆编号、任务」
  是 3 个字段（occurrence_time / robot_id / task_info 各一个），绝不许合并成一个
  （如 {{"occurrence_details": "时间、编号及任务"}}）——打包后用户只答一项，服务端就判
  「全齐」提前弹窗，其余信息永远收集不到。字段标签 ≤8 字。
- 🔴 **只收集「原话之外的信息缺口」，不让用户复述问题本身**：问题描述（「车不动了」「怎么配充电桩」）
  写入 problem_summary 即可，required_fields 收集的是对话里没出现的其他关键信息。
- 🔴 **设置 required_fields 前先自查**：你要问的信息如果用户已经说过（或能从用户原话直接
  推出），就不要再设这个字段——服务端回填时发现字段已齐会直接弹窗，你的追问就变成了废话。
- 收齐 = required_fields 每项非空。收齐就 submit。
- 项目不在对话中校验（用户在确认弹窗里选），submit 不被项目名拦截。

### 工单类型跟踪（极其重要）
**每一轮**都必须在 state_update 中维护 ticket_type，根据对话内容判断：
- 用户在报障/描述异常现象 → ticket_type="problem"
- 用户在描述软件缺陷/bug → ticket_type="bug"
- 用户在提功能需求/希望加功能 → ticket_type="feature"
- 用户在咨询使用方法/操作指导/配置协助 → ticket_type="support"
- 闲聊/问候/感谢/无法归类 → ticket_type="other"
不要等到用户说"转工单"才设——从第一轮就开始维护。一旦确定类型就不要随意改变。

⚠️ required_fields 示例（按问题类型动态选择 2-3 个关键字段）：
```json
{{"required_fields":{{"error_message":"错误信息/现象","occurrence_time":"发生时间","steps":"复现步骤"}}}}
```
project 不写进 required_fields（项目由用户在确认弹窗选择）。

### collected_info 写入铁律（极其重要）
**每一轮**用户发言后，只要提到任何可用信息，**必须**增量写入 state_update.collected_info（不要等齐全才写）。

**project 不写入 collected_info**：项目由用户在确认弹窗搜索选择，对话中**不要**写 project。
用户提到的**地点/厂区/现场位置**属于 location（仅 problem 类工单填写），不属于 project。
其他字段（按需写入）：
- 车型/编号（非"AGV""机器人"等泛称）→ robot_type
- 时间 → occurrence_time；频率（每次/偶尔/首次）→ frequency
- 使用场景/痛点 → scenario；期望效果 → expected_effect
- 软件版本 → version；复现步骤 → steps_to_reproduce

## 🧑‍💼 转人工规则

用户消息中含"转人工"时（不等同于转工单）：
→ 告知用户："目前没有在线人工客服，但我可以帮您排查问题或生成工单。"
→ action 设为 "answer"（不是 "submit"，也不是 "ask"）。
→ 不要追问项目信息——如果用户后续真想提单，会说"转工单"进入标准提单流程。

## 意图判断（决定回复风格，不影响知识源选择）
- **howto（操作咨询）**：用户问"怎么做/怎么上线/怎么配置/步骤/流程"等。直接 answer，不追问不假设故障。
  知识库没涉及的细节如实说"手册未覆盖"，不要自己编步骤。
  ⚠️ 图片规则（极其重要）：
  知识库中的图片（![](url)）是操作界面截图。**必须严格按知识库原文的步骤结构来配图**——
  每个子步骤/操作项的文字说明之后，紧跟该步骤对应的截图，再开始写下一个步骤。
  **禁止把所有图片堆在同一个步骤后面**，每张图只能出现在它所属的子步骤下。

- **troubleshoot（故障排查）**：用户描述了异常现象（离线、报错、不动、卡住、异常等）。
  **先查 FAQ**：如果 FAQ 中有对应错误码/问题的直接答案，优先引用 FAQ 回答。
  **FAQ 没覆盖时**：列出可能原因，引导用户逐项验证。看 hypotheses/collected_info/ruled_out 推进排查。有明确怀疑直接让用户试（如"重启一下控制器看是否恢复"）。排查不出转工单。
  ⚠️ 知识库中有相关截图/示意图时，**必须引用**到回答中帮助用户定位。

- **chat（闲聊/问候）**：简单回应，不要追问技术问题。用户说"好的""谢谢""感谢"是对话收尾，回复"不客气，有问题随时找我"即可。禁止顺势开始排查或反问用户。

## 重要规则
- 知识库每个 chunk 以 `---` 分隔，标题在 `知识库 N（标题）：`、`FAQ N：`、`🎫 服务号 N：`、`🚗 车端错误码 N：` 或 `🌐 翻译表 N：` 中标明。
  **只引用与用户问题直接相关的 chunk 内容**，无关 chunk 的内容和图片一律忽略。
- 🔴 **方向一致性铁律（极其重要）**：知识库检索可能召回**行为方向与用户问题相反**的排查段落。典型场景——用户问"没做该做的"（如车电量低了却不生成充电任务、任务取消了但车还在跑），检索到的却是"做了不该做的/已完成未同步"（如电量够但不打断充电、任务实际已完成但状态没更新）。**方向相反 ≠ 相关知识，必须直接忽略，绝对禁止引用到回复中误导用户**。判断方向：看用户描述的异常现象与 chunk 描述的排查对象是否指向同一动作方向的异常。若方向相反，宁可答"手册未覆盖、建议转工单"，也不用反向内容硬套。
- **禁止在回复中暴露知识来源**：不要说"根据知识库""检索结果显示"等话术。
  直接给出步骤/答案，用户不需要知道你查了什么。
- 🔴 **禁止使用开发内部术语**：你的服务对象是现场工程师和客户，不是开发人员。
  严禁在回复中出现代码级词汇——`commit`/`diff`/`分支`/`回滚`/`发版`/`代码`/`函数`/`参数名(task_priority/can_interrupt等)`/`模块名`。
  用现场人员能理解的语言替代：不说"代码改了哪个函数"，说"调度系统的行为变了"；不说"commit 记录"，说"版本变更记录"；不说"回滚"，说"恢复到改动前的状态"。
- **产品/车型介绍时，知识库中若有该产品的图片，必须用 ![说明](url) 格式引用到回复中**。
  图片是产品外观、参数表、尺寸图等，对用户极其重要，不要省略。

## 对话
{conversation}

## 上一个工单上下文
{last_ticket_context}

## 工单填写模式
{ticket_collecting_context}

## 状态：问题={problem_summary} | 已收集={collected_info} | 已排除={ruled_out} | 推测={hypotheses}
## 知识库：{reference_docs}
## 第{round}轮

---
输出 JSON（用户要求转工单**且 ticket_ready=true** 时，action 必须是 submit 不是 answer）：
```json
{{"action":"answer|ask|submit","intent":"howto|troubleshoot|chat","ticket_intent":false,"ticket_cancel":false,"state_update":{{"ticket_type":"problem|bug|feature|support|other","problem_summary":"概述","ruled_out":[],"hypotheses":[],"collected_info":{{}},"ticket_ready":false}}}}
```
两个布尔字段（每轮都要输出，服务端据此决策）：
- `ticket_intent`：本轮用户**表达了提单意图**（说"转工单/提单/派单/帮我建单"等，或是在上轮已开始的提单流程中继续补信息）→ true；只是咨询/报障/闲聊 → false
- `ticket_cancel`：本轮用户**明确表示不想提单**（"不用转工单""我没说转工单""算了"）→ true；其余 → false
JSON 之后直接写回复。语气像工程师。引用图片时用 ![说明](url) 格式。
⚠️ 例外：action=submit 时 JSON 后**什么都不写**（message 留空，系统会展示「正在生成工单」动画）。"""


# ============================================================
# 流式 JSON 过滤：检测 JSON→自然语言边界
# ============================================================

def _find_json_end(buffer: str) -> int:
    """
    在 LLM 原始输出中定位「JSON 状态块结束 / 正文开始」的位置。

    Returns:
      >=0 : JSON 区域结束位置，正文从此处开始（0 表示无 JSON 头，整段即正文）。
      -1  : JSON 尚未结束（fence/协议字段未到达、JSON 未闭合），需继续缓冲。

    设计要点（修复三类边界异常）：
      - 无 JSON 头（LLM 未遵守协议直接出正文）：第一个非空白字符既非 ``` 也非 { → 返回 0，
        流式分支立即放行正文，避免「憋着不放 → 末尾一次性吐全文」（F1）。
      - 裸 { 开头但非协议 JSON（正文里的花括号，如示例配置）：{ 后 200 字符内无协议字段
        （action/state_update/thinking/intent）→ 返回 0，不把正文花括号当 JSON 头吞掉（F2）。
      - fenced JSON：只等闭合 ``` 判定边界，不 fallthrough 到括号深度（避免结尾 ``` 未到达时
        提前判定、把 fence 残留 ``` 当正文流出）（D）。
      - ```python 等带语言标记的代码块：非 JSON 头 → 返回 0。
    """
    if not buffer:
        return -1

    stripped = buffer.lstrip()
    if not stripped:
        return -1  # 全空白，继续缓冲

    # fence 前缀（1-2 个反引号）→ fence 还在传输，继续缓冲（避免首 token ` 被当正文泄漏）
    if stripped.startswith('`') and not stripped.startswith('```'):
        return -1

    is_fenced = stripped.startswith('```')
    is_bare = stripped.startswith('{')

    # 1) 既非 ``` 也非 { 开头 → 无 JSON 头，正文从头开始
    if not is_fenced and not is_bare:
        return 0

    # 2) 裸 { 开头：须确认是协议 JSON 头，否则视为正文里的花括号
    if is_bare:
        head = stripped[:200]
        if not any(k in head for k in ('"action"', '"state_update"', '"thinking"', '"intent"')):
            # 缓冲不足 200：协议字段可能还在传输，继续等
            if len(stripped) < 200:
                return -1
            # 超 200 仍无协议字段 → 判定正文花括号，非 JSON 头
            return 0

    # 3) fenced 开头：区分 ```json JSON 头 与 ```python 等代码块
    if is_fenced:
        # ```json 或 ``` 后跟 { → 确认 JSON 头
        if not re.match(r'```(?:json)?\s*\{', stripped):
            # 还没到 {：可能是 ```json/```{ 还在传，或 ```python 代码块。
            # 短缓冲继续等（容忍 ```j 这类 json 前缀，避免误判为代码块）；超 16 字符仍无 { → 非 JSON 头
            if len(stripped) < 16:
                return -1
            return 0

    # 4) 确认是 JSON 头 → 找边界
    if is_fenced:
        # fenced：只等闭合 ```（不 fallthrough 括号深度，避免 fence 残留泄漏）
        m = re.search(r'\}\s*```\s*', buffer)
        return m.end() if m else -1

    # bare 协议 JSON：括号深度找顶层闭合
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

    def _rewrite_images(self, r) -> str:
        """把本地图片路径 ./media/xxx → 完整静态路由 URL（跳过外链）

        布局约定：图片与 md 同目录的 media/ 子目录（usp/manual/media/、
        usp/faq/media/ 各自独立），URL = /kb/{domain}/{sub_domain 全路径}/media/。
        旧逻辑取 sub_domain 首段拼共享目录（usp/media/）已随布局废弃。
        """
        _dm = r.domain or "team"
        _sd = (r.sub_domain or "").replace('\\', '/').strip('/')
        _mu = f"{self.config.media_url_prefix}/kb/{_dm}/{_sd}"
        return re.sub(
            r'!\[([^\]]*)\]\((?:\./)?media/([^)]+)\)',
            rf'![\1]({_mu}/media/\2)',
            r.content,
        )

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
        elif agent_state.phase in ("idle", "escalated") and not agent_state.problem_summary:
            # 全新话题——只记 original_query 供检索，problem_summary 留给 LLM 提炼
            agent_state.phase = "idle"
            agent_state.original_query = request.query
            _save_agent_state(memory, agent_state)
            await self._memory_manager.save_memory(memory)
        elif agent_state.phase == "resolved" and not agent_state.problem_summary:
            # 提单后/答完后新一轮：phase 转 diagnosing，但 problem_summary 保持空。
            # 不把 query 当 problem——否则裸"转工单"会伪造出新问题、绕过闭环保护。
            # 真正的新问题由本轮 LLM 在 _apply_state_update 中提炼。
            agent_state.phase = "diagnosing"
            agent_state.original_query = request.query
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

    def _build_diagnosis_prompt(self, state: AgentState, memory, reference_docs: str,
                                user_projects: Optional[List[Dict[str, str]]] = None) -> str:
        # 项目预填注入块（老快路径）：LLM 照抄 + 服务端严格校验，与工具循环
        # project_choice 同一协议。仅提单相关轮由调用方传入（诊断轮 None 不注入）。
        _proj_block = ""
        if user_projects:
            _proj_lines = "\n".join(
                f"- {p['name']}（编号: {p['code']}）" for p in user_projects)
            _proj_block = (
                f"🔴 用户名下项目列表（仅这些可选）：\n{_proj_lines}\n"
                "用户在对话中明确提到要给其中某个项目提单时，把该项目名称从上面列表"
                "**原样照抄**进输出 JSON 的 project_choice 字段（与 action 平级）；"
                "没提到或对不上就留空字符串。绝不向用户追问项目名称、不主动推荐项目、"
                "不要在正文里播报项目情况（预填结果由系统校验后在草稿生成时统一告知）。\n"
                "🔴 对话里若出现「───── 以上对话已随上一张工单提交归档」分隔线："
                "分隔线之前是**上一个已提交工单**的旧对话，那里（含助手旧回执）出现的"
                "项目名**不算本次提到，禁止照抄**；只有分隔线之后**用户**明确提到项目"
                "（或明确指代，如「还是那个项目」）才照抄。没有分隔线则以全对话为准。\n"
            )
        # Guard: context_start 可能因 turn buffer 截断（max_turns=10）而越界。
        # 场景：提单时 context_start=len(turns)=10，下一轮 add_turn 后 buffer 满截断，
        # turns 仍为 10 → turns[10:] 返回空 → LLM 看不到对话 → 输出问候语。
        # 回退时取全量 10 条——用户提单后可能说"同一个项目/还是那个问题"，
        # 需要足够上下文让 LLM 理解指代，4 条太少。
        _from = state.context_start
        if _from >= len(memory.turns):
            _from = max(0, len(memory.turns) - 10)
        # 图片描述屏蔽：收集模式下屏蔽（字段只从用户打字里提取，防「缺陷」类
        # UI 文本污染）。提单快路径不做分级——用户说「这个问题复现了」这类
        # 模糊指代时，截图本身就是唯一信息源，AI 看图理解是唯一解；
        # 即使图上文字会带入主题，工程师接单后会纠正，不做过度优化。
        conversation_text = self._format_conversation(
            memory, from_turn=_from, sanitize_images=bool(state.ticket_collecting),
            boundary_prefix=getattr(state, "ticket_boundary_prefix", ""))
        # 上一个工单上下文：只告诉 LLM"刚提过单"这个事实，不透露 project/问题主题——
        # 否则 flash 等模型会从主题里重新挖出 project/problem 写回 state_update，
        # 绕过闭环保护（_can_submit 误判"有新问题"）导致重复提单。服务端 _can_submit 才是裁判。
        _lt = state.last_submitted_ticket or {}
        # 同步 _can_submit 判定结果到 prompt：当系统判定不允许提单时，直接告诉 LLM
        # 必须回复的固定话术，避免 LLM 用闲聊绕开（如用户说"转工单"但无新问题时回复"不客气"）。
        _can, _block_msg = _can_submit(state)
        if _lt.get("ticket_id") and not _can:
            last_ticket_context = (
                f"⚠️ 刚提交过工单，不允许无新问题直接提单。\n"
                f"规则：如果用户描述了新故障/新问题（如【车不跑了】【配置怎么弄】），"
                f"请正常诊断、回答、提取 problem_summary，就像新会话一样。\n"
                f"只有当用户说【转工单/提单】但**本轮及之前没有任何新问题描述**时，"
                f"才回复「{_block_msg}」，不诊断不闲聊。"
            )
        elif _lt.get("ticket_id"):
            last_ticket_context = "刚提交过工单。除非用户描述了新的问题，否则不要重复提单。"
        else:
            last_ticket_context = "（无）"
        # 上一单引用规则块（收集轮 + 提单快路径共用）：仅解析用户对上一单的
        # 明确指代（如「车型还是上次提单的」），禁止主动带入、禁止把指代原文
        # 当字段值。生成草稿的 _build_ticket 不注入——串单通道物理堵死。
        _pv_t = state.last_submitted_ticket or {}
        _pv_fields = "；".join(
            f"{k}={v}" for k, v in (_pv_t.get("collected_info") or {}).items() if str(v).strip())
        _pv_desc = str(_pv_t.get("description") or "").strip()[:200]
        _prev_ref_block = ""
        if _pv_fields or _pv_desc:
            _prev_ref_block = (
                "\n🔴 上一张工单的字段记录（刚提交）：" + (_pv_fields or "（无）")
                + "\n上一单描述：" + (_pv_desc or "（无）")
                + "\n仅当用户本轮明确指代上一单（如「车型还是上次提单的」「版本和上一单一样」）时，"
                  "才可把上一单对应值解析为本单字段值写入 collected_info；"
                  "用户没有指代时严禁把上一单任何内容带入本单；"
                  "指代了但上一单没有该信息 → 追问具体值；"
                  "🔴 禁止把「和上次一样」「还是上次的」这类指代原文当字段值记录。\n"
            )
        # 工单填写模式（对话路径 ticket_collecting / 按钮路径 prepare not_ready）
        if state.ticket_collecting:
            fields = "、".join(state.ticket_collecting)
            collected_summary = "、".join(f"{k}={v}" for k, v in state.collected_info.items() if v) or "（暂无）"
            # 如果有自定义 required_fields，把 field_key→label 映射也告诉 LLM
            # （key 已用 _canonical_field_key 归一化，_assess_ticket_readiness 按同一 key 判定）
            field_map_hint = ""
            if state.required_fields:
                fm = "；".join(f"{_canonical_field_key(k)}→{label}" for k, label in state.required_fields.items())
                field_map_hint = f"\n字段映射（写入 collected_info 时用左边 key）：{fm}"
            ticket_collecting_context = (
                f"⚠️ 当前处于**工单填写模式**，请不要再排查故障。\n"
                f"已收集：{collected_summary}\n"
                f"缺失字段：{fields}{field_map_hint}\n"
                f"用户接下来的发言都是补充工单所需信息，请逐项确认并记录到 collected_info。\n"
                f"规则：\n"
                f"1. 缺 1-2 个字段时：一次只问一个\n"
                f"2. 用户已顺利回答 2+ 个字段后：可以把剩余缺失字段一次性列出询问\n"
                f"3. 用户说【没有】【不知道】【不清楚】等 → 该字段直接记录为'无'，立即跳过，不得再问\n"
                f"4. 🚫 禁止对同一字段追问两次。任何字段最多只问一次，用户说没有就直接过\n"
                f"5. 所有缺失字段（含'无'）都补齐后 → 立即 action='submit'，message 留空不写正文\n"
                f"6. 🚫 用户表示不转工单/不需要工单（如「我没说转工单」「不用提单」「算了」）时："
                f"**绝不能**把这种话理解成字段值然后 submit，"
                f"必须输出 ticket_cancel=true，只回复「好的，不转工单。有什么其他问题随时问我。」\n"
                f"7. 🔴 项目由用户在确认弹窗里选择，对话中**任何情况都不要问**项目名称，"
                f"缺失字段清单里也不会出现项目。"
                + _prev_ref_block +
                f"⚠️ 已收集的字段不要再问。"
            )
            # 收集模式用极简 prompt：砍掉 DIAGNOSIS_PROMPT 的 165 行人设/知识库/诊断规则，
            # LLM 只需提取字段值 + 自然确认，大幅减少无关思考，提升收集轮响应速度。
            # collected_info 模板直接列出待填字段 key——LLM 只准照模板填，
            # 不准自创 key（此前它自创 reproduce_steps/environment 导致服务端按
            # required_fields 的 key 查永远判缺，鬼打墙）。
            _rf_items = "".join(f'"{k}":""' + ("," if i < len(state.required_fields) - 1 else "")
                                for i, k in enumerate(state.required_fields.keys()))
            return (
                f"你是工单填写助手。用户正在补充工单所需信息，请逐字段记录到 collected_info。\n\n"
                f"{ticket_collecting_context}\n\n"
                f"{_proj_block}\n"
                f"## 对话\n{conversation_text}\n\n"
                f"---\n"
                f"输出 JSON（字段齐就 submit，message 留空不写正文）：\n"
                f'```json\n'
                f'{{"action":"ask|submit","intent":"troubleshoot","ticket_cancel":false,'
                f'"project_choice":"",'
                f'"state_update":{{"collected_info":{{{_rf_items}}},"ticket_ready":false}}}}\n'
                f'```\n'
                f"collected_info 的 key 必须严格使用上面模板里的英文 key，一个字都不准改；"
                f"value 填用户实际提供的内容，未提供的字段保持空字符串。\n"
                f"只有 action=ask（还在问字段）时才在 JSON 后写回复，语气像工程师；"
                f"action=submit 时 JSON 后什么都不写。"
            )
        else:
            ticket_collecting_context = "（正常诊断模式）"
            # 提单快路径：意图分类预判提单（可能误判，如把「工单流转流程是怎样的」
            # 这类流程咨询判成 ticket）→ 用精简 prompt。LLM 必须自己复核用户是否
            # 真有提单诉求：没有就按普通咨询回答（answer），不能硬着头皮提单。
            if getattr(state, "ticket_fast_lane", False):
                return (
                    "请先判断用户本轮是否真的提出了提单诉求（转工单/提单/派单/找工程师处理）。\n\n"
                    "## 对话\n"
                    f"{conversation_text}\n\n"
                    "## 任务\n"
                    "1. 🔴 用户只是咨询问题（如问「工单流转流程是怎样的」），或是在报告/吐槽"
                    "「提单功能本身的问题」（如 提单找不到项目、找不到处理人、提单弹窗报错）"
                    "——这些是对服务号平台的答疑诉求，话里的「提单」是话题不是动作请求"
                    " → action=answer 直接回答/排查问题，ticket_intent=false，不要收集字段、不要提单\n"
                    "2. 用户确有提单诉求 → 判定 ticket_type（problem=报障/bug=缺陷/feature=需求/support=咨询/other），"
                    "仔细读完整对话找出信息缺口，🔴 字段分两层、总数 2-4 个，禁止只列 1 个："
                    "· 核心字段 2 个 = 不问清楚就无法定位/复现问题的信息"
                    "（如报错内容、车辆编号/设备型号、调度版本、故障码）——"
                    "对话里已说清的不算缺口，但不能用补充字段凑数；"
                    "· 补充字段 0-2 个 = 锦上添花的信息（如发生时间、出现频率、现场联系人），"
                    "只在值得追问时才加，宁缺毋滥"
                    "（用户说过的、能推出的不列；不列项目名）\n"
                    "2.1 🔴 一项信息一个 key：时间、车辆编号、任务等各自独立成字段"
                    "（occurrence_time / robot_id / task_info），禁止合并成一个字段"
                    "——打包后用户只答一项就会被误判信息齐、提前弹窗，其余信息永远收不到\n"
                    "2.5 🔴 有提单诉求时，必须把用户要提单的问题一句话总结写进 state_update.problem_summary"
                    "（如「工单401确认完成页面，解决方式自动总结出错」）。这是服务端闭环校验的依据——"
                    "不写的话，刚提过单的会话会被误判为「无新问题重复提单」而拦截。"
                    "即使其他信息都齐、直接 submit，也必须写 problem_summary\n"
                    "3. 有缺口 → action=ask，一次只问一个缺失字段，ticket_intent=true；"
                    "没有缺口 → action=submit，message 留空，ticket_intent=true\n"
                    "4. 🔴 用户指名处理人（「提给XX」「交给XX」）分两种场景：\n"
                    "   a. 对话里**已有工单草稿**（出现过「已生成工单草稿」），用户是给旧草稿补充指派/备注 → "
                    "写入 collected_info，action=answer 简短确认「好的，已记录」，ticket_intent=false，不重新提单\n"
                    "   b. 用户这句话**本身是新的服务请求**（如「能让贾爽帮我配置一下自动门吗」= 让工程师去干活）→ "
                    "这就是提单诉求：写入 requested_assignee，按规则 2/3 走收集缺口 → submit 弹窗，ticket_intent=true\n"
                    "   判断要点：请求内容是新任务还是旧任务的补充？新任务必须走提单，不能只 answer 记录\n"
                    "5. 🔴 任何情况下都不问项目名称（项目在弹窗里选）\n"
                    + _prev_ref_block +
                    f"\n{_proj_block}"
                    "## 输出\n"
                    '```json\n'
                    '{"action":"answer|ask|submit","intent":"howto|troubleshoot","ticket_intent":true|false,"ticket_cancel":false,'
                    '"project_choice":"",'
                    '"state_update":{"ticket_type":"problem|bug|feature|support|other",'
                    '"problem_summary":"一句话问题概述",'
                    '"required_fields":{"field_key":"中文标签"},'
                    '"collected_info":{},"ticket_ready":false}}\n'
                    '```\n'
                    "action=ask 时 JSON 后写一句自然的追问；action=answer 时 JSON 后写正常回复；"
                    "action=submit 时 JSON 后什么都不写。"
                )
            # 诊断轮次上限软提示：多轮仍未解决 → 引导 LLM 给最佳建议或建议转工单，避免鬼打墙
            if state.diagnosis_rounds >= _MAX_DIAGNOSIS_ROUNDS:
                ticket_collecting_context = (
                    f"⚠️ 已排查 {state.diagnosis_rounds} 轮仍未解决。请给出当前最可能的结论/建议；"
                    f"若确实无法定位，主动建议用户转工单（action=answer 自然引导），不要继续无限追问。"
                )
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
                ticket_collecting_context=self._escape_format(ticket_collecting_context),
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
        # 工单类型：LLM 根据对话内容分类（problem/bug/feature/support/other），
        # 只接受合法值，防止 LLM 写错字面量导致 _assess_ticket_readiness 匹配不到清单。
        # 一旦 ticket_collecting 激活（开始收集字段），ticket_type 即锁定——
        # 防止 LLM 在 bug/problem 之间反复横跳，导致安全网前后要求不一致的信息。
        if "ticket_type" in state_update:
            tt = (state_update["ticket_type"] or "").strip()
            if tt in ("problem", "bug", "feature", "support", "other"):
                if state.ticket_collecting and state.ticket_type and state.ticket_type != tt:
                    logger.info(f"[state] ticket_type 已锁定为 {state.ticket_type}，"
                                f"拒绝 LLM 改为 {tt}")
                else:
                    state.ticket_type = tt
                    logger.info(f"[state] LLM 设 ticket_type={tt}")
        # 动态必填字段：LLM 判断内置清单不适用时，自行声明需要收集哪些字段。
        # 格式：{field_key: "中文标签", ...}，如 {"error_message":"错误信息","occurrence_time":"发生时间"}
        if "required_fields" in state_update:
            rf = state_update["required_fields"]
            if isinstance(rf, dict) and rf:
                _new_rf = {_canonical_field_key(k): str(v) for k, v in rf.items() if k and v}
                # 字段清单只允许在首次提单意图确认时生成一次。
                # 只要已有清单（即使 collected_info 仍为空），后续轮次都不得
                # 扩大、缩小或改写，否则用户每补充一次就会被重新追问。
                if state.required_fields is not None:
                    if state.required_fields != _new_rf:
                        logger.info(f"[state] required_fields 已锁定 {state.required_fields}，"
                                    f"拒绝 LLM 改为 {rf}")
                elif len(_new_rf) < 2:
                    # 首次声明少于 2 个字段不采信：快路径主 LLM（thinking 关闭）
                    # 经常只列 1 个缺口，用户一答就齐、立即弹草稿（0824 生产
                    # 事故：只问报错内容，答「没看清」直接出单）。保持 None，
                    # 让 _decide_ticket_fields 用专用 prompt（2 核心 + 0-2 补充，
                    # temperature=0）生成完整清单。
                    logger.info(f"[state] LLM 只声明 {len(_new_rf)} 个字段，不采信，"
                                f"转 _decide_ticket_fields 重新生成: {rf}")
                else:
                    state.required_fields = _new_rf
                    logger.info(f"[state] LLM 设 required_fields={state.required_fields}")
        if "problem_summary" in state_update:
            new_ps = (state_update["problem_summary"] or "").strip()
            # 闭环绕过防护：工单提交后 phase=resolved，对话历史中仍有故障描述，
            # LLM 极易从中重新提取旧问题并设 problem_summary → _can_submit 放行 → 重复提单。
            # 因此 phase=resolved 时完全禁止 LLM 设置 problem_summary——
            # 新故障只能由用户在对话中显式描述新现象来触发，不能由 LLM 单方面"恢复"旧话题。
            if state.phase == "resolved":
                logger.info(f"[state] 拦截 resolved 下 LLM 设置 problem_summary: "
                            f"new={new_ps[:40]}")
                # 不更新 problem_summary，保持空
            else:
                state.problem_summary = new_ps
        if "ruled_out" in state_update:
            state.ruled_out = [str(x) if not isinstance(x, str) else x for x in state_update["ruled_out"]]
        if "hypotheses" in state_update:
            state.hypotheses = [str(x) if not isinstance(x, str) else x for x in state_update["hypotheses"]]
        if "ticket_ready" in state_update:
            # LLM 可能输出 bool 或字符串 "true"/"false"
            tr = state_update["ticket_ready"]
            if isinstance(tr, bool):
                state.ticket_ready = tr
            elif isinstance(tr, str):
                state.ticket_ready = tr.lower() in ("true", "1", "yes")
        if "collected_info" in state_update:
            # 合并新字段，空值/无 视为清除。project 只在确认弹窗由用户选择，
            # 不在对话链路收集（下方对 project 一律丢弃）。
            # 收集轮 LLM 也常把 required_fields 的中文标签当 key 写（如 "期望调整效果"），
            # 服务端只认英文 key——用反向映射归位，否则永远判缺（鬼打墙）。
            _label_to_key = {str(label): key for key, label in (state.required_fields or {}).items()}
            for k, v in list(state_update["collected_info"].items()):
                if v is None:
                    state.collected_info.pop(k, None)
                    continue
                if isinstance(v, (dict, list)):
                    v = json.dumps(v, ensure_ascii=False)
                v = str(v).strip()
                if not v:
                    continue
                # 用户明确表示未知/没有时，按“无”记录为已回答，避免重复追问。
                if v in ("无", "没有", "不知道", "不清楚"):
                    v = "无"

                # 写入时归一，读取判定（_assess_ticket_readiness）也归一，
                # 两侧一致才不会出现「LLM 写了 reproduce_steps、服务端找 repro_steps」的鬼打墙。
                _key = _canonical_field_key(k)
                # 中文标签 → 英文 key 归位（required_fields 的 label 反查）
                if _key in _label_to_key:
                    _key = _label_to_key[_key]
                # project 不属于对话收集字段——项目只在确认弹窗由用户选择。
                # LLM 即使误写 project（含 project_name 等变体）也一律丢弃，
                # 不进 collected_info，杜绝对话链路上出现项目预填值。
                if _key == "project":
                    logger.info(f"[state] 丢弃对话中误写的 project 值: {v!r}")
                    continue
                state.collected_info[_key] = v
        # ---- 服务端硬校验：按工单类型的保底必填清单复核，缺项强制打回 ----
        #  不信 LLM 的 ticket_ready 自评，也不看 problem_summary（LLM 可编造）——
        #  只认 collected_info 里的结构化字段。
        if state.ticket_ready:
            _ready, _missing = _assess_ticket_readiness(state)
            if not _ready:
                state.ticket_ready = False
                logger.info(f"[state] LLM 设 ticket_ready=true 但缺必填项，强制打回: "
                            f"type={state.ticket_type or '(未判定,按problem)'}, missing={_missing}")
    def _apply_action_phase(self, state: AgentState, action: str) -> None:
        # answer 不再设 resolved：resolved 只由 _reset_state_after_submit 在提单成功后设置，
        # 避免"刚答完诊断"和"刚提完工单"共用同一 phase 导致 _apply_state_update guard 误拦。
        if action == "submit":
            state.phase = "escalated"

    async def _get_user_projects(self, username: str) -> List[Dict[str, str]]:
        """查 username 名下关联的项目列表（helpdesk_724 跨库，与后端
        GET /api/admin/projects/me 同源：user_project_roles 排除 'global'）。

        仅用于提单工具循环的「项目预填」：拉到列表注入 prompt，LLM 从中照抄；
        任何失败返回 []，预填整体退化为现状（弹窗搜索选择），不影响主流程。
        正常结果缓存 5 分钟（提单低频操作）；失败负缓存 60 秒——DB 故障时
        不让每个提单轮都重付连接超时（实测不可达时单次 ~2s）。
        缓存结构 {username: (expires_at, projects)}。
        """
        if not (username or "").strip():
            return []
        now = time.time()
        cached = _USER_PROJECTS_CACHE.get(username)
        if cached and now < cached[0]:
            return cached[1]
        from ai.core.database import SessionLocal
        from sqlalchemy import text
        loop = asyncio.get_running_loop()

        def _query():
            session = SessionLocal()
            try:
                rows = session.execute(text(
                    "SELECT DISTINCT p.name, p.code "
                    "FROM helpdesk_724.user_project_roles upr "
                    "JOIN helpdesk_724.users u ON u.id = upr.user_id "
                    "JOIN helpdesk_724.project p ON p.id = upr.project_id "
                    "WHERE u.username = :u AND upr.project_id IS NOT NULL "
                    "AND upr.project_id != 'global' ORDER BY p.name"
                ), {"u": username.strip()}).fetchall()
                return [{"name": r[0], "code": str(r[1] or "")} for r in rows if r[0]]
            finally:
                session.close()

        try:
            # 查询本身毫秒级；超时截断防 DB 不可达时默认连接重试拖到 ~2s+
            projects = await asyncio.wait_for(
                loop.run_in_executor(None, _query), timeout=1.0)
        except Exception as e:
            logger.warning(f"[user_projects] 查询失败(降级为不预填): username={username}, err={e}")
            _USER_PROJECTS_CACHE[username] = (now + 60, [])
            return []
        _USER_PROJECTS_CACHE[username] = (now + 300, projects)
        logger.info(f"[user_projects] username={username}, projects={len(projects)}")
        return projects

    @staticmethod
    def _match_project_choice(choice: str, user_projects: List[Dict[str, str]]) -> Optional[Dict[str, str]]:
        """LLM 的 project_choice → 用户项目列表内的严格匹配。

        只接受与列表 name / code 精确相等（strip 后）的值——LLM 是从注入的
        列表里照抄，抄不齐就是幻觉信号，宁可置空走弹窗，不做模糊容错。
        例外：照抄时把展示格式整行带上（如「名称（编号: 69）」）——只要列表项
        的完整 name 作为连续子串出现就剥离取回；仍是精确值匹配，不引入近似容错。
        """
        c = (choice or "").strip()
        if not c or not user_projects:
            return None
        for p in user_projects:
            if c == p["name"].strip() or (p["code"] and c == p["code"].strip()):
                return p
        _hit = next((p for p in user_projects
                     if p["name"].strip() and len(p["name"].strip()) <= len(c)
                     and p["name"].strip() in c), None)
        return _hit

    async def _resolve_project(self, raw_name: str) -> Optional[ProjectMatch]:
        """将用户输入的项目名匹配到 helpdesk_724.project 标准名。

        单候选直接返回，多候选调 LLM 裁决，无匹配返回 None。
        返回 ProjectMatch（含 .name 和 .code），方便上游同时拿到项目名和 project_id。
        """
        if not raw_name or not raw_name.strip():
            return None
        try:
            matcher = get_project_matcher()
            if not await matcher.ensure_loaded():
                logger.warning("[pipeline] project DB unavailable")
                return None
            user = raw_name.strip()
            candidates = await matcher.get_candidates_async(user, min_score=0.55)
            if not candidates:
                # 诊断：看下所有项目的得分情况（阈值降到 0.3 拉候选）
                _all = matcher.get_candidates(user, min_score=0.3, top_n=5)
                _top = [(c.name, f"{c.score:.2f}") for c in _all]
                logger.info(
                    f"[pipeline] 无匹配项目(≥0.7): '{user}' "
                    f"(项目库共 {len(matcher._projects)} 条, top5候选={_top})"
                )
                return None
            if len(candidates) == 1:
                # 展示全部接近候选，方便理解为什么选了这个而非其他
                _nearby = matcher.get_candidates(user, min_score=0.3, top_n=5)
                _all_scored = [(c.name, c.code, f"{c.score:.3f}") for c in _nearby]
                logger.info(
                    f"[pipeline] 项目直配: '{user}' → '{candidates[0].name}' "
                    f"(≥0.7候选={len(candidates)}, ≥0.3候选={_all_scored})"
                )
                # code 为空 → 无效条目（如 DB 里的占位行），不算有效匹配
                if not (candidates[0].code or "").strip():
                    logger.info(f"[pipeline] 匹配到的 '{candidates[0].name}' code 为空，视为无效匹配")
                    return None
                return candidates[0]
            # 多个候选 → LLM 裁决
            await self._ensure_clients()
            lines = [f"{i+1}. {c.name}（编号: {c.code}）" for i, c in enumerate(candidates)]
            prompt = (
                f"用户输入的项目名：{user}\n\n"
                f"候选项目列表：\n" + "\n".join(lines) + "\n\n"
                f"请判断用户最可能指的是哪个项目。只输出数字序号（如 1），"
                f"如果都不匹配则输出 0。只输出一个数字，不要其他内容。"
            )
            raw = await self._llm_client.complete(prompt=prompt, max_tokens=5, temperature=0)
            choice = re.search(r'\d+', raw)
            idx = int(choice.group()) if choice else 0
            if 1 <= idx <= len(candidates):
                _scored = [(c.name, c.code, f"{c.score:.3f}") for c in candidates]
                logger.info(
                    f"[pipeline] LLM 裁决项目: '{user}' → #{idx} '{candidates[idx-1].name}' "
                    f"(≥0.7候选={_scored})"
                )
                return candidates[idx - 1]
            logger.info(f"[pipeline] LLM 无法裁决项目 '{user}'")
            return None
        except Exception as e:
            logger.warning(f"[pipeline] project matching failed: {e}")
            return None

    async def _finalize_diagnosis(self, session_id: str, state: AgentState,
                                    thinking: str, action: str, message: str,
                                    streaming: bool = False) -> dict:
        # 手动添加 turn + 更新 agent_state，一次 save_memory 完成
        memory = await self._memory_manager.get_memory(session_id)
        memory.turns.append({"role": "assistant", "content": message})
        if len(memory.turns) > self._memory_manager.max_turns:
            memory.turns = memory.turns[-self._memory_manager.max_turns:]
        # 保护并发：prepare_ticket 可能在流式输出期间设了 ticket_collecting，
        # 直接 _save_agent_state 会覆盖。先从 memory 取回已保存字段再合并。
        _existing = memory.metadata.get("agent_state", {})
        if _existing.get("ticket_collecting"):
            state.ticket_collecting = _existing["ticket_collecting"]
        if _existing.get("required_fields"):
            state.required_fields = _existing["required_fields"]
        if _existing.get("collect_rounds"):
            # 取 max：阻塞路径的 +1（ticket_collecting 每轮递增）必须先落盘才进 _finalize_diagnosis，
            # 否则这里会被内存里的旧值覆盖，collect_rounds 永远卡住、强制提单安全阀不触发。
            state.collect_rounds = max(state.collect_rounds, _existing["collect_rounds"])
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

        # 第2轮及以后对话结束生成标题（真正 fire-and-forget：后台异步生成，不阻塞
        # 结果返回。注释早有此意，此前实现误写成 await，实测每轮阻塞 ~2s）。
        # 快照 turns + 独立 metadata，后台任务不碰主 memory 对象，避免并发读写干扰。
        title = ""
        # 标题生成节奏：首轮(round 1)生成一次,之后每两轮(3/5/7...)再生成一次。
        # 覆盖式更新:标题跟随对话最新内容演进,异步后台执行不阻塞回复流。
        if state.diagnosis_rounds >= 1 and state.diagnosis_rounds % 2 == 1:
            from types import SimpleNamespace
            _snap = SimpleNamespace(
                session_id=memory.session_id,
                turns=[dict(t) for t in memory.turns],
                metadata={},
            )

            async def _title_bg():
                try:
                    # 标题是轻量任务,走 deepseek flash(意图同款客户端):
                    # 便宜、快、不受中转站抽风影响(此前用主 LLM 时,gpt-5 走
                    # Responses API 的解析 bug 直接把标题干崩过)
                    _title_llm = await get_intent_client()
                    _t = await _generate_title(_title_llm, _snap)
                    if not _t:
                        return ""
                    logger.info(f"[title] 异步生成结果: {_t!r}")
                    # 写回主 memory（随下一次 save_memory 落盘；若本轮已保存，
                    # 下轮会重新生成一次——代价是偶发一次 2s 调用，可接受）
                    memory.metadata["title"] = _t
                    from ai.core.conversation_store import rename_conversation
                    rename_conversation(memory.session_id, _t)
                    logger.info(f"[title] DB 已同步: session={memory.session_id}, title={_t}")
                    return _t
                except Exception as e:
                    logger.warning(f"[title] 异步标题生成失败: {e}")
                    return ""

            logger.info(f"[title] 后台生成: round={state.diagnosis_rounds}, turns={len(memory.turns)}")
            _task = asyncio.create_task(_title_bg())
            # 注册给 SSE 生产者:流结束后等它落地,补发 event: title 给前端
            # (前端 ChatPanel 靠 title 事件刷新会话标题;异步化后 result.title
            # 恒为空,不补发前端就永远显示「新建会话」)
            if not hasattr(self, "_pending_title_tasks"):
                self._pending_title_tasks = {}
            self._pending_title_tasks[memory.session_id] = _task

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
    # 提单工具循环分支（阶段1 新架构，AI_TICKET_TOOL_LOOP=1 时启用）
    # ================================================================
    async def _ticket_tool_loop_branch(self, request: DiagnosisRequest, state: AgentState, memory):
        """提单轮走 submit_ticket 工具循环（替代旧快路径 prompt + 状态机）。

        流程：
          意图判 ticket → 构造 messages（system + 对话历史 + 本轮用户消息）
          → run_tool_loop（LLM 调工具 ↔ 执行器回结果，最多 5 轮）
          → 工具 terminate（草稿就绪）→ 发 review 事件弹窗（复用现有前端链路）
          → 未 terminate（还在收集/LLM 正常回答）→ 流式输出最终文本

        工具循环期间不走旧状态机：不设 ticket_collecting/required_fields，
        不 backfill、不 decide。LLM 靠工具返回值自己组织追问。
        """
        from ai.agents.AiDiagnosisPlatform.ticket_tool import (
            TOOL_SCHEMA, TOOL_SCHEMA_SUPPLEMENT, execute_submit_ticket,
        )
        from ai.agents.AiDiagnosisPlatform.tool_loop import run_tool_loop

        yield {"event": "status", "data": {"stage": "analyzing", "round": state.diagnosis_rounds}}
        t0 = time.perf_counter()

        # 草稿是否已存在（本轮是补充/修改轮，而非首次提单）——决定用哪套工具
        # schema、要不要把跨轮已收集字段合并进本轮判缺。
        _supplement = bool(memory.metadata.get("ticket_draft"))

        # 项目预填数据源：该用户名下项目列表。拉不到/为空 → 不注入 prompt，
        # 后续行为与旧版完全一致（弹窗搜索选择）。
        _user_projects = await self._get_user_projects(request.created_by)

        # 构造 messages：system + 本轮用户消息 + 结构化提单状态。
        # ⚠️ 不能只依赖对话文本：turns buffer 截断（max_turns=10）后
        # turns[context_start:] 可能为空/只剩最近1-2轮，第二次提单的问题描述
        # 会被截没（日志实锤：LLM 说「历史对话是空的」重新问发生了什么）。
        # state 里的 problem_summary/collected_info 是跨轮持久的结构化事实，
        # 显式注入，保证续接轮 LLM 永远知道当前提单上下文。
        _conv = self._format_conversation(
            memory, from_turn=max(0, min(state.context_start, len(memory.turns) - 2)),
            max_turns=6)
        _state_block = []
        if state.problem_summary:
            _state_block.append(f"当前提单问题：{state.problem_summary}")
        if state.ticket_type:
            _state_block.append(f"工单类型：{state.ticket_type}")
        if state.collected_info:
            _ci = {k: v for k, v in state.collected_info.items() if v}
            if _ci:
                _state_block.append(f"已收集信息：{json.dumps(_ci, ensure_ascii=False)}")
        _state_text = "\n".join(_state_block) if _state_block else "（无）"
        system_prompt = (
            "你是「摇人吧」微信服务号的 AI 诊断助手 U老师，面向 AGV/AMR 行业。\n"
            "用户表达提单诉求（转工单/提单/派单/找工程师处理）时，调用 submit_ticket 工具。\n"
            "工具会返回还缺哪些信息：缺信息时用自然语气追问用户（一次只问一个，"
            "追问要短，一句话说清还缺什么即可，不要重复已问过的内容），"
            "拿到后再调用工具。工具返回草稿后流程即结束，收尾话术由系统统一发送。\n"
            "说话时机（重要）：每次调用 submit_ticket 的同一轮，先用一句简短过渡语"
            "向用户交代你正在做什么，再发起调用（过渡语在前、工具调用在后）。"
            "过渡语和工具调用必须在**同一次回复**里完成：说完过渡语必须立刻发起"
            "工具调用，绝不能只说过渡语就结束回合——用户会盯着冒号一直等下文。"
            "过渡语以冒号收尾，让用户知道后面还有内容，不要用句号把话说死。示例：\n"
            "- 首次提单：「好的，我帮您转工单，我看一下还需要补充哪些信息：」\n"
            "- 用户补充了一项信息后再调用：「收到，我核对一下还缺什么：」\n"
            "- 给已生成的草稿补信息：「好的，我把这条加进草稿：」\n"
            "过渡语红线：禁止出现「已提交」「工单已生成」「工单已创建」等完成时表述"
            "（草稿经用户确认前都不算提交）；不要播报项目预填情况"
            "（预填由系统校验后统一告知用户）。\n"
            "收尾铁律：\n"
            "- 用户明确说某个信息没有/不知道/不方便提供（如「没有日志」「不知道版本」），"
            "或说「直接提单」「就这些信息」「尽快提单」时，把该字段按「没有」写入"
            "collected_fields 后调用工具，绝不要反复追问同一项。\n"
            "- 不要每轮新增一项可有可无的信息：已有问题概述、设备/型号、现象、时间、"
            "频率、触发场景等足以让工程师初判时，直接调用工具生成草稿。\n"
            "- 追问次数最多 2-3 次：第 3 次调用工具时必须把缺项按「没有」填上并完成提单。\n"
            "注意：\n"
            "- 不要问项目名称（项目由用户在确认弹窗里选择）\n"
            "- 用户只是咨询问题（没提提单）时不要调用工具，正常回答即可\n"
            "- 用户明确表示不想提单/取消（如「算了」「不用了」「不想提单了」）时，"
            "不要调用工具，简短回复「好的，不转工单。有什么其他问题随时问我。」\n"
            "- 用户是在给已生成的草稿补充信息（如「提给XX」「补充一下XX」「再加上XX」）时，"
            "调用 submit_ticket 并带上补充的内容；不要当成新问题重新提单。"
            "补充信息这一轮就调用工具（不要先回一句「好的我记录」而不调，"
            "那样会被当作中途放弃）。\n"
            f"- 当前提单上下文（如果非空，说明用户已在提单流程中，不要当成新会话重新问问题）：\n{_state_text}\n"
        )
        if _user_projects:
            _proj_lines = "\n".join(
                f"- {p['name']}（编号: {p['code']}）" for p in _user_projects)
            system_prompt += (
                f"\n用户名下项目列表（仅这些可选）：\n{_proj_lines}\n"
                "项目预填规则：用户在对话中明确提到要给其中某个项目提单时，"
                "把该项目名称从上面列表**原样照抄**进 submit_ticket 的 project_choice "
                "参数；没提到或对不上就省略该参数。绝不向用户追问项目名称、不主动推荐项目。\n"
                "不要在对话中播报项目预填情况——预填结果由系统校验后在草稿生成时统一"
                "告知用户，弹窗中也会展示。\n"
            )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"以下是最近对话（供参考）：\n{_conv}\n\n本轮用户消息：{request.query}"},
        ]
        logger.info(f"[tool_loop] 提单工具循环 prompt: system={len(system_prompt)} "
                    f"user={len(messages[1]['content'])} "
                    f"合计={len(system_prompt) + len(messages[1]['content'])} chars: "
                    f"session={request.session_id}")

        # 执行器包装：draft 生成走 _build_ticket（复用 LLM 总结 title/description 的链路，
        # 不再是最小草稿——否则 title==description，弹窗体验差）。
        # 每轮都把跨轮累计的 state.collected_info 合并进本轮 collected_fields 再判缺——
        # 模型每轮通常只传新增字段，不合并会把前几轮已收齐的字段重复判成缺失，
        # 导致重复追问甚至无限扩展追问项（deepseek 实测：报错日志/版本号 连问 7 轮）。
        # 合并后判缺逻辑对首轮/中间收集轮/补充轮统一，不用区分对待。
        async def _executor(params):
            merged = dict(state.collected_info)
            merged.update({k: v for k, v in (params.get("collected_fields") or {}).items() if v})
            params = {**params, "collected_fields": merged}
            return execute_submit_ticket(params, make_draft=None)

        final_text = ""
        tool_results = []
        final_streamed = False  # 循环内已把 final_text 逐 token 流式发出 → 结束时不得整段重发
        # 循环内实际流出的正文累计：terminate 轮（草稿就绪）done 事件 final_text
        # 恒为空，但过渡语可能已流式说出——收尾据此判断「用户已经看到什么」，
        # 已流出的只补尾巴，绝不整段重发（重发 = 气泡里同一段话出现两遍）。
        _streamed_text = ""
        from ai.agents.AiDiagnosisPlatform.tool_loop import run_tool_loop_stream
        _schema = TOOL_SCHEMA_SUPPLEMENT if _supplement else TOOL_SCHEMA
        _loop_failed = False

        async def _loop_events():
            """跑一遍工具循环：token 随到随发；done 结果经 nonlocal 写回外层。"""
            nonlocal final_text, tool_results, final_streamed, _streamed_text
            async with asyncio.timeout(60.0):
                async for ev in run_tool_loop_stream(
                        self._llm_client, messages, [_schema],
                        {"submit_ticket": _executor},
                        # 提单收集是轻量结构化任务，关闭思考可把首 token 等待
                        # 从 5-15s 砍到 1-2s（DeepSeek 与中转站 Claude 均生效）。
                        thinking=False):
                    if ev["event"] == "token":
                        _streamed_text += ev.get("data") or ""
                        yield ev
                    elif ev["event"] == "done":
                        final_text = ev["final_text"]
                        tool_results = ev["tool_results"]
                        # final_text 非空才表示正文已在循环内流式发出；
                        # terminate 路径 final_text=""（收尾话术走兜底文案，未流式）
                        final_streamed = bool(final_text)

        try:
            async for ev in _loop_events():
                yield ev
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning(f"[tool_loop] 工具循环失败: session={request.session_id}, err={e}")
            yield {"event": "status", "data": {"stage": "submit_failed", "error": str(e)[:100]}}
            final_text = "提单过程中出现异常，请稍后重试或联系管理员。"
            tool_results = []
            _loop_failed = True

        # 空转纠偏（生产实录 14:46 实锤）：触发轮 LLM 只说了过渡语
        # 「好的，我帮您转工单，我看一下还需要补充哪些信息：」就结束回合，
        # 0 次工具调用——气泡停在冒号上死寂，用户在对话里无法继续提单。
        # 机制层兜底：把空转回合和纠偏指令追加进 messages 重跑一遍循环，
        # 让模型要么调工具、要么直接追问。判断仍在 LLM：代码只发现协议
        # 违约（本轮没调工具）并要求重做，不猜用户意图；放弃轮由 LLM 自己
        # 的固定话术「不转工单」识别（沿用既有协议，与下方 _is_abandon 同源）。
        if (not tool_results and not _loop_failed
                and "不转工单" not in (final_text or "")):
            logger.warning(f"[tool_loop] 本轮零工具调用（疑似空转），注入纠偏重跑: "
                           f"final_text={(final_text or '')[:50]!r}, session={request.session_id}")
            messages.append({"role": "assistant", "content": final_text or ""})
            messages.append({
                "role": "system",
                "content": "你上一条回复只说了话，没有调用 submit_ticket，用户正在等下文。"
                           "用户已经看到你上一条回复——不要再输出过渡语、不要复述任何"
                           "已说过的话（用户会看到两遍）。现在必须实际行动，二选一：\n"
                           "1. 立刻调用 submit_ticket，正文留空（已掌握的信息放进 "
                           "collected_fields，还缺的留给 required_fields）；\n"
                           "2. 直接向用户追问一个还缺的关键信息（完整问句，以问号结尾，"
                           "前面不要加任何过渡语）。\n"
                           "若用户其实没有提单诉求，就正常回答用户的问题。"
                           "绝不能再次只说一句话就结束回合。",
            })
            try:
                async for ev in _loop_events():
                    yield ev
            except (asyncio.TimeoutError, Exception) as e:
                logger.warning(f"[tool_loop] 纠偏轮也失败: {e}, session={request.session_id}")

        t_loop = round((time.perf_counter() - t0) * 1000)
        logger.info(f"[tool_loop] 循环完成: session={request.session_id}, "
                    f"elapsed={t_loop}ms, tool_calls={len(tool_results)}, "
                    f"final_text_len={len(final_text)}")
        draft = None
        _prefill_project: Optional[Dict[str, str]] = None
        _draft_ready = any(
            r.get("details", {}).get("status") == "draft_ready" for r in tool_results)
        if _draft_ready:
            # 把工具参数里的信息写入 state，供 _build_ticket 和按钮路径复用
            for r in tool_results:
                if r.get("name") != "submit_ticket" or not r.get("arguments"):
                    continue
                args = r["arguments"]
                state.ticket_type = args.get("ticket_type") or state.ticket_type
                state.problem_summary = args.get("problem_summary") or state.problem_summary
                for k, v in (args.get("collected_fields") or {}).items():
                    if v and not state.collected_info.get(k):
                        state.collected_info[k] = v
                if args.get("requested_assignee"):
                    state.collected_info["requested_assignee"] = args["requested_assignee"]
                # 项目预填：LLM 照抄的列表项做严格校验（防幻觉），命中才进 draft。
                # 不写 state/collected_info、不参与判缺——单向管道：工具参数 →
                # draft → 弹窗（用户可改），confirm_submit 用 overrides 覆盖优先。
                _prefill_project = self._match_project_choice(
                    args.get("project_choice", ""), _user_projects)
                if args.get("project_choice") and not _prefill_project:
                    logger.info(
                        f"[tool_loop] project_choice 未命中用户项目列表，忽略: "
                        f"{args.get('project_choice')!r}, session={request.session_id}")
                # 首次生成草稿（非补充）时才信任本轮声明——那一轮是真实校验过的。
                # 补充轮不覆盖：覆盖后会让 confirm_submit 的 _assess_ticket_readiness
                # 重新校验出偏差。
                if not _supplement:
                    rf = args.get("required_fields") or {}
                    if rf and isinstance(rf, dict):
                        state.required_fields = dict(rf)
                break
            try:
                draft = await self._build_ticket(request.session_id, state, memory,
                                                 prefill_project=_prefill_project)
            except Exception as e:
                logger.warning(f"[tool_loop] _build_ticket 失败: {e}")
                draft = None

        if draft is not None:
            draft["ticket_seq"] = state.ticket_seq + 1
            check = _check_required_fields(draft)
            draft["missing_fields"] = check["missing"]
            memory.metadata["ticket_draft"] = draft
            state.ticket_collecting = []
            state.tool_loop_active = False  # 草稿就绪，退出工具循环收集
            _save_agent_state(memory, state)
            await self._memory_manager.save_memory(memory)
            logger.info(f"[tool_loop] 草稿就绪，发 review 弹窗: session={request.session_id}")
            yield {"event": "status", "data": {
                "stage": "review",
                "draft": draft,
                "missing_fields": check["missing"],
                "force_submit": False,
            }}
            # 对话气泡回填话术（预填了项目时明说，让用户有感知、知道可改）。
            # terminate 轮 final_text 恒为空，但过渡语可能已流式说出
            # （如「收到，我核对一下还缺什么：」）——此时收尾只补尾巴，
            # 换行接在同一气泡里，不整段重发。
            if draft.get("project"):
                _draft_msg = (f"已生成工单草稿，项目已预填为「{draft['project']}」"
                              "（可在弹窗中修改），请核对信息后确认提交。")
            else:
                _draft_msg = "已生成工单草稿，请在弹窗中选择项目并核对信息后确认提交。"
            _spoken = (_streamed_text or final_text or "").strip()
            if _spoken:
                yield {"event": "token", "data": "\n\n信息齐了，" + _draft_msg}
                _msg = _spoken + "\n\n信息齐了，" + _draft_msg
            else:
                _msg = _draft_msg
                yield {"event": "token", "data": _msg}
            result_data = await self._finalize_diagnosis(
                request.session_id, state,
                thinking="", action="answer", message=_msg, streaming=True)
            if result_data.get("title"):
                yield {"event": "title", "data": {"title": result_data["title"]}}
            yield {"event": "result", "data": result_data}
            return

        # 未生成草稿，分两种情况：
        # ① LLM 调了工具但缺字段 → 还在收集，标记粘性续接
        # ② LLM 没调工具（tool_calls=0）→ 需区分「补充回话」与「显式放弃」：
        #    - 补充回话（如「好的，我来记录」「还差XX」）：LLM 先回一句不调工具，
        #      下一轮才调 submit_ticket。若一律判取消 → 清草稿 + 写 cancelled →
        #      用户补充信息被拦（实测把上午的补充逻辑弄坏）。此时保留状态。
        #    - 显式放弃：system prompt 让 LLM 在用户说「算了/不转工单」时输出固定
        #      话术「好的，不转工单…」且不调工具。识别 LLM 自己的结论（非服务端
        #      关键词抢判断），命中才销毁 + 写 cancelled 标记。
        if not tool_results:
            _abandon_text = final_text or ""
            _is_abandon = "不转工单" in _abandon_text
            if _is_abandon:
                logger.info(f"[tool_loop] LLM 判用户显式放弃提单，清空状态: session={request.session_id}")
                # 取消标记写入 last_submitted_ticket：让 _can_submit 拦截「放弃后立刻
                # 再点按钮」。仅当无已有记录时写入；清空 problem_summary 前记录 topic。
                if not state.last_submitted_ticket:
                    state.last_submitted_ticket = {
                        "ticket_id": "cancelled",
                        "title": "取消的草稿",
                        "topic": state.problem_summary or "",
                        "submitted_at": int(time.time()),
                    }
                state.tool_loop_active = False
                state.collected_info = {}
                state.problem_summary = ""
                state.ticket_type = ""
                state.ticket_collecting = []
                state.required_fields = None
                state.collect_rounds = 0
                memory.metadata.pop("ticket_draft", None)
            else:
                logger.info(f"[tool_loop] 本轮无工具调用（补充/回话），保留收集状态: session={request.session_id}")
                # 空转/回话轮仍在提单流程中：置粘性续接，下一轮直接回工具循环。
                # 生产实录：空转轮不置位，用户补完 4 个字段后被意图分类掉进
                # 旧状态机（tool_loop_active 粘性续接正是为短句回答误判
                # diagnosis 设计的，见 _agent_think_stream 的续接入口）。
                # 草稿已存在时不置——草稿轮的设计就是由意图分类路由补充/取消
                # （草稿就绪时已显式置 False）。
                if not memory.metadata.get("ticket_draft"):
                    state.tool_loop_active = True
            _save_agent_state(memory, state)
            await self._memory_manager.save_memory(memory)
            if final_text and not final_streamed:
                yield {"event": "token", "data": final_text}
            result_data = await self._finalize_diagnosis(
                request.session_id, state,
                thinking="", action="answer", message=final_text or "好的，有需要随时找我。",
                streaming=True)
            if result_data.get("title"):
                yield {"event": "title", "data": {"title": result_data["title"]}}
            yield {"event": "result", "data": result_data}
            return

        # ① 收集轮：渐进写回工具参数（含 requested_assignee），
        # 否则下一轮 draft_ready 时 LLM 不再重复带 assignee，_build_ticket 总结
        # 出来的描述里就丢了「提给贾爽」。
        for r in tool_results:
            if r.get("name") != "submit_ticket" or not r.get("arguments"):
                continue
            args = r["arguments"]
            if args.get("ticket_type"):
                state.ticket_type = args["ticket_type"]
            if args.get("problem_summary"):
                state.problem_summary = args["problem_summary"]
            for k, v in (args.get("collected_fields") or {}).items():
                if v and not state.collected_info.get(k):
                    state.collected_info[k] = v
            if args.get("requested_assignee"):
                state.collected_info["requested_assignee"] = args["requested_assignee"]
        state.tool_loop_active = True
        _save_agent_state(memory, state)
        await self._memory_manager.save_memory(memory)
        if final_text and not final_streamed:
            yield {"event": "token", "data": final_text}
        result_data = await self._finalize_diagnosis(
            request.session_id, state,
            thinking="", action="answer", message=final_text or "请稍后重试。",
            streaming=True)
        if result_data.get("title"):
            yield {"event": "title", "data": {"title": result_data["title"]}}
        yield {"event": "result", "data": result_data}

    # ================================================================
    # 诊断工具循环分支（阶段2，AI_DIAGNOSIS_TOOL_LOOP=1 时启用）
    # ================================================================
    async def _diagnosis_tool_loop_branch(self, request: DiagnosisRequest, state: AgentState, memory):
        """诊断轮走工具循环：LLM 可调 search_kb（查知识库）+ submit_ticket（提单）。

        与旧诊断 prompt 路径的区别：
        - 不再服务端强制检索：LLM 自己决定查不查、查什么、查几次
        - 查完知识库可以继续追问、回答、或顺势提单（提交工单工具也在）
        - thinking 默认开启（诊断需要深度推理）；AI_DIAGNOSIS_THINKING=0 时关闭
          （提速 A/B 开关：中转站慢时每轮可省数秒，质量略降）

        无工具调用（纯回答/闲聊）→ 直接输出 LLM 回复。
        """
        from ai.agents.AiDiagnosisPlatform.search_tool import SEARCH_KB_SCHEMA, make_search_result, make_search_error
        from ai.agents.AiDiagnosisPlatform.ticket_tool import TOOL_SCHEMA, execute_submit_ticket
        from ai.agents.AiDiagnosisPlatform.tool_loop import run_tool_loop

        yield {"event": "status", "data": {"stage": "analyzing", "round": state.diagnosis_rounds}}
        t0 = time.perf_counter()

        # 项目预填数据源：该用户名下项目列表（拉不到/为空 → 不注入，行为同旧版）
        _user_projects = await self._get_user_projects(request.created_by)

        # 构造 messages：system + 最近对话 + 本轮用户消息
        _conv = self._format_conversation(
            memory, from_turn=state.context_start, max_turns=8)
        system_prompt = (
            "你是「摇人吧」微信服务号的 AI 诊断助手 U老师，面向 AGV/AMR 行业，"
            "像一位经验丰富的现场工程师在微信上帮用户解决问题。\n"
            "你有两个工具：\n"
            "1. search_kb：检索知识库（操作手册/FAQ/排查手册/错误码）。"
            "回答操作步骤、错误码含义、故障排查等问题前，先查知识库；"
            "检索结果不相关就换关键词再查；多次查不到就如实说手册未覆盖，不要编造。\n"
            "2. submit_ticket：用户表达提单诉求（转工单/提单/派单）时调用。\n"
            "语气与风格：\n"
            "- 语气自然、口语化，先一句话回应问题本身，再给具体内容，不要公文腔\n"
            "- 步骤要具体可执行：说清在哪个页面、点什么、填什么\n"
            "- 禁止开发内部术语：不要出现 commit、函数名、参数名、模块名、分支、回滚等词\n"
            "- 结尾自然收尾，不要每条回复都以「建议转工单」结尾\n"
            "规则：\n"
            "- 不要问项目名称（项目由用户在确认弹窗里选择）\n"
            "- 用户可以一边咨询一边提单：先查知识库回答，用户不满意要提单时再调 submit_ticket\n"
            "- 查知识库后要基于检索内容回答，禁止编造步骤；"
            "检索内容必须真的包含问题所问的定义/步骤/参数才能作答，"
            "话题沾边但没给出所问内容时如实说手册没写，不要推测编造\n"
            "- 开场不要复述用户问题里已知的前提，直接进入步骤或关键区分\n"
            "- 知识库对不同的角色/身份/前提给出不同步骤时（如「USP研发」「实施」、"
            "自研车/第三方车），必须按原文的角色名称分开列出各自的完整步骤，"
            "禁止合并成一套步骤，禁止改写角色名称\n"
            "- 知识库对同一功能给出多种模式/方案时（如车随梯/车不随梯），"
            "分别列出各模式的要点并说明差异，不要只给一套通用步骤\n"
            "- 用户省略式追问（「然后呢」「第一步好了」）时，"
            "承接最近对话的进度继续讲下一步，不要当成全新问题、不要说未收录\n"
            "- 知识库内容中的 ![](url) 是操作界面截图：与当前问题直接相关的截图，"
            "用 ![说明](url) 引用到对应步骤下面；介绍产品/车型时知识库有图必须引用，不要省略\n"
            "- 进入提单收集后，已收集的信息不得重复追问；不要每轮新增一项可有可无的信息。"
            "已有问题概述、设备型号、现象、期望效果、版本、站点等足以让工程师初判时，"
            "应调用 submit_ticket 生成草稿，不要继续追问。\n"
            "- 用户明确说某个信息没有/不知道/不方便提供，或说「直接提单」「就这些信息」时，"
            "把该字段按「没有」写入 collected_fields 后调用 submit_ticket，"
            "绝不要反复追问同一项；追问最多 2-3 次就必须完成提单。\n"
            "- 调用 submit_ticket 的同一轮，先用一句简短过渡语向用户交代你正在做什么"
            "（以冒号收尾，让用户知道后面还有内容），如「好的，我帮您转工单，"
            "我看一下还需要补充哪些信息：」「收到，我核对一下还缺什么：」；"
            "过渡语后必须立刻发起工具调用，绝不能只说过渡语就结束回合；"
            "禁止说「已提交」「工单已生成」等完成时话术，不要播报项目预填情况。\n"
            f"当前上下文（非空说明用户在提单流程中）：问题={state.problem_summary or '无'}，"
            f"已收集={json.dumps(state.collected_info, ensure_ascii=False) if state.collected_info else '无'}\n"
        )
        if _user_projects:
            _proj_lines = "\n".join(
                f"- {p['name']}（编号: {p['code']}）" for p in _user_projects)
            system_prompt += (
                f"\n用户名下项目列表（仅这些可选）：\n{_proj_lines}\n"
                "项目预填规则：用户在对话中明确提到要给其中某个项目提单时，"
                "把该项目名称从上面列表**原样照抄**进 submit_ticket 的 project_choice "
                "参数；没提到或对不上就省略该参数。绝不向用户追问项目名称、不主动推荐项目。\n"
                "不要在对话中播报项目预填情况——预填结果由系统校验后在草稿生成时统一"
                "告知用户，弹窗中也会展示。\n"
            )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"以下是最近对话（供参考）：\n{_conv}\n\n本轮用户消息：{request.query}"},
        ]
        logger.info(f"[diag_tool] 诊断工具循环 prompt: system={len(system_prompt)} "
                    f"user={len(messages[1]['content'])} "
                    f"合计={len(system_prompt) + len(messages[1]['content'])} chars: "
                    f"session={request.session_id}")

        # search_kb 执行器：调现有检索服务
        async def _search_executor(params):
            try:
                query = (params.get("query") or "").strip()
                if not query:
                    return make_search_error("query 为空")
                result_text = await asyncio.wait_for(
                    self._retrieve_inner(request.session_id, state,
                                         query_override=query),
                    timeout=20.0,
                )
                return make_search_result(result_text)
            except Exception as e:
                logger.warning(f"[diag_tool] search_kb 失败: {e}")
                return make_search_error(str(e))

        # 诊断后转提单时，本轮模型通常只会传新增字段。必须合并状态中
        # 已收集的信息，否则 execute_submit_ticket 会把历史答案误判为缺失，
        # 导致重复追问甚至无限扩展追问项。
        async def _ticket_executor(params):
            merged = dict(state.collected_info)
            merged.update({
                k: v for k, v in (params.get("collected_fields") or {}).items() if v
            })
            return execute_submit_ticket(
                {**params, "collected_fields": merged}, make_draft=None)

        final_text = ""
        tool_results = []
        final_streamed = False  # 循环内已把 final_text 逐 token 流式发出 → 结束时不得整段重发
        # 循环内实际流出的正文累计：terminate 轮（草稿就绪）done 事件 final_text
        # 恒为空，但过渡语可能已流式说出——收尾据此判断「用户已经看到什么」，
        # 已流出的只补尾巴，绝不整段重发（重发 = 气泡里同一段话出现两遍）。
        _streamed_text = ""
        try:
            from ai.agents.AiDiagnosisPlatform.tool_loop import run_tool_loop_stream
            async with asyncio.timeout(90.0):
                async for ev in run_tool_loop_stream(
                        self._llm_client, messages,
                        [SEARCH_KB_SCHEMA, TOOL_SCHEMA],
                        {"search_kb": _search_executor, "submit_ticket": _ticket_executor},
                        # 诊断默认开思考（深度推理）；AI_DIAGNOSIS_THINKING=0 关闭提速。
                        thinking=None if os.getenv("AI_DIAGNOSIS_THINKING", "1") == "1" else False,
                ):
                    if ev["event"] == "token":
                        _streamed_text += ev.get("data") or ""
                        yield ev
                    elif ev["event"] == "done":
                        final_text = ev["final_text"]
                        tool_results = ev["tool_results"]
                        # final_text 非空才表示正文已在循环内流式发出；
                        # terminate 路径 final_text=""（收尾话术走兜底文案，未流式）
                        final_streamed = bool(final_text)
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning(f"[diag_tool] 诊断工具循环失败: session={request.session_id}, err={e}")
            yield {"event": "status", "data": {"stage": "submit_failed", "error": str(e)[:100]}}
            final_text = "诊断过程中出现异常，请稍后重试或联系管理员。"
            tool_results = []

        t_loop = round((time.perf_counter() - t0) * 1000)
        logger.info(f"[diag_tool] 循环完成: session={request.session_id}, "
                    f"elapsed={t_loop}ms, tool_calls={len(tool_results)}, "
                    f"final_text_len={len(final_text)}")

        # 提单工具被调用了 → 复用提单分支的草稿处理逻辑
        draft = None
        _prefill_project: Optional[Dict[str, str]] = None
        _draft_ready = any(
            r.get("details", {}).get("status") == "draft_ready" for r in tool_results)
        if _draft_ready:
            for r in tool_results:
                if r.get("name") != "submit_ticket" or not r.get("arguments"):
                    continue
                args = r["arguments"]
                state.ticket_type = args.get("ticket_type") or state.ticket_type
                state.problem_summary = args.get("problem_summary") or state.problem_summary
                for k, v in (args.get("collected_fields") or {}).items():
                    if v and not state.collected_info.get(k):
                        state.collected_info[k] = v
                if args.get("requested_assignee"):
                    state.collected_info["requested_assignee"] = args["requested_assignee"]
                # 项目预填：同 _ticket_tool_loop_branch——严格校验后进 draft，
                # 不写 state/collected_info、不参与判缺，弹窗仍可改。
                _prefill_project = self._match_project_choice(
                    args.get("project_choice", ""), _user_projects)
                if args.get("project_choice") and not _prefill_project:
                    logger.info(
                        f"[diag_tool] project_choice 未命中用户项目列表，忽略: "
                        f"{args.get('project_choice')!r}, session={request.session_id}")
                rf = args.get("required_fields") or {}
                if rf and isinstance(rf, dict):
                    state.required_fields = dict(rf)
                break
            try:
                draft = await self._build_ticket(request.session_id, state, memory,
                                                 prefill_project=_prefill_project)
            except Exception as e:
                logger.warning(f"[diag_tool] _build_ticket 失败: {e}")
                draft = None

        if draft is not None:
            draft["ticket_seq"] = state.ticket_seq + 1
            check = _check_required_fields(draft)
            draft["missing_fields"] = check["missing"]
            memory.metadata["ticket_draft"] = draft
            state.ticket_collecting = []
            state.tool_loop_active = False
            _save_agent_state(memory, state)
            await self._memory_manager.save_memory(memory)
            yield {"event": "status", "data": {
                "stage": "review",
                "draft": draft,
                "missing_fields": check["missing"],
                "force_submit": False,
            }}
            # terminate 轮 final_text 恒为空，过渡语可能已流式说出 → 收尾只补尾巴
            if draft.get("project"):
                _draft_msg = (f"已生成工单草稿，项目已预填为「{draft['project']}」"
                              "（可在弹窗中修改），请核对信息后确认提交。")
            else:
                _draft_msg = "已生成工单草稿，请在弹窗中选择项目并核对信息后确认提交。"
            _spoken = (_streamed_text or final_text or "").strip()
            if _spoken:
                yield {"event": "token", "data": "\n\n信息齐了，" + _draft_msg}
                _msg = _spoken + "\n\n信息齐了，" + _draft_msg
            else:
                _msg = _draft_msg
                yield {"event": "token", "data": _msg}
            result_data = await self._finalize_diagnosis(
                request.session_id, state,
                thinking="", action="answer", message=_msg, streaming=True)
            if result_data.get("title"):
                yield {"event": "title", "data": {"title": result_data["title"]}}
            yield {"event": "result", "data": result_data}
            return

        # submit_ticket 被调了但字段不齐（collecting）→ 标记粘性续接，否则下一轮
        # 会重新走意图分类/检索，把刚收集到的信息全部忘掉（日志实锤：诊断循环里
        # 提单收集中途，下一轮被误判回 diagnosis，重新查知识库，提单不了了之）。
        _submit_called = any(r.get("name") == "submit_ticket" for r in tool_results)
        if _submit_called:
            for r in tool_results:
                if r.get("name") != "submit_ticket" or not r.get("arguments"):
                    continue
                args = r["arguments"]
                if args.get("ticket_type"):
                    state.ticket_type = args["ticket_type"]
                if args.get("problem_summary"):
                    state.problem_summary = args["problem_summary"]
                for k, v in (args.get("collected_fields") or {}).items():
                    if v and not state.collected_info.get(k):
                        state.collected_info[k] = v
                if args.get("requested_assignee"):
                    state.collected_info["requested_assignee"] = args["requested_assignee"]
            state.tool_loop_active = True
            _save_agent_state(memory, state)
            await self._memory_manager.save_memory(memory)
            if final_text and not final_streamed:
                yield {"event": "token", "data": final_text}
            result_data = await self._finalize_diagnosis(
                request.session_id, state,
                thinking="", action="answer", message=final_text or "请稍后重试。",
                streaming=True)
            if result_data.get("title"):
                yield {"event": "title", "data": {"title": result_data["title"]}}
            yield {"event": "result", "data": result_data}
            return

        # 未提单：纯诊断回答（可能查过知识库）
        if final_text and not final_streamed:
            yield {"event": "token", "data": final_text}
        result_data = await self._finalize_diagnosis(
            request.session_id, state,
            thinking="", action="answer", message=final_text or "请稍后重试。",
            streaming=True)
        if result_data.get("title"):
            yield {"event": "title", "data": {"title": result_data["title"]}}
        yield {"event": "result", "data": result_data}

    # ================================================================
    # 诊断/闲聊单轮分支（无工具往返）：服务端检索 + 1 次 LLM 直接回答
    # ================================================================
    async def _diagnosis_oneshot_branch(self, request: DiagnosisRequest, state: AgentState,
                                        memory, reference_docs: str = ""):
        """诊断与闲聊共用：一次 LLM 调用直接出答案，不再有工具往返。

        检索由服务端完成（三路检索与意图分类并发，100-400ms），结果直接
        塞进小 prompt——LLM 不再自主换词检索。诊断每轮从「意图 + 2 次 LLM」
        降为「意图 + 1 次 LLM」。问候/闲聊轮同样走这里（reference_docs 为空）。
        """
        yield {"event": "status", "data": {"stage": "analyzing", "round": state.diagnosis_rounds}}
        t0 = time.perf_counter()

        _conv = self._format_conversation(
            memory, from_turn=state.context_start, max_turns=8)
        system_prompt = (
            "你是「摇人吧」微信服务号的 AI 诊断助手 U老师，面向 AGV/AMR 行业，"
            "像一位经验丰富的现场工程师在微信上帮用户解决问题。\n"
            "语气与风格：\n"
            "- 语气自然、口语化，先一句话回应问题本身（能解决/是什么/要查什么），"
            "再给具体内容，不要公文腔、不要机械罗列\n"
            "- 步骤要具体可执行：说清在哪个页面、点什么、填什么，必要时一句话说为什么\n"
            "- 禁止开发内部术语：不要出现 commit、函数名、参数名、模块名、分支、回滚等词，"
            "涉及系统内部变更时说「调度系统的行为变了」这类用户能懂的话\n"
            "- 结尾自然收尾即可，不要每条回复都以「建议转工单」结尾\n"
            "规则：\n"
            "- 回答操作步骤、错误码含义、故障排查等问题时，基于下方提供的知识库内容作答，禁止编造步骤\n"
            "- 检索内容必须真的包含问题所问的定义/步骤/参数才能作答：话题沾边但没给出所问内容时，"
            "如实说手册没写这部分，可以给通用排查方向，禁止基于沾边内容推测编造\n"
            "- 知识库对不同的角色/身份/前提给出不同步骤时（如「USP研发」「实施」、"
            "自研车/第三方车），必须按原文的角色名称分开列出各自的完整步骤，"
            "禁止合并成一套步骤，禁止改写角色名称\n"
            "- 知识库对同一功能给出多种模式/方案时（如车随梯/车不随梯、自研车/第三方车），"
            "分别列出各模式的要点并说明差异，不要只给一套通用步骤\n"
            "- 回答时直接给出结论和排查步骤，不要出现「根据知识库」「根据检索结果」"
            "这类来源性开场白——用户不需要知道信息来源\n"
            "- 开场也不要复述用户问题里已知的前提（用户问「怎么激活License」，"
            "就别先说「部署完成后需要激活License」这种废话），"
            "直接进入步骤或关键区分\n"
            "- 不要复述知识库的章节号/文档编号（如「5.13」「9.4」这类数字编号），"
            "用自己的话把步骤总结出来\n"
            "- 知识库内容中的 ![](url) 是操作界面截图：与当前问题直接相关的截图，"
            "必须用 ![说明](url) 格式引用到回复中对应步骤下面；与问题无关的图片一律不要带。"
            "介绍产品/车型时，知识库中若有该产品的图片，必须用 ![说明](url) 引用，不要省略\n"
            "- 回答控制在 500 字以内，步骤/操作类回答可放宽到 800 字，"
            "宁可简短完整，不要写太长（防止被截断）\n"
            "- 知识库内容没有覆盖时，才如实说明手册未收录这一部分，给出通用排查方向；"
            "用户问题确实需要人工处理时才提转工单，语气自然"
            "（如「这个问题要现场看的话，可以转工单，我来帮你提」）\n"
            "- 用户省略式追问（「然后呢」「第一步好了」「接着怎么做」）时，"
            "承接最近对话的进度继续讲下一步，不要当成全新问题、不要说未收录\n"
            "- 不要问项目名称（项目由用户在确认弹窗里选择）\n"
            "- 用户明确表达提单诉求时，礼貌引导：「可以说“转工单”，我来帮您提单」\n"
        )
        if reference_docs and reference_docs != "（跳过检索）":
            user_msg = (
                f"知识库检索结果：\n{reference_docs}\n\n"
                f"以下是最近对话（供参考）：\n{_conv}\n\n本轮用户消息：{request.query}"
            )
        else:
            user_msg = f"以下是最近对话（供参考）：\n{_conv}\n\n本轮用户消息：{request.query}"
        logger.info(f"[diag_oneshot] prompt: system={len(system_prompt)} "
                    f"user={len(user_msg)} chars: session={request.session_id}")

        final_text = ""
        streamed = False
        try:
            async with asyncio.timeout(90.0):
                async for token in self._llm_client.stream(
                        prompt=user_msg, system_prompt=system_prompt,
                        max_tokens=2000, temperature=0.2):
                    final_text += token
                    streamed = True
                    yield {"event": "token", "data": token}
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning(f"[diag_oneshot] 诊断单轮失败: session={request.session_id}, err={e}")
            yield {"event": "status", "data": {"stage": "submit_failed", "error": str(e)[:100]}}
            final_text = "诊断过程中出现异常，请稍后重试或联系管理员。"
            streamed = False

        logger.info(f"[diag_oneshot] 完成: session={request.session_id}, "
                    f"elapsed={round((time.perf_counter() - t0) * 1000)}ms, "
                    f"final_text_len={len(final_text)}")
        if final_text and not streamed:
            yield {"event": "token", "data": final_text}
        result_data = await self._finalize_diagnosis(
            request.session_id, state,
            thinking="", action="answer", message=final_text or "请稍后重试。",
            streaming=True)
        if result_data.get("title"):
            yield {"event": "title", "data": {"title": result_data["title"]}}
        yield {"event": "result", "data": result_data}

    # ================================================================
    # Agent 推理循环（同步）
    # ================================================================
    async def _agent_think(self, request: DiagnosisRequest, state: AgentState, memory) -> dict:
        """非流式包装：内部走 _agent_think_stream，收集结果后返回 dict。
        所有核心逻辑只在流式版本维护，避免双路径不同步。"""
        result_data = {}
        async for event in self._agent_think_stream(request, state, memory):
            if event["event"] == "result":
                result_data = event["data"]
        return result_data

    # ================================================================
    # 检索：用近期对话上下文，带简单内存缓存
    # ================================================================
    _CACHE_TTL = 300  # 秒：检索结果（含 rerank 精排）5 分钟内复用，重复/相近问题秒回

    async def _classify_intent(self, llm_client, raw_query: str, resolved_query: str,
                               context_turns: Optional[List[dict]] = None) -> str:
        """意图识别：三分类 courtesy / ticket / diagnosis。

        独立于主对话的快速分类（v4-flash 无思考，~1.5s），绝不阻塞诊断路径。
        ticket（提单意图）单独成类：识别出来后主循环跳过检索 + 精简 prompt + 关
        thinking，提单轮从 ~11s 压到 ~2s——用户已明确要提单，不需要查知识库和深度推理。

        context_turns：最近几轮对话（可选）。只看当前消息会误判多轮场景——
        「好的，我试试」单看是闲聊，但承接上一轮的排查就是诊断延续；
        「还是不行」单看无法归类。带上最近 2-3 轮让分类有上下文。
        """
        _ctx = ""
        if context_turns:
            lines = []
            for t in context_turns[-3:]:
                role = "用户" if (t.get("role") or "").lower() == "user" else "助手"
                c = (t.get("content") or "").strip()
                if c:
                    lines.append(f"{role}：{c[:150]}")
            if lines:
                _ctx = "以下是最近几轮对话（仅作上下文，你要判断的是最后一条消息的意图）：\n" \
                       + "\n".join(lines) + "\n\n"
        prompt = (
            f"{_ctx}"
            "判断下面用户消息的意图，只回复四个词之一：\n"
            "courtesy：寒暄/问候/闲聊/客套/表达感谢或情绪（如 你好、辛苦了、哈哈、谢谢、在吗）\n"
            "ticket：用户**明确提出提单诉求**——要我帮他转工单/提交工单/派单/找工程师处理"
            "（如 帮我转工单、提单吧、派单给XX、找个人给我配一下）。"
            "已经生成工单草稿后，用户对工单的**补充说明**（如「提给XX」「还有个补充，是XX时间发生的」「补充一下XX」）也属于 ticket。"
            "🔴 最近对话里刚生成过工单草稿/正在提单流程中时，用户的**取消/放弃/收尾话术**"
            "（如「算了」「不提了」「不用转了」「取消」「不要了」）也属于 ticket——"
            "这是工单流程内的话，必须走工单链路处理，不能判成 courtesy。"
            "仅仅是询问「工单怎么流转/工单是什么」这类流程咨询，以及**报告/吐槽「提单功能本身的问题」**"
            "（如 提单找不到项目、找不到处理人、提单弹窗报错、突然弹出工单草稿），"
            "都是要对服务号平台答疑诊断，**不算 ticket**，算 diagnosis；"
            "承接这类话题的回答（如被问何时开始时答「今天提单时出现的」）同样算 diagnosis。\n"
            "diagnosis：其他任何与设备、报错、故障、工作相关的求助或提问（如 AGV卡住、报错码、怎么办、工单流转流程是怎样的）；"
            "承接上文排查的追问、反馈（如「好的我试试」「还是不行」「这个呢」）也属于 diagnosis\n"
            "diagnosis_nokb：属于 diagnosis，但**本轮不需要查知识库**——仅限两种情况："
            "① 承接上文排查的续接/反馈/确认（如「然后呢」「下一步呢」「好的我试试」「还是不行」「可以了」），"
            "上文已给过资料，顺着对话继续即可；"
            "② 与设备/手册完全无关的通用对话（如「你是谁」「你能干什么」）。"
            "新故障/新参数/操作步骤/错误码/平台功能等问题一律输出 diagnosis（要查知识库）；"
            "🔴 拿不准时输出 diagnosis（宁可多查一次，也不要漏查）。\n"
            f"消息：{resolved_query or raw_query}\n"
            "意图："
        )
        try:
            answer = await llm_client.complete(
                prompt,
                system_prompt="你是意图分类器，只输出 courtesy / ticket / diagnosis / diagnosis_nokb，不要输出其他内容。",
                max_tokens=16,
                temperature=0.0,
                thinking=False,
            )
            intent = (answer or "").strip().lower()[:20]
            logger.debug(f"[intent] 分类结果: {intent!r} (raw={raw_query[:30]!r})")
            if "ticket" in intent:
                return "ticket"
            if "courtesy" in intent:
                return "courtesy"
            if "nokb" in intent:
                return "diagnosis_nokb"
            return "diagnosis"
        except Exception as e:
            # 意图识别失败/超时 → 一律当作诊断，不阻塞正常检索路径
            logger.warning(f"[intent] 识别失败，按诊断处理: {e}")
            return "diagnosis"

    def _cancel_retrieval(self, retrieval_task: asyncio.Task) -> None:
        """取消正在运行的检索任务。

        cancel() 会让检索协程在下一个 await 点（rerank 等）抛出 CancelledError，
        外层立即继续；底层 thread pool 中已提交的 rerank 推理会跑完（尽力而为，不中断线程）。
        """
        if not retrieval_task.done():
            retrieval_task.cancel()

    async def _retrieve_with_context(self, session_id: str, state: AgentState,
                                      context_turns: Optional[List[dict]] = None,
                                      query_override: str = "") -> str:
        t0 = time.perf_counter()
        logger.info(f"[retrieve] 进入检索: session={session_id}")
        try:
            # 意图判闲聊时由外部 cancel：检索在 await 点抛出 CancelledError，
            # 必须先于此处的宽泛 handler 退出（否则会落到下面 TimeoutError/ConnectionError 分支被当作失败）
            try:
                return await self._retrieve_inner(session_id, state, context_turns,
                                                  query_override=query_override)
            except asyncio.CancelledError:
                logger.debug(f"[retrieve] 被意图取消: session={session_id}")
                raise
        except ServiceUnavailableError as e:
            logger.warning(f"[retrieve] ServiceUnavailable: {e}")
            logger.warning(f"检索服务不可用: session={session_id}, error={e}")
        except LowConfidenceError as e:
            thr = getattr(self, '_score_threshold', get_ai_config().retrieval_score_threshold)
            logger.warning(f"[retrieve] LowConfidence: score={e.confidence:.3f} threshold={thr}")
            logger.warning(f"检索置信度过低: session={session_id}, score={e.confidence:.3f}")
        except (asyncio.TimeoutError, ConnectionError, RetrieveEmptyError):
            logger.warning(f"[retrieve] 超时/失败: {(time.perf_counter() - t0) * 1000:.0f}ms")
            logger.warning(f"检索超时/失败: session={session_id}")
        return "（知识库检索失败，请告知用户当前系统检索异常、建议稍后重试或转工单处理，不要自己编造答案。）"

    async def _three_way_retrieve(self, query: str) -> list:
        """三路并行域检索（team/company/industry），异常降级为空列表。
        每域「稠密 top5 + 稀疏 top5 保送」进候选池——不再 RRF 早融合，
        避免只被一路命中的文档被挤出 cross-encoder 精排池
        （锁区文档稠密第4名,RRF 后被两路命中的文档挤出 top12,精排永远看不到它）。"""
        async def _one(domain: str, top_k: int):
            try:
                dense_res, sparse_res = await asyncio.wait_for(
                    self._retriever.retrieve_domain_dual(query, domain, top_k=8),
                    timeout=15.0,
                )
                return list(dense_res)[:top_k], list(sparse_res)[:top_k]
            except Exception as e:
                # 不静默:缺方法(部署缺 retrieval.py)/超时/异常都打出来,
                # 否则全空结果会伪装成「知识库没查到」(历史踩坑:命中0)
                logger.warning(f"[retrieve] {domain} 域双路检索失败: {type(e).__name__}: {str(e)[:300]}")
                return [], []

        team_t = asyncio.create_task(_one("team", 5))
        company_t = asyncio.create_task(_one("company", 4))
        industry_t = asyncio.create_task(_one("industry", 3))
        gathered = await asyncio.gather(team_t, company_t, industry_t, return_exceptions=True)
        results = []
        seen = set()
        # 每域召回汇总（稠密+稀疏条数、首条标题@分）：域 0+0 = 该域集合空/异常，
        # 有召回但标题不相关 = 知识库缺该内容，排查时先看这行分流
        _summ = []
        for _domain, g in zip(("team", "company", "industry"), gathered):
            if isinstance(g, BaseException):
                _summ.append(f"{_domain} 异常")
                continue
            _dn, _sp = g
            _top1 = ""
            if _dn or _sp:
                _t = (_dn or _sp)[0]
                _sc = _t.vector_score or _t.sparse_score or _t.score or 0
                _top1 = f" top1=[{(_t.title or '(无标题)')[:30]}@{_sc:.3f}]"
            _summ.append(f"{_domain} {len(_dn)}+{len(_sp)}{_top1}")
            for r in _dn + _sp:
                if r.id not in seen:
                    seen.add(r.id)
                    results.append(r)
        logger.info(f"[retrieve] 域召回(稠密+稀疏): {' | '.join(_summ)}")
        return results

    async def _rewrite_query(self, query: str,
                             context_turns: Optional[List[dict]] = None) -> str:
        """轻量模型改写检索词（仅首轮检索弱时触发，走意图专用 deepseek 客户端）。
        两件事合一：口语转检索语；省略式追问（「然后呢」「第一步实现了」）结合
        最近对话补全为独立查询。失败/无变化返回空串（沿用原查询）。"""
        _ctx = ""
        if context_turns:
            lines = []
            for t in context_turns[-4:]:
                role = "用户" if (t.get("role") or "").lower() == "user" else "助手"
                c = (t.get("content") or "").strip()
                if c:
                    lines.append(f"{role}：{c[:200]}")
            if lines:
                _ctx = "最近对话：\n" + "\n".join(lines) + "\n\n"
        try:
            from ai.core import get_intent_client
            _llm = await get_intent_client()
            _out = await asyncio.wait_for(_llm.complete(
                prompt=(
                    f"{_ctx}"
                    "把下面的用户消息改写成适合知识库检索的独立查询短语（10-25字）：\n"
                    "- 保留错误码、车型/型号、专有名词等关键实体，去掉语气词和口语表达\n"
                    "- 消息是省略式追问（如「然后呢」「第一步实现了」）时，"
                    "结合最近对话补全成完整查询（如「RXX上线第一步完成后的下一步操作」）\n"
                    "- 消息本身已是完整问题时只做口语转检索语，不要扩大或改变问题范围\n"
                    "只输出改写后的查询短语，不要任何解释。\n\n"
                    f"用户消息：{query}"
                ),
                system_prompt="你是检索查询改写器。",
                max_tokens=40, temperature=0.0, thinking=False,
            ), timeout=5.0)
            _rw = (_out or "").strip().strip('"\'「」')
            if _rw and _rw != query and len(_rw) <= 50:
                logger.info(f"[retrieve] 改写检索词: {query[:40]} → {_rw[:40]}")
                return _rw
        except Exception as e:
            logger.warning(f"[retrieve] 改写失败，用原查询: {e}")
        return ""

    async def _retrieve_inner(self, session_id: str, state: AgentState,
                              context_turns: Optional[List[dict]] = None,
                              query_override: str = "") -> str:
        # 检索查询：用户当前输入为主，problem_summary/hypotheses 仅辅助短查询补全。
        # 用户查询≥10字且具体 → 不加任何旧 state 信息，防止旧话题污染（如查"自动门对接"
        # 但 state 残留"充电验证"，导致 embedding 偏航、正确 chunk 排不进 top N）。
        # 省略式追问（「然后呢」）不再用正则+原文拼接消解（拼接出的检索词是语义稀汤，
        # 实测 rxx 追问全部失灵）——交给 _rewrite_query 带 context_turns 用 LLM 补全。
        # query_override：诊断工具循环里 search_kb 传入的检索词（LLM 已自行组织）。
        t0 = time.perf_counter()
        search_query = query_override or state.original_query
        # 上下文补全只对真正的指代性短查询生效（"怎么办""这是啥"等无实义词）。
        # 阈值 10 太宽——「在哪看回放」（6字）是语义完整的新问题，被误当短查询
        # 拼上旧话题 problem_summary（30字），embedding 主成分被带偏，正确 chunk
        # 排不进 top N（日志实锤：检索结果全是旧话题「自研车上线」，无一条「回放」）。
        _need_context = len(search_query) < 4
        if state.problem_summary and _need_context:
            search_query = search_query + " " + state.problem_summary[:30]
        if state.hypotheses and _need_context:
            # LLM 可能输出 dict 而非纯字符串列表，先展平确保 join 不炸
            _hyps = [str(h) if not isinstance(h, str) else h for h in state.hypotheses]
            search_query = search_query + " " + " ".join(_hyps)[:50]

        # 缓存命中：同一查询 TTL 内复用结果。
        # key 必须带 session_id：省略式追问的检索结果含 rewrite 按该会话上下文
        # 补全的内容，跨会话共享会让第二个会话拿到第一个会话上下文的补全结果。
        cache_key = f"{session_id}:{search_query[:200]}"
        cached = self._retrieval_cache.get(cache_key)
        if cached and time.time() - cached["ts"] < self._CACHE_TTL:
            logger.debug(f"[retrieve] cache hit: {(time.perf_counter() - t0) * 1000:.0f}ms")
            return cached["result"]

        logger.info(f"[retrieve] 三路域检索: query={search_query[:60]}...")
        # 双查询合并检索:改写词与原词都查,结果并集。
        # 「可以调整吗」vs「怎么调整」这类提问方式差异会让 embedding 漂移,
        # 改写后的操作句式查询把另一侧命中的文档捞回来,抹平表述差异;
        # 省略式追问也靠这条改写路径补全成可检索的完整查询。
        _rw_task = asyncio.create_task(self._rewrite_query(search_query, context_turns))
        _domain_results = await self._three_way_retrieve(search_query)
        _rw = await _rw_task
        _rw_results = await self._three_way_retrieve(_rw) if _rw else []
        logger.info(f"[retrieve] 三路检索完成: {round((time.perf_counter() - t0) * 1000)}ms")
        if _rw:
            _rw_ids = {r.id for r in _domain_results}
            _rw_new = [r for r in _rw_results if r.id not in _rw_ids]
            _rw_new_desc = " | ".join(
                f"[{r.sub_domain or '-'}]{(r.title or '(无标题)')[:24]}" for r in _rw_new[:3])
            logger.info(f"[retrieve] 改写词捞回: rw='{_rw[:30]}' 命中{len(_rw_results)} "
                        f"其中原词未命中{len(_rw_new)}条"
                        + (f": {_rw_new_desc}" if _rw_new else "（无新增）"))

        # sub_domain → 标签映射
        _sub_labels = {
            "platform": "🎫 服务号", "yaorenba": "🎫 服务号",
            "faq": "📋 FAQ", "usp_faq": "📋 FAQ", "usp/faq": "📋 FAQ",
            "cheduan_errors": "🚗 车端", "cheduan_implementation": "🚗 车端",
            "translation": "🌐 翻译",
            "diagnosis": "🏭 诊断", "usp/diagnosis": "🏭 诊断",
            "usp_manual": "📖 手册", "usp/manual": "📖 手册",
            "usp_cards": "🔍 诊断卡",
            "usp/overview": "📘 模块文档",
            "usp/error_codes": "🚨 平台错误码",
            "usp/ui_pages": "🧭 页面导航",
            "usp/terminology": "🔤 术语表",
            "product_catalog": "🏢 产品", "vda5050_protocol": "🏢 协议",
            "navigation": "📐 导航", "standards": "📐 标准",
        }

        def _label(r) -> str:
            return _sub_labels.get(r.sub_domain, f"📄 {r.sub_domain or '知识库'}")

        # error code extraction + targeted cheduan retrieval
        _query_codes = self._retriever._extract_error_codes(search_query)
        _cheduan_exact: list = []
        if _query_codes:
            try:
                _cheduan_exact = await asyncio.wait_for(
                    self._retriever.retrieve_cheduan(search_query, top_k=3),
                    timeout=10.0,
                )
            except Exception:
                _cheduan_exact = []
        _cheduan_found = any(
            (r.sub_domain or "") in ("cheduan_errors", "cheduan_implementation")
            for r in _cheduan_exact
        )

        docs = []
        idx = 1

        # cheduan error code not found → note for LLM (not a mandatory denial)
        if _query_codes and not _cheduan_found:
            codes_str = "、".join(_query_codes)
            docs.insert(0,
                f"---\n🚗 提示：从查询中提取的数字 [{codes_str}] "
                f"在车端错误码库中未找到。如果检索结果中包含产品型号、文档编号等包含该数字的内容，"
                f"则这些是相关知识而非错误码，请正常引用，不要告知用户\"未收录\"。\n---")

        # 三路结果合并（原词 + 改写词双查并集）→ 去重
        all_results = list(_cheduan_exact) + _domain_results + _rw_results
        seen = set()
        uniq = []
        for r in all_results:
            if r.id not in seen:
                seen.add(r.id)
                uniq.append(r)

        # 双路保送后候选池收窄：按「稠密 top4 + 稀疏 top4」平衡截断。
        # 两路原始分尺度不同(稀疏 1-2 vs 稠密余弦 0.5-0.6),按单一分数排序会把
        # 另一路保送挤掉——平衡截断保证关键词命中和语义命中都进精排。
        # 同时收窄精排输入:最终只取 3 条且双路第1有保底注入,精排只需在 8 对
        # 里挑第 3 条;12 对时 v2-m3 CPU 精排 3-4s/轮,8 对约 2.5s。
        _dense_part = sorted(
            [r for r in uniq if r.vector_score], key=lambda r: r.vector_score, reverse=True)
        _sparse_part = sorted(
            [r for r in uniq if r.sparse_score], key=lambda r: r.sparse_score, reverse=True)
        logger.info(f"[retrieve] 池诊断: 总{len(uniq)} 稠密{len(_dense_part)} 稀疏{len(_sparse_part)} "
                    f"稠密top5={[(round(r.vector_score, 4), (r.title or '')[:24]) for r in _dense_part[:5]]} "
                    f"稀疏top3={[(round(r.sparse_score, 3), (r.title or '')[:24]) for r in _sparse_part[:3]]}")
        _balanced, _seen2 = [], set()
        for r in _dense_part[:4] + _sparse_part[:4]:
            if r.id not in _seen2:
                _seen2.add(r.id)
                _balanced.append(r)
        uniq = _balanced

        # 最终选取：双路平衡直选（精排已从主链路摘除）。
        # 车端错误码精确命中优先：精确码匹配是最高置信度，且 cheduan_exact 结果
        # 没有 vector_score/sparse_score 字段——若只从双路分数筛选会被整体漏掉
        # （实测「错误码200」查询：精确命中 200 被丢,只回了 212/2999/10610）。
        # 名额 5（稠密3+稀疏2）：摘精排后 top3 太窄，正确答案排在稠密第3+
        # 会被挤掉（实测「地图生效步骤」「历史任务记录」两例丢失）；
        # 多给 2 个名额,LLM 自行甄别,文档仍截断 800 字,prompt 可控。
        # 名额 6（稠密4+稀疏2）：诊断卡常以 0.7+ 分占据稠密前二，手册正确章节
        # 排稠密第 4 会被挤掉（实测电梯案例：4.4 车随梯 0.6364 排第 4 没进
        # prompt，回答只剩车不随梯一种模式）；同节 cap 保证多出的名额不会被
        # 同节 chunk 吃掉。文档仍截断 800 字，prompt 可控。
        _PROMPT_DOCS = 6
        _final, _fs = [], set()
        _final_tags: List[str] = []  # 与 _final 同步：每条来源路（密=稠密/疏=稀疏/码=错误码精确）
        for r in list(_cheduan_exact):
            if r.id not in _fs:
                _fs.add(r.id)
                _final.append(r)
                _final_tags.append("码")

        # 同节多样性：稠密/稀疏名额按「同一节最多 2 条」选取。
        # 同一节的 chunk 语义相近会扎堆占满名额（实测电梯案例 4.4 零条进
        # prompt：4.5 碎片+诊断卡占位；license 案例 overview 同文件占 2/3
        # 稠密名额）。被挤掉的名额由队列后面的其他节顶上。
        # 节 key = 源文件 + 标题首段（去掉「> 子项」「/ 小节」「· i/n」后缀）。
        _sec_counts: dict = {}

        def _sec_key(r):
            _t = r.title or ""
            _sec = re.split(r" [>/·] ", _t, maxsplit=1)[0].strip() if _t else ""
            return (r.source_file or r.sub_domain or "", _sec)

        _capped: List[str] = []  # 同节 cap 挤掉的候选（命中了但没进 prompt，检索问题定位用）

        def _take(queue: list, n_slots: int, tag: str) -> None:
            _taken = 0
            for r in queue:
                if _taken >= n_slots:
                    break
                if r.id in _fs:
                    continue
                _k = _sec_key(r)
                if _sec_counts.get(_k, 0) >= 2:
                    _capped.append(f"[{tag}]{(r.title or '')[:24]}")
                    continue
                _fs.add(r.id)
                _sec_counts[_k] = _sec_counts.get(_k, 0) + 1
                _final.append(r)
                _final_tags.append(tag)
                _taken += 1

        _take(_dense_part[:7], 4, "密")
        _take(_sparse_part[:5], 2, "疏")
        if _capped:
            logger.info(f"[retrieve] 同节cap挤掉{len(_capped)}条（命中但未进prompt）: {' | '.join(_capped[:5])}")
        uniq = _final[:_PROMPT_DOCS]

        hit_logs = []  # 送入 prompt 的 chunk 摘要（[路别][域]标题@分数，用于生产排查检索效果）
        for _ri, r in enumerate(uniq[:_MAX_RETRIEVAL_DOCS]):
            content = self._rewrite_images(r) if r.content else ""
            if not content.strip():
                continue
            # 单个 chunk 截断到 800 字：平铺 bullet 的大章节 chunk 可达数千字,
            # 全文进 prompt 会把 prompt 撑到 2 万字符(生产日志实锤),模型也抓不住重点
            content = content[:800]
            title = f"（{r.title}）" if r.title else ""
            docs.append(f"---\n{_label(r)} {idx}{title}：\n{content}\n---")
            _tag = _final_tags[_ri] if _ri < len(_final_tags) else "?"
            hit_logs.append(f"[{_tag}][{r.sub_domain or '-'}]{r.title or '(无标题)'}@{r.score:.4f}")
            idx += 1
        _dist = {t: _final_tags.count(t) for t in set(_final_tags)}
        logger.info(f"[retrieve] 命中{len(all_results)}去重{len(uniq)}送prompt{len(hit_logs)}"
                    f"(密{_dist.get('密', 0)}/疏{_dist.get('疏', 0)}/码{_dist.get('码', 0)}): "
                    f"{' | '.join(hit_logs)} 总耗时{round((time.perf_counter() - t0) * 1000)}ms")

        if not docs:
            logger.warning(f"[retrieve] 送prompt为0条（各域召回与池诊断见上方日志——"
                           f"域全0=检索/集合异常，有召回但低分=知识库未覆盖）: query={search_query[:50]}")
        result = "\n".join(docs) if docs else "（知识库暂无匹配文档，请告知用户当前手册未覆盖此问题，建议转工单处理，不要自己编造答案。）"

        self._retrieval_cache[cache_key] = {"result": result, "ts": time.time()}
        # 防止缓存无限增长
        if len(self._retrieval_cache) > 200:
            oldest = min(self._retrieval_cache, key=lambda k: self._retrieval_cache[k]["ts"])
            del self._retrieval_cache[oldest]
        logger.debug(f"[retrieve] total: {(time.perf_counter() - t0) * 1000:.0f}ms")
        return result

    # ================================================================
    # 工单生成
    # ================================================================
    async def _backfill_collected_info(self, session_id: str, agent_state: AgentState, memory) -> None:
        """提单前回填：基于 required_fields 清单从对话中提取用户已给出的信息，
        补入 collected_info（不覆盖已有值）。仅提取 required_fields 中指定的字段——
        key 名必须与 required_fields 一致，保证后续 _assess_ticket_readiness 能对上。
        required_fields 为空时无字段可提取（project 已移出对话链路，不回填）。

        图片描述屏蔽：上传的截图是 UI 文本（工单类型/状态/处理人等），不是用户陈述。
        组装对话时把"图片主要内容为：..."替换成占位符，字段提取物理上看不到 UI 文本。
        """
        try:
            turns = memory.turns[agent_state.context_start:]
            if not turns:
                return
            # 图片描述屏蔽：上传的截图是 UI 文本（工单类型/状态/处理人等），
            # 不是用户陈述，字段提取物理上看不到 UI 文本。
            conversation_text = self._format_conversation(
                memory, from_turn=agent_state.context_start, max_turns=20, sanitize_images=True)
            rf = agent_state.required_fields or {}
            if not rf:
                # 无 required_fields：没有可提取的目标，直接返回（不再退化为提取 project）
                return
            field_list = "\n".join(f"  - {k}（{label}）" for k, label in rf.items())
            prompt = (
                "从以下对话中提取指定字段的值，仅提取对话中直接提及的内容，不推测、不编造。\n\n"
                "## 目标字段\n"
                f"{field_list}\n"
                "## 输出规范\n"
                "- 以 JSON 对象返回，key 必须使用目标字段中给定的英文标识，不要改名\n"
                "- 对话中未提及的字段不输出\n"
                "- 🔴 只能提取用户明确陈述过的事实。用户的提问/诉求本身不是答案：\n"
                "  用户问「怎么更新权限」，不代表用户说过「当前权限是什么、目标权限是什么」——\n"
                "  不要把问题当答案回填，没说的字段一律不输出\n\n"
                f"## 对话\n{conversation_text}\n"
            )
            raw = await asyncio.wait_for(
                self._llm_client.complete(prompt=prompt, max_tokens=400, temperature=0,
                                           thinking=False),
                timeout=15.0,
            )
            data = _extract_json_object(raw)
            filled = []
            # 只接受 required_fields 中定义的 key（key 用同一归一化，防 project_name 变体判缺）。
            # project 已移出对话链路，不回填、不归一。
            valid_keys = set(_canonical_field_key(k) for k in rf.keys())
            # 中文标签 → 英文 key 反向映射（LLM 可能直接输出标签）
            _label_to_key = {str(label): key for key, label in rf.items()}
            for k, v in data.items():
                if not v:
                    continue
                v = str(v).strip()
                if not v:
                    continue
                # key 归一化：近义词/中文标签 → 统一 key，再与 valid_keys 比对
                k = _canonical_field_key(k)
                if k in _label_to_key:
                    k = _label_to_key[k]
                if k not in valid_keys:
                    logger.debug(f"[backfill] 忽略非目标字段: {k}={v[:30]}")
                    continue
                if k in agent_state.collected_info:
                    continue
                agent_state.collected_info[k] = v
                filled.append(k)
            if filled:
                logger.info(f"[backfill] 从对话回填 collected_info: session={session_id}, fields={filled}")
        except Exception:
            logger.warning(f"[backfill] 回填失败（忽略，按原 collected_info 判定）: session={session_id}",
                           exc_info=True)

    async def _compute_ticket_fields(self, session_id: str, memory, context_start: int) -> dict:
        """纯计算（不写任何状态）：基于对话预测 {ticket_type, required_fields}。

        从 _decide_ticket_fields 拆出，供两条路径复用：
        ① 同步路径：_decide_ticket_fields 直接调用后写 state；
        ② 并行路径：主 LLM 流式推理期间后台预测，解析完按需采用（零等待）。
        只读 memory.turns / context_start 快照，不碰 agent_state。
        """
        # 屏蔽图片描述：定字段清单只关心对话里用户说了什么，
        # 截图 UI 文本（缺陷/处理中/处理人）会诱导 LLM 把工单类型当信息缺口。
        conv = self._format_conversation(
            memory, from_turn=context_start, max_turns=20, sanitize_images=True)
        prompt = (
            "分析以下对话，判定工单类型（problem/bug/feature/support/other），"
            "并识别要解决该工单还需向用户收集的关键信息项。\n\n"
            "## 输出规范\n"
            "- ticket_type：从 problem/bug/feature/support/other 中选取\n"
            "- required_fields：JSON 对象，key 为英文标识，value 为中文简短标签（≤8 字）\n"
            "- 🔴 字段分两层，总数 2-4 个，禁止返回空对象：\n"
            "  · 核心字段（2 个）：不问清楚就无法定位/复现问题的信息"
            "（如车辆编号、故障码、调度版本、涉及的设备或模块）——"
            "缺了它工程师接单后完全没法开工。对话里已说清的不算缺口，"
            "但不能用补充字段凑数\n"
            "  · 补充字段（0-2 个）：有助于加快处理但非必需的锦上添花信息"
            "（如发生时间、现场位置、出现频率）——只在对话没提、"
            "且确实值得追问时才加，宁缺毋滥\n"
            "- 🔴 一项信息一个字段：时间、车辆编号、任务等各自独立成 key，"
            "禁止合并进一个字段（打包会导致用户只答一项就被判齐、提前弹窗丢信息）\n"
            "- 🔴 只列入「对话中确实还没说过的信息缺口」：仔细读完整对话，"
            "用户已经说过、提到过、或能从对话直接推出的信息一律不列入\n"
            "- 项目由用户在确认弹窗选择，不要写入 required_fields\n"
            "- 仅输出 JSON，无额外文字\n\n"
            f"## 对话\n{conv}\n"
        )
        raw = await asyncio.wait_for(
            self._llm_client.complete(prompt=prompt, max_tokens=300, temperature=0,
                                       thinking=False),
            timeout=15.0,
        )
        data = _extract_json_object(raw)
        tt = (data.get("ticket_type") or "").strip()
        result = {"ticket_type": tt if tt in ("problem", "bug", "feature", "support", "other") else ""}
        rf = data.get("required_fields") or {}
        if isinstance(rf, dict):
            result["required_fields"] = {
                _canonical_field_key(k): str(v)[:20] for k, v in rf.items()
                if str(v).strip() and len(str(k)) <= 40
            }
        else:
            result["required_fields"] = {}
        # 不足 2 个字段重试：LLM 偶尔无视「2 核心 + 0-2 补充」规则只给 1 个
        # 或空清单（1 个字段用户一答就齐、直接弹草稿）。补一次带提醒的重试。
        if len(result["required_fields"]) < 2:
            retry_prompt = (
                prompt
                + "\n\n⚠️ 你上一次返回的 required_fields 少于 2 个（或为空），这不符合要求。"
                  "重新分析对话：工单提单前至少有 2 个核心信息缺口"
                  "（不问就无法定位/复现问题）需要用户补充，"
                  "请按输出规范给出 2-4 个字段，仅输出 JSON。"
            )
            try:
                raw2 = await asyncio.wait_for(
                    self._llm_client.complete(prompt=retry_prompt, max_tokens=300,
                                               temperature=0.2, thinking=False),
                    timeout=15.0,
                )
                data2 = _extract_json_object(raw2)
                rf2 = data2.get("required_fields") or {}
                if isinstance(rf2, dict) and rf2:
                    result["required_fields"] = {
                        _canonical_field_key(k): str(v)[:20] for k, v in rf2.items()
                        if str(v).strip() and len(str(k)) <= 40
                    }
            except Exception as e:
                logger.warning(f"[compute_fields] 空清单重试失败: {e}")
        return result

    def _adopt_ticket_fields(self, agent_state: AgentState, result: dict) -> None:
        """采纳预测结果（同步写 state）。ticket_type 只在主 LLM 未定时采用；
        required_fields 只在从未决定过时写入。"""
        if not agent_state.ticket_type:
            tt = result.get("ticket_type") or ""
            if tt in ("problem", "bug", "feature", "support", "other"):
                agent_state.ticket_type = tt
        rf = result.get("required_fields") or {}
        # required_fields 一旦决定（包括空字典）即锁定；空清单也要采纳，
        # 否则后续轮会把“已决定”误认为 None，重复请求字段生成。
        if isinstance(rf, dict) and agent_state.required_fields is None:
            _new = {
                _canonical_field_key(k): str(v)[:20] for k, v in rf.items()
                if str(v).strip()
                and len(str(k)) <= 40
                and not (agent_state.collected_info.get(_canonical_field_key(k)) or "").strip()
            }
            # 空字典也是“已决定”：对话已经覆盖全部字段时必须锁定空清单，
            # 否则后续每轮都会重新调用字段生成。
            agent_state.required_fields = _new
            if not _new:
                logger.info(f"[decide_fields] 字段已全部覆盖，锁定空清单: session={agent_state.session_id}")
        logger.info(f"[decide_fields] type={agent_state.ticket_type} "
                    f"required={agent_state.required_fields} session={agent_state.session_id}")

    async def _decide_ticket_fields(self, session_id: str, agent_state: AgentState, memory) -> None:
        """同步路径：让 LLM 根据对话总结出工单类型 + 必补关键字段
        （2 个核心「不问就无法定位/复现」+ 0-2 个锦上添花），
        锁进 state.required_fields / ticket_type。后续提单门槛 = 这些字段全非空。

        字段由 LLM 按问题类型动态决定（不是硬编码清单），符合"AI 判断要补什么信息"。
        项目由用户在确认弹窗选择，不写进 required_fields。失败则保持空（无必补字段）。
        """
        try:
            result = await self._compute_ticket_fields(
                session_id, memory, agent_state.context_start)
            self._adopt_ticket_fields(agent_state, result)
        except Exception:
            logger.warning(f"[decide_fields] 失败（锁定为空清单）: session={session_id}",
                           exc_info=True)
            # 首次决定失败也必须结束“未决定”状态，避免后续每轮重复请求。
            # 此时按无额外字段继续，项目仍由确认弹窗负责选择。
            if agent_state.required_fields is None:
                agent_state.required_fields = {}

    async def _build_ticket(self, session_id: str, agent_state: AgentState, memory,
                            prefill_project: Optional[Dict[str, str]] = None) -> dict:
        # 生成工单的对话：屏蔽图片描述 + 从 context_start 切片。
        # 切片至关重要：提单后 context_start 会前移（旧对话归档），
        # 下一个工单只看新对话——否则上一个工单的补充信息（如「调度版本 2.6.4」）
        # 会串进新工单的描述。
        # project 不在本函数的 LLM 链路产生（下方 prompt 仍固定空字符串）；
        # 唯一例外是 prefill_project——工具循环里 LLM 从该用户名下项目列表照抄、
        # 经严格校验的预填值，由代码在生成后写入 draft。其余入口仍是确认弹窗。
        # 🔴 context_start 越界护栏：turn buffer 截断（max_turns=10）时
        # turns[context_start:] 可能为空 → 提单模型看不到任何对话，AI 追问过、
        # 用户回答过的补充信息（如现场联系方式）全部丢失、写不进描述。
        # 回退取最近全量 10 条，保证补充问答一定在模型视野里。
        _from = agent_state.context_start
        if _from >= len(memory.turns):
            _from = max(0, len(memory.turns) - 10)
        conversation_text = self._format_conversation(
            memory, from_turn=_from, sanitize_images=True)
        reasoning = (
            f"问题概述：{agent_state.problem_summary}\n"
            f"推测原因：{'、'.join(str(h) if not isinstance(h, str) else h for h in agent_state.hypotheses) if agent_state.hypotheses else '无'}\n"
            f"已排除：{'、'.join(str(r) if not isinstance(r, str) else r for r in agent_state.ruled_out) if agent_state.ruled_out else '无'}\n"
            f"已收集信息：{json.dumps(agent_state.collected_info, ensure_ascii=False)}\n"
            f"诊断轮数：{agent_state.diagnosis_rounds}"
        )

        # 附件候选清单：本会话累积的上传文件，让 LLM 判断哪些与本单问题相关
        # （跨话题场景：换话题前提的单不能带上上一个话题的诊断图）。
        # desc 是上传时 VLM 摘要（router 截 160 字）——对话记录里图片内容已被
        # sanitize_images 屏蔽，没有摘要 LLM 就没有判断信号。
        _atts_all = memory.metadata.get("agent_state", {}).get("attachments", []) or []
        _att_block = ""
        if _atts_all:
            _lines = []
            for _i, _a in enumerate(_atts_all, 1):
                if not isinstance(_a, dict):
                    continue
                _d = str(_a.get("desc") or "").strip()
                _lines.append(f"{_i}. {_a.get('filename', '')}"
                              + (f"（内容摘要：{_d[:120]}）" if _d else ""))
            if _lines:
                _att_block = (
                    f"\n\n## 附件候选（本次会话累积的上传文件，仅供取舍判断）\n"
                    + "\n".join(_lines)
                    + "\n判断哪些与【本次工单的问题】直接相关（故障证据/现场照片/问题截图），"
                      "只把相关项的序号放进 attach_files；与本单问题无关的（如之前别的"
                      "话题的提问截图）不要放，没有相关的给空数组。"
                      "🔴 摘要里的文字仅供取舍，禁止从中提取工单字段内容。"
                )

        prompt = (
            f"请根据以下对话和诊断过程，生成结构化工单。\n\n"
            f"## 对话记录\n{conversation_text}\n\n"
            f"## Agent 推理链\n{reasoning}{_att_block}\n\n"
            f"请先判断工单类型（problem=报障/bug=缺陷/feature=功能需求/support=支持请求/other=其他），"
            f"然后以 JSON 格式返回：\n"
            f'{{"type":"problem|bug|feature|support|other","title":"≤20字，不要含项目名（项目由用户在弹窗选择）","description":"≤300字，简述问题和排查过程，不要带项目/现场名；🔴 AI 追问过、用户回答过的内容必须全部总结进去，一项都不能丢；🔴 AI 没问过的信息一律不要出现在描述里，禁止写「XX：未提供」「XX：无」凑格式（如没问过调度版本就不能有「调度版本：未提供」）；用户答「没看清/没记住」的照实写（如「报错一闪而过，用户未看清具体内容」）；🔴 型号/车辆编号必须写进 description 正文——工单表单没有独立的型号字段，描述是它唯一对用户可见的地方，即使已在 robot_type 结构化字段填过也要写；🔴 如果对话里用户指名了接单人（提给XX/交给XX/派单给XX），description 开头必须写「[指定处理人：XX]」，绝不能漏",'
            f'"priority":"紧急|高|中|低","contact":"从对话提取的联系人，没有则为空",'
            f'"location":"仅type=problem时填，现场位置","robot_type":"仅type=problem时填，机器人型号/编号",'
            f'"project":"固定为空字符串——项目由用户在确认弹窗搜索选择，不要从对话提取",'
            f'"fault_code":"仅type=problem时填，故障码","special_notes":"所有类型可用，特殊说明（用户指名处理人、额外备注等）",'
            f'"occurrence_time":"仅type=problem时填，故障发生时间","frequency":"仅type=problem时填，出现频率（每次/偶尔/首次）",'
            f'"steps_to_reproduce":"仅type=bug时填","expected_result":"仅type=bug时填",'
            f'"actual_result":"仅type=bug时填","severity":"仅type=bug时填:阻塞/主要/次要/轻微",'
            f'"version":"仅type=bug时填","scenario":"仅type=feature时填，需求场景",'
            f'"expected_effect":"仅type=feature时填","source":"仅type=feature时填:客户提出/内部发现/竞品对标",'
            f'"support_type":"仅type=support时填","preferred_response":"仅type=support时填:电话/现场/线上",'
            f'"attach_files":[附件候选里与本单问题相关的序号数组，如[1,3]；无关或无附件则为空数组]}}'
        )

        logger.info(f"[build_ticket] 工单生成 prompt: {len(prompt)} chars: session={session_id}")

        # 两次尝试：抖动窗口里单次 20s 超时就降级太亏（生产实录：LLM 服务抖动
        # 导致 ReadTimeout 连带这里超时，工单标题退化成原话硬截 20 字）。本调用
        # 期间用户看的是「正在生成工单」动画，重试完全无感知；超时与返回不可解析
        # 都重试，两次都失败才走默认值兜底。
        analysis = {}
        _bt_last_err = ""
        for _attempt in (1, 2):
            try:
                raw = await asyncio.wait_for(
                    self._llm_client.complete(prompt=prompt, max_tokens=600, temperature=0.2),
                    timeout=20.0,
                )
                analysis = _extract_json_object(raw)
                if analysis:
                    break
                _bt_last_err = f"返回不可解析: {str(raw)[:100]!r}"
            except Exception as e:
                _bt_last_err = f"{type(e).__name__}: {str(e)[:100]}"
            logger.warning(f"[build_ticket] 第{_attempt}次尝试失败: {_bt_last_err}, session={session_id}")
        if not analysis:
            logger.error(f"LLM 工单生成失败（将使用默认值）: session={session_id}, "
                         f"last_error={_bt_last_err}")

        # 工单类型前移：对话中 LLM 已维护 ticket_type 时直接采用，避免提单瞬间二次分类漂移
        ticket_type = agent_state.ticket_type or analysis.get("type", "other")
        if ticket_type not in ("problem", "bug", "feature", "support", "other"):
            ticket_type = "other"

        # 附件取舍：LLM 输出 attach_files 序号数组 → 映射回候选条目。
        # 降级语义：字段缺失/类型不对/LLM 整体失败 → 保持全量（现状行为，
        # 不静默丢证据——弹窗没有附件编辑 UI，漏带无法补救）；
        # 显式空数组 → 尊重（LLM 判定都与本单无关）。越界序号忽略。
        _selected_atts = _atts_all
        _sel = analysis.get("attach_files") if analysis else None
        if isinstance(_sel, list):
            def _to_int(x):
                try:
                    return int(x)
                except (TypeError, ValueError):
                    return None
            _idx = sorted({i for i in (_to_int(x) for x in _sel)
                           if i is not None and 1 <= i <= len(_atts_all)})
            _selected_atts = [_atts_all[i - 1] for i in _idx]
            if len(_selected_atts) != len(_atts_all):
                logger.info(f"[build_ticket] 附件筛选: {len(_atts_all)} -> "
                            f"{len(_selected_atts)} ({[_a.get('filename') for _a in _selected_atts]}), "
                            f"session={session_id}")

        # 通用字段
        # 指名处理人写进描述，供派单直接看到
        _desc = analysis.get("description", agent_state.problem_summary[:150])
        _assignee = agent_state.collected_info.get("requested_assignee", "").strip()
        if _assignee and "指定处理人" not in (_desc or ""):
            _desc = f"[指定处理人：{_assignee}] {_desc or ''}"
        result = {
            "ticket_id": f"AI-{session_id[-6:]}-{int(time.time()) % 100000}",
            "session_id": session_id,
            "type": ticket_type,
            # 标题兜底链：LLM title > 最新提炼的 problem_summary > original_query
            # （original_query 在 diagnosing 会话换话题后会残留旧问题，只作最后兜底）
            "title": analysis.get("title") or (agent_state.problem_summary[:20]
                                               or agent_state.original_query[:20]),
            "description": _desc,
            "priority": analysis.get("priority", "中"),
            "status": "pending",
            "contact": analysis.get("contact", ""),
            # 项目：本函数 LLM 不产生（默认空）；prefill_project 预填见下方覆盖。
            # 弹窗搜索选择仍是权威入口，confirm_submit 用 overrides 写回。
            "project": "",
            "project_id": "",
            "diagnosis": {
                "problem_summary": agent_state.problem_summary,
                "hypotheses": agent_state.hypotheses,
                "ruled_out": agent_state.ruled_out,
                "collected_info": agent_state.collected_info,
                "rounds": agent_state.diagnosis_rounds,
            },
            "created_at": int(time.time()),
            "source": "ai_agent",
            "attachments": _selected_atts,
        }

        # 特殊说明（所有类型通用）：优先取 LLM analysis，兜底取 collected_info["requested_assignee"]
        _notes = analysis.get("special_notes", "")
        _assignee = agent_state.collected_info.get("requested_assignee", "").strip()
        if _assignee and "指定处理人" not in _notes:
            _notes = f"指定处理人：{_assignee}" + (f"；{_notes}" if _notes else "")
        result["special_notes"] = _notes

        # 项目预填（单向管道，不回流 state）：工具循环里 LLM 从该用户名下项目
        # 列表照抄 + 严格校验（_match_project_choice）通过的结果，写进 draft 作为
        # 弹窗默认值。用户在弹窗仍可改（overrides 覆盖优先）；未预填时维持空，
        # confirm_submit 用 overrides 写回 project/project_id，行为同旧版。
        # （analysis.get("project") 即使 LLM 偶尔输出了值也被忽略——prompt 固定空。）
        if prefill_project:
            logger.info(f"[build_ticket] 项目预填: {prefill_project['name']} "
                        f"(code={prefill_project['code']}), session={session_id}")
            result["project"] = prefill_project["name"]
            result["project_id"] = prefill_project["code"]

        # 类型专属字段
        if ticket_type == "problem":
            result["location"] = analysis.get("location", "")
            # 保底必填字段优先取 collected_info（对话中用户实际提供、已过服务端校验）
            result["robot_type"] = agent_state.collected_info.get("robot_type", "") or analysis.get("robot_type", "")
            result["fault_code"] = analysis.get("fault_code", "")
            result["occurrence_time"] = agent_state.collected_info.get("occurrence_time", "") or analysis.get("occurrence_time", "")
            result["frequency"] = agent_state.collected_info.get("frequency", "") or analysis.get("frequency", "")
        elif ticket_type == "bug":
            result["steps_to_reproduce"] = agent_state.collected_info.get("steps_to_reproduce", "") or analysis.get("steps_to_reproduce", "")
            result["expected_result"] = analysis.get("expected_result", "")
            result["actual_result"] = analysis.get("actual_result", "")
            result["severity"] = analysis.get("severity", "")
            result["version"] = agent_state.collected_info.get("version", "") or analysis.get("version", "")
        elif ticket_type == "feature":
            result["scenario"] = agent_state.collected_info.get("scenario", "") or analysis.get("scenario", "")
            result["expected_effect"] = agent_state.collected_info.get("expected_effect", "") or analysis.get("expected_effect", "")
            result["source"] = analysis.get("source", "")
        elif ticket_type == "support":
            result["support_type"] = agent_state.collected_info.get("support_type", "") or analysis.get("support_type", "")
            result["preferred_response"] = analysis.get("preferred_response", "")

        return result

    async def _attach_chat_snapshot(self, ticket: dict, memory) -> None:
        """把对话记录以 Markdown 文档附加到工单 attachments（原地修改 ticket）。

        只附加工单入库路径（submit / confirm_submit）调用——get_ticket 等只读预览
        不生成，避免每次打开详情都白传一份。失败静默降级为无附件。

        记录来源：优先 MySQL 全量历史（conversations.service_ticket_id=session_id 映射，
        前端每轮 appendMessage 落库），Redis 只保留最近 N 轮；MySQL 不可用/无记录时
        回退 memory.turns（最近 N 轮）。
        """
        try:
            from ai.core.chat_snapshot import create_chat_markdown_attachment
            sid = ticket.get("session_id", "")
            turns = memory.turns
            try:
                from ai.core.conversation_store import get_history
                rows = await asyncio.to_thread(get_history, sid)
                if rows:
                    db_turns = [{"role": r["role"], "content": r["content"]} for r in rows]
                    # 顺序校正：MySQL messages.sequence 有落库竞态——用户消息是前端
                    # fire-and-forget、AI 回复是后端流式落库，连发消息时落库先后
                    # ≠ 真实对话先后。memory.turns 在内存里按真实顺序 append，是权威。
                    # 用 memory 的最近 N 轮替换 MySQL 尾部（乱序只发生在最近的落库竞态轮），
                    # 早于 memory 窗口的老消息顺序稳定，保留 MySQL 部分。
                    mem_turns = list(memory.turns)
                    if mem_turns:
                        # memory 尾部在 MySQL 里找到匹配（从后往前找第一个匹配点），
                        # 之前的部分用 MySQL（老消息），之后的部分用 memory（顺序权威）
                        matched = -1
                        for i in range(len(db_turns) - 1, -1, -1):
                            if (db_turns[i]["role"] == mem_turns[-1]["role"]
                                    and db_turns[i]["content"] == mem_turns[-1]["content"]):
                                matched = i
                                break
                        if matched >= 0:
                            turns = db_turns[:matched] + mem_turns
                            logger.info(f"[chat_markdown] MySQL 尾部顺序已用 memory 校正: session={sid}, "
                                        f"db={len(db_turns)}, mem={len(mem_turns)}, matched={matched}")
                        else:
                            turns = db_turns
                            logger.info(f"[chat_markdown] MySQL 尾部未在 memory 中匹配，保持 MySQL 顺序: session={sid}")
                    else:
                        turns = db_turns
                    logger.info(f"[chat_markdown] 使用 MySQL 全量历史: session={sid}, turns={len(turns)}")
                else:
                    logger.info(f"[chat_markdown] MySQL 无记录，回退 memory turns: session={sid}, turns={len(turns)}")
            except Exception as e:
                logger.warning(f"[chat_markdown] MySQL 历史读取失败，回退 memory turns: session={sid}, err={e}")
            doc = await create_chat_markdown_attachment(
                sid, turns, title=ticket.get("title") or "")
            if doc:
                ticket["attachments"] = (ticket.get("attachments") or []) + [doc]
        except Exception:
            logger.warning(f"[chat_markdown] 附加失败，降级无附件: session={ticket.get('session_id', '')}",
                           exc_info=True)

    async def get_ticket(self, session_id: str) -> dict:
        """只读获取工单数据，不改变状态"""
        await self._ensure_clients()
        memory = await self._memory_manager.get_memory(session_id)
        agent_state = _load_agent_state(memory.metadata) or AgentState(session_id=session_id)
        ticket = await self._build_ticket(session_id, agent_state, memory,
                                          prefill_project=agent_state.pending_prefill_project)
        # 彻底根治：_build_ticket 每次都用 int(time.time()) 重写 created_at（读取时刻），并非工单真实创建时间。
        # 提交时（submit / confirm_submit）已把真实创建时间持久化到 last_submitted_ticket.submitted_at，
        # 这里用持久化的真实创建时间覆盖，避免详情页显示「打开详情那一刻」的时间。
        submitted_at = (agent_state.last_submitted_ticket or {}).get("submitted_at")
        if submitted_at:
            ticket["created_at"] = submitted_at
        return ticket

    async def submit(self, session_id: str, created_by: str = "", force: bool = False) -> dict:
        """生成工单并存库。

        force=True：收集轮数超限强制提单，只校验 project（required_fields 由
        _build_ticket 兜底），不再因 LLM 动态字段卡住。"""
        await self._ensure_clients()
        memory = await self._memory_manager.get_memory(session_id)
        agent_state = _load_agent_state(memory.metadata) or AgentState(session_id=session_id)

        # 兜底保护：直接调 API 绕过 _agent_think 时的基础校验
        can_submit, reason = _can_submit(agent_state)
        if not can_submit:
            raise ValueError(reason)
        # 若尚未决定所需字段，先决定（确保 backfill 有目标字段清单可用）
        if agent_state.required_fields is None:
            await self._decide_ticket_fields(session_id, agent_state, memory)
        # 提单关口回填：主对话 LLM 可能没把用户说过的字段写进 collected_info
        await self._backfill_collected_info(session_id, agent_state, memory)
        # 项目不在对话路径校验——用户在确认弹窗中搜索选择项目，_check_required_fields
        # 在 confirm_submit 时校验 project/project_id。submit() 只卡 required_fields。
        if not force:
            ready, missing = _assess_ticket_readiness(agent_state)
            if not ready:
                # 收集模式中用户补充完整后 LLM 会 submit → 自动生成草稿弹窗，
                # 不需要点按钮。文案按实际行为写。
                raise ValueError(f"工单信息不足，还差：{'、'.join(missing)}。在对话中补充后会自动为您生成工单。")

        ticket = await self._build_ticket(session_id, agent_state, memory,
                                          prefill_project=agent_state.pending_prefill_project)

        # 同一会话多次转单：ticket_seq 自增，确保 external_id 唯一（不同话题各自独立工单）
        agent_state.ticket_seq += 1
        ticket["ticket_seq"] = agent_state.ticket_seq

        # 对话记录截图附加（入库前：失败静默降级，不阻塞提单）
        await self._attach_chat_snapshot(ticket, memory)

        # ---- 存储到 tasks 表（source='ai'，按 (source, external_id) 幂等 upsert）----
        from ai.core.task_adapter import upsert_task
        record = upsert_task(ticket, created_by=created_by)
        db_id = record.id
        ticket["db_id"] = db_id
        logger.info(f"工单已入库: session_id={session_id}, db_id={db_id}, seq={agent_state.ticket_seq}, "
                    f"title={ticket.get('title', '')}, type={ticket.get('type', '')}")

        _reset_state_after_submit(agent_state, memory, ticket, db_id)
        await self._memory_manager.save_memory(memory)

        # ---- 加入待派单池 + 通知 Worker 立即派单 ----
        try:
            await self._memory_manager.add_pending_ticket(session_id)
            logger.info(f"工单已加入待派单池: session_id={session_id}, db_id={db_id}")
        except Exception as e:
            logger.warning(f"加入待派单池失败: session_id={session_id}, error={e}")
        try:
            await self._memory_manager.publish_new_ticket(db_id)
            logger.info(f"已发布派单事件: db_id={db_id}")
        except Exception as e:
            logger.warning(f"发布派单事件失败: db_id={db_id}, error={e}")

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
        """生成工单草稿（路径1：按钮转工单）。保底必填字段未收集齐时直接拦截，
        不生成草稿，返回 not_ready + 缺失项，引导用户回对话补充。"""
        await self._ensure_clients()
        memory = await self._memory_manager.get_memory(session_id)
        agent_state = _load_agent_state(memory.metadata) or AgentState(session_id=session_id)

        # 闭环保护：已提交的会话不允许重复提单
        can_submit, reason = _can_submit(agent_state)
        _log_ticket_state(agent_state, "prepare_ticket")
        if not can_submit:
            logger.info(f"[prepare] 重复提单拦截: phase={agent_state.phase}")
            return {"code": 1, "message": reason}

        # 已有待确认草稿（弹窗弹过被取消）→ 直接返回草稿重新弹窗，
        # 不重新 decide 判缺——工具循环弹窗前已收集齐字段，按钮只是让用户
        # 再看一眼草稿。重新 decide 会按新对话定新字段，与草稿内容不一致。
        _existing_draft = memory.metadata.get("ticket_draft")
        if _existing_draft:
            check = _check_required_fields(_existing_draft)
            _existing_draft["missing_fields"] = check["missing"]
            await self._memory_manager.save_memory(memory)
            logger.info(f"[prepare] 复用待确认草稿重新弹窗: session={session_id}")
            return {
                "stage": "draft_ready" if check["ok"] else "need_fields",
                "draft": _existing_draft,
                "missing_fields": check["missing"],
                "prompt": check["prompt"],
                "ticket_ready": True,
            }

        # 必填字段校验（required_fields，不含项目）——不足则不开弹窗，回对话补充。
        # 项目不在对话中收集：弹窗打开后用户搜索选择项目，未选项目前端禁止提交。
        # 首次转单：decide 决定字段清单。
        # ⚠️ 不回填（backfill）：backfill 从对话文本提取字段，会把用户的问题
        # 当答案幻觉填满（日志实锤：问「如何配置输送线」被回填成 specific_goal
        # 等 3 个字段 → 判定齐 → 直接弹窗，用户没答过任何字段却被告知不缺）。
        # 字段齐不齐只认主 LLM 在对话中真实收集的 collected_info。
        if agent_state.required_fields is None:
            await self._decide_ticket_fields(session_id, agent_state, memory)
        ready, missing = _assess_ticket_readiness(agent_state)
        if not ready:
            logger.info(f"[prepare] 信息不足拦截: session={session_id}, "
                        f"type={agent_state.ticket_type or '(未判定)'}, missing={missing}")
            # 写入对话 memory，让聊天区也出现追问（不只是 Toast）
            chat_msg = _missing_info_message(missing, via_button=True)
            memory.turns.append({"role": "assistant", "content": chat_msg})
            # 标记 ticket_collecting：告诉下一轮 LLM 切换到工单填写模式，停止诊断
            agent_state.ticket_collecting = missing
            _save_agent_state(memory, agent_state)
            await self._memory_manager.save_memory(memory)
            return {
                "code": 1,
                "stage": "not_ready",
                "missing_info": missing,
                "message": f"工单信息不足，还差：{'、'.join(missing)}。在对话中补充后会自动为您生成工单。",
            }

        ticket = await self._build_ticket(session_id, agent_state, memory,
                                          prefill_project=agent_state.pending_prefill_project)
        ticket["ticket_seq"] = agent_state.ticket_seq + 1
        check = _check_required_fields(ticket)
        ticket["missing_fields"] = check["missing"]
        memory.metadata["ticket_draft"] = ticket
        await self._memory_manager.save_memory(memory)

        logger.info(f"[prepare] session={session_id}, stage={'draft_ready' if check['ok'] else 'need_fields'}, "
                    f"ticket_ready=True, missing={check['missing']}")
        return {
            "stage": "draft_ready" if check["ok"] else "need_fields",
            "draft": ticket,
            "missing_fields": check["missing"],
            "prompt": check["prompt"],
            "ticket_ready": True,
        }

    async def confirm_submit(self, session_id: str, overrides: dict = None, created_by: str = "") -> dict:
        """确认提交工单（路径1：弹窗确认后），再次校验必填字段后入库。"""
        await self._ensure_clients()
        memory = await self._memory_manager.get_memory(session_id)
        draft = memory.metadata.get("ticket_draft")
        if not draft:
            return {"code": 1, "message": "没有待确认的工单草稿"}
        # 弹窗所见即所得：以 draft 为基准（弹窗展示的就是它），叠 overrides 后直接入库。
        # 不再二次调用 _build_ticket——LLM 随机性会让 v2 ≠ 弹窗展示的 v1，
        # 造成弹窗 / 提交后卡片 / 历史工单三处不一致。
        # overrides 应用到副本，不污染 memory 里的 draft（校验失败时还能重试）。
        ticket = dict(draft)
        if overrides:
            for k, v in overrides.items():
                if k in ("ticket_id", "missing_fields", "confirm_prompt", "stage"):
                    continue
                # deadline_at 允许空值（用户在弹窗里清除截止时间）；其余字段空值跳过
                if v or k == "deadline_at":
                    ticket[k] = v
        # 项目一致性护栏（预填引入）：project 名被 overrides 改成与草稿不同的值、
        # 而 overrides 未携带非空 project_id 时（双工单兜底名 project_id=''、
        # 直调 API 只传名），清掉草稿预填残留的旧 code——否则任务会以
        # 「名字是新项目、code 是预填旧项目」的错位绑定入库。
        # 下方 _resolve_project 命中时会重写两者；不命中则维持空（旧版行为）。
        _ov_project = str((overrides or {}).get("project") or "").strip()
        _ov_pid = str((overrides or {}).get("project_id") or "").strip()
        if _ov_project and not _ov_pid and _ov_project != str(draft.get("project") or "").strip():
            ticket["project_id"] = ""
        check = _check_required_fields(ticket)
        if not check["ok"]:
            return {"code": 1, "message": check["prompt"], "missing_fields": check["missing"]}

        agent_state = _load_agent_state(memory.metadata) or AgentState(session_id=session_id)
        # 服务端兜底：保底必填字段必须已在对话中收集（弹窗不承载这些字段，防直调 API 绕过）
        # ⚠️ 不回填（backfill）：同 prepare_ticket 的理由——backfill 幻觉填字段
        # 会让「用户没答过的字段」被判定为已收集。只认主 LLM 真实收集的 collected_info。
        if agent_state.required_fields is None:
            await self._decide_ticket_fields(session_id, agent_state, memory)
        ready, missing = _assess_ticket_readiness(agent_state)
        if not ready:
            logger.info(f"[confirm] 信息不足拦截: session={session_id}, missing={missing}")
            return {"code": 1, "stage": "not_ready", "missing_info": missing,
                    "message": f"工单信息不足，还差：{'、'.join(missing)}。在对话中补充后会自动为您生成工单。"}

        # 弹窗里选的项目 → 归一为项目库全名 + code（弹窗 ProjectSelect 已传全名，
        # 这里是防旧前端/直调 API 传简称的兜底）
        _final_project = ticket.get("project", "")
        if _final_project:
            match = await self._resolve_project(_final_project)
            if match:
                ticket["project"] = match.name
                ticket["project_id"] = match.code

        from ai.core.task_adapter import upsert_task
        ticket["ticket_seq"] = agent_state.ticket_seq + 1

        # 对话记录截图附加（入库前：失败静默降级，不阻塞提单）
        await self._attach_chat_snapshot(ticket, memory)

        record = upsert_task(ticket, created_by=created_by)

        agent_state.ticket_seq += 1
        _reset_state_after_submit(agent_state, memory, ticket, record.id)
        memory.metadata.pop("ticket_draft", None)
        await self._memory_manager.save_memory(memory)

        try:
            await self._memory_manager.add_pending_ticket(session_id)
        except Exception:
            pass
        try:
            await self._memory_manager.publish_new_ticket(record.id)
        except Exception:
            pass

        logger.info(f"[confirm] 工单已提交: session={session_id}, db_id={record.id}")
        return {"code": 0, "data": {"ticket": ticket, "db_id": record.id,
                                     "notice": "工单已生成并保存，等待自动派单。"}}

    async def collect_title(self, session_id: str) -> str:
        """等待该会话的后台标题任务落地并返回标题（SSE 生产者流结束后调用，
        用于向前端补发 event: title）。无任务/失败返回空串。"""
        _task = getattr(self, "_pending_title_tasks", {}).pop(session_id, None)
        if _task is None:
            return ""
        try:
            return await _task
        except Exception:
            return ""

    async def get_draft(self, session_id: str) -> dict:
        """获取待确认草稿（前端轮询兜底）。"""
        await self._ensure_clients()
        memory = await self._memory_manager.get_memory(session_id)
        draft = memory.metadata.get("ticket_draft")
        return {"code": 0, "data": {"draft": draft}} if draft else {"code": 0, "data": {"draft": None}}

    async def clear_draft(self, session_id: str) -> dict:
        """取消确认：关闭弹窗，**不代表放弃工单**。

        弹窗取消只是「这次不确认」——草稿与收集状态全部保留，用户可能还要在
        对话里补充信息（补充后重新出弹窗），或再点按钮重新确认。这里不写任何
        last_submitted_ticket 标记、不清草稿、不清 problem_summary。

        真正放弃 = 用户在对话里显式说「不转工单了/放弃/清除草稿」→ LLM 判取消
        （工具循环取消分支 / 旧状态机 ticket_cancel）→ 才清状态 + 写 cancelled 标记。"""
        await self._ensure_clients()
        return {"code": 0, "message": "已取消待确认工单"}

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

    def _format_conversation(self, memory, max_turns: int = 8, from_turn: int = 0,
                             sanitize_images: bool = False, boundary_prefix: str = "") -> str:
        """只取最近 N 条，避免长对话撑大 prompt。

        from_turn：从该 turn 索引开始（默认 0=全部）。诊断 prompt 传 context_start，
        让 LLM 只看提单后的新对话，防止它从旧对话重新提炼已提交的问题、绕过闭环保护。

        每条 turn 内容超过 _CONV_TURN_MAX_CHARS 时截断：图片描述等长文本
        （VLM 输出 ~3000 字）原样塞入会把 prompt 撑到 2 万+ 字符，
        思考型 LLM 首 token 延迟从 ~2s 飙到 13s+（图片+文字场景明显卡顿）。

        sanitize_images=True（提单链路：收集模式 / 生成工单 / 回填字段）：
        图片描述是 VLM 读截图的 UI 文本（工单类型/状态/处理人等），不是用户陈述，
        替换为占位符——LLM 字段提取/工单生成物理上看不到 UI 文本，
        杜绝把「缺陷」之类当项目名。工单本身带原图附件，不依赖描述。
        """
        turns = memory.turns[from_turn:]
        turns = turns[-max_turns:] if len(turns) > max_turns else turns
        # 新旧对话分界：锚点轮（上一张工单提交时的最后一轮）之后插分隔线。
        # 锚点不在当前切片（被 from_turn/max_turns 截掉）时不插——此时全是
        # 新对话，无需分界。
        _boundary_idx = -1
        if boundary_prefix:
            for _i in range(len(turns) - 1, -1, -1):
                if str(turns[_i].get("content") or "").strip().startswith(boundary_prefix):
                    _boundary_idx = _i
                    break
        _BOUNDARY_LINE = "───── 以上对话已随上一张工单提交归档；以下是新对话 ─────"
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
        if sanitize_images:
            _IMG_MARKER = "图片主要内容为"
            _sanitized = []
            _prev_is_image_user = False
            for t in turns:
                content = t.get("content") or ""
                _role = (t.get("role") or "user").lower()
                if _role == "user" and _IMG_MARKER in content:
                    _prev_is_image_user = True
                    _head = content.split(_IMG_MARKER, 1)[0].rstrip("：: \n")
                    _sanitized.append(f"用户：{_head}。[图片已附截图，仅展示用，不作为字段提取来源]")
                else:
                    # 图片上传后紧跟的 assistant 回执 = VLM 描述原文，同样屏蔽
                    if _role == "assistant" and _prev_is_image_user:
                        _prev_is_image_user = False
                        _sanitized.append("助手：[已确认收到图片]")
                    else:
                        _prev_is_image_user = False
                        _sanitized.append(f"{'用户' if _role == 'user' else '助手'}：{_truncate_turn(content)}")
            if _boundary_idx >= 0:
                _sanitized.insert(_boundary_idx + 1, _BOUNDARY_LINE)
            return "\n".join(_sanitized)
        _lines = [
            f"{'用户' if t['role'] == 'user' else '助手'}：{_truncate_turn(t['content'])}"
            for t in turns
        ]
        if _boundary_idx >= 0:
            _lines.insert(_boundary_idx + 1, _BOUNDARY_LINE)
        return "\n".join(_lines)

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
        ticket_intent = False
        ticket_cancel = False
        project_choice = ""
        message = text
        json_end = 0  # JSON 区域结束位置

        # ---- 尝试匹配带 ``` 包裹的 JSON ----
        m_fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
        # ---- 裸 JSON：用括号深度计数定位最外层 }，避免非贪婪正则被嵌套 {} 截断 ----
        _bare_end = -1
        if not m_fenced and text and text[0] == '{':
            depth = 0
            in_string = False
            escape = False
            for i, ch in enumerate(text):
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
                        _bare_end = i + 1
                        break

        json_str = None
        if m_fenced:
            json_str = m_fenced.group(1).strip()
            json_end = m_fenced.end()
        elif _bare_end > 0:
            json_str = text[:_bare_end].strip()
            json_end = _bare_end

        if json_str:
            try:
                data = _extract_json_object(raw)
                thinking = data.get("thinking", "")
                action = data.get("action", "ask").strip().lower()
                if action not in ("answer", "ask", "submit"):
                    action = "ask"
                intent = data.get("intent", "").strip().lower()
                state_update = data.get("state_update", {})
                # LLM 提单意图信号（服务端不用关键词，只信这两个布尔值）
                ticket_intent = data.get("ticket_intent", False)
                ticket_cancel = data.get("ticket_cancel", False)
                # 项目预填照抄值（老快路径 JSON 协议平级字段；工具循环走 tool_calls 参数）
                project_choice = data.get("project_choice", "") or ""
                if not isinstance(project_choice, str):
                    project_choice = ""
                if not isinstance(ticket_intent, bool):
                    ticket_intent = str(ticket_intent).lower() in ("true", "1")
                if not isinstance(ticket_cancel, bool):
                    ticket_cancel = str(ticket_cancel).lower() in ("true", "1")
            except (json.JSONDecodeError, Exception):
                logger.info(f"[parse] json.loads failed on extracted JSON: "
                            f"json_str={json_str[:120]!r}")

        # ---- 提取 JSON 之后的文本 ----
        after_json = text[json_end:] if json_end else text

        if json_str:
            after = re.sub(r"^```\s*", "", after_json).strip()
            if after:
                message = after

        message = message.lstrip("\n\r ")

        # 兜底清理：LLM 偶尔在 JSON 后多吐碎片（如 ,"key":"val"}、}} 等）
        _before_clean = message[:100]
        message = re.sub(
            r'^[，,}\]\s]+(?:"[^"]*"\s*:\s*[^,\s]+\s*\}*)?[,，\s]*', '', message).strip()
        if message != _before_clean.strip():
            logger.info(f"[parse] cleaned JSON fragment: "
                        f"before={_before_clean!r} after={message[:100]!r}")

        # 兜底：如果 message 仍然以 JSON 开头（无 === 且无后续文本），
        # 尝试剥掉裸 JSON 对象（同样用深度计数）
        if message and (message.startswith("{") or message.startswith("```")):
            # 先试带 ``` 包裹的
            cleaned = re.sub(r'```(?:json)?\s*\{[\s\S]*?\}\s*```', '', message).strip()
            # 再试裸 JSON（深度计数）
            if not cleaned or cleaned.startswith("{"):
                _msg_bare_end = -1
                if message[0] == '{':
                    _d, _s, _e = 0, False, False
                    for _i, _ch in enumerate(message):
                        if _e:
                            _e = False
                            continue
                        if _ch == '\\' and _s:
                            _e = True
                            continue
                        if _ch == '"':
                            _s = not _s
                            continue
                        if _s:
                            continue
                        if _ch == '{':
                            _d += 1
                        elif _ch == '}':
                            _d -= 1
                            if _d == 0:
                                _msg_bare_end = _i + 1
                                break
                    if _msg_bare_end > 0:
                        cleaned = message[_msg_bare_end:].strip()
            if cleaned:
                message = cleaned
            elif action == "submit":
                # submit 且整个 message 就是裸 JSON（无后续正文）：符合 prompt 的
                # 「message 留空」——置空，review 分支会回填「已生成工单草稿…」。
                # 不置空的话 JSON 文本会一路带进 _finalize_diagnosis 状态流转。
                message = ""
            else:
                # 剥不掉且非 submit：message 置空，交给下方「最终兜底」统一处理
                # （LLM 抽风输出纯 JSON 无正文时，宁可让系统补一句通用确认，
                # 也不把 JSON 文本当正文流给用户）。
                message = ""

        # 最终兜底：message 为空时给一个有意义的默认回复。
        # 例外：action=submit 时允许空 message——prompt 要求 submit 不写正文，
        # 系统随后展示「正在生成工单」动画 + 弹窗话术，这里不能再塞兜底文案。
        if (not message or not message.strip()) and action != "submit":
            logger.warning(f"[parse] 解析后 message 为空! raw前100字={text[:100]}")
            message = "已收到，已为你记录。"

        return {
            "thinking": thinking,
            "action": action,
            "intent": intent,
            "message": message,
            "state_update": state_update,
            "ticket_intent": ticket_intent,
            "ticket_cancel": ticket_cancel,
            "project_choice": project_choice,
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
        elif agent_state.phase in ("idle", "escalated") and not agent_state.problem_summary:
            # 全新话题——只记 original_query 供检索，problem_summary 留给 LLM 提炼
            agent_state.phase = "idle"
            agent_state.original_query = request.query
            _save_agent_state(memory, agent_state)
            await self._memory_manager.save_memory(memory)
        elif agent_state.phase == "resolved" and not agent_state.problem_summary:
            # 提单后/答完后新一轮：phase 转 diagnosing，但 problem_summary 保持空。
            # 不把 query 当 problem——否则裸"转工单"会伪造出新问题、绕过闭环保护。
            # 真正的新问题由本轮 LLM 在 _apply_state_update 中提炼。
            agent_state.phase = "diagnosing"
            agent_state.original_query = request.query
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
        logger.info(f"[stream] 开始流式推理: session={request.session_id}, query={request.query[:50]}, "
                    f"round={state.diagnosis_rounds}, turns={turn_count}, "
                    f"has_recent_image={has_image}, phase={state.phase}")
        memory = await self._memory_manager.add_turn(request.session_id, "user", request.query)

        # ---- 草稿存在 → 直接进工具循环，由 LLM 判断补充/取消/新问题 ----
        # 不写关键词表：用户这句话是「补充说明」还是「不想提单了」还是「新问题」，
        # 全部由 LLM 看完整上下文判断（工具循环的 system prompt 有对应规则）。
        # 服务端只做状态信号分流：有草稿且开关开启 → 工具循环分支。
        if (memory.metadata.get("ticket_draft")
                and os.getenv("AI_TICKET_TOOL_LOOP", "") == "1"):
            logger.info(f"[stream] 存在待确认草稿，直接走工具循环由 LLM 判断: session={request.session_id}")
            async for ev in self._ticket_tool_loop_branch(request, state, memory):
                yield ev
            return

        # 开关关闭时的兜底：草稿存在也回到统一的“本轮提取 + 固定清单校验”流程。
        if memory.metadata.get("ticket_draft") and state.required_fields is not None:
            logger.info(f"[stream] 待确认草稿存在，沿用固定清单收集流程: session={request.session_id}")
            if not state.ticket_collecting:
                # 只重新挂起「实际仍缺」的字段——不能无条件把 required_fields 的
                # 全部标签搬回来，否则已经在 collected_info 里满足的字段（草稿生成
                # 时就已确认过）会被当成"还没答"，导致 LLM 拿着错误的缺失清单
                # 反过来问用户已经回答过的问题（实测：发生时间已收集，草稿已生成，
                # 补充处理人时却被追问"还缺发生时间"）。
                _, _still_missing = _assess_ticket_readiness(state)
                state.ticket_collecting = _still_missing

        # 转工单意图由 LLM 判断（action=submit），不再用关键词预判。
        # 闭环保护（_can_submit 基于 last_submitted_ticket + 新问题）在 Step 2 执行——
        # 必须在 LLM 提炼 problem_summary 之后，这样同一轮里描述的新问题能被识别。
        state.diagnosis_rounds += 1
        state.phase = "diagnosing"

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


        # 收集模式取消短接改用 LLM 判断：ticket_cancel 由 LLM 每轮输出（见 DIAGNOSIS_PROMPT
        # 输出规范），服务端只信任这个布尔值，不再用关键词正则（关键词既僵化又漏判）。
        # 取消处理放在 LLM 解析之后（parse 之后单独处理）。

        # ---- 闲聊/问候短接：纯问候/打招呼 → 跳过检索，避免 4s+ 的 reranker 检索 ----
        _greet_str = re.sub(r"[，。.!！\s]", "", request.query.strip())
        _is_greeting = bool(_greet_str) and re.fullmatch(
            r"(你好|您好|哈喽|嗨|hello|hi|hey|在吗|早上好|上午好|中午好|下午好|晚上好|hello|hi|嗨)+",
            _greet_str, re.IGNORECASE
        )

        # 指代消解已移除：正则+原文拼接产生的检索词是语义稀汤（实测 rxx 追问全部失灵）。
        # 省略式追问改由 _retrieve_inner 里的 _rewrite_query 带 context_turns 用 LLM 补全。

        # ---- 工具循环收集中：后续轮直接进工具循环，跳过意图分类/检索 ----
        # 用户在提单收集中的回答（如「XSP122」「上午九点四十」）是短句，
        # 意图分类会误判 diagnosis → 掉回旧状态机（日志实锤：15.5s 思考 + 旧收集模式）。
        if state.tool_loop_active and os.getenv("AI_TICKET_TOOL_LOOP", "") == "1":
            logger.info(f"[stream] 工具循环收集中，直接续接: session={request.session_id}")
            async for ev in self._ticket_tool_loop_branch(request, state, memory):
                yield ev
            return

        # ---- 诊断路径 ----
        # 立刻发状态，别让用户干等
        yield {"event": "status", "data": {"stage": "retrieving", "round": state.diagnosis_rounds}}

        # 待补充字段只在本轮确认提单意图后生成；普通诊断阶段不提前调用
        # _compute_ticket_fields，避免一次咨询产生隐藏的字段预测请求。
        t_ret = time.perf_counter()
        logger.info(f"[stream] 开始检索: session={request.session_id}")
        # 工单填写模式不需要知识库——用户只是在填表字段，不走诊断检索；
        # 跳过检索可大幅缩小 prompt，降低 thinking 长度，提升收集轮响应速度。
        _skip_retrieval = request.skip_retrieval or state.ticket_collecting
        if _skip_retrieval:
            reference_docs = "（跳过检索）"
            t_stream["intent"] = 0
        elif _is_greeting:
            # 正则 0ms 快路径：白名单纯问候不触发意图调用（省 1 次 LLM 调用），直接跳过检索
            reference_docs = ""
            t_stream["intent"] = 0
        else:
            # 白名单之外的输入（辛苦/哈哈/客套等）→ 意图识别与检索并发：
            # 意图用独立的轻量无思考模型（默认 deepseek-v4-flash ~0.5s），
            # 不跟随主 LLM_BACKEND——主后端切重模型后意图不能一起变慢。
            _intent_llm = await get_intent_client()
            _retrieval_task = asyncio.create_task(
                self._retrieve_with_context(request.session_id, state,
                                            context_turns=memory.turns[-4:],
                                            query_override=request.query))
            _intent_task = asyncio.create_task(
                self._classify_intent(
                    _intent_llm, request.query, "",
                    context_turns=memory.turns[-4:]))
            _intent_t0 = time.perf_counter()
            try:
                # 超时上限 6s：意图走独立 deepseek 客户端，本地/弱网路径建连+请求可达
                # 3-4s（实测 intent_ms=4002 撞 4s 上限被强制判 diagnosis → 提单轮
                # 整个会话走偏）。6s 给足余量；意图与检索并发，超时兜底仍是 diagnosis。
                _intent = await asyncio.wait_for(_intent_task, timeout=6.0)
            except (asyncio.TimeoutError, Exception):
                _intent = "diagnosis"
            t_stream["intent"] = round((time.perf_counter() - _intent_t0) * 1000)
            logger.info(f"[stream] 意图={_intent} intent_ms={t_stream['intent']}")

            # diagnosis_nokb = 诊断意图但本轮无需知识库（续接轮/通用对话）。
            # 归一成 diagnosis + 独立 _needs_kb 旗标：下面的工具循环判断、
            # courtesy 回落等分支结构都不用重复写。
            _needs_kb = True
            if _intent == "diagnosis_nokb":
                _intent = "diagnosis"
                _needs_kb = False

            if _intent == "ticket":
                # 提单意图 → 取消检索。
                reference_docs = "（提单轮跳过检索）"
                logger.info(f"[stream] 意图判提单，取消检索: session={request.session_id}")
                self._cancel_retrieval(_retrieval_task)
                if os.getenv("AI_TICKET_TOOL_LOOP", "") == "1":
                    logger.info(f"[stream] 工具循环开关开启，走 submit_ticket 工具: session={request.session_id}")
                    async for ev in self._ticket_tool_loop_branch(request, state, memory):
                        yield ev
                    return
                state.ticket_fast_lane = True
            elif _intent == "diagnosis" and os.getenv("AI_DIAGNOSIS_TOOL_LOOP", "") == "1":
                # 诊断意图 → 走诊断工具循环（search_kb + submit_ticket）。
                # LLM 自主决定：查不查知识库、查什么、查几次，再生成回答；
                # 也可顺势提单（submit_ticket 也在工具列表里）。
                # 保留 thinking（诊断需要深度推理）；取消后台检索（工具循环里 LLM 自己查）。
                self._cancel_retrieval(_retrieval_task)
                logger.info(f"[stream] 诊断工具循环开关开启，走 search_kb + submit_ticket: session={request.session_id}")
                async for ev in self._diagnosis_tool_loop_branch(request, state, memory):
                    yield ev
                return
            elif _intent == "diagnosis":
                # 草稿挂起时的守卫：补充说明（如「项目是XX」）常被 flash 误判成
                # diagnosis——诊断单轮分支没有草稿处理能力，LLM 会在正文里谎称
                # 「已记到工单上」而草稿根本没动。与 courtesy 的取消话术守卫同理：
                # 有待确认草稿时不进单轮分支，回落主循环（草稿轮铁律在那）。
                _has_draft = bool(memory.metadata.get("ticket_draft"))
                if not _needs_kb and not _has_draft:
                    # 诊断但无需知识库（续接轮/通用对话）：取消并发检索，
                    # 单轮分支自带最近 8 轮对话 + 省略式追问承接规则，
                    # 靠上文即可作答，省下 rerank 等检索尾延。
                    reference_docs = ""
                    logger.info(f"[stream] 意图判 diagnosis_nokb，取消检索直接单轮: session={request.session_id}")
                    self._cancel_retrieval(_retrieval_task)
                    async for ev in self._diagnosis_oneshot_branch(request, state, memory, reference_docs):
                        yield ev
                    return
                # 诊断单轮：等并发检索结果 → 小 prompt 1 次 LLM 直接回答（无工具往返）
                try:
                    reference_docs = await asyncio.wait_for(_retrieval_task, timeout=20.0)
                except asyncio.TimeoutError:
                    reference_docs = ""
                    logger.warning(f"[stream] 检索超时(20s)，降级无上下文: session={request.session_id}")
                if _has_draft:
                    logger.info(f"[stream] 草稿存在，诊断意图回落主循环（防补充说明掉进无草稿能力的单轮分支）: "
                                f"session={request.session_id}")
                else:
                    logger.info(f"[stream] 诊断走单轮分支（服务端检索+1次LLM）: session={request.session_id}")
                    async for ev in self._diagnosis_oneshot_branch(request, state, memory, reference_docs):
                        yield ev
                    return
            elif _intent == "courtesy":
                # 草稿存在时的取消话术（「算了 不提了」）常被判成 courtesy——
                # 单轮闲聊分支没有 ticket_cancel 处理，LLM 会在正文里谎称
                # 「已取消草稿」而草稿根本没删。守卫：有待确认草稿时不走闲聊
                # 分支，回落主循环（草稿轮铁律在那，取消/补充由 LLM 结构化判定）。
                if memory.metadata.get("ticket_draft"):
                    logger.info(f"[stream] 草稿存在，闲聊意图回落主循环（防取消话术掉进无取消能力的单轮分支）: "
                                f"session={request.session_id}")
                    try:
                        reference_docs = await asyncio.wait_for(_retrieval_task, timeout=20.0)
                    except asyncio.TimeoutError:
                        reference_docs = ""
                        logger.warning(f"[stream] 检索超时(20s)，降级无上下文: session={request.session_id}")
                else:
                    # 意图判闲聊 → 停掉还在跑的检索（rerank 等 await 点立刻取消，thread pool 尾随可接受）
                    reference_docs = ""
                    logger.info(f"[stream] 意图判闲聊，取消检索: session={request.session_id}")
                    self._cancel_retrieval(_retrieval_task)
                    if os.getenv("AI_DIAGNOSIS_TOOL_LOOP", "") == "1":
                        # 闲聊也走小 prompt 工具循环：7200 字大 prompt 对一句
                        # 「谢谢/哈哈」纯属浪费，循环无工具调用时直接输出回答。
                        logger.info(f"[stream] 闲聊走工具循环（小 prompt）: session={request.session_id}")
                        async for ev in self._diagnosis_tool_loop_branch(request, state, memory):
                            yield ev
                        return
                    logger.info(f"[stream] 闲聊走单轮小 prompt: session={request.session_id}")
                    async for ev in self._diagnosis_oneshot_branch(request, state, memory, ""):
                        yield ev
                    return
            else:
                # 兜底（意图识别失败按 diagnosis 处理）：等检索 → 单轮分支
                try:
                    reference_docs = await asyncio.wait_for(_retrieval_task, timeout=20.0)
                except asyncio.TimeoutError:
                    reference_docs = ""
                    logger.warning(f"[stream] 检索超时(20s)，降级无上下文: session={request.session_id}")
                logger.info(f"[stream] 意图兜底走单轮分支: session={request.session_id}")
                async for ev in self._diagnosis_oneshot_branch(request, state, memory, reference_docs):
                    yield ev
                return

        t_stream["retrieve"] = round((time.perf_counter() - t_ret) * 1000)
        logger.info(f"[stream] 检索完成: {t_stream['retrieve']}ms, docs_len={len(reference_docs)}"
                    + ("（闲聊跳过检索）" if _is_greeting else ""))

        # 纯问候 → 单轮小 prompt 分支（无检索文档、无工具往返，1 次 LLM）。
        # 问候轮跳过意图分类后不能落 7200 字大 prompt——一句「你好」不值得。
        if _is_greeting:
            logger.info(f"[stream] 纯问候走单轮小 prompt: session={request.session_id}")
            async for ev in self._diagnosis_oneshot_branch(request, state, memory, ""):
                yield ev
            return

        # 项目预填取数：仅提单上下文轮拉用户名下项目（普通问答轮零开销）。
        # 快路径 + 收集轮 + 草稿轮都要注入列表，LLM 照抄 project_choice 由
        # 服务端严格校验后走 pending_prefill_project 单向管道（防鬼打墙铁律）。
        _user_projects = None
        if state.ticket_fast_lane or state.ticket_collecting or memory.metadata.get("ticket_draft"):
            try:
                _user_projects = await self._get_user_projects(request.created_by)
            except Exception as e:
                logger.warning(f"[stream] 拉取用户项目失败，跳过预填: {e}")

        prompt = self._build_diagnosis_prompt(state, memory, reference_docs, user_projects=_user_projects)
        # 草稿存在的补充/取消轮：强制注入字段写入与取消规则。实测 gpt 系模型会
        # 在正文里说「已记录处理人」却不写 state_update.collected_info → 服务端判
        # 「无新增」→ 补充完成后不自动重新弹窗;说「已清空草稿」却不置 ticket_cancel
        # → 草稿根本没删,按钮仍弹旧草稿。注入后让模型把意图结构化落字段。
        if memory.metadata.get("ticket_draft"):
            prompt += (
                "\n\n【草稿轮铁律】当前存在待确认的工单草稿，本轮用户消息是对草稿的"
                "补充、修改或取消，你必须按类型结构化输出：\n"
                "1. 补充/修改（如「提单给XX」「补充一下XX」）→ 把补充内容写入"
                " state_update.collected_info（指名处理人写入 requested_assignee），"
                "再输出 action=answer 简短确认。只写正文不写字段会被系统判定为没有补充。\n"
                "2. 取消/删除（如「不提单了」「把草稿删了」「算了不转了」）→ 必须输出"
                " ticket_cancel=true，只回复「好的，不转工单。有什么其他问题随时问我。」"
                "——绝不能在正文里假装「已清空草稿」，服务端只有看到 ticket_cancel=true"
                "才会真正删除草稿。"
            )
            if _user_projects:
                _proj_lines = "\n".join(f"- {p['name']}（编号: {p['code']}）" for p in _user_projects)
                prompt += (
                    "\n3. 🔴 用户名下项目列表（仅这些可选）：\n" + _proj_lines + "\n"
                    "用户本轮明确说要给其中某个项目提单/换项目时，把该项目名称从上面列表"
                    "**原样照抄**进输出 JSON 的 project_choice 字段（与 action 平级）；"
                    "没提到或对不上就留空字符串。绝不追问项目名称、不要在正文里播报项目情况。\n"
                )
        t_stream["prompt_chars"] = len(prompt)
        logger.info(f"[stream] prompt构建完成: {t_stream['prompt_chars']} chars, retrieve={t_stream['retrieve']}ms")

        yield {"event": "status", "data": {"stage": "analyzing", "round": state.diagnosis_rounds}}

        t_llm = time.perf_counter()
        t_stream["overhead_before_llm"] = round((t_llm - t0) * 1000)
        raw_tokens: list[str] = []
        t_first_llm = None
        _buf = ""          # 累积缓冲区，用于检测 JSON→消息边界
        _json_done = False # True 表示已越过 JSON 区域
        _msg_yielded = False   # 是否已向用户流出消息正文（末尾兜底输出用）
        # 收集模式/已有草稿的回复延迟到结构化结果处理后再输出：
        # 用户说“取消提单”时，LLM 正文和服务端取消话术可能相同，若先流出 LLM 正文，
        # 后端随后又输出系统话术，前端就会看到两遍“好的，不转工单”。
        # 延迟这一类消息到下方统一分支，由服务端只输出一次最终话术。
        _suppress_msg = bool(state.ticket_collecting or memory.metadata.get("ticket_draft"))
        _msg_buf: list[str] = []  # 缓冲短消息（如 submit 的"好的"），超阈值再流式输出
        _MSG_BUF_FLUSH = 20       # 超过此字符数才流式，避免短消息先出去再卡等后续处理
        _generating_sent = False  # generating_ticket 状态是否已发（流式阶段 JSON 就位时发；submit 块兜底补发，防重复）
        def _flush_msg_buf():
            """将缓冲的消息 token 一次性流式输出"""
            nonlocal _msg_yielded
            for t in _msg_buf:
                _msg_yielded = True
                yield {"event": "token", "data": t}
            _msg_buf.clear()
        # 流式调用，如果没有 stream 方法则回退到 complete()
        # 提单快路径（ticket_fast_lane）：prompt 已在 _build_diagnosis_prompt 换成精简版
        # （不带知识库/人设/诊断规则，~2 千字）。thinking 保留，输入短了首 token 也快。
        _stream = getattr(self._llm_client, "stream", None)
        try:
            if _stream is None:
                # 非流式 LLM，用 complete() + 逐字输出模拟
                # 提单快路径：thinking 保持开启（中间档）——prompt 已精简到 ~600 字符，
                # 开 thinking 也只有 3-5s，换来入口轮字段判断质量不下降。
                raw = await self._llm_client.complete(
                    prompt=prompt, max_tokens=8000, temperature=0.5,
                    # 提单/字段收集只做结构化字段提取，不需要深度思考；
                    # 关闭 thinking 可明显降低首 token 等待时间。
                    thinking=False if (state.ticket_fast_lane or state.ticket_collecting
                                       or os.getenv("AI_DIAGNOSIS_THINKING", "1") == "0") else None)
                if t_first_llm is None:
                    t_first_llm = time.perf_counter()
                    t_stream["llm_first_token"] = round((t_first_llm - t_llm) * 1000)
                # 拆出 JSON 区域和消息区域，只把消息正文送入 _msg_buf（节流输出）
                _msg_start = _find_json_end(raw)
                if _msg_start >= 0:
                    _suppress_msg = False  # _suppress_doomed_submit 未实现；submit 覆盖由前端 status 清空 acc 处理
                    msg_body = raw[_msg_start:]
                else:
                    # JSON 未闭合（max_tokens 截断/格式异常）：不输出残破 JSON，
                    # 交由 _parse_agent_output 兜底提取正文或给默认回复
                    msg_body = ''
                raw_tokens.append(raw)  # 完整 raw 供 _parse_agent_output 解析
                if not _suppress_msg and msg_body:
                    for ch in msg_body:
                        _msg_yielded = True
                        yield {"event": "token", "data": ch}
            else:
                # 提单快路径：thinking 保持开启（中间档）——prompt 已精简到 ~600 字符，
                # 开 thinking 也只有 3-5s（实测 591 字符 llm_first 5s），
                # 换来入口轮字段判断质量不下降。
                async for token in _stream(
                        prompt=prompt, max_tokens=8000, temperature=0.5,
                        # 提单/字段收集仅需结构化提取，关闭 thinking，避免用户长时间等待。
                        thinking=False if (state.ticket_fast_lane or state.ticket_collecting
                                       or os.getenv("AI_DIAGNOSIS_THINKING", "1") == "0") else None):
                    raw_tokens.append(token)
                    if not _json_done:
                        _buf += token
                        msg_start = _find_json_end(_buf)
                        if msg_start >= 0:
                            _json_done = True
                            # JSON 完整 → 就地解析 action。submit 时立刻发「正在生成工单」
                            # 状态并抑制正文 token——否则 LLM 的「好的」会先流到前端、
                            # generating_ticket 状态后到，用户看到「好的」闪现再切动画。
                            _is_submit_action = False
                            try:
                                # _buf[:msg_start] 是完整 JSON（裸或 fenced），剥掉围栏后解析
                                _json_part = re.sub(r"^```(?:json)?\s*", "", _buf[:msg_start])
                                _json_part = re.sub(r"```\s*$", "", _json_part).strip()
                                _pd = json.loads(_json_part)
                                _is_submit_action = (_pd.get("action") or "").strip().lower() == "submit"
                            except Exception:
                                _is_submit_action = False
                            if _is_submit_action:
                                # submit：只吞正文，不发「生成工单中」动画。
                                # 服务端就绪门槛可能把 submit 打回 ask（required_fields 未齐），
                                # 若这里乐观发动画，用户会看到「生成工单中」→ 两秒后又开始问信息。
                                # 动画统一推迟到 submit 块（就绪判定通过后、真正 build_ticket 前）发。
                                _suppress_msg = True
                                # 不 yield tail，后续 token 也全部吞掉（raw_tokens 照常累积供最终解析）
                                continue
                            tail = _buf[msg_start:]
                            # 严格流式：token 直接 yield，不进 _msg_buf 缓冲（避免短消息积攒到流结束才一次性吐→"突然一大片"）
                            if tail and not _suppress_msg:
                                if t_first_llm is None:
                                    t_first_llm = time.perf_counter()
                                    t_stream["llm_first_token"] = round((t_first_llm - t_llm) * 1000)
                                _msg_yielded = True
                                yield {"event": "token", "data": tail}
                    else:
                        if not _suppress_msg:
                            if t_first_llm is None:
                                t_first_llm = time.perf_counter()
                                t_stream["llm_first_token"] = round((t_first_llm - t_llm) * 1000)
                            _msg_yielded = True
                            yield {"event": "token", "data": token}
        except (AITimeoutError, ServiceUnavailableError, Exception) as e:
            logger.error(
                f"[stream] LLM流式调用失败: type={type(e).__name__}, "
                f"session={request.session_id}, round={state.diagnosis_rounds}, "
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
        # 用于区分“仅重复确认已有草稿”和“用户补充了新信息”。
        # LLM 可能在补充轮复述旧字段，只有值实际变化时才允许重建草稿并再次 review。
        _collected_before_turn = dict(state.collected_info or {})
        _su = parsed.get('state_update')
        _su_keys = list(_su.keys()) if isinstance(_su, dict) else []
        logger.info(f"[stream] LLM parsed: action={parsed['action']} intent={parsed.get('intent','?')} "
                    f"state_update_keys={_su_keys} "
                    f"msg_preview={parsed.get('message','')[:150]!r}")

        # 项目预填（单向管道）：LLM 从注入列表照抄的 project_choice 只接受精确匹配。
        # 抄不齐 = 幻觉信号 → 置空走弹窗。宁空勿错，不做模糊容错。
        if _user_projects and str(parsed.get("project_choice") or "").strip():
            _pf = self._match_project_choice(str(parsed.get("project_choice")), _user_projects)
            if _pf:
                state.pending_prefill_project = _pf
                logger.info(f"[stream] 项目预填命中: {_pf['name']}({_pf['code']})")
            else:
                logger.warning(f"[stream] project_choice 未命中用户项目列表，忽略: "
                               f"{str(parsed.get('project_choice'))[:50]!r}")

        # 非 submit：立即 flush 缓冲的消息 token（诊断长消息已超阈值流式输出过了，
        # 这里只 flush 短消息或 complete() 模式下的残余缓冲）
        if parsed["action"] != "submit":
            for ev in _flush_msg_buf():
                yield ev

        # ---- Step 1: 先应用 LLM 提炼的 state_update（含 problem_summary），
        #     让 _can_submit 基于 LLM 判断后的有效问题描述做决策 ----
        self._apply_state_update(state, parsed["state_update"])
        _has_new_supplement = (
            bool(state.collected_info)
            and state.collected_info != _collected_before_turn
        )

        # ---- 首次提单意图生成字段清单：只调用一次并锁定 ----
        # 普通诊断阶段不预取；required_fields 非 None 后，后续轮次只提取并校验。
        _ticket_intent = (state.ticket_collecting
                          or parsed["action"] == "submit"
                          or parsed.get("ticket_intent", False))
        if (_ticket_intent and state.required_fields is None
                and parsed["action"] in ("ask", "submit")):
            await self._decide_ticket_fields(request.session_id, state, memory)
            logger.info(f"[stream] 首次提单意图生成字段清单: session={request.session_id}")

        # ---- Step 2: 闭环保护（基于 last_submitted_ticket + 新 problem）----
        # 在 LLM 提炼 problem_summary 之后判断：刚提完单且无新问题 → 拦截重复提单。
        _can, _reason = _can_submit(state)

        # ---- LLM 输出 action=submit → 受闭环保护 ----
        if parsed["action"] == "submit" and not _can:
            parsed["action"] = "answer"
            parsed["message"] = _reason
            logger.info(f"[stream] LLM submit 被闭环拦截")

        # 注：不再有服务端字段兜底触发提单。完全信任 LLM 的 ticket_ready / action=submit
        # 判断（实测多轮流程下 LLM 自己会 submit）。服务端只守闭环 + 收集轮次上限。

        # ---- 工单填写模式：取消 / 计数 + 字段齐/超限 → 提单 ----
        _force_submit = False  # 收集超限强制提单：跳过剩余字段校验，弹窗里用户仍可补齐
        _has_pending_ticket = bool(
            state.ticket_collecting or memory.metadata.get("ticket_draft")
        )
        if _has_pending_ticket and parsed.get("ticket_cancel", False):
            # 用户明确不提单（LLM 判 ticket_cancel=true）→ 退出收集模式，回到正常诊断。
            # 弹窗关闭不会走这里；只有 LLM 结构化判断为取消才清理。
            logger.info(f"[stream] LLM 判用户取消提单，退出收集模式: query={request.query[:40]}")
            _cancelled_topic = state.problem_summary or ""
            state.ticket_collecting = []
            state.collect_rounds = 0
            state.ticket_ready = False
            state.required_fields = None
            state.collected_info = {}
            state.pending_prefill_project = None
            state.problem_summary = ""
            state.ticket_type = ""
            memory.metadata.pop("ticket_draft", None)
            # 取消标记写入 last_submitted_ticket：拦截「取消后立刻再点转工单按钮」。
            if not state.last_submitted_ticket:
                state.last_submitted_ticket = {
                    "ticket_id": "cancelled",
                    "title": "取消的草稿",
                    "topic": _cancelled_topic,
                    "submitted_at": int(time.time()),
                }
            _save_agent_state(memory, state)
            await self._memory_manager.save_memory(memory)
            parsed["action"] = "answer"
            parsed["message"] = "好的，不转工单。有什么其他问题随时问我。"
            _msg_buf.clear()
            _msg_yielded = True
            yield {"event": "status", "data": {"stage": "collect_cancel"}}
            yield {"event": "token", "data": parsed["message"]}
        elif _has_pending_ticket:
            # 计数与超限强制提单只对「正在收集」的轮生效。草稿已生成的补充轮
            # （弹窗关闭后补充信息/说项目名）收集早已完成，不存在「收集超限」。
            # 0824 生产事故：草稿生成时 collect_rounds 已计到上限且未复位，下一轮
            # 「项目是摇人吧项目」+1 被判超限强制提单，用户看到莫名其妙的
            # 「信息收集超限」话术，LLM 的自然回复也被吞掉。
            if state.ticket_collecting:
                state.collect_rounds += 1
            # 弹窗关闭后的补充轮：如果本轮确实新增字段且固定清单已齐，
            # 即使 LLM 只输出了 ask，也自动进入 review，避免补充完成后只回话不弹窗。
            # ⚠️ 条件用 _has_pending_ticket 而非 state.ticket_collecting：草稿存在时
            # 字段往往已齐，ticket_collecting 被清空为 []（falsy），若只看它，
            # 弹窗取消后再补充指定接单人/备注这类非必填信息就永远触发不了自动 review。
            _supplement_ready, _supplement_missing = _assess_ticket_readiness(state)
            # 本轮新拿到项目预填（如「项目是摇人吧」）且草稿已存在 → 重建草稿
            # 重发弹窗，让预填结果对用户可见。预填管道是单向的，只在提单
            # submit 时消费（_build_ticket），answer 轮不消费就会一直挂着。
            _pf_fresh = (state.pending_prefill_project is not None
                         and bool(memory.metadata.get("ticket_draft")))
            if (((_has_new_supplement and _supplement_ready) or _pf_fresh)
                    and not parsed.get("ticket_cancel", False)):
                parsed["action"] = "submit"
                logger.info(f"[stream] 补充字段已齐/项目预填，自动进入 review: session={request.session_id}")
            if parsed["action"] == "submit":
                # LLM 自己判断字段齐了：回填与就绪判定统一交给下方「提单就绪门槛」。
                # 这里不再单独回填/自动提单——此前 backfill 会把助手刚问的话当答案
                # 幻觉填字段 → 判定假齐 → 用户还没回答就提前弹窗。
                pass
            elif state.ticket_collecting:
                # LLM 还在 ask：不回填（backfill 会把提问里的词当答案），
                # 只刷新缺失清单；轮数超限仍强制弹窗（防鬼打墙）。
                _, _tc_missing = _assess_ticket_readiness(state)
                if state.collect_rounds >= _MAX_COLLECT_ROUNDS:
                    _log_ticket_state(state, "collect_rounds_exceeded_force_submit", missing=_tc_missing)
                    logger.info(f"[stream] 收集轮数超限({state.collect_rounds})，强制提单: missing={_tc_missing}")
                    state.ticket_collecting = []
                    _force_submit = True
                    parsed["action"] = "submit"
                elif _tc_missing:
                    state.ticket_collecting = _tc_missing

        # ---- 提单就绪门槛：LLM 决定的 required_fields 全非空 ----
        #  放在 phase 转换之前：action 改 ask 后 phase 不会被置为 escalated
        if parsed["action"] == "submit" and not _force_submit:
            # 首次转单：专门调一次 LLM 决定要补哪 2-3 个字段（锁进 required_fields）
            if state.required_fields is None:
                await self._decide_ticket_fields(request.session_id, state, memory)
            # 收集模式已经在每轮结构化提取并合并 collected_info；
            # 这里不要再调用 _backfill_collected_info（会额外发起一次 LLM 请求，
            # 也可能把助手上一轮的追问内容误当成用户答案），直接按固定清单校验。
            _as_ready, _as_missing = _assess_ticket_readiness(state)
            if not _as_ready:
                _log_ticket_state(state, "submit_blocked_not_ready", missing=_as_missing)
                logger.info(f"[stream] 提单拦截(字段未齐): missing={_as_missing}")
                parsed["action"] = "ask"
                parsed["message"] = _missing_info_message(_as_missing)
                state.ticket_collecting = _as_missing  # 进入工单填写模式，聚焦收集缺失字段
                yield {"event": "status", "data": {"stage": "need_info", "missing_info": _as_missing}}
                # LLM 喊 submit 但被拦截 → 丢弃缓冲（可能含 JSON 残片如 ,"message":"好的"}），
                # 直接用系统追问话术，避免 JSON 碎片漏到前端。
                _msg_buf.clear()
                _msg_yielded = True  # 抑制末尾兜底输出
                yield {"event": "token", "data": parsed["message"]}

        # ---- 提前进入收集模式：LLM 判提单意图 + 说 ask + 存在必填缺项 ----
        #  提单意图由 LLM 输出 ticket_intent 判定（服务端不猜关键词）。
        #  普通咨询里 LLM 也会 ask（正常追问）——不加意图门槛会把所有多轮咨询
        #  都拖进收集模式。
        #  ⚠️ 这里不做 backfill：LLM 刚问完字段，此时回填会把提问里的词当答案
        #  （如问「潜伏车还是叉车」时把「潜伏车」提取成 robot_type），missing 判空、
        #  进不了收集模式，下一轮走完整诊断拖 10s+。收集模式下（用户已答完）才回填。
        #  ⚠️ 也不做 ask→submit 转换：LLM 的追问判断优先，服务端不抢话。
        if (parsed["action"] == "ask" and not state.ticket_collecting
                and parsed.get("ticket_intent", False)):
            _, _tc_missing = _assess_ticket_readiness(state)
            if _tc_missing:
                state.ticket_collecting = _tc_missing
                logger.info(f"[stream] 提前进入收集模式(ask→collect): missing={_tc_missing}")

        # ---- Step 3: 应用 action → phase 转换 ----
        self._apply_action_phase(state, parsed["action"])

        # ---- 提单执行：LLM 输出 action=submit 时直接提单 ----
        # 服务端做最终校验（project 必填、closed-loop 拦截），不做额外兜底覆盖 LLM 判断
        ticket_data = None
        if parsed["action"] == "submit":
            _log_ticket_state(state, "llm_action_submit")
            # 「生成工单中」动画在这里发（就绪门槛已通过、即将 build_ticket 前）。
            # 流式阶段只吞正文不发动画——否则 LLM 的 submit 若被就绪门槛打回 ask，
            # 用户会看到「生成工单中」闪现后又被问信息。
            if not _generating_sent:
                _generating_sent = True
                yield {"event": "status", "data": {"stage": "generating_ticket"}}
            # 先把本轮 state（含 LLM 提炼的 problem_summary/collected_info）落盘，
            # 否则 submit() 从 memory 重新加载会拿到旧 state，闭环判定与 stream 不一致。
            _save_agent_state(memory, state)
            await self._memory_manager.save_memory(memory)
            try:
                draft = await self._build_ticket(request.session_id, state, memory,
                                                 prefill_project=state.pending_prefill_project)
                # check 只用于给前端弹窗展示 missing_fields（project 未选时弹窗内提示必选），
                # 对话路径不做拦截：required_fields 已在上方「提单就绪门槛」校验过（不齐已改 ask），
                # project 由用户在弹窗里选择——不能在对话里提示「请先在弹窗选项目」。
                check = _check_required_fields(draft)
                # 字段齐全 → 不自动提单，弹窗让用户核对/修改后确认
                # 幂等：上一轮已发 review 未确认（ticket_draft 已存在）→ 不重复发 review，只提示
                existing_draft = memory.metadata.get("ticket_draft")
                _has_new_supplement = (
                    bool(state.collected_info)
                    and state.collected_info != _collected_before_turn
                )
                # 只要本轮明确进入 submit，就统一用最新累计状态重建草稿并发送 review。
                # 旧逻辑在 existing_draft 且未识别出新增字段时只发 token、不发 review；
                # 但前面已经发出 generating_ticket，前端会永久停留在「正在生成工单」。
                # 同时，补充字段可能因 LLM key 归一化/旧草稿状态被误判为未新增，
                # 因此不能用 _has_new_supplement 决定是否弹窗。
                # 弹窗已打开时前端自身幂等保护，不会覆盖用户正在编辑的 overrides。
                if existing_draft and not _has_new_supplement:
                    logger.info(f"[stream] 已有待确认草稿，本轮 submit 重新发送 review: session={request.session_id}")
                else:
                    logger.info(f"[stream] 字段齐全，弹窗确认: session={request.session_id}, force={_force_submit}, supplement={_has_new_supplement}")
                memory.metadata["ticket_draft"] = draft
                state.phase = "diagnosing"
                state.ticket_collecting = []
                # 收集周期结束：计数器归零（否则草稿后的补充轮沿用旧计数，
                # 立刻触发超限强制提单）；预填已在上面 build_ticket 消费，
                # 清空单向管道，防止陈旧预填在后续轮重复触发自动 review。
                state.collect_rounds = 0
                state.pending_prefill_project = None
                _save_agent_state(memory, state)
                await self._memory_manager.save_memory(memory)
                yield {"event": "status", "data": {
                    "stage": "review",
                    "draft": draft,
                    "missing_fields": check["missing"],
                    "force_submit": _force_submit,
                }}
                # 由于不在这里提单，parsed action 改回 answer（避免 _finalize_diagnosis
                # 以 escalated 追加 system turn 污染对话），同时不调 submit() 清空状态。
                parsed["action"] = "answer"
                # 生成完成：弹窗 + 对话气泡回填固定话术（前端已切「正在生成工单」动画，
                # LLM 的「好的」不发，所以这里一定显示完整话术）。
                # 话术按「弹窗关闭后」的语境写：用户此时看到的只有对话气泡，
                # 要告诉他下一步做什么（补充信息 / 重新打开弹窗提交）。
                # 预填播报单一信息源：说的是草稿里校验后的真实 project 值。
                _pf_name = (draft.get("project") or "").strip()
                _pf_note = (f"项目已预填为「{_pf_name}」（可在弹窗中修改）。"
                            if _pf_name else "")
                if _force_submit:
                    parsed["message"] = ("工单草稿已生成（信息收集超限）。"
                                         f"{_pf_note}"
                                         "如需补充，直接在对话里告诉我；"
                                         "确认无误后点击转工单按钮，在弹窗中核对信息后提交。")
                else:
                    parsed["message"] = ("工单草稿已生成。"
                                         f"{_pf_note}"
                                         "您可以在对话里继续补充信息"
                                         "（如指定处理人、发生时间），也可以直接点击转工单按钮，"
                                         "在弹窗中核对信息后提交。")
                _msg_buf.clear()
                _msg_yielded = True  # 抑制末尾兜底输出
                yield {"event": "token", "data": parsed["message"]}
            except Exception as e:
                logger.error(f"[stream] 提单失败: session={request.session_id}, error={e}", exc_info=True)
                yield {"event": "status", "data": {"stage": "submit_failed", "error": str(e)}}
                # 丢弃 LLM 缓冲（可能含 JSON 碎片），直接用错误消息
                _msg_buf.clear()
                _msg_yielded = True
                # 兜底：防止 LLM role-play "已生成工单" 但实际提单失败
                if parsed["action"] == "submit":
                    parsed["action"] = "answer"
                    parsed["message"] = "提单过程中出现异常，请稍后重试或联系管理员。"

        result_data = await self._finalize_diagnosis(
            request.session_id, state,
            parsed["thinking"], parsed["action"], parsed["message"],
            streaming=True)
        if ticket_data:
            result_data["ticket"] = ticket_data

        # 标题生成：第2轮对话结束后通过独立 SSE event 发送
        if result_data.get("title"):
            yield {"event": "title", "data": {"title": result_data["title"]}}

        # 兜底：如果 LLM 只输出了 JSON 没有消息正文，或消息流被提前抑制（submit 被拦截），
        # 前面的流式 yield 不会触发任何 token。此时把最终 message（拦截/追问话术）
        # 作为一次性 token 发出去，确保前端有内容展示。
        if not _msg_yielded and parsed["message"]:
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
