"""tasks 评论附件代理下载：前端通过后端代理读取 MinIO 对象，避免预签名 URL host 问题。

前端图片 src / 文件下载链接 = /api/tasks/files/{object_path}。
与 call 模块 /api/call/files/ 同构，用于工单评论附件（helpdesk-comment 桶）回显。
不要求登录（img src 无法带 Authorization header），靠 object_path 不可猜测性保护。
"""
import mimetypes
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from urllib.parse import quote

from app.utils.minio_client import minio_client

router = APIRouter(prefix="/files", tags=["tasks-attachments"])


@router.get("/{file_path:path}", summary="代理下载评论附件（图片/文件）")
async def download_attachment(file_path: str):
    """file_path = object_path（{bucket}/{...}/{filename}）。

    后端直连 MinIO 读取并流式返回，浏览器经后端域名访问，永远可达。
    """
    parts = file_path.split('/', 1)
    if len(parts) < 2 or not parts[0].strip() or not parts[1].strip():
        raise HTTPException(status_code=400, detail="路径格式错误，应为 {bucket}/{object}")
    bucket_name, object_name = parts[0], parts[1]
    try:
        stat = minio_client.get_file_info(f"{bucket_name}/{object_name}")
        if stat is None:
            raise HTTPException(status_code=404, detail="附件不存在")
        # 微信原生查看器（wx.previewImage）要求严格的 image/* MIME，而 MinIO 元数据常为
        # application/octet-stream 导致微信端图片不显示；浏览器 <img> 会内容嗅探故正常。
        # 故优先按扩展名推断 MIME，回退 MinIO 元数据，再回退 octet-stream。
        content_type = (
            mimetypes.guess_type(filename)[0]
            or getattr(stat, 'content_type', None)
            or 'application/octet-stream'
        )
        response = minio_client.client.get_object(bucket_name, object_name)
        filename = object_name.rsplit('/', 1)[-1]
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
            headers={"Content-Disposition": disposition},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"附件读取失败: {e}")
