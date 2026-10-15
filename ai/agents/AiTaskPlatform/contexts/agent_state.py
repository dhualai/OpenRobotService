"""工单 Agent 状态：U老师 对一张工单的结构化工作记忆。

## 为什么要有它

讨论区每轮 @U老师 都要「知道之前发生了什么」。原实现把该工单**全部评论原文**每轮塞进
prompt —— 长工单 token 浪费；而只有一句话的 `metadata_info.ai_summary` 又太粗（丢关键信息）。
折中是分层：

    进 prompt = 结构化状态（覆盖较早的全部讨论）
              + 最近 N 条讨论原文（逐字，`AGENT_STATE_RAW_WINDOW`）
              + 本轮问题 / 引用 / 附件

短工单（评论 < N 条）状态为空、全部原文 —— 与改造前完全一致，**无回归**；
机制随工单变长才逐步接管。

## 两个关键性质

1. **可丢弃、可重建**：评论原文永远在 `task_comments`，本表数据坏了直接删行重建即可。
   所以不需要 `AiDiagnosisPlatform.AgentState` 那套复杂的三态迁移纪律
   （那边是 session 维度、Redis 里唯一副本，坏了真没了）。
2. **允许有损**：漏了什么，agent 还能用 `discussion_read` 能力回去读原文。
   这是它与「一句话摘要」最大的区别 —— 摘要是唯一副本，状态只是索引。

## 与诊断侧的 AgentState 刻意不共用

两个 Agent 零交叉导入、各留一份实现（见 issues.md「刻意不共用」）：那边是 session 维度、
存 Redis、字段与提单流程强耦合；这边是 ticket 维度、存 DB、字段只服务讨论问答。
形状上互为参照，实现不共享。

## 状态字段（沿用 history_compact 的九栏口径）

目标 / 当前判断 / 已确认事实（带出处）/ 已排除方向（带依据）/ 失败过的尝试 /
关键标识（车号、故障码、版本、时间——逐字保留）/ 未完成的下一步 / 还没定的问题 /
被纠正过的结论
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ai.core.logging import get_logger

logger = get_logger("TASK_AGENT")

# 九栏：键 → 展示标题。顺序即渲染顺序。
STATE_FIELDS: list[tuple[str, str]] = [
    ("goal", "目标"),
    ("current_judgment", "当前判断"),
    ("confirmed_facts", "已确认的事实（带出处）"),
    ("ruled_out", "已排除的方向（带依据）"),
    ("failed_attempts", "失败过的尝试"),
    ("key_identifiers", "关键标识"),
    ("open_next_steps", "未完成的下一步"),
    ("open_questions", "还没定的问题"),
    ("corrected", "被纠正过的结论"),
]

EMPTY_STATE: Dict[str, Any] = {k: ([] if k not in ("goal", "current_judgment") else "")
                               for k, _ in STATE_FIELDS}


def empty_state() -> Dict[str, Any]:
    return {k: ("" if k in ("goal", "current_judgment") else []) for k, _ in STATE_FIELDS}


def normalize_state(raw: Any) -> Dict[str, Any]:
    """把任意来源的状态规整成九栏结构；缺失的键补空、类型不对的丢弃。

    防御式加载：状态是 JSON 列，可能被历史版本写脏、也可能手工改错。
    坏数据一律**降级为空**而不是抛异常 —— 状态没了不致命（原文还在）。
    """
    out = empty_state()
    if not isinstance(raw, dict):
        return out
    for key, _ in STATE_FIELDS:
        val = raw.get(key)
        if key in ("goal", "current_judgment"):
            out[key] = str(val).strip() if isinstance(val, (str, int, float)) else ""
        else:
            if isinstance(val, list):
                out[key] = [v for v in val if v not in (None, "", {}, [])]
            elif isinstance(val, (str, dict)) and val:
                out[key] = [val]
    return out


def is_empty(state: Any) -> bool:
    """状态是否没有任何内容（用于「短工单不注入状态块」）。"""
    s = normalize_state(state)
    return all(not s.get(k) for k, _ in STATE_FIELDS)


def _render_value(val: Any) -> List[str]:
    if isinstance(val, list):
        lines: List[str] = []
        for item in val:
            if isinstance(item, dict):
                # 支持 {fact, source} / {direction, why} 这类带出处的结构
                main = item.get("fact") or item.get("direction") or item.get("text") \
                    or item.get("content") or ""
                why = item.get("source") or item.get("why") or item.get("reason") or ""
                text = str(main).strip()
                if not text:
                    continue
                lines.append(f"- {text}" + (f"（出处：{why}）" if why else ""))
            else:
                text = str(item).strip()
                if text:
                    lines.append(f"- {text}")
        return lines
    text = str(val or "").strip()
    return [text] if text else []


def render_state_block(state: Any) -> str:
    """把状态渲染成 prompt 段。**空状态返回空串**（调用方据此整段省略）。"""
    s = normalize_state(state)
    if is_empty(s):
        return ""
    parts: List[str] = [
        "## 工单状态（此前讨论的结构化结论）",
        "这是本工单此前讨论沉淀下来的结论层，按时间早于下面「最近讨论」的原文。",
    ]
    for key, title in STATE_FIELDS:
        lines = _render_value(s.get(key))
        if not lines:
            continue
        parts.append(f"### {title}")
        parts.extend(lines)
    return "\n".join(parts) + "\n"


# ── 折叠（把新评论并入旧状态）──────────────────────────────

MERGE_SYSTEM_PROMPT = """你在维护一张工单讨论的「结构化状态」，供后续回答时接着用。

