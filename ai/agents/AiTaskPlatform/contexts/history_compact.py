"""讨论上下文按窗口压缩。

未超过模型窗口时，描述和讨论都用原文。
只有拼进提示词的内容到达窗口时，才把放不下的更早部分压成结构化摘要，
最近仍放得下的轮次保持原文。评论原文仍在工单上，摘要不另存一份案情。
"""

from __future__ import annotations

import os

from ai.core.logging import get_logger

logger = get_logger("TASK_AGENT")

# deepseek-v4-flash / v4-pro 的上下文是 100 万 token。留出系统提示和本轮回复。
_DEFAULT_WINDOW_TOKENS = 1_000_000
_DEFAULT_RESERVE_TOKENS = 32_000
# 摘要本身占用的上限（与 complete 的 max_tokens 对齐，略留余量）。
_SUMMARY_BUDGET = 4_000
# 单次压缩调用放入的原文上限，避免压缩请求自己顶满窗口。
_CHUNK_TOKENS = 120_000

_SUMMARY_SYSTEM = """你在压缩一张工单里放不进上下文窗口的更早材料，供后续轮次接着分析。
评论原文仍留在工单上。这段摘要只替代窗口里放不下的那一部分。
按下面九个栏目逐项写，没有内容的栏目写「无」。闲聊和客套不要写。
工单号、车号、故障码、版本、路径、命令、报错原文、时间点保持原样，不要改写。
不确定的地方保持不确定，不要收成一句肯定结论。"""

_SUMMARY_USER = """## 已有摘要
{prev}

## 需要压入摘要的更早材料
{older}

请按下面栏目输出一份新的完整摘要。已有摘要里的标识和结论，只要这段材料没有推翻，就保留。

1. 目标
2. 当前判断
3. 已经确认的事实（每条带出处）
4. 已经排除的方向（每条带依据）
5. 失败过的尝试
6. 关键标识
7. 未完成的下一步
8. 还没定的问题
9. 被纠正过的结论
"""

_DESC_FOLDED = "（描述过长，已并入此前讨论摘要）"


def context_limit() -> int:
    """本轮提示词可用的 token 上限：窗口减去留给系统提示和回复的余量。"""
    try:
        window = int(os.getenv("DISCUSS_CONTEXT_WINDOW_TOKENS", str(_DEFAULT_WINDOW_TOKENS)))
    except ValueError:
        window = _DEFAULT_WINDOW_TOKENS
    try:
        reserve = int(os.getenv("DISCUSS_CONTEXT_RESERVE_TOKENS", str(_DEFAULT_RESERVE_TOKENS)))
    except ValueError:
        reserve = _DEFAULT_RESERVE_TOKENS
    return max(window - reserve, 8_000)


def estimate_tokens(text: str) -> int:
    """偏大估算。中文按 1 字 1 token，其余按 4 字符 1 token。"""
    if not text:
        return 0
    cjk = 0
    other = 0
    for ch in text:
        if ord(ch) > 127:
            cjk += 1
        else:
            other += 1
    return cjk + (other + 3) // 4


