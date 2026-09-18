"""项目名字面模糊匹配器（零依赖）。

解决「连续子串匹配」对定语/换序/简称不鲁棒的问题，分层打分 + 消歧决策：

- 打分：code 精确 > name 精确 > name 包含（连续子串）> 片段覆盖 + bigram 相似
- 决策：唯一高置信直接采用；多候选并列/低置信时不猜，返回候选交给 clarify 消歧

用法::

    from .project_matcher import match_projects, resolve_project

    cands = match_projects("罗勇项目")          # 全局候选，按分数降序
    code, cands = resolve_project("泰国项目")   # 多候选并列 → code=None，cands 供消歧
    code, cands = resolve_project("泰国项目", scope_ids=["35"])  # 缩域后唯一 → 直连
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

_logger = None  # 延迟绑定，避免模块级 import 级联（评测 stub 加载环境无 DB）


def _get_logger():
    global _logger
    if _logger is None:
        from .logging_config import get_logger

        _logger = get_logger("ProjectMatcher")
    return _logger


# ── 归一化与停用词 ──────────────────────────────────────────────

# 项目名中的通用后缀/虚词：归一化时剔除，避免把「项目」等无区分度字符计入
_NORM_STOPWORDS = ("项目", "项目号", "项目代码", "的")

_PUNCT_RE = re.compile(r"[\s，,。.、；;：:！!？?（）()【】\[\]《》<>\"'“”‘’\-—_·…]+")

# 前导时间短语：用户把时间词与项目名连写时（如「近7天罗勇项目」被贪婪正则
# 整体提为 hint），剥离前导时间词避免污染匹配。归一化双侧同步剥离：
# hint「8月交付项目」与项目名「8月交付项目」剥离后仍一致，匹配不受影响。
_LEADING_TIME_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"^(?:最近|近)[一二两三四五六七八九十\d]{1,3}\s*[个]?[天日周月]"),
    re.compile(r"^[本这上][个]?[周月]"),
    re.compile(r"^[今昨明][天日]"),
)


def _strip_leading_time(s: str) -> str:
    """循环剥离前导时间短语（「近7天罗勇」→「罗勇」）。"""
    changed = True
    while changed:
        changed = False
        for pat in _LEADING_TIME_PATTERNS:
            m = pat.match(s)
            if m:
                s = s[m.end() :]
                changed = True
                break
    return s


def _norm(text: str) -> str:
    """小写 + 去空白标点 + 剥离前导时间词 + 剔除停用词。"""
    s = _PUNCT_RE.sub("", (text or "").lower())
    s = _strip_leading_time(s)
    for w in _NORM_STOPWORDS:
        s = s.replace(w, "")
    return s


def _bigrams(s: str) -> set[str]:
    if len(s) < 2:
        return {s} if s else set()
    return {s[i : i + 2] for i in range(len(s) - 1)}


def _bigram_dice(a: str, b: str) -> float:
    sa, sb = _bigrams(a), _bigrams(b)
    if not sa or not sb:
        return 0.0
    return 2.0 * len(sa & sb) / (len(sa) + len(sb))


def _segment_coverage(hint: str, name: str) -> float:
    """hint 的连续子片段在 name 中的加权覆盖率（长度≥2 片段命中计长）。

    如「罗勇潜伏车」在「泰国罗勇LST-CAT潜伏车」中命中 罗勇/潜伏/伏车/潜伏车，
    覆盖率 1.0——解决「加定语/跳词」后子串不连续的问题。
    """
    total, n = 0, len(hint)
    if n < 2:
        return 1.0 if hint and hint in name else 0.0
    for i in range(n):
        for j in range(i + 2, n + 1):
            seg = hint[i:j]
            if seg in name:
                total += j - i
    return min(1.0, total / n)


# ── 候选打分 ────────────────────────────────────────────────────

#: 唯一候选的最低采用分；低于此分视为未命中
ACCEPT_THRESHOLD = 0.55
#: 直接采用要求的第一名分数
DIRECT_THRESHOLD = 0.9
#: 与第二名的分数差距要求（差距不足说明候选并列，应消歧而非猜测）
GAP_THRESHOLD = 0.2


@dataclass
class ProjectMatch:
    """单个候选项目的匹配结果。"""

    code: str
    name: str
    score: float = 0.0
    extra: dict = field(default_factory=dict)


def _score(hint_norm: str, code: str, name_norm: str) -> float:
    """对单个项目打分（hint/name 均为归一化后文本）。"""
    if not hint_norm:
        return 0.0
    code_l = _norm(code) if code else ""
    # 1) 代码精确（用户直接报项目编号）
    if code_l and code_l == hint_norm:
        return 2.0
    # 2) 代码包含（如 hint「35」命中 code「xx35xx」）
    if code_l and len(hint_norm) >= 2 and hint_norm in code_l:
        return 1.7
    if not name_norm:
        return 0.0
    # 3) 名称精确
    if name_norm == hint_norm:
        return 1.9
    # 4) 名称包含（连续子串；hint 越接近全名分越高）
    if hint_norm in name_norm:
        return 1.0 + 0.5 * (len(hint_norm) / len(name_norm))
    # 5) 用户话更长（带定语完整名），名称是其中片段
    if len(name_norm) >= 2 and name_norm in hint_norm:
        return 0.95 + 0.35 * (len(name_norm) / len(hint_norm))
    # 6) 片段覆盖 + bigram 相似（换序/跳词/加词）
    coverage = _segment_coverage(hint_norm, name_norm)
    dice = _bigram_dice(hint_norm, name_norm)
    if coverage > 0:
        return 0.8 * coverage + 0.2 * dice
    # 7) 纯 bigram 兜底（错字/相近表达）
    return 0.6 * dice


# ── 项目名缓存 ──────────────────────────────────────────────────

_cache: list[tuple[str, str]] | None = None
_cache_ts: float = 0.0
_CACHE_TTL = 300.0  # 进程内缓存有效期（秒），避免每轮查库


def _load_projects() -> list[tuple[str, str]]:
    """全量加载 (code, name) 列表，带进程内 TTL 缓存。"""
    global _cache, _cache_ts
    now = time.monotonic()
    if _cache is not None and now - _cache_ts < _CACHE_TTL:
        return _cache
    from ai.core.database import ProjectDelivery, SessionLocal

    db = SessionLocal()
    try:
        rows = db.query(ProjectDelivery.id, ProjectDelivery.name).all()
        _cache = [(str(r[0] or ""), str(r[1] or "")) for r in rows]
        _cache_ts = now
        _get_logger().info("项目名缓存刷新：%d 条", len(_cache))
    except Exception as exc:
        # 刷新失败：优先沿用旧缓存，保证服务可用性
        _get_logger().warning("项目名缓存刷新失败，沿用旧缓存: %s", exc)
    finally:
        db.close()
    return _cache or []


# ── 对外接口 ────────────────────────────────────────────────────

def match_projects(
    hint: str, scope_ids: list[str] | None = None, limit: int = 5
) -> list[ProjectMatch]:
    """候选召回 + 打分排序，返回按分数降序的候选列表。

    Args:
        hint: 项目名线索（用户话里的原始表达，如「罗勇项目」）。
        scope_ids: 候选集限定（如用户关联项目 code 列表）；None 表示全局。
        limit: 返回候选上限（消歧按钮最多渲染 3 个）。
    """
    hint_norm = _norm(hint or "")
    if len(hint_norm) < 2:
        return []
    scored: list[ProjectMatch] = []
    for code, name in _load_projects():
        if scope_ids is not None and code not in scope_ids:
            continue
        s = _score(hint_norm, code, _norm(name))
        # 粗筛：远低于可接受线的候选直接丢弃，减少排序噪声
        if s < ACCEPT_THRESHOLD * 0.5:
            continue
        scored.append(ProjectMatch(code=code, name=name, score=round(s, 4)))
    scored.sort(key=lambda m: m.score, reverse=True)
    return scored[:limit]


def resolve_project(
    hint: str, scope_ids: list[str] | None = None, limit: int = 5
) -> tuple[str | None, list[ProjectMatch]]:
    """匹配决策：返回 (直接采用的 code 或 None, 候选列表)。

    决策规则（宁可消歧，不猜错）：
    - 第一名高分且与第二名差距明显（或唯一候选）→ 直接采用；
    - 多候选并列 / 低置信 → code=None，候选列表交给 clarify 渲染按钮消歧。
    """
    cands = match_projects(hint, scope_ids=scope_ids, limit=limit)
    if not cands:
        return None, []
    top, second = cands[0], cands[1] if len(cands) > 1 else None
    if top.score >= DIRECT_THRESHOLD and (
        second is None or top.score - second.score >= GAP_THRESHOLD
    ):
        _get_logger().info("项目线索 %r → 直接命中 %s (score=%.3f)", hint, top.code, top.score)
        return top.code, cands
    if second is None and top.score >= ACCEPT_THRESHOLD:
        _get_logger().info("项目线索 %r → 唯一候选直接命中 %s (score=%.3f)", hint, top.code, top.score)
        return top.code, cands
    _get_logger().info(
        "项目线索 %r → 多候选/低置信，进入消歧（%d 个候选）", hint, len(cands)
    )
    return None, cands