规则：
- 工单号、车号、故障码、版本、路径、命令、报错原文、时间点**保持原样**，不要改写或概括。
- 不确定的地方保持不确定，不要收成一句肯定结论。
- 与旧状态矛盾的新材料，以新材料为准，并把旧的结论移到「被纠正过的结论」。
- 没有内容的栏目保留空（不要写「无」）。
- 闲聊、客套不要写进去。
- 只输出 JSON，不要其它文字。"""

MERGE_USER_TEMPLATE = """## 已有状态（JSON）
{prev_state}

## 需要并入的新讨论
{new_lines}

请输出**更新后的完整状态** JSON，键为：
goal, current_judgment, confirmed_facts, ruled_out, failed_attempts,
key_identifiers, open_next_steps, open_questions, corrected

字段形态：
- goal / current_judgment：字符串
- confirmed_facts：[{{"fact": "...", "source": "出处（评论 id 或发言人）"}}]
- ruled_out：[{{"direction": "...", "why": "依据"}}]
- 其余：字符串数组
"""


def build_merge_prompt(prev_state: Any, new_text: Any) -> str:
    """组装「把新讨论并入旧状态」的 prompt。new_text 可传字符串或行列表。"""
    import json

    if isinstance(new_text, (list, tuple)):
        new_text = "\n".join(str(x) for x in new_text)
    return MERGE_USER_TEMPLATE.format(
        prev_state=json.dumps(normalize_state(prev_state), ensure_ascii=False, indent=1),
        new_lines=(str(new_text or "").strip() or "（无）"),
    )


def _extract_json(raw: str) -> Optional[dict]:
    import json
    import re

    text = (raw or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    else:
        s, e = text.find("{"), text.rfind("}")
        if s != -1 and e > s:
            text = text[s:e + 1]
    try:
        data = json.loads(text)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


# ── 持久化（读/写 task_agent_state）─────────────────────────


def load_state(task_id) -> dict:
    """读工单状态。**取不到/解析失败一律返回空状态**，不抛 —— 状态丢了不致命。"""
    from app.models.task import TaskAgentState
    from app.core.database import SessionLocal

    try:
        tid = int(task_id)
    except (TypeError, ValueError):
        return {"state": empty_state(), "covered_comment_id": 0, "rounds": 0}
    try:
        db = SessionLocal()
        try:
            row = db.query(TaskAgentState).filter(TaskAgentState.task_id == tid).first()
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"[agent_state] 读取失败 task={task_id}: {e}")
        return {"state": empty_state(), "covered_comment_id": 0, "rounds": 0}
    if not row:
        return {"state": empty_state(), "covered_comment_id": 0, "rounds": 0}
    return {
        "state": normalize_state(row.state),
        "covered_comment_id": int(row.covered_comment_id or 0),
        "rounds": int(row.rounds or 0),
    }


def save_state(task_id, state: dict, covered_comment_id: int, rounds: int) -> bool:
    """Upsert 工单状态与水位线。失败返回 False（调用方据此保留水位线不动）。"""
    from app.models.task import TaskAgentState
    from app.core.database import SessionLocal

    try:
        tid = int(task_id)
    except (TypeError, ValueError):
        return False
    db = SessionLocal()
    try:
        row = db.query(TaskAgentState).filter(TaskAgentState.task_id == tid).first()
        if row is None:
            row = TaskAgentState(task_id=tid)
            db.add(row)
        row.state = normalize_state(state)
        row.covered_comment_id = int(covered_comment_id or 0)
        row.rounds = int(rounds or 0)
        db.commit()
        return True
    except Exception as e:
        logger.warning(f"[agent_state] 保存失败 task={task_id}: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return False
    finally:
        db.close()


def merge_state(llm_raw: str, prev_state: Any) -> Optional[dict]:
    """解析模型输出的新状态；解析失败返回 None（调用方不推进水位线）。"""
    data = _extract_json(llm_raw)
    if data is None:
        return None
    merged = normalize_state(data)
    if is_empty(merged) and not is_empty(prev_state):
        # 模型把已有状态清空了：视为异常输出，不采纳（防止一轮把状态抹掉）
        return None
    return merged
