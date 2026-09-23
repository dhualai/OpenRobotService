"""问题文档内嵌图片的对象存储外置（API 保存/解析 与 存量治理脚本共用）。

背景：md 正文内嵌 base64 图片会形成 100KB+ 超长单行；前端问题文档编辑器
（@uiw/react-md-editor）挂载时用 Prism/refractor 做语法高亮，其 setext 标题
正则在「超长单行 + == 结尾」形态下呈 O(n²) 灾难性回溯，可把主线程阻塞数十秒
（工单 836 实测 81s，页面完全无法刷新/点击）。外置后正文回到 KB 级。

约定：
- 与编辑器上传图片（POST /spec-doc/image）落在同一目录（spec-doc/images/）；
- object_path 为服务端 uuid 命名，规避路径穿越与覆盖；
- 上传失败返回空串，调用方降级「保持内联 base64」，图片永不丢。
"""
import uuid

from app.core.config import settings
from app.utils.minio_client import minio_client

# content_type → 扩展名（emf/wmf 等按原样保存；未知类型统一 .png）
CONTENT_TYPE_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
    "image/x-emf": ".emf",
    "image/x-wmf": ".wmf",
}

# 内嵌图片在对象存储中的前缀
INLINE_IMAGE_PREFIX = "spec-doc/images"

# 内嵌图片外置后的代理 URL 前缀（前端 img src 直接可用）
INLINE_IMAGE_URL_PREFIX = "/api/tasks/files/"


def upload_inline_image(data: bytes, content_type: str) -> str:
    """内嵌图片落 MinIO，返回 /api/tasks/files 代理 URL；失败返回空串。"""
    ct = (content_type or "").strip().lower() or "image/png"
    ext = CONTENT_TYPE_EXT.get(ct, ".png")
    object_path = f"{settings.COMMENT_BUCKET}/{INLINE_IMAGE_PREFIX}/{uuid.uuid4().hex}{ext}"
    if not minio_client.upload_bytes(data, object_path, ct):
        return ""
    return f"{INLINE_IMAGE_URL_PREFIX}{object_path}"
