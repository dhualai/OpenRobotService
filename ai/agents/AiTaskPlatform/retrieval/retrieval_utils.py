"""检索结果格式化工具（从 pipeline.py 拆分出纯逻辑，不依赖 self）

职责：
  - format_retrieval_results: 把检索返回的结果列表按类型格式化成带序号的文本

供 AiTaskAgent 的 _retrieve_* 方法内部调用（这些方法仍需 self._retriever，但不抽整方法，
只把"格式化结果列表"这一不依赖 self 的纯逻辑提出来复用）。
"""

# 结果前缀标签（按检索类型）
_KIND_LABEL = {
    "troubleshooting": "排查树",
    "task_resolutions": "历史工单",
    "platform_reference": "平台参考",
}

# 空结果/失败文案（按类型）
_KIND_EMPTY = {
    "troubleshooting": "（无匹配的排查树结论）",
    "task_resolutions": "（无相似的历史工单方案）",
    "platform_reference": "（无匹配的平台参考文档）",
}

_KIND_FAIL = {
    "troubleshooting": "（排查树检索失败）",
    "task_resolutions": "（历史方案检索失败）",
    "platform_reference": "（平台参考文档检索失败）",
}


def _verified_tag(r) -> str:
    """根据验证状态返回标注标签（P2：经验证/被推翻/复发）。"""
    v = (getattr(r, "verified", None) or "unknown")
    if v == "confirmed":
        return " [已验证]"
    if v == "rejected":
        return " [已被推翻]"
    if v == "recurred":
        return " [该问题复发过]"
    return ""


def format_retrieval_results(results, kind: str, err: bool = False) -> str:
    """把检索结果列表格式化成带序号的文本。

    Args:
        results: 支持 .title/.content/.id 的对象列表（如 RetrievalResult）
        kind: troubleshooting / task_resolutions / platform_reference
        err: True 表示检索本身失败（返回失败文案）

    Returns:
        格式化文本；无结果返回"空"文案；失败返回"失败"文案
    """
    if err:
        return _KIND_FAIL.get(kind, "（检索失败）")
    if not results:
        return _KIND_EMPTY.get(kind, "（无结果）")

    label = _KIND_LABEL.get(kind, "结果")
    lines = []
    for i, r in enumerate(results, 1):
        title = getattr(r, "title", None) or ""
        content = getattr(r, "content", None) or ""
        if not content.strip():
            continue
        if kind == "task_resolutions" and not title:
            title = f"工单 #{getattr(r, 'id', '')}"
        # P2：历史方案标注验证状态 + 根因类型（供 LLM 评估可信度）
        tag = ""
        if kind == "task_resolutions":
            tag = _verified_tag(r)
            rct = getattr(r, "root_cause_type", "") or ""
            if rct and rct != "unknown":
                tag += f" [根因类型:{rct}]"
        lines.append(f"{label} {i}：{title}{tag}\n{content}\n---")
    return "\n".join(lines) if lines else _KIND_EMPTY.get(kind, "（无结果）")
