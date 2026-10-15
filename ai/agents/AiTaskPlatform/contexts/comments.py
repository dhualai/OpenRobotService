"""工单讨论评论的读取与落库（从 pipeline.py 拆分，独立成模块）

职责：
  - _load_discussion: 读取讨论评论（含工程师 + U老师/AI 历史分析，标注来源）
  - add_diagnosis_comment / add_diagnosis_comment_short: 把 AI 结果写入 task_comments
  - notify_backend_comment_broadcast: 写库后回调后端 WS 广播

依赖：TaskComment 模型 + SessionLocal（后端 DB）。
不依赖 AiTaskAgent 实例状态，可独立使用。
"""

import re
import threading
from typing import Optional

from ai.core.logging import get_logger

logger = get_logger("TASK_AGENT")

_HTML_TAG = re.compile(r"<[^>]+>")


def _plain_comment_text(raw: str, limit: int | None = 800) -> str:
    text = _HTML_TAG.sub(" ", str(raw or ""))
    text = re.sub(r"\s+", " ", text).strip()
    if limit is None:
        return text
    return text[:limit]


def load_quoted_comment(comment_id) -> dict:
    """按评论 id 读被引用的那一条（作者 + 正文），读不到返回空 dict。"""
    from app.models.task import TaskComment
    from app.core.database import SessionLocal

    try:
        cid = int(comment_id)
    except (TypeError, ValueError):
        return {}
    db = SessionLocal()
    try:
        c = db.query(TaskComment).filter(TaskComment.id == cid).first()
        if not c:
            return {}
        return {
            "id": c.id,
            "author": author_display_name(getattr(c, "created_by", None)),
            "content": c.content or "",
        }
    except Exception as e:
        logger.warning(f"[quoted_comment] 读取失败 id={comment_id}: {e}")
        return {}
    finally:
        db.close()


# 引用一句话时，前后各取几条作为语境（可由调用方覆盖）。
_QUOTED_CONTEXT_K = 5


def format_quoted_comment_block(context: dict, k: int | None = None) -> str:
    """把本轮「引用这句话」做成独立 prompt 段。

    优先按被引用评论的 id 取**前后各 k 条**上下文：用户引用一句话再 @U老师 时，
    只注入孤零零那一句，模型读不到它所在的对话，容易答偏。
    取不到上下文时回退为只注入该条（行为与从前一致）。

    前端没带正文时按 `reply_to` 查库。
    """
    ctx = context or {}
    kk = _QUOTED_CONTEXT_K if k is None else max(0, int(k))
    qc = ctx.get("quoted_comment")
    if not isinstance(qc, dict) or not str(qc.get("content") or "").strip():
        reply_to = ctx.get("reply_to")
        qc = load_quoted_comment(reply_to) if reply_to else {}
    if not isinstance(qc, dict):
        return ""

    qid = ctx.get("reply_to") or qc.get("id")
    if qid:
        window = load_comment_context(qid, k=kk)
        if window:
            lines: list[str] = []
            for r in window:
                rendered = render_comment_lines([r], per_comment=None)
                if not rendered:
                    continue
                mark = "▶ " if str(r.get("id")) == str(qid) else "  "
                lines.append(mark + rendered[0])
            if lines:
                return (
                    "## 用户本轮引用的评论（含上下文）\n"
                    f"用户引用的是标「▶」的那条；前后各 {kk} 条是它的上下文，"
                    "供理解语境，**不要当成用户本轮问的内容**。\n"
                    + "\n".join(lines) + "\n"
                )

    content = _plain_comment_text(qc.get("content") or "", limit=None)
    if not content:
        return ""
    author = qc.get("author") or qc.get("created_by_name") or qc.get("created_by") or "?"
    return f"## 用户本轮引用的评论\n[{author}] {content}\n"


# 显式传入 total 时才截断（单测与兜底）。讨论主路径未到窗口不走这里，见 history_compact。
_THREAD_PER_COMMENT = 2000
_THREAD_TOTAL = 16000


