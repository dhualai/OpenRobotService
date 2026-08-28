"""tasks 评论附件代理下载：前端通过后端代理读取 MinIO 对象，避免预签名 URL host 问题。

前端图片 src / 文件下载链接 = /api/tasks/files/{object_path}。
与 call 模块 /api/call/files/ 同构，用于工单评论附件（helpdesk-comment 桶）回显。
不要求登录（img src 无法带 Authorization header），靠 object_path 不可猜测性保护。
"""
import logging
import mimetypes
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from urllib.parse import quote

from app.core.config import settings
from app.utils.minio_client import minio_client

router = APIRouter(prefix="/files", tags=["tasks-attachments"])

logger = logging.getLogger(__name__)


@router.get("/{file_path:path}", summary="代理下载评论附件（图片/文件）")
async def download_attachment(request: Request, file_path: str):
    """file_path = object_path（{bucket}/{...}/{filename}）。

    后端直连 MinIO 读取并流式返回，浏览器经后端域名访问，永远可达。
    查找策略：严格路径 → 跨已知 bucket 兜底 → object 名 URL 编码再试（兼容中文名编码存储）。
    """
    parts = file_path.split('/', 1)
    if len(parts) < 2 or not parts[0].strip() or not parts[1].strip():
        raise HTTPException(status_code=400, detail="路径格式错误，应为 {bucket}/{object}")
    bucket_name, object_name = parts[0], parts[1]

    known_buckets = [settings.MINIO_BUCKET, settings.COMMENT_BUCKET, settings.FILE_IMAGES]
    candidates = [(bucket_name, object_name)]
    for b in known_buckets:
        candidates.append((b, object_name))
    candidates.append((bucket_name, quote(object_name)))
    for b in known_buckets:
        candidates.append((b, quote(object_name)))

    last_err: Exception | None = None
    for bucket, obj in candidates:
        try:
            if not minio_client.check_bucket_exists(bucket):
                continue
            stat = minio_client.get_file_info(f"{bucket}/{obj}")
            if stat is None:
                continue
            response = minio_client.client.get_object(bucket, obj)
            filename = obj.rsplit('/', 1)[-1]
            # 微信原生查看器（wx.previewImage）要求严格的 image/* MIME，而 MinIO 元数据常为
            # application/octet-stream 导致微信端图片不显示；浏览器 <img> 会内容嗅探故正常。
            # 故优先按扩展名推断 MIME，回退 MinIO 元数据，再回退 octet-stream。
            content_type = (
                mimetypes.guess_type(filename)[0]
                or getattr(stat, 'content_type', None)
                or 'application/octet-stream'
            )
            # RFC 5987 编码文件名（兼容中文）；inline 让浏览器内联显示图片、文件按类型预览/下载
            disposition = f"inline; filename*=UTF-8''{quote(filename)}"

            def iter_stream():
                try:
                    for chunk in response.stream(amt=64 * 1024):
                        yield chunk
                finally:
                    response.close()
                    response.release_conn()

            return StreamingResponse(
                iter_stream(),
                media_type=content_type,
                headers={
                    "Content-Disposition": disposition,
                    # 附件 object_path 不可猜测（含 session_id 随机串），可安全缓存；
                    # 缺少 Cache-Control 时浏览器不缓存，前端 ImageLightbox 预加载 new Image()
                    # 每次都重新请求 → imgReady 延迟 → loading 遮罩可见 → 预览闪烁。
                    # 加缓存后缩略图已加载则预加载命中缓存，imgReady 瞬时，无闪烁（与 /api/ai/media StaticFiles 一致）。
                    "Cache-Control": "public, max-age=86400",
                },
            )
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001 - 记录真实原因而非静默跳过
            last_err = e
            logger.warning('[tasks/files] 候选 (%s/%s) 失败: %s', bucket, obj, e)
            continue

    detail = "附件不存在"
    if last_err:
        detail += f"（末次错误: {last_err}）"
    raise HTTPException(status_code=404, detail=detail)
