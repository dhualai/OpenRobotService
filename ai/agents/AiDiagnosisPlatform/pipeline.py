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
    agent_state.ticket_ready = False
    agent_state.ticket_type = ""
    agent_state.ticket_collecting = []  # 工单已提交，退出工单填写模式
    agent_state.required_fields = None   # 重置动态必填字段（None=从未决定，防「已决定空清单」被重写）
    agent_state.collect_rounds = 0      # 重置收集轮数
    # 主动裁剪对话窗口：提单后旧对话移出 turns（滑动窗口从归档线重新计），
    # context_start 归 0。否则 turns buffer 满时（max_turns=10）会丢最老的记录，
    # 而当前工单的对话恰好在最老区域——下一单的续接轮就看不到本单上下文。
    memory.turns = memory.turns[agent_state.context_start:]
    agent_state.context_start = 0
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
- 🔴 **required_fields 必须包含至少 1 个字段，禁止空清单**：「什么都不收集直接提单」是不允许的。
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
        """把本地图片路径 ./media/xxx → 完整 CDN URL（跳过外链）"""
        _dm = r.domain or "team"
        _sd = r.sub_domain or "faq"
        # images are in shared parent directory (e.g., usp/media/), not per-sub_domain
        # sub_domain like "usp\faq" → parent is "usp"
        _sd_parent = _sd.replace('\\', '/').split('/')[0]
        _mu = f"{self.config.media_url_prefix}/kb/{_dm}/{_sd_parent}"
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

    def _build_diagnosis_prompt(self, state: AgentState, memory, reference_docs: str) -> str:
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
            memory, from_turn=_from, sanitize_images=bool(state.ticket_collecting))
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
                f"## 对话\n{conversation_text}\n\n"
                f"---\n"
                f"输出 JSON（字段齐就 submit，message 留空不写正文）：\n"
                f'```json\n'
                f'{{"action":"ask|submit","intent":"troubleshoot","ticket_cancel":false,'
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
                    "1. 🔴 用户只是咨询问题（如问「工单流转流程是怎样的」），没有提「帮我转工单」等诉求"
                    " → action=answer 直接回答问题，ticket_intent=false，不要收集字段、不要提单\n"
                    "2. 用户确有提单诉求 → 判定 ticket_type（problem=报障/bug=缺陷/feature=需求/support=咨询/other），"
                    "仔细读完整对话找出工程师接单后必须知道、但对话里确实还没说过的 1-4 个关键信息缺口"
                    "（用户说过的、能推出的不列；不列项目名）\n"
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
                    "5. 🔴 任何情况下都不问项目名称（项目在弹窗里选）\n\n"
                    "## 输出\n"
                    '```json\n'
                    '{"action":"answer|ask|submit","intent":"howto|troubleshoot","ticket_intent":true|false,"ticket_cancel":false,'
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
        # 标题每两轮生成一次（第 2/4/6... 轮，异步后台，不阻塞回复流）。
        # 覆盖式更新：每两轮重新生成，标题跟随对话最新内容演进。
        if state.diagnosis_rounds >= 2 and state.diagnosis_rounds % 2 == 0:
            from types import SimpleNamespace
            _snap = SimpleNamespace(
                session_id=memory.session_id,
                turns=[dict(t) for t in memory.turns],
                metadata={},
            )

            async def _title_bg():
                try:
                    _t = await _generate_title(self._llm_client, _snap)
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
            "拿到后再调用工具；工具返回草稿后简短收尾。\n"
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
        try:
            from ai.agents.AiDiagnosisPlatform.tool_loop import run_tool_loop_stream
            _schema = TOOL_SCHEMA_SUPPLEMENT if _supplement else TOOL_SCHEMA
            async with asyncio.timeout(60.0):
                async for ev in run_tool_loop_stream(
                        self._llm_client, messages, [_schema],
                        {"submit_ticket": _executor},
                        # 提单收集是轻量结构化任务，关闭思考可把首 token 等待
                        # 从 5-15s 砍到 1-2s（DeepSeek 与中转站 Claude 均生效）。
                        thinking=False):
                    if ev["event"] == "token":
                        yield ev
                    elif ev["event"] == "done":
                        final_text = ev["final_text"]
                        tool_results = ev["tool_results"]
                        # final_text 非空才表示正文已在循环内流式发出；
                        # terminate 路径 final_text=""（收尾话术走兜底文案，未流式）
                        final_streamed = bool(final_text)
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning(f"[tool_loop] 工具循环失败: session={request.session_id}, err={e}")
            yield {"event": "status", "data": {"stage": "submit_failed", "error": str(e)[:100]}}
            final_text = "提单过程中出现异常，请稍后重试或联系管理员。"
            tool_results = []

        t_loop = round((time.perf_counter() - t0) * 1000)
        logger.info(f"[tool_loop] 循环完成: session={request.session_id}, "
                    f"elapsed={t_loop}ms, tool_calls={len(tool_results)}, "
                    f"final_text_len={len(final_text)}")
        draft = None
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
                # 首次生成草稿（非补充）时才信任本轮声明——那一轮是真实校验过的。
                # 补充轮不覆盖：覆盖后会让 confirm_submit 的 _assess_ticket_readiness
                # 重新校验出偏差。
                if not _supplement:
                    rf = args.get("required_fields") or {}
                    if rf and isinstance(rf, dict):
                        state.required_fields = dict(rf)
                break
            try:
                draft = await self._build_ticket(request.session_id, state, memory)
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
            # 对话气泡回填话术
            _msg = final_text.strip() or "已生成工单草稿，请在弹窗中选择项目并核对信息后确认提交。"
            if _msg and not final_streamed:
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

        # 构造 messages：system + 最近对话 + 本轮用户消息
        _conv = self._format_conversation(
            memory, from_turn=state.context_start, max_turns=8)
        system_prompt = (
            "你是「摇人吧」微信服务号的 AI 诊断助手 U老师，面向 AGV/AMR 行业。\n"
            "你有两个工具：\n"
            "1. search_kb：检索知识库（操作手册/FAQ/排查手册/错误码）。"
            "回答操作步骤、错误码含义、故障排查等问题前，先查知识库；"
            "检索结果不相关就换关键词再查；多次查不到就如实说手册未覆盖，不要编造。\n"
            "2. submit_ticket：用户表达提单诉求（转工单/提单/派单）时调用。\n"
            "规则：\n"
            "- 不要问项目名称（项目由用户在确认弹窗里选择）\n"
            "- 用户可以一边咨询一边提单：先查知识库回答，用户不满意要提单时再调 submit_ticket\n"
            "- 查知识库后要基于检索内容回答，禁止编造步骤\n"
            "- 进入提单收集后，已收集的信息不得重复追问；不要每轮新增一项可有可无的信息。"
            "已有问题概述、设备型号、现象、期望效果、版本、站点等足以让工程师初判时，"
            "应调用 submit_ticket 生成草稿，不要继续追问。\n"
            "- 用户明确说某个信息没有/不知道/不方便提供，或说「直接提单」「就这些信息」时，"
            "把该字段按「没有」写入 collected_fields 后调用 submit_ticket，"
            "绝不要反复追问同一项；追问最多 2-3 次就必须完成提单。\n"
            f"当前上下文（非空说明用户在提单流程中）：问题={state.problem_summary or '无'}，"
            f"已收集={json.dumps(state.collected_info, ensure_ascii=False) if state.collected_info else '无'}\n"
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
                    self._retrieve_inner(request.session_id, state, query),
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
                rf = args.get("required_fields") or {}
                if rf and isinstance(rf, dict):
                    state.required_fields = dict(rf)
                break
            try:
                draft = await self._build_ticket(request.session_id, state, memory)
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
            _msg = final_text.strip() or "已生成工单草稿，请在弹窗中选择项目并核对信息后确认提交。"
            if _msg and not final_streamed:
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
            "你是「摇人吧」微信服务号的 AI 诊断助手 U老师，面向 AGV/AMR 行业。\n"
            "规则：\n"
            "- 回答操作步骤、错误码含义、故障排查等问题时，基于下方提供的知识库内容作答，禁止编造步骤\n"
            "- 回答时直接给出结论和排查步骤，不要出现「根据知识库」「根据检索结果」"
            "这类来源性开场白——用户不需要知道信息来源\n"
            "- 不要复述知识库的章节号/文档编号（如「5.13」「9.4」这类数字编号），"
            "用自己的话把步骤总结出来\n"
            "- 知识库内容中的 ![](url) 是操作界面截图：与当前问题直接相关的截图，"
            "必须用 ![说明](url) 格式引用到回复中对应步骤下面；与问题无关的图片一律不要带\n"
            "- 回答控制在 500 字以内，宁可简短完整，不要写太长（防止被截断）\n"
            "- 知识库内容没有覆盖时，才如实说明手册未收录，给出通用排查方向，并建议用户转工单\n"
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
            "判断下面用户消息的意图，只回复三个词之一：\n"
            "courtesy：寒暄/问候/闲聊/客套/表达感谢或情绪（如 你好、辛苦了、哈哈、谢谢、在吗）\n"
            "ticket：用户**明确提出提单诉求**——要我帮他转工单/提交工单/派单/找工程师处理"
            "（如 帮我转工单、提单吧、派单给XX、找个人给我配一下）。"
            "已经生成工单草稿后，用户对工单的**补充说明**（如「提给XX」「还有个补充，是XX时间发生的」「补充一下XX」）也属于 ticket。"
            "仅仅是询问「工单怎么流转/工单是什么」这类流程咨询**不算 ticket**，算 diagnosis。\n"
            "diagnosis：其他任何与设备、报错、故障、工作相关的求助或提问（如 AGV卡住、报错码、怎么办、工单流转流程是怎样的）；"
            "承接上文排查的追问、反馈（如「好的我试试」「还是不行」「这个呢」）也属于 diagnosis\n"
            f"消息：{resolved_query or raw_query}\n"
            "意图："
        )
        try:
            answer = await llm_client.complete(
                prompt,
                system_prompt="你是意图分类器，只输出 courtesy / ticket / diagnosis，不要输出其他内容。",
                max_tokens=8,
                temperature=0.0,
                thinking=False,
            )
            intent = (answer or "").strip().lower()[:20]
            logger.debug(f"[intent] 分类结果: {intent!r} (raw={raw_query[:30]!r})")
            if "ticket" in intent:
                return "ticket"
            if "courtesy" in intent:
                return "courtesy"
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
                                      resolved_query: str = "") -> str:
        t0 = time.perf_counter()
        logger.info(f"[retrieve] 进入检索: session={session_id}")
        try:
            # 意图判闲聊时由外部 cancel：检索在 await 点抛出 CancelledError，
            # 必须先于此处的宽泛 handler 退出（否则会落到下面 TimeoutError/ConnectionError 分支被当作失败）
            try:
                return await self._retrieve_inner(session_id, state, resolved_query)
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
        for g in gathered:
            if isinstance(g, BaseException):
                continue
            for r in g[0] + g[1]:
                if r.id not in seen:
                    seen.add(r.id)
                    results.append(r)
        return results

    async def _rewrite_query(self, query: str) -> str:
        """轻量模型改写检索词（仅首轮检索弱时触发，走意图专用 deepseek 客户端）。
        保留错误码/型号等实体，口语转检索语。失败/无变化返回空串（沿用原查询）。"""
        try:
            from ai.core import get_intent_client
            _llm = await get_intent_client()
            _out = await asyncio.wait_for(_llm.complete(
                prompt=(
                    "把下面的用户问题改写成适合知识库检索的查询短语（10-25字），"
                    "保留错误码、车型/型号、专有名词等关键实体，去掉语气词和口语表达。"
                    "只输出改写后的查询短语，不要任何解释。\n\n"
                    f"用户问题：{query}"
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
                              resolved_query: str = "") -> str:
        # 检索查询：用户当前输入为主，problem_summary/hypotheses 仅辅助短查询补全。
        # 用户查询≥10字且具体 → 不加任何旧 state 信息，防止旧话题污染（如查"自动门对接"
        # 但 state 残留"充电验证"，导致 embedding 偏航、正确 chunk 排不进 top N）。
        t0 = time.perf_counter()
        search_query = resolved_query if resolved_query else state.original_query
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

        # 缓存命中：同一查询 TTL 内复用结果
        cache_key = search_query[:200]
        cached = self._retrieval_cache.get(cache_key)
        if cached and time.time() - cached["ts"] < self._CACHE_TTL:
            logger.debug(f"[retrieve] cache hit: {(time.perf_counter() - t0) * 1000:.0f}ms")
            return cached["result"]

        logger.info(f"[retrieve] 三路域检索: query={search_query[:60]}...")
        # 双查询合并检索:改写词与原词都查,结果并集。
        # 「可以调整吗」vs「怎么调整」这类提问方式差异会让 embedding 漂移,
        # 改写后的操作句式查询把另一侧命中的文档捞回来,抹平表述差异。
        _rw_task = asyncio.create_task(self._rewrite_query(search_query))
        _domain_results = await self._three_way_retrieve(search_query)
        _rw = await _rw_task
        _rw_results = await self._three_way_retrieve(_rw) if _rw else []
        logger.info(f"[retrieve] 三路检索完成: {round((time.perf_counter() - t0) * 1000)}ms")

        # sub_domain → 标签映射
        _sub_labels = {
            "platform": "🎫 服务号",
            "faq": "📋 FAQ", "usp_faq": "📋 FAQ",
            "cheduan_errors": "🚗 车端", "cheduan_implementation": "🚗 车端",
            "translation": "🌐 翻译",
            "diagnosis": "🏭 诊断",
            "usp_manual": "📖 手册", "usp_product": "📖 产品",
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

        # 双路保送后候选池可能超过精排上限：按「稠密 top15 + 稀疏 top15」平衡截断。
        # 两路原始分尺度不同(稀疏 1-2 vs 稠密余弦 0.5-0.6),按单一分数排序会把
        # 另一路保送挤掉——平衡截断保证关键词命中和语义命中都能进精排。
        if len(uniq) > 30:
            _dense_part = sorted(
                [r for r in uniq if r.vector_score],
                key=lambda r: r.vector_score, reverse=True)
            _sparse_part = sorted(
                [r for r in uniq if r.sparse_score],
                key=lambda r: r.sparse_score, reverse=True)
            _balanced, _seen2 = [], set()
            for r in _dense_part[:15] + _sparse_part[:15]:
                if r.id not in _seen2:
                    _seen2.add(r.id)
                    _balanced.append(r)
            uniq = _balanced

        # 三路已 skip_rerank（只检索未精排）→ 合并去重后统一 rerank 一次，
        # 从三路各自 rerank 的 3 次 CPU 推理降为 1 次，且候选放宽到合并后的 top N。
        # 触发条件 >= 4（此前 >6 导致去重后恰好 6 条时 rerank 从不执行——
        # cross-encoder 配了却空转,RRF 名次说了算)。
        if len(uniq) >= 4:
            _reranked = await self._retriever._rerank_results(search_query, uniq, _MAX_RETRIEVAL_DOCS)
            if _reranked:
                uniq = _reranked

        hit_logs = []  # 送入 prompt 的 chunk 摘要（标题@分数，用于生产排查检索效果）
        for r in uniq[:_MAX_RETRIEVAL_DOCS]:
            content = self._rewrite_images(r) if r.content else ""
            if not content.strip():
                continue
            title = f"（{r.title}）" if r.title else ""
            docs.append(f"---\n{_label(r)} {idx}{title}：\n{content}\n---")
            hit_logs.append(f"[{r.sub_domain or '-'}]{r.title or '(无标题)'}@{r.score:.4f}")
            idx += 1
        logger.info(f"[retrieve] 命中{len(all_results)}去重{len(uniq)}送prompt{len(hit_logs)}: {' | '.join(hit_logs)} "
                    f"总耗时{round((time.perf_counter() - t0) * 1000)}ms")

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
                timeout=8.0,
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
            "- 🔴 required_fields 必须包含 1-4 个字段，禁止返回空对象\n"
            "- 🔴 只列入「对话中确实还没说过的信息缺口」：仔细读完整对话，"
            "用户已经说过、提到过、或能从对话直接推出的信息一律不列入；"
            "只收集工程师接单后必须知道、但对话里确实没有的关键信息\n"
            "- 项目由用户在确认弹窗选择，不要写入 required_fields\n"
            "- 仅输出 JSON，无额外文字\n\n"
            f"## 对话\n{conv}\n"
        )
        raw = await asyncio.wait_for(
            self._llm_client.complete(prompt=prompt, max_tokens=300, temperature=0,
                                       thinking=False),
            timeout=8.0,
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
        # 空清单重试：LLM 偶尔无视「禁止空清单」规则。补一次带提醒的重试，
        # 让「提单必补字段」不依赖 LLM 单次输出碰运气。
        if not result["required_fields"]:
            retry_prompt = (
                prompt
                + "\n\n⚠️ 你上一次返回了空的 required_fields，这是不允许的。"
                  "重新分析对话：工单提单前至少有 1 个关键信息缺口需要用户补充，"
                  "请给出 1-4 个字段，仅输出 JSON。"
            )
            try:
                raw2 = await asyncio.wait_for(
                    self._llm_client.complete(prompt=retry_prompt, max_tokens=300,
                                               temperature=0.2, thinking=False),
                    timeout=8.0,
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
        """同步路径：让 LLM 根据对话总结出工单类型 + 2-3 个必补关键字段，
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

    async def _build_ticket(self, session_id: str, agent_state: AgentState, memory) -> dict:
        # 生成工单的对话：屏蔽图片描述 + 从 context_start 切片。
        # 切片至关重要：提单后 context_start 会前移（旧对话归档），
        # 下一个工单只看新对话——否则上一个工单的补充信息（如「调度版本 2.6.4」）
        # 会串进新工单的描述。
        # project 不在对话/LLM 链路产生——项目选择的唯一入口是确认弹窗的搜索选择
        # （confirm_submit 时写回）。LLM 提取 title/description/contact 时不需要看
        # VLM 描述，看到反而会把截图 UI 文本（缺陷/处理中/处理人）当字段值填进工单。
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

        prompt = (
            f"请根据以下对话和诊断过程，生成结构化工单。\n\n"
            f"## 对话记录\n{conversation_text}\n\n"
            f"## Agent 推理链\n{reasoning}\n\n"
            f"请先判断工单类型（problem=报障/bug=缺陷/feature=功能需求/support=支持请求/other=其他），"
            f"然后以 JSON 格式返回：\n"
            f'{{"type":"problem|bug|feature|support|other","title":"≤20字，不要含项目名（项目由用户在弹窗选择）","description":"≤300字，简述问题和排查过程，不要带项目/现场名；🔴 必须把对话中 AI 追问过、用户回答过的全部内容总结进去（现场联系人及联系方式、调度版本、发生时间、设备型号等），一项都不能丢；🔴 如果对话里用户指名了接单人（提给XX/交给XX/派单给XX），description 开头必须写「[指定处理人：XX]」，绝不能漏",'
            f'"priority":"紧急|高|中|低","contact":"从对话提取的联系人，没有则为空",'
            f'"location":"仅type=problem时填，现场位置","robot_type":"仅type=problem时填，机器人型号/编号",'
            f'"project":"固定为空字符串——项目由用户在确认弹窗搜索选择，不要从对话提取",'
            f'"fault_code":"仅type=problem时填，故障码","special_notes":"所有类型可用，特殊说明（用户指名处理人、额外备注等）",'
            f'"occurrence_time":"仅type=problem时填，故障发生时间","frequency":"仅type=problem时填，出现频率（每次/偶尔/首次）",'
            f'"steps_to_reproduce":"仅type=bug时填","expected_result":"仅type=bug时填",'
            f'"actual_result":"仅type=bug时填","severity":"仅type=bug时填:阻塞/主要/次要/轻微",'
            f'"version":"仅type=bug时填","scenario":"仅type=feature时填，需求场景",'
            f'"expected_effect":"仅type=feature时填","source":"仅type=feature时填:客户提出/内部发现/竞品对标",'
            f'"support_type":"仅type=support时填","preferred_response":"仅type=support时填:电话/现场/线上"}}'
        )

        logger.info(f"[build_ticket] 工单生成 prompt: {len(prompt)} chars: session={session_id}")

        try:
            raw = await asyncio.wait_for(
                self._llm_client.complete(prompt=prompt, max_tokens=600, temperature=0.2),
                timeout=20.0,
            )
            analysis = _extract_json_object(raw)
        except Exception as e:
            logger.error(f"LLM 工单生成失败（将使用默认值）: session={session_id}, error={e}", exc_info=True)
            analysis = {}

        # 工单类型前移：对话中 LLM 已维护 ticket_type 时直接采用，避免提单瞬间二次分类漂移
        ticket_type = agent_state.ticket_type or analysis.get("type", "other")
        if ticket_type not in ("problem", "bug", "feature", "support", "other"):
            ticket_type = "other"

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
            "title": analysis.get("title", agent_state.original_query[:20]),
            "description": _desc,
            "priority": analysis.get("priority", "中"),
            "status": "pending",
            "contact": analysis.get("contact", ""),
            # 项目：对话/LLM 不再产生。唯一入口是确认弹窗搜索选择，
            # confirm_submit 用 overrides 里的 project/project_id 写回工单。
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
            "attachments": memory.metadata.get("agent_state", {}).get("attachments", []),
        }

        # 特殊说明（所有类型通用）：优先取 LLM analysis，兜底取 collected_info["requested_assignee"]
        _notes = analysis.get("special_notes", "")
        _assignee = agent_state.collected_info.get("requested_assignee", "").strip()
        if _assignee and "指定处理人" not in _notes:
            _notes = f"指定处理人：{_assignee}" + (f"；{_notes}" if _notes else "")
        result["special_notes"] = _notes

        # 项目：对话/LLM 不再产生，保持空。用户在弹窗搜索选择后
        # confirm_submit 用 overrides 写回 project/project_id，无需在此兜底解析。
        # （analysis.get("project") 即使 LLM 偶尔输出了值也被忽略——弹窗必选才是权威。）

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
        ticket = await self._build_ticket(session_id, agent_state, memory)
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

        ticket = await self._build_ticket(session_id, agent_state, memory)

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

        ticket = await self._build_ticket(session_id, agent_state, memory)
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
                             sanitize_images: bool = False) -> str:
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
            return "\n".join(_sanitized)
        formatted = "\n".join(
            f"{'用户' if t['role'] == 'user' else '助手'}：{_truncate_turn(t['content'])}"
            for t in turns
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
        ticket_intent = False
        ticket_cancel = False
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

        # 指代消解："然后呢"等省略表达 → 用上文补全为完整查询
        resolved_query, _ = await self._memory_manager.resolve_pronoun(
            request.query, request.session_id)

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
                self._retrieve_with_context(request.session_id, state, resolved_query))
            _intent_task = asyncio.create_task(
                self._classify_intent(
                    _intent_llm, request.query, resolved_query,
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
                # 诊断单轮：等并发检索结果 → 小 prompt 1 次 LLM 直接回答（无工具往返）
                try:
                    reference_docs = await asyncio.wait_for(_retrieval_task, timeout=20.0)
                except asyncio.TimeoutError:
                    reference_docs = ""
                    logger.warning(f"[stream] 检索超时(20s)，降级无上下文: session={request.session_id}")
                logger.info(f"[stream] 诊断走单轮分支（服务端检索+1次LLM）: session={request.session_id}")
                async for ev in self._diagnosis_oneshot_branch(request, state, memory, reference_docs):
                    yield ev
                return
            elif _intent == "courtesy":
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

        prompt = self._build_diagnosis_prompt(state, memory, reference_docs)
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
            state.collect_rounds += 1
            # 弹窗关闭后的补充轮：如果本轮确实新增字段且固定清单已齐，
            # 即使 LLM 只输出了 ask，也自动进入 review，避免补充完成后只回话不弹窗。
            # ⚠️ 条件用 _has_pending_ticket 而非 state.ticket_collecting：草稿存在时
            # 字段往往已齐，ticket_collecting 被清空为 []（falsy），若只看它，
            # 弹窗取消后再补充指定接单人/备注这类非必填信息就永远触发不了自动 review。
            _supplement_ready, _supplement_missing = _assess_ticket_readiness(state)
            if (_has_new_supplement and _supplement_ready
                    and not parsed.get("ticket_cancel", False)):
                parsed["action"] = "submit"
                logger.info(f"[stream] 补充字段已齐，自动进入 review: session={request.session_id}")
            if parsed["action"] == "submit":
                # LLM 自己判断字段齐了：回填与就绪判定统一交给下方「提单就绪门槛」。
                # 这里不再单独回填/自动提单——此前 backfill 会把助手刚问的话当答案
                # 幻觉填字段 → 判定假齐 → 用户还没回答就提前弹窗。
                pass
            else:
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
                draft = await self._build_ticket(request.session_id, state, memory)
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
                if _force_submit:
                    parsed["message"] = ("工单草稿已生成（信息收集超限）。"
                                         "如需补充，直接在对话里告诉我；"
                                         "确认无误后点击转工单按钮，在弹窗中核对信息、选择项目后提交。")
                else:
                    parsed["message"] = ("工单草稿已生成。您可以在对话里继续补充信息"
                                         "（如指定处理人、发生时间），也可以直接点击转工单按钮，"
                                         "在弹窗中选择项目并提交。")
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
