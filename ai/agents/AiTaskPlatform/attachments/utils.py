"""附件与日志路径工具（从 pipeline.py 拆分，独立成模块）

职责（均为纯静态工具，不依赖 AiTaskAgent 实例状态）：
  - read_attachment_content: 读取附件文本内容（≤100KB）
  - extract_log_errors: 从日志文本提取 ERROR/WARN 行摘要
  - materialize_path: path 归一化（本地路径原样 / MinIO 预签名 URL 下载到本地）
  - extract_log_paths: 从附件列表提取日志文件路径（压缩包先解压到临时目录）
"""

import os
import re
import tempfile
import zipfile
import tarfile
import gzip
import io
from pathlib import Path as _Path

import httpx

from ai.core.logging import get_logger

logger = get_logger("TASK_AGENT")


async def read_attachment_content(att: dict) -> str:
    """读取附件文本内容（≤100KB）。"""
    path = att.get("path") or att.get("url", "")
    if not path:
        return ""

    try:
        if path.startswith("http"):
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                resp = await client.get(path)
                if resp.status_code == 200:
                    return resp.text[:100_000]
        else:
            local = _Path(path)
            if local.exists():
                return local.read_text(encoding="utf-8", errors="replace")[:100_000]
    except Exception:
        pass

    return ""


def extract_log_errors(text: str) -> str:
    """从日志文本提取 ERROR/WARN 行 + 时间线上下文。

    Returns:
        摘要文本 (≤2000 chars)
    """
    lines = text.split("\n")
    error_lines = []
    for line in lines:
        upper = line.upper()
        if any(kw in upper for kw in ("ERROR", "WARN", "EXCEPTION", "FAIL", "FATAL")):
            error_lines.append(line.strip()[:200])

    if not error_lines:
        first_ts = next((l for l in lines if len(l) > 20), "")
        last_ts = next((l for l in reversed(lines) if len(l) > 20), "")
        return f"日志 {len(lines)} 行，无明显错误。首行: {first_ts[:120]}, 尾行: {last_ts[:120]}"

    summary_lines = [
        f"日志 {len(lines)} 行，提取到 {len(error_lines)} 条异常："
    ] + error_lines[:20]
    return "\n".join(summary_lines)[:2000]


def materialize_path(raw_path: str, tmp_dirs: list) -> str:
    """path 归一化：本地路径原样返回；MinIO 预签名 URL 解析出桶名 + 资源路径，
    通过后端 minio_client 下载到本地临时目录后返回本地文件路径。

    解析或下载失败返回 ""。
    """
    if not raw_path:
        return ""
    if not raw_path.startswith(("http://", "https://")):
        return raw_path

    from urllib.parse import unquote, urlparse, urlunparse
    from ai.config import get_ai_config

    _strip_path = raw_path
    _prefix = (getattr(get_ai_config(), "minio_api_prefix", "") or "").strip("/")
    if _prefix:
        _u = urlparse(raw_path)
        _segs = [s for s in _u.path.split("/") if s]
        if _segs and _segs[0] == _prefix:
            _newpath = "/" + "/".join(_segs[1:])
            _strip_path = urlunparse(_u._replace(path=_newpath))

    m = re.match(r"https?://[^/]+/([^/]+)/(.+?)(?:\?|$)", _strip_path)
    if not m:
        logger.warning(f"无法从 URL 解析 bucket/object: {raw_path[:120]}")
        return ""
    bucket_name = unquote(m.group(1))
    object_name = unquote(m.group(2))

    try:
        from ai.core.minio_client import minio_client
        tmp_dir = tempfile.mkdtemp(prefix="log_dl_")
        tmp_dirs.append(tmp_dir)
        local_name = os.path.basename(object_name) or "download.bin"
        local_path = os.path.join(tmp_dir, local_name)
        object_name = minio_client.resolve_key(object_name)
        minio_client.client.fget_object(bucket_name, object_name, local_path)
        logger.info(f"附件下载完成: {bucket_name}/{object_name} -> {local_path}")
        return local_path
    except Exception as e:
        logger.warning(f"MinIO 下载失败 {bucket_name}/{object_name}: {e}")
        return ""


def extract_log_paths(attachments: list) -> tuple[list, list]:
    """从附件列表中提取日志文件路径（压缩包先解压到临时目录）。

    Returns:
        (log_paths, tmp_dirs): 日志文件绝对路径列表 + 待清理的临时目录列表
    """
    log_paths: list = []
    tmp_dirs: list = []
    _ARCHIVE_MAX = 50

    for att in attachments:
        if not isinstance(att, dict):
            continue
        raw_path = att.get("path") or att.get("url") or ""
        name = (att.get("filename") or att.get("name") or "").lower()
        if not raw_path:
            continue

        path = materialize_path(raw_path, tmp_dirs)
        if not path:
            continue

        if name.endswith((".log", ".txt", ".csv")) or (".log." in name):
            log_paths.append(path)
            continue

        if name.endswith((".zip", ".tar", ".tgz", ".gz")) and os.path.isfile(path):
            try:
                tmp_dir = tempfile.mkdtemp(prefix="log_extract_")
                tmp_dirs.append(tmp_dir)

                if name.endswith(".zip"):
                    with zipfile.ZipFile(path) as zf:
                        for info in zf.infolist()[:_ARCHIVE_MAX]:
                            if info.is_dir():
                                continue
                            zf.extract(info, tmp_dir)
                            inner = os.path.join(tmp_dir, info.filename)
                            iname = info.filename.lower()
                            if iname.endswith((".log", ".txt", ".csv")) or (".log." in iname):
                                log_paths.append(inner)

                elif name.endswith((".tar", ".tgz", ".gz")):
                    bio = io.BytesIO(open(path, "rb").read())
                    if name.endswith((".tgz", ".gz")):
                        bio = io.BytesIO(gzip.decompress(bio.read()))
                    with tarfile.open(fileobj=bio, mode="r:*") as tf:
                        for member in tf.getmembers()[:_ARCHIVE_MAX]:
                            if member.isdir():
                                continue
                            tf.extract(member, tmp_dir)
                            inner = os.path.join(tmp_dir, member.name)
                            iname = member.name.lower()
                            if iname.endswith((".log", ".txt", ".csv")) or (".log." in iname):
                                log_paths.append(inner)
            except Exception:
                pass

    return log_paths, tmp_dirs
