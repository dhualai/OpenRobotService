"""ImageAnalyzeCapability — 图片分析能力（简单能力，方案甲收敛）

把 `attachments.parser.analyze_images` 包装为 `BaseCapability` 子类，让 Supervisor 可调度。
产品无关：attachments 与图上下文由 runtime_ctx 或调用方传入。

对应设计（见 TASK_AGENT_TARGET_ARCH.md §6c / 方案甲）：
  - 收敛 discuss_flow 的 3a 图片分析路径
  - 依赖运行时上下文：attachments（要分析的图片）、img_ctx（图上下文，可选）
"""

from __future__ import annotations

from ai.core.logging import get_logger
from ai.agents.AiTaskPlatform.capabilities.base import BaseCapability, CapabilityResult

logger = get_logger("TASK_AGENT")


def _is_image_att(att: dict) -> bool:
    """判断附件是否为图片（按 object_path/path 扩展名）。"""
    if not isinstance(att, dict):
        return False
    name = str(
        att.get("filename") or att.get("name")
        or att.get("object_path") or att.get("path") or att.get("url") or ""
    ).lower()
    return name.endswith((".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"))


class ImageAnalyzeCapability(BaseCapability):
    """图片分析：分析工单里的图片（界面截图/故障照片/图表），VLM + 文本两阶段。

    适用于工单附带截图/图片、且用户问'看下这张图'的场景。
    输入: attachments(含图片的附件列表)。输出: 图片内容描述/结论。
    """

    name = "image_analyze"
    description = (
        "图片分析：分析工单中的图片（界面截图/故障照片），识别其中信息。"
        "适用于用户上传了图片、或问'看下这张截图上是什么/这个界面哪里不对'的问题。"
        "输入: attachments(含图片附件)。输出: 图片内容描述。"
    )
    tags = ["image", "图片", "截图"]

    async def run(self, **kwargs) -> CapabilityResult:
        attachments = kwargs.get("attachments")
        # 兜底：本次"需新读"附件为空时，回退到全量附件（all_attachments），
        # 覆盖已解读(known)图片或用户要求"分析全部附件"的场景。只保留图片类附件。
        if not attachments:
            all_atts = kwargs.get("all_attachments") or []
            img_atts = [
                a for a in all_atts
                if _is_image_att(a)
            ]
            if img_atts:
                from ai.core.logging import get_logger
                get_logger("TASK_AGENT").info(
                    f"[image_analyze] attachments 为空，回退全量图片附件({len(img_atts)} 个)"
                )
                attachments = img_atts
        if not attachments:
            return CapabilityResult.failure("图片分析需要 attachments 参数（含图片的附件列表）")

        img_ctx = kwargs.get("img_ctx")
        try:
            from ai.agents.AiTaskPlatform.attachments.parser import analyze_images
            result = await analyze_images(attachments, img_ctx)
            if not result:
                return CapabilityResult(text="（图片分析未得到结论）", meta={}, ok=False, error="图片分析无结论")
            return CapabilityResult(text=result, meta={"has_image": True}, ok=True)
        except Exception as e:
            logger.warning(f"ImageAnalyzeCapability 执行失败: {e}")
            return CapabilityResult.failure(f"图片分析失败: {type(e).__name__}: {e}")