def format_discussion_thread(
    comments: list,
    *,
    per_comment: int = _THREAD_PER_COMMENT,
    total: int = _THREAD_TOTAL,
) -> str:
    """把一张工单的评论按时间排成讨论史。

    comments: [{author|created_by_name|created_by, content}, ...]，调用方保证从早到晚。
    """
    lines: list[str] = []
    for c in comments or []:
        if not isinstance(c, dict):
            continue
        author = c.get("author") or c.get("created_by_name") or c.get("created_by") or "?"
        content = _plain_comment_text(c.get("content") or "", limit=per_comment)
        if not content:
            continue
        if str(author) == "U老师":
            lines.append(f"[U老师] {content}")
        else:
            lines.append(f"[{author}] {content}")
    if not lines:
        return ""
    if sum(len(line) + 1 for line in lines) <= total:
        return "\n".join(lines)
    head = lines[0]
    kept: list[str] = []
    used = len(head) + 1
    for line in reversed(lines[1:]):
        extra = len(line) + 1
        if used + extra > total:
            break
        kept.append(line)
        used += extra
    kept.reverse()
    omitted = len(lines) - 1 - len(kept)
    if omitted > 0:
        return "\n".join([head, f"（中间省略 {omitted} 条，下面是最近的对话）", *kept])
    return "\n".join([head, *kept])


def render_comment_lines(comments: list, *, per_comment: int | None = None) -> list[str]:
    """按时间把评论渲染成一行一条。per_comment 为空则保留正文，不预先截断。"""
    lines: list[str] = []
    for c in comments or []:
        if not isinstance(c, dict):
            continue
        author = c.get("author") or c.get("created_by_name") or c.get("created_by") or "?"
        content = _plain_comment_text(c.get("content") or "", limit=per_comment)
        if not content:
            continue
        if str(author) == "U老师":
            lines.append(f"[U老师] {content}")
        else:
            lines.append(f"[{author}] {content}")
    return lines


def _author_display_map() -> dict:
    """created_by → 显示名映射。

    TaskComment 模型没有 created_by_name 列（后端 API 的 created_by_name 是派生
    字段），created_by 存的是 users.id 或 username；微信用户的 username 形如
    wechat_{openid[:10]}（见 wechat/utils/crypto.generate_wechat_username），只有
    这张映射能还原成真实姓名。复用后端 user_service 的进程内缓存（TTL 600s）。

    **拿不到映射时降级为「不还原」**（讨论史仍能读，只是名字退化成原始 ID）。
    这本身不致命，但**整片退化**必须显形：这条链一旦断，所有作者（包括有名字的）
    都变成 ID，模型就又开始猜人了 —— 也就是 issues.md #4 那个 bug 换个方式复现。
    所以这里是 **ERROR**，不是一行 warning。
    """
    try:
        from app.services.user_service import user_service
        name_map = user_service.get_user_map() or {}
    except Exception as e:
        logger.error(f"[discuss] 加载用户姓名映射失败，讨论史将退化为原始 ID: {e}")
        return {}
    if not name_map:
        logger.error(
            "[discuss] 用户姓名映射为空，讨论史将退化为原始 ID（检查 user_service "
            "的缓存加载：DB 连接 / users 表是否可读）"
        )
    return name_map


# 未实名账号的稳定代称（进程内）。见 issues.md #21 的口径：
# 一律「（微信用户N）」，同人同号、不同人不同号；**不写 openid**（那是指纹），
# 也不编名字（编出来会与真人重名）。
_WX_PLACEHOLDERS: dict = {}
_WX_LOCK = threading.Lock()


def _wx_body(raw_id: str) -> str:
    """openid 本体（去掉 `wechat_` 前缀）。"""
    key = str(raw_id or "").strip()
    return key[len("wechat_"):] if key.startswith("wechat_") else key


