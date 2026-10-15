"""DiscussionReadCapability — 读本工单讨论区原文（按需翻原话 / 找出处）。

背景：讨论区每轮 @U老师 进 prompt 的是「结构化状态（较早讨论的结论层）+ 最近 N 条原文」。
状态是**有损**的——它是索引，不是唯一副本。当问题需要核对原话、找出处、或问的状态里
没覆盖到时，由调度 LLM 主动调本能力回去读原文。

与「引用一句话再 @U老师」的分工：
  - 引用场景是**确定性**的，由 `format_quoted_comment_block` 在入口预加载（必发生，不占配额）；
  - 本能力是**按需**的，覆盖其余「状态不够用」的情况，占调度配额（见 usage.py 的边界说明）。

依赖注入：`task_id` 从 kwargs / runtime_ctx 传入；可按 `comment_id`（取该条上下文）
或 `keyword`（在正文里找）定位。

参考实现：`ticket_ref.py`（同形状，目标是别的工单的上下文）。
"""

from __future__ import annotations

from ai.core.logging import get_logger
from ai.agents.AiTaskPlatform.capabilities.core.base import BaseCapability, CapabilityResult

logger = get_logger("TASK_AGENT")

# 关键词命中后，每条前后各取多少条做语境
_NEIGHBOR_K = 3
# 单次最多返回多少条
_MAX_ROWS = 30


class DiscussionReadCapability(BaseCapability):
    """读取**本工单**讨论区的原文（可定位到某条评论，或按关键词检索发言）。

    输入（任一）:
      - comment_id: 评论 id —— 返回该条及其前后各几条；
      - keyword   : 关键词 —— 返回正文命中的评论及少量上下文；
      - 都不给    : 返回最近若干条原文。
    输出: 渲染好的讨论原文文本。
    """

    name = "discussion_read"
    description = (
        "读取本工单讨论区的**原始发言**（区别于已在上下文里的结构化状态）。"
        "适用于：需要核对某句话说没说、谁说的、找出处；或问题涉及较早的讨论细节，"
        "而当前上下文里的状态不足以判断时。"
        "输入: comment_id(可选，定位到某条评论) 或 keyword(可选，按关键词找发言)。"
        "输出: 讨论原文（含发言人）。"
    )
    tags = ["discussion_read", "讨论原文", "原话", "出处", "核对"]
    # 占调度配额，与 log_analyze / project_info 同档；避免一轮里反复翻。
    max_usage_per_session = 3

    def is_available(self) -> bool:
        """始终可用：纯 DB 只读，不依赖外部服务。"""
        return True

    async def run(self, **kwargs) -> CapabilityResult:
        runtime_ctx = kwargs.get("runtime_ctx") or {}
        task_id = (
            kwargs.get("task_id")
            or runtime_ctx.get("task_id")
            or (runtime_ctx.get("current_task") or {}).get("task_id")
            or ""
        )
        if not task_id:
            return CapabilityResult.failure("discussion_read 缺少 task_id")

        comment_id = kwargs.get("comment_id") or runtime_ctx.get("comment_id")
        keyword = (kwargs.get("keyword") or kwargs.get("query") or "").strip()

        try:
            from ai.agents.AiTaskPlatform.contexts.comments import (
                load_comment_context,
                load_comment_rows_after,
            )
        except Exception as e:
            return CapabilityResult.failure(f"读取讨论原文失败: {type(e).__name__}: {e}")

        try:
            # 形态一：点名某条评论 → 该条 + 前后各 _NEIGHBOR_K 条
            if comment_id:
                rows = load_comment_context(comment_id, k=_NEIGHBOR_K)
                if rows:
                    return self._ok(rows, source="comment_id", comment_id=comment_id)
                return CapabilityResult(
                    text="（未找到该条评论，可能已被删除）",
                    meta={"count": 0, "comment_id": comment_id},
                    ok=True,
                )

            rows = load_comment_rows_after(task_id, after_id=0)

            # 形态二：按关键词在正文里找
            if keyword:
                hits = [r for r in rows if keyword in str(r.get("content") or "")]
                if not hits:
                    return CapabilityResult(
                        text=f"（讨论原文里没有出现「{keyword}」）",
                        meta={"count": 0, "keyword": keyword},
                        ok=True,
                    )
                picked = self._with_neighbors(rows, [r.get("id") for r in hits])
                return self._ok(picked, source="keyword", keyword=keyword)

            # 形态三：没给定位 → 最近若干条原文
            return self._ok(rows[-_MAX_ROWS:], source="recent")
        except Exception as e:
            logger.warning(f"[discussion_read] 执行失败 task={task_id}: {e}")
            return CapabilityResult.failure(f"读取讨论原文失败: {type(e).__name__}: {e}")

    @staticmethod
    def _with_neighbors(rows: list, hit_ids: list) -> list:
        """把命中的 id 各扩前后 _NEIGHBOR_K 条，按原顺序去重返回。"""
        ids = [r.get("id") for r in rows]
        keep: set = set()
        for hid in hit_ids:
            try:
                i = ids.index(hid)
            except ValueError:
                continue
            for r in rows[max(0, i - _NEIGHBOR_K): i + _NEIGHBOR_K + 1]:
                keep.add(r.get("id"))
        return [r for r in rows if r.get("id") in keep][-_MAX_ROWS:]

    @staticmethod
    def _ok(rows: list, **meta) -> CapabilityResult:
        from ai.agents.AiTaskPlatform.contexts.comments import render_comment_lines

        lines = render_comment_lines(rows, per_comment=None)
        if not lines:
            return CapabilityResult(text="（没有可读的讨论发言）", meta={"count": 0, **meta}, ok=True)
        return CapabilityResult(
            text="## 本工单讨论原文\n" + "\n".join(lines),
            meta={"count": len(lines), **meta},
            ok=True,
        )