def select_kept(
    description: str,
    comment_lines: list[str],
    fixed_tokens: int,
    limit: int,
    summary_budget: int = _SUMMARY_BUDGET,
) -> tuple[str, list[str], list[str]]:
    """决定描述和哪些评论保持原文。

    返回 (保留的描述, 需要压缩的文本, 原文保留的评论)。
    从最早的材料开始挪进压缩区，直到剩余部分加上摘要预算能放进窗口。
    未超窗口时，压缩列表为空。
    """
    desc = description or ""
    lines = [ln for ln in (comment_lines or []) if ln]
    pieces: list[tuple[str, str]] = []
    if desc:
        pieces.append(("desc", desc))
    for ln in lines:
        pieces.append(("cmt", ln))

    def raw_tokens(items: list[tuple[str, str]]) -> int:
        return sum(estimate_tokens(text) + 1 for _, text in items)

    if fixed_tokens + raw_tokens(pieces) <= limit:
        return desc, [], lines

    room = max(limit - fixed_tokens, 0)
    budget = min(summary_budget, max(room // 4, 1)) if room else 0
    rest = pieces
    while rest and budget + raw_tokens(rest) > room:
        rest = rest[1:]

    evicted = len(pieces) - len(rest)
    older: list[str] = []
    for kind, text in pieces[:evicted]:
        if kind == "desc":
            older.append(f"[工单描述]\n{text}")
        else:
            older.append(text)

    kept_desc = ""
    recent: list[str] = []
    for kind, text in rest:
        if kind == "desc":
            kept_desc = text
        else:
            recent.append(text)
    return kept_desc, older, recent


def _chunks(lines: list[str], chunk_tokens: int) -> list[str]:
    batches: list[str] = []
    buf: list[str] = []
    used = 0
    for line in lines:
        t = estimate_tokens(line) + 1
        if buf and used + t > chunk_tokens:
            batches.append("\n".join(buf))
            buf = []
            used = 0
        buf.append(line)
        used += t
    if buf:
        batches.append("\n".join(buf))
    return batches


def _clip_to_tokens(text: str, token_budget: int) -> str:
    if estimate_tokens(text) <= token_budget:
        return text
    keep = max(token_budget, 200)
    head = text[:keep]
    tail = text[-keep:]
    return f"{head}\n（中间未能压缩，已省略）\n{tail}"


def _join(summary: str, recent: list[str], *, clipped: bool) -> str:
    title = (
        "## 此前讨论（压缩未完成，仅保留首尾）"
        if clipped
        else "## 此前讨论（窗口已满，以下是压缩摘要，评论原文仍在工单上）"
    )
    parts = [title, (summary or "").strip() or "（无）"]
    if recent:
        parts.append("## 最近对话（原文）")
        parts.append("\n".join(recent))
    return "\n".join(parts)


async def _summarize_older(older: list[str], llm_client) -> str:
    summary = "（无）"
    for chunk in _chunks(older, _CHUNK_TOKENS):
        summary = await llm_client.complete(
            prompt=_SUMMARY_USER.format(prev=summary, older=chunk),
            system_prompt=_SUMMARY_SYSTEM,
            max_tokens=2000,
            temperature=0.1,
            thinking=False,
        )
        summary = (summary or "").strip() or "（无）"
    return summary


async def fit_ticket_history(
    *,
    description: str,
    comment_lines: list[str],
    fixed_text: str,
    llm_client,
    limit: int | None = None,
) -> tuple[str, str]:
    """返回放进提示词的 (描述, 讨论历史)。未到窗口时两者都是原文。"""
    cap = context_limit() if limit is None else limit
    fixed_tokens = estimate_tokens(fixed_text or "")
    kept_desc, older, recent = select_kept(
        description or "",
        comment_lines or [],
        fixed_tokens,
        cap,
    )
    if not older:
        disc = "\n".join(recent) if recent else "（暂无讨论）"
        return kept_desc, disc

    logger.info(
        "[discuss] 上下文到达窗口，压缩更早材料 %s 段，原文保留 %s 条，上限 %s",
        len(older),
        len(recent),
        cap,
    )
    try:
        summary = await _summarize_older(older, llm_client)
        disc = _join(summary, recent, clipped=False)
    except Exception as e:
        logger.warning(f"[discuss] 讨论压缩失败，改为保留首尾: {type(e).__name__}: {e}")
        room = max(cap - fixed_tokens - estimate_tokens("\n".join(recent)) - 200, 500)
        disc = _join(_clip_to_tokens("\n".join(older), room), recent, clipped=True)

    if kept_desc:
        return kept_desc, disc
    if description:
        return _DESC_FOLDED, disc
    return "", disc