def _wx_placeholder(raw_id: str) -> str:
    """未实名账号 →「（微信用户N）」，同人同号。

    同一个账号有两种写法：`created_by` 里是 `wechat_{openid[:10]}`（username），
    而 users.id 本身可以是**裸 openid**。按前缀 / openid 头 10 位认人，
    避免同一个人在作者位拿到两个号。
    """
    key = str(raw_id or "").strip()
    if not key:
        return "（未知）"
    body = _wx_body(key)
    with _WX_LOCK:
        for known, placeholder in _WX_PLACEHOLDERS.items():
            kbody = _wx_body(known)
            if key.startswith(known) or known.startswith(key):
                return placeholder
            if len(body) >= 10 and kbody.startswith(body[:10]):
                return placeholder
        placeholder = f"（微信用户{len(_WX_PLACEHOLDERS) + 1}）"
        _WX_PLACEHOLDERS[key] = placeholder
        return placeholder


def author_display_name(raw_author, name_map: dict | None = None) -> str:
    """把 created_by（users.id 或 username）还原成显示名。

    - 能还原出真名 → 返回真名；
    - **命中映射、但给不出真名**（= 该用户没填实名）→ 返回「（微信用户N）」，同人同号。
      「给不出真名」有两种形态，都要挡：
        · 值仍是 `wechat_…` 形式的 username；
        · 值是**裸 openid**（映射的取值规则是 `name or username or user_id`，
          username 也为空时回落到 user_id，而 users.id 本身可以是裸 openid）。
      这两种都是**指纹**，绝不交给模型；
    - 映射里**没有**这个人 → 保留原始 ID（便于排查，见 issues.md #4），不做代称。

    最后一种保留原样是有意的：映射加载失败时全部作者都会走这条路，
    此时把原始 ID 藏起来等于把故障也藏起来了。
    """
    if not raw_author:
        return "?"
    if name_map is None:
        name_map = _author_display_map()
    real = name_map.get(raw_author, raw_author)
    if raw_author in name_map and (
        str(real).startswith("wechat_") or str(real) == str(raw_author)
    ):
        return _wx_placeholder(raw_author)
    return real


def _rows_from_comments(comments: list, name_map: dict) -> list[dict]:
    """ORM 评论行 → {author, content}，author 还原为真实姓名。

    走 `author_display_name` 而不是就地 `.get()`：未实名账号的代称保护在里面，
    就地查映射会绕过它（主路径反而漏保护）。
    """
    rows = []
    for c in comments or []:
        raw = getattr(c, "created_by", None)
        rows.append({
            "author": author_display_name(raw, name_map),
            "content": getattr(c, "content", "") or "",
        })
    return rows


def _row_with_id(c, name_map: dict) -> dict:
    """ORM 评论行 → {id, author, content}。带 id 才能做水位线过滤与出处标注。"""
    raw = getattr(c, "created_by", None)
    return {
        "id": getattr(c, "id", None),
        "author": author_display_name(raw, name_map),
        "content": getattr(c, "content", "") or "",
    }


def load_comment_rows_after(task_id: str, after_id: int = 0) -> list[dict]:
    """按 id 水位线读取该工单的评论（id > after_id），从早到晚，**每行带 id**。

    供「工单 Agent 状态」增量刷新：只取水位线之后的新评论去折叠进状态。
    与 `load_ticket_comment_rows` 的区别就是带了 id——那个函数丢掉 id 是为了
    讨论史渲染，这里要 id 才能推进水位线。
    """
    from app.models.task import TaskComment
    from app.core.database import SessionLocal

    try:
        tid = int(task_id)
    except (TypeError, ValueError):
        return []
    try:
        db = SessionLocal()
        try:
            q = db.query(TaskComment).filter(TaskComment.task_id == tid)
            if after_id:
                q = q.filter(TaskComment.id > int(after_id))
            comments = q.order_by(TaskComment.created_at.asc()).all()
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"[discuss] 按水位线读取评论失败 task={task_id}: {e}")
        return []
    name_map = _author_display_map()
    return [_row_with_id(c, name_map) for c in comments]


