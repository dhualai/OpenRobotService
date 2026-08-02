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
from ai.core.project_matcher import get_project_matcher, ProjectMatch

logger = get_logger("AI")


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
    required_fields: dict = field(default_factory=dict)  # LLM 声明的动态字段清单 {field_key: chinese_label}，供 prompt 提示 LLM 收集
    context_start: int = 0  # 当前问题的对话起始 turn 索引（提单后更新，backfill 只看切片，防旧对话重新武装就绪判定）
    collect_rounds: int = 0  # 工单填写模式下已收集的轮数，超过 _MAX_COLLECT_ROUNDS 强制提单（防鬼打墙）


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
        required_fields=s.get("required_fields", {}),
        context_start=s.get("context_start", 0),
        collect_rounds=s.get("collect_rounds", 0),
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
        "required_fields": state.required_fields,
        "context_start": state.context_start,
        "collect_rounds": state.collect_rounds,
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

    不依赖 phase——run_stream 会提前把 phase 改成 diagnosing，phase 不可靠。
    对话路径和按钮路径都调用此函数，行为一致。
    """
    if state.last_submitted_ticket and not (state.problem_summary or "").strip():
        return False, "工单刚提交，如需处理新问题请先描述新现象。"
    return True, ""


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
        "prompt": "" if ok else "请给出工单关联的项目名称，我好帮你提交工单。",
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
    agent_state.required_fields = {}    # 重置动态必填字段
    agent_state.collect_rounds = 0      # 重置收集轮数
    agent_state.context_start = len(memory.turns)  # 旧对话归档：backfill 只看之后的 turns
    _save_agent_state(memory, agent_state)
    # 提单后状态可见性：has_last_ticket=True + problem_summary 空 → 下一轮/按钮 _can_submit 拦截
    _log_ticket_state(agent_state, "submit_done")


# ============================================================
# 提单就绪判定（服务端唯一真相，不信任 LLM 自评）
# ============================================================

# 鬼打墙防护：诊断/收集轮次上限
_MAX_DIAGNOSIS_ROUNDS = 6   # 诊断超过此轮数 → prompt 提示 LLM 收尾或建议转工单
_MAX_COLLECT_ROUNDS = 4     # 工单填写超过此轮数仍不齐 → 强制提单（project 缺则用"摇人吧服务号提单"兜底）
_MAX_RETRIEVAL_DOCS = 6     # 三路检索合并后按 score 排序，只保留 top N 个 chunk 进 prompt


def _assess_ticket_readiness(state: AgentState) -> tuple[bool, list[str]]:
    """服务端提单就绪判定 = project 铁律 + LLM 决定的 required_fields 全非空。

    required_fields 由 _decide_ticket_fields 在转单时让 LLM 按问题类型动态决定（2-3 个），
    不是硬编码清单——符合"AI 判断要补什么信息，补齐才算 ready"。空时退化为 project-only。
    返回 (ready, missing)：missing 为面向用户的缺失项中文名列表。
    """
    missing = []
    has_project = bool((state.collected_info.get("project") or "").strip()
                       or (state.collected_info.get("project_id") or "").strip())
    if not has_project:
        missing.append("项目名称")
    for field_key, label in (state.required_fields or {}).items():
        if not (state.collected_info.get(field_key) or "").strip():
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
        "has_project": bool(state.collected_info.get("project", "").strip() or state.collected_info.get("project_id", "").strip()),
        "project": (state.collected_info.get("project") or state.collected_info.get("project_id") or "")[:20],
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


async def _generate_title(llm_client, memory) -> str:
    """第2轮对话结束后，用前两轮对话生成会话标题（中文不超过15字，英文不超过50字符）"""
    turns = memory.turns
    if len(turns) < 2 or "title" in memory.metadata:
        return ""
    # 动态取可用 turns（最多前4条）构造对话，避免 Redis 降级 turns 不全时索引越界/直接放弃
    dialog = "\n".join(
        f"{'用户' if t.get('role') == 'user' else '助手'}：{t.get('content', '')}"
        for t in turns[:4]
    )
    prompt = (
        "根据以下对话生成一个简短标题（中文不超过15字，英文不超过50字符）：\n\n"
        f"{dialog}\n\n"
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

| project 是否已有 | required_fields 是否收齐 | action |
|---|---|---|
| 否 | — | ask（只追问 project） |
| 是 | 否 | ask（一次只问一个缺失字段） |
| 是 | 是 | **submit**（立即，message 只写"好的"） |

🔴 **submit 时 message 只能写"好的"两个字，绝对禁止写"工单已提交/已生成/工程师会处理/汇总如下..."等任何确认话术**。
服务端会自己判断字段是否齐全、并生成最终确认消息；你提前写"已提交"会和服务端冲突——服务端若发现还缺字段会拦截，
此时你已经吐出去的"已提交"就成了骗用户。所以 submit 时闭嘴，只写"好的"。

🔴 **第三行是默认期望结果**：用户都明确要转工单了，只要 project 和必填关键字段齐 → **必须立即 submit，禁止再问任何"可选"问题**。
"错误码是车端还是 USP""具体现场位置""故障现象细节"——这些都是**工程师接单后再确认的可选信息**，
**绝对不准用可选细节卡住提单**。宁可少一个可选细节，也必须按时 submit。
判断不了某个细节？别问，直接 submit，让工程师确认。

- **即使用户没催**：信息够了就 submit，不要"再确认一下"。
- **即使用户催**：project 或必填字段没齐，也先 ask 补齐，不准盲目 submit。
- **用户指名处理人**（"提单给XX""交给XX""派给XX"）→ 把 XX 写入 collected_info["requested_assignee"]。

用户表示不想继续排查（"不想排查""算了""不用了"）→ action="answer"，简短收尾（"好的，有需要随时找我"），不追问不排查。

### 提单前信息检查 / required_fields
- **由你决定要收集哪些字段**：根据问题类型 + 知识库判断，提单需要补哪 2-3 个关键字段
  （报障类：occurrence_time/robot_type/frequency；软件问题：version/steps_to_reproduce；
  需求类：scenario/expected_effect）。写入 state_update.required_fields（格式 {{字段key: 中文名}}）。
  **project 永远必填，不写进 required_fields。**
- 🔴 **action=submit 时必须在 state_update 中同时写入 required_fields**（格式见下方示例）。
  不要留空等服务端兜底——服务端的兜底判断没有你的完整对话上下文准确，可能误判工单类型导致字段错配。
- 收齐 = project 非空 + required_fields 每项非空。收齐就 submit。
- **服务端铁律**：project 必须非空，否则 submit 会被拦截追问；其他字段是否齐全信任你的判断。

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
project 不用写在 required_fields 里（系统强制要求）。

### collected_info 写入铁律（极其重要）
**每一轮**用户发言后，只要提到任何可用信息，**必须**增量写入 state_update.collected_info（不要等齐全才写）。

🔴 **project（项目名称）——提单最重要的字段，最高优先级**：
用户消息里任何**地名/厂区/客户/现场/公司名**都极可能是项目名，**只要用户提到了，就必须写入 project**（写用户原话里的简称即可，系统会用项目库匹配真实项目全名）。
项目名常见形态是「地区+公司+车型+项目」，如"浙江湖州中力安吉北区调度升级项目""河南郑州东昇汽配厂潜伏车项目""江苏常州多摩川混场项目""河南郑州思念食品潜伏车项目"。
用户通常只说**简称**，下列都要识别并写入 project：
- "安吉北区的车不动了" → project="安吉北区"
- "东昇汽配厂那边出问题" → project="东昇汽配厂"
- "多摩川产线离线" → project="多摩川"
- "顾家智能的潜伏车" → project="顾家智能"
- "思念食品" → project="思念食品"
**只要用户提到了任何地点/客户/厂区名，就必须写 project；只有用户完全没提任何地点或客户名时才留空。**

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
- **禁止在回复中暴露知识来源**：不要说"根据知识库""检索结果显示"等话术。
  直接给出步骤/答案，用户不需要知道你查了什么。
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
{{"action":"answer|ask|submit","intent":"howto|troubleshoot|chat","state_update":{{"ticket_type":"problem|bug|feature|support|other","problem_summary":"概述","ruled_out":[],"hypotheses":[],"collected_info":{{}},"ticket_ready":false}}}}
```
JSON 之后直接写回复。语气像工程师。引用图片时用 ![说明](url) 格式。"""


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

    def _build_diagnosis_prompt(self, state: AgentState, memory, reference_docs: str) -> str:
        # Guard: context_start 可能因 turn buffer 截断（max_turns）而越界。
        # 场景：提单时 context_start=len(turns)=10，下一轮 add_turn 后 buffer 满截断，
        # turns 仍为 10 → turns[10:] 返回空 → LLM 看不到对话 → 输出问候语。
        # 回退时取最近 4 条（≈2 轮对话），足够 LLM 理解上下文，不撑 prompt。
        _from = state.context_start
        if _from >= len(memory.turns):
            _from = max(0, len(memory.turns) - 4)
        conversation_text = self._format_conversation(memory, from_turn=_from)
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
            field_map_hint = ""
            if state.required_fields:
                fm = "；".join(f"{k}→{label}" for k, label in state.required_fields.items())
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
                f"5. 所有缺失字段（含'无'）都补齐后 → 立即 action='submit'，message 只写'好的'\n"
                f"⚠️ 已收集的字段不要再问（比如上面已收集里已经有 project，就不要再追问项目）。"
            )
        else:
            ticket_collecting_context = "（正常诊断模式）"
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
                state.required_fields = {k: str(v) for k, v in rf.items() if k and v}
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
            state.ruled_out = state_update["ruled_out"]
        if "hypotheses" in state_update:
            state.hypotheses = state_update["hypotheses"]
        if "ticket_ready" in state_update:
            # LLM 可能输出 bool 或字符串 "true"/"false"
            tr = state_update["ticket_ready"]
            if isinstance(tr, bool):
                state.ticket_ready = tr
            elif isinstance(tr, str):
                state.ticket_ready = tr.lower() in ("true", "1", "yes")
        if "collected_info" in state_update:
            # 合并新字段，空值/无 视为清除。project 由 LLM 提取用户提到的地点/客户名，
            # 提单时 _build_ticket 会调 _resolve_project 把它匹配成真实项目全名。
            for k, v in state_update["collected_info"].items():
                if v is None:
                    state.collected_info.pop(k, None)
                    continue
                if isinstance(v, (dict, list)):
                    v = json.dumps(v, ensure_ascii=False)
                v = str(v).strip()
                if v:
                    state.collected_info[k] = v
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
            candidates = await matcher.get_candidates_async(user, min_score=0.7)
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
            state.collect_rounds = _existing["collect_rounds"]
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

        # 第2轮及以后对话结束生成标题（fire-and-forget，不阻塞结果返回）。
        # 门槛放宽到 >=2：防第2轮因 LLM 抖动未生成时，后续轮还能补上；
        # 防重复靠 memory.metadata["title"]（_generate_title 内部写入，"title" not in 判定拦住重复生成）。
        title = ""
        if state.diagnosis_rounds >= 2 and "title" not in memory.metadata:
            logger.info(f"[title] 尝试生成: round={state.diagnosis_rounds}, turns={len(memory.turns)}")
            title = await _generate_title(self._llm_client, memory)
            logger.info(f"[title] 生成结果: {title!r}")
            # 同步到 DB：会话列表 / 切回 / 刷新都读 DB title，否则始终是默认「新会话」
            if title:
                try:
                    from ai.core.conversation_store import rename_conversation
                    rename_conversation(memory.session_id, title)
                    logger.info(f"[title] DB 已同步: session={memory.session_id}, title={title}")
                except Exception as e:
                    logger.warning(f"[title] DB 同步失败: {e}")

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
    _CACHE_TTL = 60  # 秒

    async def _retrieve_with_context(self, session_id: str, state: AgentState,
                                      resolved_query: str = "") -> str:
        t0 = time.perf_counter()
        logger.info(f"[retrieve] 进入检索: session={session_id}")
        try:
            # 检索查询：用户当前输入为主，problem_summary/hypotheses 仅辅助短查询补全。
            # 用户查询≥10字且具体 → 不加任何旧 state 信息，防止旧话题污染（如查"自动门对接"
            # 但 state 残留"充电验证"，导致 embedding 偏航、正确 chunk 排不进 top N）。
            search_query = resolved_query if resolved_query else state.original_query
            _need_context = len(search_query) < 10  # 极短查询（"怎么办""这是啥"）才需要上下文
            if state.problem_summary and _need_context:
                search_query = search_query + " " + state.problem_summary[:30]
            if state.hypotheses and _need_context:
                search_query = search_query + " " + " ".join(state.hypotheses)[:50]

            # 缓存命中：同一查询 60 秒内复用结果
            cache_key = search_query[:200]
            cached = self._retrieval_cache.get(cache_key)
            if cached and time.time() - cached["ts"] < self._CACHE_TTL:
                logger.debug(f"[retrieve] cache hit: {(time.perf_counter() - t0) * 1000:.0f}ms")
                return cached["result"]

            # 三路并行域检索（team / company / industry）
            # chunk 自带 sub_domain 字段，按 sub_domain 自动贴标签
            config = get_ai_config()
            team_task = asyncio.wait_for(
                self._retriever.retrieve_domain(search_query, "team", top_k=6),
                timeout=15.0,
            )
            company_task = asyncio.wait_for(
                self._retriever.retrieve_domain(search_query, "company", top_k=4),
                timeout=10.0,
            )
            industry_task = asyncio.wait_for(
                self._retriever.retrieve_domain(search_query, "industry", top_k=3),
                timeout=10.0,
            )

            logger.info(f"[retrieve] 三路域检索: query={search_query[:60]}...")
            gathered = await asyncio.wait_for(
                asyncio.gather(team_task, company_task, industry_task, return_exceptions=True),
                timeout=20.0,
            )
            logger.info(f"[retrieve] 三路检索完成")
            team_results, company_results, industry_results = gathered
            if isinstance(team_results, BaseException):
                team_results = []
            if isinstance(company_results, BaseException):
                company_results = []
            if isinstance(industry_results, BaseException):
                industry_results = []

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

            # 三路结果合并 → 按 score 降序 → 取 top N（避免 13+ 个 chunk 撑爆 prompt）
            all_results = list(_cheduan_exact) + list(team_results) + list(company_results) + list(industry_results)
            # 去重（同 id 只保留最高分）
            seen = set()
            uniq = []
            for r in sorted(all_results, key=lambda r: r.score, reverse=True):
                if r.id not in seen:
                    seen.add(r.id)
                    uniq.append(r)
            for r in uniq[:_MAX_RETRIEVAL_DOCS]:
                content = self._rewrite_images(r) if r.content else ""
                if not content.strip():
                    continue
                title = f"（{r.title}）" if r.title else ""
                docs.append(f"---\n{_label(r)} {idx}{title}：\n{content}\n---")
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
            logger.warning(f"检索服务不可用: session={session_id}, error={e}")
        except LowConfidenceError as e:
            thr = getattr(self, '_score_threshold', get_ai_config().retrieval_score_threshold)
            logger.warning(f"[retrieve] LowConfidence: score={e.confidence:.3f} threshold={thr}")
            logger.warning(f"检索置信度过低: session={session_id}, score={e.confidence:.3f}")
        except (asyncio.TimeoutError, ConnectionError, RetrieveEmptyError):
            logger.warning(f"[retrieve] 超时/失败: {(time.perf_counter() - t0) * 1000:.0f}ms")
            logger.warning(f"检索超时/失败: session={session_id}")
        return "（知识库检索失败，请告知用户当前系统检索异常、建议稍后重试或转工单处理，不要自己编造答案。）"

    # ================================================================
    # 工单生成
    # ================================================================
    async def _backfill_collected_info(self, session_id: str, agent_state: AgentState, memory) -> None:
        """提单前专用回填：主对话 LLM 经常嘴上"已记录"但没写进 collected_info，
        这里对当前问题的对话做一次聚焦提取，把提到的字段补齐（不覆盖已有值）。
        仅在 submit/prepare 等提单关口调用，一轮一次 LLM 调用。
        只取 context_start 之后的 turns——上一张工单提交前的旧对话不参与，
        防止已清空的 collected_info 被旧轮次重新填满、绕过闭环保护。"""
        try:
            turns = memory.turns[agent_state.context_start:]
            if not turns:
                return
            conversation_text = "\n".join(
                f"{'用户' if t['role'] == 'user' else '助手'}：{t['content']}"
                for t in turns[-20:]
            )
            prompt = (
                "请从以下对话中提取工单字段，只提取对话中明确出现的信息，不要推测、不要编造。\n"
                "⚠️ **铁律**：scenario 和 expected_effect 字段**永远留空**（这两个字段只能由主对话 LLM 在需求类对话中填写，backfill 不准填）。\n"
                "以 JSON 返回，没提到的字段给空字符串：\n"
                '{"project":"项目/现场名称","occurrence_time":"故障发生时间","robot_type":"具体车型/编号（AGV、机器人这类泛称留空）",'
                '"frequency":"出现频率（每次/偶尔/首次）","scenario":"","expected_effect":"",'
                '"version":"软件版本号","steps_to_reproduce":"复现步骤","support_type":"支持类型"}\n\n'
                f"## 对话\n{conversation_text}"
            )
            raw = await self._llm_client.complete(prompt=prompt, max_tokens=400, temperature=0)
            clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
            data = json.loads(clean)
            filled = []
            for k, v in data.items():
                if not v or k in agent_state.collected_info:
                    continue
                v = str(v).strip()
                if v:
                    agent_state.collected_info[k] = v
                    filled.append(k)
            if filled:
                logger.info(f"[backfill] 从对话回填 collected_info: session={session_id}, fields={filled}")
        except Exception:
            logger.warning(f"[backfill] 回填失败（忽略，按原 collected_info 判定）: session={session_id}",
                           exc_info=True)

    async def _decide_ticket_fields(self, session_id: str, agent_state: AgentState, memory) -> None:
        """转单瞬间调用一次：让 LLM 根据对话总结出工单类型 + 2-3 个必补关键字段，
        锁进 state.required_fields / ticket_type。后续提单门槛 = project + 这些字段全非空。

        字段由 LLM 按问题类型动态决定（不是硬编码清单），符合"AI 判断要补什么信息"。
        project 系统已强制，不写进 required_fields。失败则保持空（回退到 project-only 门槛）。
        """
        try:
            turns = memory.turns[agent_state.context_start:]
            conv = "\n".join(
                f"{'用户' if t['role'] == 'user' else '助手'}：{t['content']}"
                for t in turns[-20:]
            )
            prompt = (
                "根据以下对话，判断工单类型，并决定生成有效工单还需向用户确认哪 2-3 个关键字段。\n"
                "key 用英文，value 写简短中文标签（≤8字，如【错误现象】【发生时间】，不要写整句话）。\n"
                "参考字段（不限，按需选用）：\n"
                "  robot_type(车型/编号) occurrence_time(发生时间) frequency(出现频率)\n"
                "  fault_code(故障码) location(现场位置) version(版本) steps_to_reproduce(复现步骤)\n"
                "  scenario(需求场景) expected_effect(期望效果) support_type(支持类型)\n"
                "  error_message(错误信息/现象描述)\n"
                "对话里已经明确给过的字段不要再要求。不要用可选细节卡提单——只问真正缺的关键信息。\n"
                "⚠️ project（项目名称）系统已强制要求，**不要写进 required_fields**。\n"
                "🔴 如果问题不涉及具体车辆/机器人（如平台功能、登录问题、软件配置），"
                "不要要求 robot_type、fault_code、location。只选和问题实际相关的字段。\n"
                "只返回 JSON，无多余文字。示例——\n"
                "报障(涉及车)：{\"ticket_type\":\"problem\",\"required_fields\":{\"robot_type\":\"具体车型/编号\","
                "\"occurrence_time\":\"发生时间\"}}\n"
                "报障(登录/平台)：{\"ticket_type\":\"problem\",\"required_fields\":{\"error_message\":\"登录问题的具体现象\"}}\n"
                "support：{\"ticket_type\":\"support\",\"required_fields\":{\"support_type\":\"所需支持\"}}\n\n"
                f"## 对话\n{conv}"
            )
            raw = await self._llm_client.complete(prompt=prompt, max_tokens=300, temperature=0)
            clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
            data = json.loads(clean)
            # 只有主 LLM 还没定 ticket_type 时才设。主 LLM 有完整对话上下文，
            # 判断比这里的独立 LLM 调用更准。覆盖会导致报障被错标为 support，
            # 后续 required_fields 与实际故障不匹配，LLM 陷入收集死循环。
            if not agent_state.ticket_type:
                tt = (data.get("ticket_type") or "").strip()
                if tt in ("problem", "bug", "feature", "support", "other") and tt:
                    agent_state.ticket_type = tt
            rf = data.get("required_fields") or {}
            if isinstance(rf, dict):
                # 不再用固定词表限制——LLM 根据问题类型自主选字段，
                # 只做基本合理性过滤（key 长度、value 简短标签、非空、不重复收集）
                agent_state.required_fields = {
                    k: str(v)[:12] for k, v in rf.items()
                    if str(v).strip()
                    and len(str(k)) <= 40
                    and not (agent_state.collected_info.get(k) or "").strip()
                }
            logger.info(f"[decide_fields] type={agent_state.ticket_type} "
                        f"required={agent_state.required_fields} session={session_id}")
        except Exception:
            logger.warning(f"[decide_fields] 失败（回退 project-only 门槛）: session={session_id}",
                           exc_info=True)

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
            f'"project":"所有类型必填，从对话提取的项目/现场名称，没有则为空",'
            f'"fault_code":"仅type=problem时填，故障码","special_notes":"所有类型可用，特殊说明（用户指名处理人、额外备注等）",'
            f'"occurrence_time":"仅type=problem时填，故障发生时间","frequency":"仅type=problem时填，出现频率（每次/偶尔/首次）",'
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
            # 项目（所有类型提单必填）：对话收集 > LLM 提取 > 兜底"摇人吧服务号提单"
            "project": agent_state.collected_info.get("project", "") or analysis.get("project", "") or "摇人吧服务号提单",
            "project_id": agent_state.collected_info.get("project_id", ""),
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

        # 项目名 normalize：把用户原话里的简称（如"安吉北区"）匹配成项目库里的真实全名。
        # 匹配不上 → 兜底"摇人吧服务号提单"（同时更新 result 和 collected_info，保证一致性）
        _raw_proj = (agent_state.collected_info.get("project", "") or analysis.get("project", "")).strip()
        if _raw_proj:
            match = await self._resolve_project(_raw_proj)
            if match:
                agent_state.collected_info["project"] = match.name      # 回写全名，后续一致
                agent_state.collected_info["project_id"] = match.code   # 回写 project_id
                _raw_proj = match.name
                result["project"] = match.name          # 弹窗展示用全名（之前漏了，导致显示用户原话）
                result["project_id"] = match.code
            else:
                # 项目库匹配不上（如用户说了"摇人吧"但 DB 里没有对应项目）
                agent_state.collected_info["project"] = "摇人吧服务号提单"
                result["project"] = "摇人吧服务号提单"

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
        # 提单关口回填：主对话 LLM 可能没把用户说过的字段写进 collected_info
        await self._backfill_collected_info(session_id, agent_state, memory)
        if not agent_state.collected_info.get("project", "").strip() and not agent_state.collected_info.get("project_id", "").strip():
            raise ValueError("请先通过对话提供项目名称，再转工单。")
        if not force:
            ready, missing = _assess_ticket_readiness(agent_state)
            if not ready:
                raise ValueError(f"工单信息不足，还差：{'、'.join(missing)}。请先在对话中补充后再转工单。")

        ticket = await self._build_ticket(session_id, agent_state, memory)

        # 同一会话多次转单：ticket_seq 自增，确保 external_id 唯一（不同话题各自独立工单）
        agent_state.ticket_seq += 1
        ticket["ticket_seq"] = agent_state.ticket_seq

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

        # 保底必填字段校验（与对话路径同标准）——不足则不开弹窗，回对话补充
        # 先回填：主对话 LLM 可能没把用户说过的字段写进 collected_info
        await self._backfill_collected_info(session_id, agent_state, memory)
        # 首次转单：动态决定工单类型和必补字段（与 stream 路径一致），
        # 避免第一次只拦 project、第二次又问 robot_type 的"分批追问"
        if not agent_state.required_fields:
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
                "message": f"工单信息不足，还差：{'、'.join(missing)}。"
                           f"请直接在对话中告诉我，补全后再点转工单。",
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
        if overrides:
            draft.update(overrides)
        check = _check_required_fields(draft)
        if not check["ok"]:
            return {"code": 1, "message": check["prompt"], "missing_fields": check["missing"]}

        agent_state = _load_agent_state(memory.metadata) or AgentState(session_id=session_id)
        # 服务端兜底：保底必填字段必须已在对话中收集（弹窗不承载这些字段，防直调 API 绕过）
        # 先回填：主对话 LLM 可能没把用户说过的字段写进 collected_info
        await self._backfill_collected_info(session_id, agent_state, memory)
        ready, missing = _assess_ticket_readiness(agent_state)
        if not ready:
            logger.info(f"[confirm] 信息不足拦截: session={session_id}, missing={missing}")
            return {"code": 1, "stage": "not_ready", "missing_info": missing,
                    "message": f"工单信息不足，还差：{'、'.join(missing)}。请先在对话中补充后再提交。"}

        ticket = await self._build_ticket(session_id, agent_state, memory)
        # 用 draft 中用户编辑过的值覆盖 LLM 重新生成的字段
        ticket.update({k: v for k, v in draft.items()
                       if v and k not in ("ticket_id", "missing_fields", "confirm_prompt", "stage")})

        # 用户在弹窗里改了项目名 → 重新匹配 project_id，否则 project_id 还是旧的
        _final_project = ticket.get("project", "")
        if _final_project and _final_project != agent_state.collected_info.get("project", ""):
            match = await self._resolve_project(_final_project)
            if match:
                ticket["project"] = match.name
                ticket["project_id"] = match.code

        from ai.core.task_adapter import upsert_task
        ticket["ticket_seq"] = agent_state.ticket_seq + 1
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

    async def get_draft(self, session_id: str) -> dict:
        """获取待确认草稿（前端轮询兜底）。"""
        await self._ensure_clients()
        memory = await self._memory_manager.get_memory(session_id)
        draft = memory.metadata.get("ticket_draft")
        return {"code": 0, "data": {"draft": draft}} if draft else {"code": 0, "data": {"draft": None}}

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

    def _format_conversation(self, memory, max_turns: int = 8, from_turn: int = 0) -> str:
        """只取最近 N 条，避免长对话撑大 prompt。

        from_turn：从该 turn 索引开始（默认 0=全部）。诊断 prompt 传 context_start，
        让 LLM 只看提单后的新对话，防止它从旧对话重新提炼已提交的问题、绕过闭环保护。"""
        turns = memory.turns[from_turn:]
        turns = turns[-max_turns:] if len(turns) > max_turns else turns
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
                data = json.loads(json_str)
                thinking = data.get("thinking", "")
                action = data.get("action", "ask").strip().lower()
                if action not in ("answer", "ask", "submit"):
                    action = "ask"
                intent = data.get("intent", "").strip().lower()
                state_update = data.get("state_update", {})
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


        # 指代消解："然后呢"等省略表达 → 用上文补全为完整查询
        resolved_query, _ = await self._memory_manager.resolve_pronoun(
            request.query, request.session_id)

        # ---- 诊断路径 ----
        # 立刻发状态，别让用户干等
        yield {"event": "status", "data": {"stage": "retrieving", "round": state.diagnosis_rounds}}
        t_ret = time.perf_counter()
        logger.info(f"[stream] 开始检索: session={request.session_id}")
        # 工单填写模式不需要知识库——用户只是在填表字段，不走诊断检索；
        # 跳过检索可大幅缩小 prompt，降低 thinking 长度，提升收集轮响应速度。
        reference_docs = (
            "（跳过检索）" if request.skip_retrieval or state.ticket_collecting
            else await self._retrieve_with_context(request.session_id, state, resolved_query)
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
        _msg_yielded = False   # 是否已向用户流出消息正文（末尾兜底输出用）
        _suppress_msg = False  # 默认不抑制；complete 分支 JSON 未闭合(else)路径也读取，必须在此初始化避免 UnboundLocalError
        _msg_buf: list[str] = []  # 缓冲短消息（如 submit 的"好的"），超阈值再流式输出
        _MSG_BUF_FLUSH = 20       # 超过此字符数才流式，避免短消息先出去再卡等后续处理
        def _flush_msg_buf():
            """将缓冲的消息 token 一次性流式输出"""
            nonlocal _msg_yielded
            for t in _msg_buf:
                _msg_yielded = True
                yield {"event": "token", "data": t}
            _msg_buf.clear()
        # 流式调用，如果没有 stream 方法则回退到 complete()
        _stream = getattr(self._llm_client, "stream", None)
        _collect_mode = bool(state.ticket_collecting)  # 收集轮关 thinking，提速
        t_stream["thinking"] = "off" if _collect_mode else "on"
        logger.info(f"[stream] LLM 调用: thinking={t_stream['thinking']}, prompt_chars={t_stream['prompt_chars']}")
        try:
            if _stream is None:
                # 非流式 LLM，用 complete() + 逐字输出模拟
                raw = await self._llm_client.complete(
                    prompt=prompt, max_tokens=8000, temperature=0.5,
                    thinking=not _collect_mode)
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
                async for token in _stream(
                    prompt=prompt, max_tokens=8000, temperature=0.5,
                    thinking=not _collect_mode):
                    raw_tokens.append(token)
                    if not _json_done:
                        _buf += token
                        msg_start = _find_json_end(_buf)
                        if msg_start >= 0:
                            _json_done = True
                            _suppress_msg = False  # _suppress_doomed_submit 未实现；submit 覆盖由前端 status 清空 acc 处理
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

        # ---- Step 2: 闭环保护（基于 last_submitted_ticket + 新 problem）----
        # 在 LLM 提炼 problem_summary 之后判断：刚提完单且无新问题 → 拦截重复提单。
        _can, _reason = _can_submit(state)

        # ---- LLM 输出 action=submit → 受闭环保护 ----
        if parsed["action"] == "submit" and not _can:
            parsed["action"] = "answer"
            parsed["message"] = _reason
            logger.info(f"[stream] LLM submit 被闭环拦截")

        # 注：不再有服务端字段兜底触发提单。完全信任 LLM 的 ticket_ready / action=submit
        # 判断（实测多轮流程下 LLM 自己会 submit）。服务端只守 project 铁律 + 闭环 + 收集轮次上限。

        # ---- 工单填写模式：计数 + 字段齐/超限 → 提单 ----
        _force_submit = False  # 收集超限强制提单：跳过 project 铁律拦截（_build_ticket 兜底"摇人吧服务号提单"）
        if state.ticket_collecting:
            state.collect_rounds += 1
            await self._backfill_collected_info(request.session_id, state, memory)
            _tc_ready, _tc_missing = _assess_ticket_readiness(state)
            if _tc_ready:
                # project 已齐 → 提单
                _log_ticket_state(state, "ticket_collecting_ready_auto")
                logger.info(f"[stream] ticket_collecting 字段集齐，自动提单: query={request.query[:40]}")
                state.ticket_collecting = []
                parsed["action"] = "submit"
            elif state.collect_rounds >= _MAX_COLLECT_ROUNDS:
                # 防鬼打墙：收集超限仍不齐 → 强制提单（project 缺由 _build_ticket "摇人吧服务号提单" 兜底）
                _log_ticket_state(state, "collect_rounds_exceeded_force_submit", missing=_tc_missing)
                logger.info(f"[stream] 收集轮数超限({state.collect_rounds})，强制提单: missing={_tc_missing}")
                state.ticket_collecting = []
                _force_submit = True
                parsed["action"] = "submit"
            else:
                # 字段尚未收齐：刷新 ticket_collecting 为当前仍缺失的字段
                # （上一轮可能缺 project，这轮用户给了 project 后缺 error_message）
                state.ticket_collecting = _tc_missing

        # ---- 提单就绪门槛：project 铁律 + LLM 决定的 required_fields 全非空 ----
        #  放在 phase 转换之前：action 改 ask 后 phase 不会被置为 escalated
        if parsed["action"] == "submit" and not _force_submit:
            # 首次转单：专门调一次 LLM 决定要补哪 2-3 个字段（锁进 required_fields）
            if not state.required_fields:
                await self._decide_ticket_fields(request.session_id, state, memory)
            await self._backfill_collected_info(request.session_id, state, memory)
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

        # ---- 提前进入收集模式：LLM 说 ask 但 required_fields 已设定且有缺失 ——
        #   原逻辑只在 submit 被拦截时设 ticket_collecting（line 1905），但 LLM
        #   经常直接说 ask 逐个收集字段，导致下一轮仍跑完整检索+思考。
        #   这里提前设好，下一轮就能跳过检索、prompt 减半。
        if parsed["action"] == "ask" and not state.ticket_collecting and state.required_fields:
            await self._backfill_collected_info(request.session_id, state, memory)
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
            # 先把本轮 state（含 LLM 提炼的 problem_summary/collected_info）落盘，
            # 否则 submit() 从 memory 重新加载会拿到旧 state，闭环判定与 stream 不一致。
            _save_agent_state(memory, state)
            await self._memory_manager.save_memory(memory)
            try:
                draft = await self._build_ticket(request.session_id, state, memory)
                check = _check_required_fields(draft)
                if not check["ok"]:
                    logger.info(f"[stream] 缺必填字段: {check['missing']}, 引导用户补充")
                    # 先改 action/message + 发 status 再写 memory：避免 save_memory
                    # 抛异常时前端看到 LLM role-play 的"已生成工单"但实际没提单
                    parsed["action"] = "answer"
                    parsed["message"] = check["prompt"]
                    yield {"event": "status", "data": {
                        "stage": "need_fields", "missing_fields": check["missing"], "prompt": check["prompt"],
                    }}
                    # 丢弃 LLM 缓冲（可能含 JSON 碎片），直接用系统提示
                    _msg_buf.clear()
                    _msg_yielded = True
                    yield {"event": "token", "data": check["prompt"]}
                    try:
                        memory.metadata["ticket_draft"] = draft
                        await self._memory_manager.save_memory(memory)
                    except Exception:
                        logger.warning(f"[stream] 草稿保存失败: session={request.session_id}", exc_info=True)
                else:
                    # 字段齐全 → 不自动提单，弹窗让用户核对/修改后确认
                    # 幂等：上一轮已发 review 未确认（ticket_draft 已存在）→ 不重复发 review，只提示
                    existing_draft = memory.metadata.get("ticket_draft")
                    if existing_draft:
                        logger.info(f"[stream] 复用待确认草稿(未确认),不重复弹窗: session={request.session_id}")
                        parsed["action"] = "answer"
                        state.ticket_collecting = []
                        parsed["message"] = "您有待确认的工单，请先在弹窗中确认或修改后提交。"
                        _msg_buf.clear()
                        _msg_yielded = True
                        yield {"event": "token", "data": parsed["message"]}
                    else:
                        memory.metadata["ticket_draft"] = draft
                        # review 阶段工单未建：回退 _apply_action_phase 误置的 escalated → diagnosing，
                        # 清空 ticket_collecting 退出工单填写模式，一并持久化（原代码只改局部 state 未 save）
                        state.phase = "diagnosing"
                        state.ticket_collecting = []
                        _save_agent_state(memory, state)
                        await self._memory_manager.save_memory(memory)
                        logger.info(f"[stream] 字段齐全，弹窗确认: session={request.session_id}, force={_force_submit}")
                        yield {"event": "status", "data": {
                            "stage": "review",
                            "draft": draft,
                            "missing_fields": check["missing"],
                            "force_submit": _force_submit,
                        }}
                        # 由于不在这里提单，parsed action 改回 answer（避免 _finalize_diagnosis
                        # 以 escalated 追加 system turn 污染对话），同时不调 submit() 清空状态。
                        parsed["action"] = "answer"
                        proj = state.collected_info.get("project", "")
                        if _force_submit:
                            parsed["message"] = "信息收集超限，请核对工单信息后提交。"
                        elif proj:
                            parsed["message"] = f"已为「{proj}」生成工单草稿，请核对信息后确认提交。"
                        else:
                            parsed["message"] = "已生成工单草稿，请核对信息后确认提交。"
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
