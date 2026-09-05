"""附件代理的 HTTP Range / MIME 公共实现。

iOS Safari / WKWebView 的 <video> 播放强制依赖 HTTP Range 请求：
先发 `Range: bytes=0-1` 探测，要求服务端返回 206 Partial Content +
`Accept-Ranges: bytes`，否则拒绝播放（表现为黑屏斜线图标）。
安卓 Chrome 容忍 200 全量响应，故无 Range 支持时只表现为 iPhone 无法播放。

代理端点须把 Range 透传为 MinIO get_object 的 offset/length，返回 206。
"""
import mimetypes
import re
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.utils.minio_client import minio_client

# Windows 下 mimetypes 读注册表，可能缺 .mov/.m4v 等映射（返回 None → 落到
# application/octet-stream，浏览器不识别为视频）。统一补齐常见视频 MIME。
mimetypes.add_type("video/quicktime", ".mov")
mimetypes.add_type("video/x-m4v", ".m4v")
mimetypes.add_type("video/mp4", ".mp4")
mimetypes.add_type("video/webm", ".webm")
mimetypes.add_type("video/ogg", ".ogg")

_RANGE_RE = re.compile(r"^(\d*)-(\d*)$")


def parse_single_range(range_header: str | None, size: int):
    """解析单区间 Range 头，返回闭区间 [start, end]；无/不适用返回 None。

    - 无 Range 头 / 非 bytes 单位 / 多区间（`,` 分隔）/ 格式非法 / end < start
      → 返回 None（调用方退回 200 全量响应，符合 RFC 9110 对不适用 Range 的容忍）。
    - start >= size → 抛 ValueError（调用方转 416 + Content-Range: bytes */size）。
    """
    if not range_header:
        return None
    header = range_header.strip()
    if not header.lower().startswith("bytes="):
        return None
    spec = header[6:].strip()
    if "," in spec:  # 多区间：不支持，退回全量 200
        return None
    m = _RANGE_RE.match(spec)
    if not m:
        return None
    start_s, end_s = m.groups()
    if start_s == "" and end_s == "":
        return None
    if start_s == "":
        # 后缀区间 bytes=-N → 最后 N 字节
        n = int(end_s)
        if n <= 0:
            raise ValueError("unsatisfiable range")
        start = max(0, size - n)
        end = size - 1
    else:
        start = int(start_s)
        end = int(end_s) if end_s else size - 1
        if end_s and end < start:
            return None  # last-byte-pos < first-byte-pos → 忽略 Range
    if start >= size:
        raise ValueError("range not satisfiable")
    return start, min(end, size - 1)


def guess_attachment_content_type(filename: str, stat=None) -> str:
    """附件 MIME：扩展名推断优先（微信原生查看器要求严格 image/* 等），
    回退 MinIO 元数据，再回退 octet-stream。"""
    return (
        mimetypes.guess_type(filename)[0]
        or getattr(stat, "content_type", None)
        or "application/octet-stream"
    )


def minio_ranged_response(
    bucket: str,
    obj: str,
    size: int,
    range_header: str | None,
    content_type: str,
    disposition: str,
    cache_control: str = "public, max-age=86400",
):
    """按 Range 头从 MinIO 取区间并构造 200/206 流式响应（附件代理专用）。

    206 响应带 Content-Range 与精确 Content-Length；200 响应带 Accept-Ranges: bytes，
    告知浏览器（尤其 iOS Safari）本端点支持 Range，<video> 才能正常拉流播放。
    """
    try:
        rng = parse_single_range(range_header, size)
    except ValueError:
        raise HTTPException(
            status_code=416,
            detail="Range Not Satisfiable",
            headers={"Content-Range": f"bytes */{size}"},
        )

    if rng is None:
        status, offset, length, content_length = 200, 0, None, size
    else:
        start, end = rng
        status = 206
        offset, length = start, end - start + 1
        content_length = length

    headers = {
        "Content-Disposition": disposition,
        "Cache-Control": cache_control,
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
    }
    if status == 206:
        start, end = rng
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"

    response = minio_client.client.get_object(bucket, obj, offset=offset, length=length)

    def iter_stream():
        try:
            for chunk in response.stream(amt=64 * 1024):
                yield chunk
        finally:
            response.close()
            response.release_conn()

    return StreamingResponse(
        iter_stream(),
        status_code=status,
        media_type=content_type,
        headers=headers,
    )