def load_comment_context(comment_id, k: int = 5) -> list[dict]:
    """取被引用评论**及其前后各 k 条**（同工单、按时间排序）。

    用于「用户引用一句话再 @U老师」：只注入被引用的那一句时，模型读不到那句所在的
    对话，容易答偏。这里按 id 定位后再扩窗口。

    取不到被引用那条时返回空列表（调用方回退为只注入该条文本）。
    """
    from app.models.task import TaskComment
    from app.core.database import SessionLocal

    try:
        cid = int(comment_id)
    except (TypeError, ValueError):
        return []
    k = max(0, int(k or 0))
    try:
        db = SessionLocal()
        try:
            anchor = db.query(TaskComment).filter(TaskComment.id == cid).first()
            if not anchor:
                return []
            comments = db.query(TaskComment).filter(
                TaskComment.task_id == anchor.task_id
            ).order_by(TaskComment.created_at.asc()).all()
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"[discuss] 读取引用上下文失败 id={comment_id}: {e}")
        return []

    ids = [getattr(c, "id", None) for c in comments]
    try:
        i = ids.index(cid)
    except ValueError:
        return []
    window = comments[max(0, i - k): i + k + 1]
    name_map = _author_display_map()
    return [_row_with_id(c, name_map) for c in window]


def load_ticket_comment_rows(task_id: str) -> list[dict]:
    """读取这张工单的全部讨论评论（从早到晚），返回原文行，不做长度裁剪。"""
    from app.models.task import TaskComment
    from app.core.database import SessionLocal

    try:
        tid = int(task_id)
    except (TypeError, ValueError):
        return []
    try:
        db = SessionLocal()
        try:
            comments = db.query(TaskComment).filter(
                TaskComment.task_id == tid
            ).order_by(TaskComment.created_at.asc()).all()
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"[discuss] 读取工单讨论史失败 task={task_id}: {e}")
        return []
    return _rows_from_comments(comments, _author_display_map())


def load_ticket_discussion(task_id: str) -> str:
    """读取这张工单的全部讨论评论（从早到晚）。未到窗口时调用方应使用这份原文。"""
    return "\n".join(render_comment_lines(load_ticket_comment_rows(task_id)))


def load_discussion(task_id: str, limit: int = 20) -> str:
    """读取工单讨论评论（含工程师与 U老师/AI 的历史分析），返回格式化文本。

    - 工程师评论 → 作第一手事实线索
    - U老师(AI) 评论 → 保留但标注"AI历史分析，仅供参考"，避免回声放大、又不错失已有排查进展
    """
    from app.models.task import TaskComment
    from app.core.database import SessionLocal

    try:
        db = SessionLocal()
        try:
            comments = db.query(TaskComment).filter(
                TaskComment.task_id == int(task_id)
            ).order_by(TaskComment.created_at.asc()).all()
        finally:
            db.close()
    except Exception:
        return ""

    name_map = _author_display_map()
    lines = []
    for c in comments:
        author = author_display_name(getattr(c, 'created_by', None), name_map)
        content = str(getattr(c, 'content', "") or "")[:300]
        if not content.strip():
            continue
        if author == "U老师":
            lines.append(f"[U老师(AI历史分析，仅供参考)] {content}")
        else:
            lines.append(f"[{author}] {content}")
    return "\n".join(lines[-limit:])


def add_diagnosis_comment(task_id: int, draft, created_by: str = "U老师") -> bool:
    """将 AI 诊断结果写入 task_comments 表。

    Args:
        draft: 支持 .root_cause_analysis 和 .suggested_actions 的对象
    """
    from app.models.task import TaskComment
    from app.core.db import SessionLocal

    content_parts = []
    if getattr(draft, 'root_cause_analysis', None):
        content_parts.append(draft.root_cause_analysis)
    if getattr(draft, 'suggested_actions', None):
        content_parts.append("")
        if len(draft.suggested_actions) == 1:
            content_parts.append(f"> {draft.suggested_actions[0]}")
        else:
            for action in draft.suggested_actions:
                content_parts.append(f"- {action}")

    db = SessionLocal()
    try:
        comment = TaskComment(
            task_id=task_id,
            content="\n".join(content_parts),
            created_by=created_by,
            is_public=True,
        )
        db.add(comment)
        db.commit()
        return True
    except Exception as e:
        logger.warning(f"Diagnosis comment failed: {e}")
        return False
    finally:
        db.close()


