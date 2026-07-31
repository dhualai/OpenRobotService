"""附件代理下载：前端通过后端代理读取 MinIO 对象，避免预签名 URL 的 host 问题。

前端图片 src / 文件下载链接 = /api/call/files/{object_path}
（object_path = {bucket}/{session_id}/{filename}）。
后端用凭据直连内网 MinIO 读取，流式返回浏览器。不依赖预签名 URL——预签名 URL 的 host
是 MINIO_ENDPOINT（生产为 localhost:9000），浏览器在用户设备上访问不了 localhost → 碎图。
代理走后端域名，浏览器永远可达。

安全：接口不要求登录（img src 无法带 Authorization header），靠 object_path 中的
session_id（随机串 sess_{ts}_{rand}）不可猜测性保护，等同预签名 URL 的安全模型。
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from urllib.parse import quote

from app.utils.minio_client import minio_client

router = APIRouter(prefix="/files", tags=["call-attachments"])


@router.get("/{file_path:path}", summary="代理下载附件（图片/文件）")
async def download_attachment(file_path: str):
    """file_path = object_path（{bucket}/{session_id}/{filename}）。

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
        content_type = getattr(stat, 'content_type', None) or 'application/octet-stream'
        response = minio_client.client.get_object(bucket_name, object_name)
        filename = object_name.rsplit('/', 1)[-1]
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
            headers={"Content-Disposition": disposition},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"附件读取失败: {e}")
