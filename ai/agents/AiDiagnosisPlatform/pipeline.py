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
from ai.core.project_matcher import get_project_matcher

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
    pending_submit: bool = False  # 用户说了转工单但缺项目 → 等用户补完项目后自动提单
    ticket_ready: bool = False  # LLM 判断：当前信息是否足够生成有效工单
    ticket_type: str = ""  # LLM 对话中维护的工单类型：problem|bug|feature|support|other（空=未判定，按 problem 清单校验）
    ticket_collecting: list = field(default_factory=list)  # prepare 按钮返回 not_ready 后，LLM 应聚焦收集的缺失字段列表（空=正常诊断模式）
    context_start: int = 0  # 当前问题的对话起始 turn 索引（提单后更新，backfill 只看切片，防旧对话重新武装就绪判定）


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
        pending_submit=s.get("pending_submit", False),
        ticket_ready=s.get("ticket_ready", False),
        ticket_type=s.get("ticket_type", ""),
        ticket_collecting=s.get("ticket_collecting", []),
        context_start=s.get("context_start", 0),
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
        "pending_submit": state.pending_submit,
        "ticket_ready": state.ticket_ready,
        "ticket_type": state.ticket_type,
        "ticket_collecting": state.ticket_collecting,
        "context_start": state.context_start,
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
    """检查当前会话是否允许提单（闭环：防止重复提交）。

    resolved / escalated 且没有活跃问题描述时拦截；
    如果有新的 problem_summary（用户描述了新故障），则允许提单。
    """
    if state.phase in ("resolved", "escalated") and not state.problem_summary:
        if state.phase == "escalated":
            return False, "工单已提交处理中，请耐心等待工程师回复。如有新问题请先描述现象。"
        return False, "当前没有待处理的故障，无需重复提交工单。如有新问题请先描述现象。"
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


# ============================================================
# 提单就绪判定（服务端唯一真相，不信任 LLM 自评）
# ============================================================

# 泛化车型词表：LLM 把"AGV""机器人"这类泛称写进 robot_type 不算已收集
_GENERIC_MODELS = {
    "agv", "amr", "机器人", "小车", "车", "车子", "agv小车", "amr小车",
    "移动机器人", "robot", "robots", "无人车", "智能小车",
}

# 各工单类型的保底必填字段（collected_info key → 面向用户的中文名）
# 所有信息都在对话中收集；收集不齐不允许提单（对话路径和按钮路径同标准）。
_TICKET_REQUIRED_FIELDS = {
    "problem": {  # 故障报障
        "occurrence_time": "发生时间",
        "robot_type": "车型（具体型号/编号）",
        "frequency": "出现频率（每次/偶尔/首次）",
    },
    "bug": {  # 软件缺陷
        "version": "系统版本",
        "steps_to_reproduce": "复现步骤",
    },
    "feature": {  # 功能需求
        "scenario": "需求场景",
        "expected_effect": "期望效果",
    },
    "support": {  # 支持请求
        "support_type": "支持类型",
    },
}


def _infer_ticket_type(state: AgentState) -> str:
    """从已收集信息推断工单类型，仅在 submit 时 ticket_type 未设置时使用。

    工单类型不在对话过程中由 LLM 维护——用户只是咨询时不需要分类。
    只有用户明确要提单时才通过已收集的结构化字段反推类型。
    返回 "problem"|"bug"|"feature"|"support"|"other"，兜底 "problem"（清单最严，防漏）。

    推断优先级：bug > support > feature > problem(兜底)
    scenario/expected_effect 现在只由主对话 LLM 在需求类对话中填写（backfill 已禁填），
    因此 feature 信号可信度提高——当 feature 和 problem 信号共存时优先 feature。
    """
    ci = state.collected_info
    # 软件缺陷特征优先（version/复现步骤是强 bug 信号）
    if ci.get("version") or ci.get("steps_to_reproduce"):
        return "bug"
    # 支持请求特征（明确的 support_type 值）
    if ci.get("support_type"):
        return "support"
    # 需求/功能类特征——scenario/expected_effect 只由主对话 LLM 在需求对话中填写
    # （backfill 已禁填这两个字段），因此命中=高置信度需求
    if ci.get("scenario") or ci.get("expected_effect"):
        return "feature"
    # 报障信号
    if ci.get("robot_type") or ci.get("occurrence_time") or ci.get("frequency"):
        return "problem"
    # 兜底：按报障处理（必填字段最多、最严）
    return "problem"