def add_diagnosis_comment_short(task_id: int, content: str) -> Optional[int]:
    """简短回复写入 task_comments（用于 @AI 讨论/摘要/诊断），返回 comment_id。

    写库后回调后端 WS 广播（实时上屏，best-effort）。
    """
    from app.models.task import TaskComment
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        comment = TaskComment(task_id=task_id, content=content,
                              created_by="U老师", is_public=True)
        db.add(comment)
        db.commit()
        db.refresh(comment)
        cid = comment.id
    except Exception:
        db.rollback()
        return None
    finally:
        db.close()
    # 实时推送：写库后回调后端 WS 广播（跨进程 pub-sub，best-effort 不阻塞主流程）
    try:
        notify_backend_comment_broadcast(task_id, cid)
    except Exception:
        pass
    return cid


def notify_backend_comment_broadcast(task_id: int, comment_id: int) -> None:
    """写库后回调后端 WS 广播端点（跨进程 pub-sub，best-effort 不阻塞主流程）。"""
    import asyncio
    import httpx
    from ai.config import get_ai_config

    try:
        cfg = get_ai_config()
        if not cfg.backend_base_url or not cfg.internal_api_key:
            return
        url = f"{cfg.backend_base_url.rstrip('/')}/api/tasks/{task_id}/internal/broadcast-comment"
        headers = {"X-API-Key": cfg.internal_api_key}

        async def _post() -> None:
            async with httpx.AsyncClient(timeout=2.0) as client:
                await client.post(url, json={"comment_id": comment_id}, headers=headers)

        async def _run() -> None:
            await _post()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None:
            loop.run_in_executor(None, lambda: asyncio.run(_run()))
        else:
            import threading
            threading.Thread(target=lambda: asyncio.run(_run()), daemon=True).start()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"AI 评论广播回调失败 task_id={task_id} comment_id={comment_id}: {e}")


def notify_backend_ai_progress(task_id, run_id, todos, phase: str = "running") -> None:
    """跨进程把 AI 执行过程广播进该工单 WS 房间（ai.progress 事件，best-effort）。

    用途：在前端动态展示"正在做哪一步/已完成哪步"，最终回复不含过程块。
    """
    import asyncio
    import httpx
    from ai.config import get_ai_config

    try:
        cfg = get_ai_config()
        if not cfg.backend_base_url or not cfg.internal_api_key:
            return
        url = f"{cfg.backend_base_url.rstrip('/')}/api/tasks/{task_id}/internal/broadcast-ai-progress"
        headers = {"X-API-Key": cfg.internal_api_key}
        payload = {"run_id": run_id, "phase": phase, "todos": todos or []}

        async def _post() -> None:
            async with httpx.AsyncClient(timeout=2.0) as client:
                await client.post(url, json=payload, headers=headers)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None:
            loop.run_in_executor(None, lambda: asyncio.run(_post()))
        else:
            import threading
            threading.Thread(target=lambda: asyncio.run(_post()), daemon=True).start()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"AI 进度广播回调失败 task_id={task_id}: {e}")


async def notify_backend_ai_progress_await(task_id, run_id, todos, phase: str) -> None:
    """跨进程广播 AI 进度（await 版本）：确保 done 信号一定送达后端。

    供收尾阶段使用（如讨论完成时发 phase=done 让前端收起执行过程），
    避免 best-effort 线程模式偶发未送达导致前端"一直转不停"。
    """
    import httpx
    from ai.config import get_ai_config

    try:
        cfg = get_ai_config()
        if not cfg.backend_base_url or not cfg.internal_api_key:
            return
        url = f"{cfg.backend_base_url.rstrip('/')}/api/tasks/{task_id}/internal/broadcast-ai-progress"
        headers = {"X-API-Key": cfg.internal_api_key}
        payload = {"run_id": run_id, "phase": phase, "todos": todos or []}
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(url, json=payload, headers=headers)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"AI 进度收尾广播失败 task_id={task_id}: {e}")
