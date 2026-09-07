"""Step2 打标 + 给大模型看的人员写法（姓名 + ID 都要出现）。"""

from typing import Any, Dict, List, Optional, Sequence
import re


TAG_CREATOR = "提单人"
TAG_CONTACT = "项目对接人"
TAG_PREFERRED = "倾向接单人"
TAG_PREV_UNSATISFIED = "原用户不满意的接单人"


def llm_person_label(
    eid: Optional[str] = None,
    name: Optional[str] = None,
    *,
    eng: Any = None,
) -> str:
    """给大模型看的人员写法：姓名在前、ID 在后。缺一则用占位，两边都必须出现。"""
    if eng is not None:
        if eid is None:
            eid = getattr(eng, "id", None)
        if name is None:
            name = getattr(eng, "name", None)
    eid_s = str(eid or "").strip() or "?"
    name_s = str(name or "").strip() or "未知"
    return f"姓名:{name_s} ID:{eid_s}"


def match_engineer_from_llm(
    raw_id: Optional[str],
    engineers: Sequence[Any],
    raw_name: Optional[str] = None,
):
    """从 LLM 回填的 engineer_id / engineer_name 对上候选人。优先 id，再唯一姓名。"""
    blob = " ".join(x for x in ((raw_id or ""), (raw_name or "")) if x).strip()
    eid = ""
    m_id = re.search(r"ID:(\S+)", blob)
    if m_id:
        eid = m_id.group(1).strip()
    else:
        text = (raw_id or "").strip()
        if "（" in text:
            text = text.split("（", 1)[0].strip()
        eid = text
    if eid:
        eng = next((e for e in engineers if getattr(e, "id", None) == eid), None)
        if eng is not None:
            return eng
    names: List[str] = []
    m_name = re.search(r"姓名:(\S+)", blob)
    if m_name:
        names.append(m_name.group(1).strip())
    for extra in (raw_name, raw_id):
        extra = (extra or "").strip()
        if extra and extra not in names:
            names.append(extra)
    for key in names:
        if not key or key.startswith("ID:") or key.startswith("姓名:"):
            continue
        hits = [e for e in engineers if (getattr(e, "name", None) or "") == key]
        if len(hits) == 1:
            return hits[0]
    return None


def match_engineer_id_strict(
    raw_id: Optional[str],
    engineers: Sequence[Any],
):
    """Step6 铁律：只认「ID:」后的 users.id，或与候选人 id 完全相等。姓名当 id 视为自造。"""
    blob = (raw_id or "").strip()
    if not blob:
        return None
    m_id = re.search(r"ID:(\S+)", blob)
    eid = m_id.group(1).strip() if m_id else blob
    if not eid or eid.startswith("姓名:"):
        return None
    return next((e for e in engineers if getattr(e, "id", None) == eid), None)


def recall_source_label(d: Optional[Dict[str, Any]]) -> str:
    """给 Step6：此人命中了哪几路，是否被历史从收紧名单外捞回。"""
    if not d:
        return "来源: 未命中召回"
    def _hit(flag: str, score_key: str) -> bool:
        if flag in d and d.get(flag) is not None:
            return bool(d.get(flag))
        return float(d.get(score_key) or 0) > 0

    hits = []
    if _hit("hit_llm", "llm_score"):
        hits.append("LLM")
    if _hit("hit_similar", "similar_score") or _hit("hit_similar", "history_score"):
        hits.append("相似工单")
    if _hit("hit_cluster", "cluster_score"):
        hits.append("问题域")
    src = "+".join(hits) if hits else "未命中召回"
    miss = []
    if "LLM" not in hits:
        miss.append("LLM未召回")
    if "相似工单" not in hits:
        miss.append("相似未命中")
    if "问题域" not in hits:
        miss.append("问题域未命中")
    extra = "；不在部门/产品收紧名单，由历史捞回" if d.get("outside_tighten") else ""
    tail = ("；" + "、".join(miss)) if hits else ""
    return f"来源: {src}{extra}{tail}"


def score_tag_labels(d: Optional[Dict[str, Any]]) -> List[str]:
    """从精排分数字典取出本版标签（顺序：提单人 → 对接人 → 原不满意 → 倾向）。"""
    if not d:
        return []
    tags: List[str] = []
    if d.get("is_creator"):
        tags.append(TAG_CREATOR)
    if d.get("contact_assignee"):
        tags.append(TAG_CONTACT)
    if d.get("prev_unsatisfied"):
        tags.append(TAG_PREV_UNSATISFIED)
    if d.get("preferred_assignee"):
        tags.append(TAG_PREFERRED)
    return tags


def match_vague_strong_signal(ticket, config) -> bool:
    """问题描述是否命中「模糊强信号」。keywords 为空或未启用 → False（不截断）。"""
    cfg = getattr(config, "vague_strong_signals", None)
    if cfg is None:
        cfg = {}
    if isinstance(cfg, dict):
        enabled = bool(cfg.get("enabled", True))
        raw = cfg.get("keywords") or []
    else:
        enabled = bool(getattr(cfg, "enabled", True))
        raw = getattr(cfg, "keywords", None) or []
    if not enabled:
        return False
    keywords = [str(k).strip() for k in raw if str(k).strip()]
    if not keywords:
        return False
    text = f"{getattr(ticket, 'title', '') or ''}\n{getattr(ticket, 'problem_description', '') or ''}"
    return any(kw in text for kw in keywords)
