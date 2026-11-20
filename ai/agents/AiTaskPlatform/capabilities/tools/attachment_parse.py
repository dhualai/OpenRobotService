"""AttachmentParseCapability — 附件解析能力

把 `attachments.parser.parse_attachments` 包装为 `BaseCapability`。
解析工单附件（日志/txt/文档/结构化/压缩包），生成摘要。产品无关。

对应设计（见 TASK_AGENT_TARGET_ARCH.md §6b.4）：`attachment_parse` 能力。
"""

from __future__ import annotations

from ai.core.logging import get_logger
from ai.agents.AiTaskPlatform.capabilities.core.base import BaseCapability, CapabilityResult

logger = get_logger("TASK_AGENT")


class AttachmentParseCapability(BaseCapability):
    """附件解析：解析工单附件的文本/日志/文档/结构化内容，生成摘要。

    适用于工单带附件（非图片）、需要从附件内容里提取信息帮助判断的场景。
    输入: attachments(附件列表)。输出: 附件分析摘要。
    """

    name = "attachment_parse"
    description = (
        "附件解析：解析工单附件（日志/文本/文档/结构化文件/压缩包）并生成摘要。"
        "适用于工单带有非图片附件、需要从其中提取关键信息的问题。"
        "输入: attachments(附件列表)。输出: 附件内容摘要。"
    )
    tags = ["attachment", "附件"]

    async def run(self, **kwargs) -> CapabilityResult:
        attachments = kwargs.get("attachments")
        # 兜底：本次"需新读"附件为空时，回退到全量附件（all_attachments），
        # 覆盖用户明确要求"分析全部附件"但它们此前已被标记为已解读(known)的情况。
        if not attachments:
            all_atts = kwargs.get("all_attachments")
            if all_atts:
                from ai.core.logging import get_logger
                get_logger("TASK_AGENT").info(
                    f"[attachment_parse] attachments 为空，回退全量 all_attachments({len(all_atts)} 个)"
                )
                attachments = all_atts
        if not attachments:
            return CapabilityResult.failure("附件解析需要 attachments 参数")

        try:
            from ai.agents.AiTaskPlatform.attachments.parser import parse_attachments
            result = await parse_attachments(attachments)
            # result 是 AttachmentAnalysis（Pydantic BaseModel），用 model_dump 取字段
            data = result.model_dump() if hasattr(result, "model_dump") else {}
            has_logs = data.get("has_logs", False)
            log_summary = data.get("log_summary", "")

            parts = []
            if log_summary:
                parts.append(f"日志/文本摘要: {log_summary[:500]}")
            if data.get("has_replay"):
                parts.append(f"回放摘要: {data.get('replay_summary', '')[:200]}")
            if parts:
                text = "\n".join(parts)
            else:
                text = "（附件未解析出有效文本内容）"

            return CapabilityResult(
                text=text,
                meta={"file_count": data.get("file_count", 0), "has_logs": has_logs},
                ok=True,
            )
        except Exception as e:
            logger.warning(f"AttachmentParseCapability 执行失败: {e}")
            return CapabilityResult.failure(f"附件解析失败: {type(e).__name__}: {e}")