def _assess_ticket_readiness(state: AgentState) -> tuple[bool, list[str]]:
    """服务端提单就绪判定：按工单类型检查保底必填字段是否已在对话中收集齐。

    不信任 LLM 的 ticket_ready 自评，也不看 problem_summary（LLM 可把
    "车不动了"扩写成一段长文骗过长度校验）——只认 collected_info 里的
    结构化字段。ticket_type 未判定时从 collected_info 推断，推断不出按
    problem 清单兜底（最严，防漏）。
    返回 (ready, missing)：missing 为面向用户的缺失项中文名列表。
    """
    ticket_type = state.ticket_type or _infer_ticket_type(state)
    required = _TICKET_REQUIRED_FIELDS.get(ticket_type) or _TICKET_REQUIRED_FIELDS["problem"]
    missing = []
    for field_key, label in required.items():
        v = (state.collected_info.get(field_key) or "").strip()
        if not v:
            missing.append(label)
            continue
        if field_key == "robot_type" and v.lower() in _GENERIC_MODELS:
            missing.append(f"{label}（“{v}”太笼统，需要具体型号/编号）")
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
        "pending_submit": state.pending_submit,
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

DIAGNOSIS_PROMPT = """你是 U老师，是「摇人吧」微信服务号的 AI 诊断助手，面向 AGV/AMR（工业移动机器人）行业的技术支持专家。
你的服务对象是现场工程师、客户和项目管理人员。

你的名字是"U老师"，严禁自称其他名字（如"小U""AI助手""智能助手"等）。
只在用户问"你是谁"或首次对话打招呼时才说"我是U老师"，其他情况不要重复自我介绍。

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
4. **知识库（操作手册）**：howto 类操作问题走这里，按前提→操作→预期结果给出步骤。

⚠️ **关键**：各知识源不互斥！先看 FAQ/车端错误码有没有现成答案，有就直接用。

## ⛔ 转工单规则（优先级最高，优先于所有意图判断）

用户表示要创建/提交工单时（不管措辞如何，包括"提工单""提单""帮我转""下工单""创建工单""给我提一个"等），**必须按顺序判断**：
1. ticket_ready 为 false（保底必填字段还没收集齐）→ action 设为 "ask"，追问缺失的必填字段（一次只问一个）。
   **即使用户催促、不耐烦、反复要求直接提单，也必须先收集齐必填字段，严禁 submit。**
2. ticket_ready 为 true → action 设为 "submit"，message 写"好的"即可（系统会自动生成提单确认消息）。

用户表示不想继续排查时（如"不想排查""算了""不用了"等）：
→ action 设为 "answer"，回复简短收尾（如"好的，有需要随时找我"），
  不要追问、不要继续排查。

### 提单前信息检查
用户表示要转工单时，先判断对话中是否已收集到足够的故障/需求信息。
- **信息不足** → action 设为 "ask"，追问缺失的关键信息（一次只问一个），不要盲目提单。
  **即使用户催促、不耐烦、反复要求直接提单，也必须先收集齐信息，严禁 submit。**
  报障类至少需要：发生时间(occurrence_time)、具体车型/编号(robot_type，不能是"AGV""机器人"等泛称)、出现频率(frequency)。
  需求类至少需要：需求场景(scenario)、期望效果(expected_effect)。
  缺陷类至少需要：版本号(version)、复现步骤(steps_to_reproduce)。
  ⚠️ **所有类型都必须收集项目名称(project)**——放在 collected_info 里，和 scenario/occurrence_time 同级。
- **信息充足** → action 设为 "submit"，message 写"好的"即可（系统会自动生成提单确认消息）。
**注意：服务端会按上述清单做硬校验——信息不足时即使 action=submit 也会被拦截并追问。**
**禁止过度追问**：追问只允许针对上述保底必填字段。字段集齐后，禁止再以"确认细节""确认呈现形式""固定还是动态"等理由继续提问——细节由工程师评估时确认。
字段集齐且用户已表达提单/生成意愿（或上一轮你说过"确认后帮你提单"）时，本轮直接 action=submit，不要再问。

### collected_info 写入铁律（极其重要）
**每一轮**用户发言后，不管 action 是什么，只要用户提供了可用于 collected_info 的信息，**必须**在 state_update 的 collected_info 中写入对应字段：
- 用户描述了**使用场景/痛点** → 写入 scenario（如"货架倾斜AGV入库存取货时角度不准"）
- 用户说了**想要什么效果/怎么做** → 写入 expected_effect（如"库位配置加航向角字段，下发给车自动调整"）
- 用户说了**项目/现场名称** → 写入 project
- 用户说了**车型/编号**（不是"AGV""机器人"这种泛称）→ 写入 robot_type
- 用户说了**时间** → 写入 occurrence_time
- 用户说了**频率**（每次/偶尔/首次）→ 写入 frequency
**不要等到"信息齐全"才一次性填——每轮都要增量更新。否则服务端会因 collected_info 为空而误判工单类型。**

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
- 知识库每个 chunk 以 `---` 分隔，标题在 `知识库 N（标题）：`、`FAQ N：`、`🚗 车端错误码 N：` 或 `🌐 翻译表 N：` 中标明。
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
{{"action":"answer|ask|submit","intent":"howto|troubleshoot|chat","state_update":{{"problem_summary":"概述","ruled_out":[],"hypotheses":[],"collected_info":{{}},"ticket_ready":false}}}}
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
            # 全新话题
            agent_state.phase = "idle"
            agent_state.original_query = request.query
            agent_state.problem_summary = request.query
            _save_agent_state(memory, agent_state)
            await self._memory_manager.save_memory(memory)
        elif agent_state.phase == "resolved" and not agent_state.problem_summary:
            # 工单刚提交、状态已清空。判断用户是补充信息还是描述新故障：
            # 短消息（≤10字）视为补充信息，长消息可能描述新问题，正常启动诊断。
            if len(request.query.strip()) > 10:
                agent_state.phase = "diagnosing"
                agent_state.original_query = request.query
                agent_state.problem_summary = request.query
                _save_agent_state(memory, agent_state)
                await self._memory_manager.save_memory(memory)
            # 短消息 → pass，不设 problem_summary，_can_submit 继续保持拦截

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
        # 按钮提单路径：prepare 返回 not_ready 后，LLM 应聚焦收集工单字段，停止排查
        if state.ticket_collecting:
            fields = "、".join(state.ticket_collecting)
            ticket_collecting_context = (
                f"⚠️ 用户刚才点击了转工单按钮，但信息不足。当前处于**工单填写模式**，请不要再排查故障。\n"
                f"用户接下来的发言都是补充工单所需信息，请逐项确认并记录到 collected_info，"
                f"缺什么就问什么。信息补齐后提醒用户「信息已齐，请点转工单按钮」即可。\n"
                f"缺失字段：{fields}"
            )
        else:
            ticket_collecting_context = "（正常诊断模式）"
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
            # 合并新字段，空值/无 视为清除
            # project 只能由用户显式输入经 _resolve_project 设置，LLM 无权改动
            for k, v in state_update["collected_info"].items():
                if k == "project":
                    continue
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
        if action == "answer":
            state.phase = "resolved"
        elif action == "submit":
            state.phase = "escalated"

    async def _resolve_project(self, raw_name: str) -> str:
        """将用户输入的项目名匹配到 helpdesk_724.project 标准名。

        单候选直接返回，多候选调 LLM 裁决，无匹配返回原始输入。
        """
        if not raw_name or not raw_name.strip():
            return raw_name
        try:
            matcher = get_project_matcher()
            if not await matcher.ensure_loaded():
                logger.warning("[pipeline] project DB unavailable, using raw input")
                return raw_name.strip()
            user = raw_name.strip()
            candidates = await matcher.get_candidates_async(user, min_score=0.7)
            if not candidates:
                return user
            if len(candidates) == 1:
                return candidates[0].name
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
                logger.info(f"[pipeline] LLM 裁决项目: '{user}' → #{idx} '{candidates[idx-1].name}'")
                return candidates[idx - 1].name
            logger.info(f"[pipeline] LLM 无法裁决项目 '{user}'，使用原始输入")
            return user
        except Exception as e:
            logger.warning(f"[pipeline] project matching failed: {e}")
            return raw_name.strip()

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
        logger.info(f"[retrieve] 进入检索: session={session_id[:8]}")
        try:
            # ⚠️ 不使用原始用户消息拼接检索 query——旧话题关键词会污染新话题检索
            # 只用指代消解后的当前查询 + LLM 提炼的问题概述 + 推测
            search_query = resolved_query if resolved_query else state.original_query
            if state.problem_summary:
                search_query = search_query + " " + state.problem_summary
            if state.hypotheses:
                search_query = search_query + " " + " ".join(state.hypotheses)

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

            # unified formatting: cheduan exact match first, then all 3-domain results
            all_results = list(_cheduan_exact) + list(team_results) + list(company_results) + list(industry_results)
            for r in all_results:
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
    async def _backfill_collected_info(self, session_id: str, agent_state: AgentState, memory) -> None:
        """提单前专用回填：主对话 LLM 经常嘴上"已记录"但没写进 collected_info，
        这里对当前问题的对话做一次聚焦提取，把提到的字段补齐（不覆盖已有值）。
        仅在 submit/prepare/pending_submit 等提单关口调用，一轮一次 LLM 调用。
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
                if v and v not in ("无", "不清楚", "不知道", "暂无", "未知"):
                    agent_state.collected_info[k] = v
                    filled.append(k)
            if filled:
                logger.info(f"[backfill] 从对话回填 collected_info: session={session_id[:8]}, fields={filled}")
        except Exception:
            logger.warning(f"[backfill] 回填失败（忽略，按原 collected_info 判定）: session={session_id[:8]}",
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
            f'"fault_code":"仅type=problem时填，故障码","special_notes":"仅type=problem时填，特殊说明",'
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
        result = {
            "ticket_id": f"AI-{session_id[-6:]}-{int(time.time()) % 100000}",
            "session_id": session_id,
            "type": ticket_type,
            "title": analysis.get("title", agent_state.original_query[:20]),
            "description": analysis.get("description", agent_state.problem_summary[:150]),
            "priority": analysis.get("priority", "中"),
            "status": "pending",
            "contact": analysis.get("contact", ""),
            # 项目（所有类型提单必填）：优先取对话中已收集的，不区分工单类型
            "project": agent_state.collected_info.get("project", "") or analysis.get("project", ""),
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

        # 类型专属字段
        if ticket_type == "problem":
            result["location"] = analysis.get("location", "")
            # 保底必填字段优先取 collected_info（对话中用户实际提供、已过服务端校验）
            result["robot_type"] = agent_state.collected_info.get("robot_type", "") or analysis.get("robot_type", "")
            result["fault_code"] = analysis.get("fault_code", "")
            result["special_notes"] = analysis.get("special_notes", "")
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

    async def submit(self, session_id: str, created_by: str = "") -> dict:
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
        agent_state.ticket_ready = False
        agent_state.ticket_type = ""
        agent_state.ticket_collecting = []  # 工单已提交，退出工单填写模式
        agent_state.context_start = len(memory.turns)  # 旧对话归档：backfill 只看之后的 turns
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
        ready, missing = _assess_ticket_readiness(agent_state)
        if not ready:
            logger.info(f"[prepare] 信息不足拦截: session={session_id[:8]}, "
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

        logger.info(f"[prepare] session={session_id[:8]}, stage={'draft_ready' if check['ok'] else 'need_fields'}, "
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
            logger.info(f"[confirm] 信息不足拦截: session={session_id[:8]}, missing={missing}")
            return {"code": 1, "stage": "not_ready", "missing_info": missing,
                    "message": f"工单信息不足，还差：{'、'.join(missing)}。请先在对话中补充后再提交。"}

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
        agent_state.ticket_ready = False
        agent_state.ticket_type = ""
        agent_state.ticket_collecting = []  # 工单已提交，退出工单填写模式
        agent_state.context_start = len(memory.turns)  # 旧对话归档：backfill 只看之后的 turns
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

    def _suppress_doomed_submit(self, state: AgentState, json_header: str) -> bool:
        """流式 JSON 头解析完成时调用：若 LLM 要 submit 但注定被服务端拦截
        （闭环保护 / 保底必填字段不足），提前应用 state_update 并返回 True——
        调用方据此抑制 LLM 的消息流（通常只是"好的"），避免用户先看到"好的"
        再看到拦截/追问话术。解析失败或非 submit → False（不影响后续完整解析）。
        """
        try:
            txt = re.sub(r"^```(?:json)?\s*|\s*```\s*$", "", json_header.strip(), flags=re.MULTILINE).strip()
            data = json.loads(txt)
            if not isinstance(data, dict) or data.get("action") != "submit":
                return False
            self._apply_state_update(state, data.get("state_update") or {})
            can, _ = _can_submit(state)
            ready, missing = _assess_ticket_readiness(state)
            if not can or not ready:
                logger.info(f"[stream] 提前拦截 submit: can={can}, ready={ready}, missing={missing}, 抑制 LLM 消息流")
                return True
            return False
        except Exception:
            return False

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
            # 全新话题
            agent_state.phase = "idle"
            agent_state.original_query = request.query
            agent_state.problem_summary = request.query
            _save_agent_state(memory, agent_state)
            await self._memory_manager.save_memory(memory)
        elif agent_state.phase == "resolved" and not agent_state.problem_summary:
            # 工单刚提交、状态已清空。判断用户是补充信息还是描述新故障：
            # 短消息（≤10字）视为补充信息，长消息可能描述新问题，正常启动诊断。
            if len(request.query.strip()) > 10:
                agent_state.phase = "diagnosing"
                agent_state.original_query = request.query
                agent_state.problem_summary = request.query
                _save_agent_state(memory, agent_state)
                await self._memory_manager.save_memory(memory)
            # 短消息 → pass，不设 problem_summary，_can_submit 继续保持拦截

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

        # ---- 转工单关键词 → 直接拦截常见情况，不调 LLM ----
        # 注意：必须在 state.phase = "diagnosing" 之前，否则 _can_submit 拿不到真实 phase
        _short_kw = ("转工单", "转单", "生成工单", "提交工单", "提单", "提个工单", "提工单", "帮我转", "我要转", "帮我提单")
        if any(kw in request.query for kw in _short_kw):
            _log_ticket_state(state, "keyword_hit")
            _can, _reason = _can_submit(state)
            if not _can:
                # 已提交且无新问题 → 直接拒绝
                _log_ticket_state(state, "keyword_blocked", block_reason=_reason[:30])
                logger.info(f"[stream] _can_submit 拦截: session={request.session_id[:8]}, phase={state.phase}")
                for ch in _reason:
                    yield {"event": "token", "data": ch}
                result = await self._finalize_diagnosis(
                    request.session_id, state,
                    thinking="", action="answer", message=_reason,
                    streaming=True)
                yield {"event": "result", "data": result}
                return
            if not state.collected_info.get("project", "").strip() and not state.collected_info.get("project_id", "").strip():
                # 缺项目 → 引导补充，记住"在等提单"，下一轮用户补完自动触发
                _log_ticket_state(state, "keyword_no_project")
                logger.info(f"[stream] 缺项目直接拦截: session={request.session_id[:8]}")
                state.pending_submit = True
                short_msg = "请给出工单关联的项目名称，我好帮你提交工单。"
                for ch in short_msg:
                    yield {"event": "token", "data": ch}
                result = await self._finalize_diagnosis(
                    request.session_id, state,
                    thinking="", action="answer", message=short_msg,
                    streaming=True)
                yield {"event": "result", "data": result}
                return
            # 保底必填字段收集齐了才短路提单，不够则走 LLM 让 AI 追问（服务端重算，不信 LLM 自评）
            # 先回填：主对话 LLM 可能没把用户说过的字段写进 collected_info
            await self._backfill_collected_info(request.session_id, state, memory)
            _ready, _missing = _assess_ticket_readiness(state)
            if _ready:
                _log_ticket_state(state, "keyword_direct_submit")
                logger.info(f"[stream] 关键词直接提单: query={request.query[:40]}, created_by={request.created_by}")
                state.diagnosis_rounds += 1
                state.phase = "diagnosing"
                try:
                    yield {"event": "status", "data": {"stage": "submitting"}}
                    ticket_data = await self.submit(request.session_id, created_by=request.created_by)
                    # submit() 已清空诊断状态并保存，刷新本地 state 避免 _finalize_diagnosis 覆写旧状态
                    memory = await self._memory_manager.get_memory(request.session_id)
                    state = _load_agent_state(memory.metadata) or state
                    ticket_info = ticket_data.get('data', {}).get('ticket', {})
                    yield {"event": "status", "data": {
                        "stage": "submitted",
                        "ticket_id": ticket_info.get("ticket_id", ""),
                        "title": ticket_info.get("title", ""),
                        "db_id": ticket_data.get("data", {}).get("db_id", 0),
                    }}
                    proj = state.collected_info.get("project", "")
                    short_msg = f"好的，已为「{proj}」生成工单，工程师会尽快处理。" if proj else "好的，已为你生成工单，工程师会尽快处理。"
                    for ch in short_msg:
                        yield {"event": "token", "data": ch}
                    result = await self._finalize_diagnosis(
                        request.session_id, state,
                        thinking="", action="answer", message=short_msg,
                        streaming=True)
                    result["ticket"] = ticket_data
                    yield {"event": "result", "data": result}
                    return
                except Exception as e:
                    logger.error(f"[stream] 关键词直接提单失败: {e}", exc_info=True)
                    yield {"event": "status", "data": {"stage": "submit_failed", "error": str(e)}}
                    short_msg = "提单过程中出现异常，请稍后重试或联系管理员。"
                    for ch in short_msg:
                        yield {"event": "token", "data": ch}
                    result = await self._finalize_diagnosis(
                        request.session_id, state,
                        thinking="", action="answer", message=short_msg,
                        streaming=True)
                    yield {"event": "result", "data": result}
                    return
            # ticket_ready 为 false → 不短路，继续走 LLM 诊断流程让 AI 判断追什么
            _log_ticket_state(state, "keyword_fallthrough_llm", missing=_missing)

        # ---- pending_submit 快捷提单：上一轮缺项目被拦截，本轮补了项目 ----
        #  保底必填字段齐 → 直接提单；不齐 → 走 LLM 让 AI 继续收集故障信息
        if state.pending_submit:
            _log_ticket_state(state, "pending_submit_enter", raw_query=request.query[:30])
            state.pending_submit = False
            raw_project = request.query.strip()
            state.collected_info["project"] = await self._resolve_project(raw_project)
            # 回填：之前聊过的字段（发生时间/车型/频率等）主对话 LLM 可能没写进 collected_info
            await self._backfill_collected_info(request.session_id, state, memory)
            _ready, _missing = _assess_ticket_readiness(state)
            logger.info(f"[stream] pending_submit: raw={raw_project} -> project={state.collected_info['project']}, ready={_ready}")

            if _ready:
                # 信息足够 → 直接提单
                _log_ticket_state(state, "pending_submit_direct")
                state.diagnosis_rounds += 1
                state.phase = "diagnosing"
                try:
                    yield {"event": "status", "data": {"stage": "submitting"}}
                    ticket_data = await self.submit(request.session_id, created_by=request.created_by)
                    memory = await self._memory_manager.get_memory(request.session_id)
                    state = _load_agent_state(memory.metadata) or state
                    ticket_info = ticket_data.get('data', {}).get('ticket', {})
                    yield {"event": "status", "data": {
                        "stage": "submitted",
                        "ticket_id": ticket_info.get("ticket_id", ""),
                        "title": ticket_info.get("title", ""),
                        "db_id": ticket_data.get("data", {}).get("db_id", 0),
                    }}
                    proj = state.collected_info.get("project", "")
                    short_msg = f"好的，已为「{proj}」生成工单，工程师会尽快处理。" if proj else "好的，已为你生成工单，工程师会尽快处理。"
                    for ch in short_msg:
                        yield {"event": "token", "data": ch}
                    result = await self._finalize_diagnosis(
                        request.session_id, state,
                        thinking="", action="answer", message=short_msg,
                        streaming=True)
                    result["ticket"] = ticket_data
                    yield {"event": "result", "data": result}
                    return
                except Exception as e:
                    logger.error(f"[stream] pending_submit 提单失败: {e}", exc_info=True)
                    yield {"event": "status", "data": {"stage": "submit_failed", "error": str(e)}}
                    short_msg = "提单过程中出现异常，请稍后重试或联系管理员。"
                    for ch in short_msg:
                        yield {"event": "token", "data": ch}
                    result = await self._finalize_diagnosis(
                        request.session_id, state,
                        thinking="", action="answer", message=short_msg,
                        streaming=True)
                    yield {"event": "result", "data": result}
                    return
            else:
                # 保底字段不齐 → 项目已收到，但故障信息不足，继续走 LLM 让 AI 追问
                _log_ticket_state(state, "pending_submit_fallthrough", missing=_missing)
                logger.info(f"[stream] pending_submit 信息不足，走 LLM 继续收集: project={state.collected_info['project']}, missing={_missing}")
                # 不设置 diagnosis_rounds/phase，留给后面通用流程统一处理

        # 不是转工单关键词 → 清除 pending_submit
        state.pending_submit = False
        # 闭环绕过防护：工单刚提交后 phase=resolved，用户发短消息（如"本川项目"）
        # 是补充信息而非新故障。跳过 phase 切换，保持 resolved → _can_submit 继续拦截。
        # run_stream 入口已判断：长消息(>10字)进入时已设 phase=diagnosing，此处不冲突。
        if state.phase == "resolved":
            logger.info(f"[stream] 保持 phase=resolved（短消息/补充信息），不启动新诊断: "
                        f"query={request.query[:30]}")
        else:
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
        _msg_yielded = False   # 是否已向用户流出消息正文（被抑制时为 False，走末尾兜底输出）
        _suppress_msg = False  # LLM 要 submit 但注定被拦截 → 抑制其消息流（通常只是"好的"）
        # 流式调用，如果没有 stream 方法则回退到 complete()
        _stream = getattr(self._llm_client, "stream", None)
        try:
            if _stream is None:
                # 非流式 LLM，用 complete() + 逐字输出模拟
                raw = await self._llm_client.complete(prompt=prompt, max_tokens=1500, temperature=0.5)
                if t_first_llm is None:
                    t_first_llm = time.perf_counter()
                    t_stream["llm_first_token"] = round((t_first_llm - t_llm) * 1000)
                # 拆出 JSON 区域和消息区域，只流式输出消息
                _msg_start = _find_json_end(raw)
                if _msg_start >= 0:
                    raw_tokens.append(raw[:_msg_start])  # JSON 部分
                    _suppress_msg = self._suppress_doomed_submit(state, raw[:_msg_start])
                    msg_body = raw[_msg_start:]
                else:
                    msg_body = raw
                if not _suppress_msg:
                    for ch in msg_body:
                        _msg_yielded = True
                        yield {"event": "token", "data": ch}
                raw_tokens.append(msg_body)
            else:
                async for token in _stream(prompt=prompt, max_tokens=1500, temperature=0.5):
                    raw_tokens.append(token)

                    if not _json_done:
                        _buf += token
                        msg_start = _find_json_end(_buf)
                        if msg_start >= 0:
                            _json_done = True
                            # JSON 头已完整：submit 注定被拦截（闭环/保底字段不足）→ 抑制消息流
                            _suppress_msg = self._suppress_doomed_submit(state, _buf[:msg_start])
                            tail = _buf[msg_start:]
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

        # ---- Step 1: 先应用 LLM 提炼的 state_update（含 problem_summary），
        #     让 _can_submit 基于 LLM 判断后的有效问题描述做决策 ----
        self._apply_state_update(state, parsed["state_update"])

        # ---- Step 2: 闭环保护（基于 LLM 提炼后的 problem_summary）----
        _can, _reason = _can_submit(state)
        if any(kw in request.query for kw in ("转工单", "转单", "生成工单", "提交工单", "提单", "提个工单", "提工单", "帮我转", "我要转", "帮我提单")) and not _can:
            logger.info(f"[stream] 重复提单拦截: phase={state.phase}")
            parsed["action"] = "answer"
            parsed["message"] = _reason

        # ---- LLM 输出 action=submit → 同样受闭环保护 ----
        if parsed["action"] == "submit" and not _can:
            parsed["action"] = "answer"
            parsed["message"] = _reason
            logger.info(f"[stream] LLM submit 被闭环拦截: phase={state.phase}")

        # ---- 保底必填字段不足 → submit 转为确定性追问（不提单、不报"提单异常"）----
        #  放在 phase 转换之前：action 改 ask 后 phase 不会被置为 escalated
        if parsed["action"] == "submit":
            # 回填：用户说过的字段主对话 LLM 可能没写进 collected_info
            await self._backfill_collected_info(request.session_id, state, memory)
            _as_ready, _as_missing = _assess_ticket_readiness(state)
            if not _as_ready:
                _log_ticket_state(state, "submit_blocked_not_ready", missing=_as_missing)
                logger.info(f"[stream] 提单拦截(保底字段不足): missing={_as_missing}")
                parsed["action"] = "ask"
                parsed["message"] = _missing_info_message(_as_missing)
                yield {"event": "status", "data": {"stage": "need_info", "missing_info": _as_missing}}

        # ---- pending_submit 自动提单：上一轮缺项目被拦截，本轮补了项目 → 触发提单 ----
        #  注意：新的 pending_submit 处理已在 LLM 调用前完成（见上文 line ~1558），
        #  此分支仅作为兜底：如果 LLM 回复后 pending_submit 仍为 true（极端边缘情况）
        if state.pending_submit and _can and parsed["action"] != "submit":
            if state.collected_info.get("project", "").strip() or state.collected_info.get("project_id", "").strip():
                _ps_ready, _ps_missing = _assess_ticket_readiness(state)
                if _ps_ready:
                    _log_ticket_state(state, "pending_submit_auto")
                    state.pending_submit = False
                    # 匹配项目名到数据库标准名
                    raw_proj = state.collected_info.get("project", "")
                    if raw_proj.strip():
                        state.collected_info["project"] = await self._resolve_project(raw_proj)
                        logger.info(f"[stream] pending_submit 自动提单: raw={raw_proj} -> project={state.collected_info['project']}")
                    parsed["action"] = "submit"
                else:
                    _log_ticket_state(state, "pending_submit_auto_skip", missing=_ps_missing)
                    state.pending_submit = False

        # ---- Step 3: 应用 action → phase 转换 ----
        self._apply_action_phase(state, parsed["action"])

        # ---- 服务端兜底：LLM 嘴嗨说"已生成工单"但 action 没设 submit，或用户消息含工单
        #  意图但 LLM 没识别 → 都先过服务端就绪判定，不达标一律不强制提单 ----
        _msg = parsed.get("message", "")
        _llm_claimed_submit = bool(re.search(
            r'已(生成|提交|创建)|工单已|已为你|'
            r'(马上|立刻|这就|现在|这就去|帮你|为您).{0,4}(生成|提交|创建|提单)', _msg))
        _user_wants_submit = bool(re.search(r'(提|转|生成|提交|下|创建|开|帮我|给我).{0,4}(工单|单子)|(工单|单子).{0,4}(提|转|生成|提交|下|创建)', request.query))
        if _can and parsed["action"] != "submit":
            # 回填：用户说过的字段主对话 LLM 可能没写进 collected_info
            await self._backfill_collected_info(request.session_id, state, memory)
            _sn_ready, _sn_missing = _assess_ticket_readiness(state)
            if _llm_claimed_submit and _sn_ready:
                _log_ticket_state(state, "safety_net_llm_claimed")
                logger.info(f"[stream] 服务端兜底提单(llm_claimed): query={request.query[:40]}")
                parsed["action"] = "submit"
            elif _llm_claimed_submit:
                # LLM 声称已提单但保底字段不足 → 不提单（其消息已流出，记日志，
                # 用户下一轮再提时按就绪判定正常走追问）
                _log_ticket_state(state, "safety_net_llm_claimed_skip", missing=_sn_missing)
                logger.info(f"[stream] LLM 声称已提单但信息不足，不强制提单: missing={_sn_missing}")
            elif _user_wants_submit and _sn_ready:
                _log_ticket_state(state, "safety_net_user_wants")
                logger.info(f"[stream] 服务端兜底提单(user_wants+ready): query={request.query[:40]}")
                parsed["action"] = "submit"
            elif _user_wants_submit:
                _log_ticket_state(state, "safety_net_skip", missing=_sn_missing)
                logger.info(f"[stream] 服务端兜底跳过(信息不足): query={request.query[:40]}, missing={_sn_missing}")

        # ---- 自动提单：LLM 输出 action=submit 时先校验必填字段，完整则直接提单 ----
        ticket_data = None
        if parsed["action"] == "submit":
            _log_ticket_state(state, "llm_action_submit")
            try:
                draft = await self._build_ticket(request.session_id, state, memory)
                check = _check_required_fields(draft)
                if not check["ok"]:
                    logger.info(f"[stream] 缺必填字段: {check['missing']}, 引导用户补充")
                    # 先改 action/message + 发 status 再写 memory：避免 save_memory
                    # 抛异常时前端看到 LLM role-play 的"已生成工单"但实际没提单
                    parsed["action"] = "answer"
                    parsed["message"] = check["prompt"]
                    # 记住"在等用户补项目"：下一轮用户输入直接走 pending_submit
                    # 确定性提取+自动提单，不再依赖主对话 LLM 把项目写进 collected_info
                    if "project" in check["missing"]:
                        state.pending_submit = True
                    yield {"event": "status", "data": {
                        "stage": "need_fields", "missing_fields": check["missing"], "prompt": check["prompt"],
                    }}
                    # 如果 LLM 已经流式输出了误导消息（如"已生成工单"），
                    # 把正确的提示语作为追加 token 发出去覆盖误导
                    if _msg_yielded:
                        yield {"event": "token", "data": "\n\n⚠️ " + check["prompt"]}
                    try:
                        memory.metadata["ticket_draft"] = draft
                        await self._memory_manager.save_memory(memory)
                    except Exception:
                        logger.warning(f"[stream] 草稿保存失败: session={request.session_id[:8]}", exc_info=True)
                else:
                    yield {"event": "status", "data": {"stage": "submitting"}}
                    ticket_data = await self.submit(request.session_id, created_by=request.created_by)
                    ticket_info = ticket_data.get('data', {}).get('ticket', {})
                    logger.info(f"[stream] 自动提单成功: session={request.session_id[:8]}, "
                                f"ticket={ticket_info.get('ticket_id', '?')}")
                    proj = state.collected_info.get("project", "")
                    parsed["message"] = f"好的，已为「{proj}」生成工单，工程师会尽快处理。" if proj else "好的，已为你生成工单，工程师会尽快处理。"
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
                # 兜底：防止 LLM role-play "已生成工单" 但实际提单失败
                if parsed["action"] == "submit":
                    parsed["action"] = "answer"
                    parsed["message"] = "提单过程中出现异常，请稍后重试或联系管理员。"

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
