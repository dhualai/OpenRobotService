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
from typing import Optional

import httpx

from ai.core.logging import get_logger

logger = get_logger("TASK_AGENT")

_LOCALPROXY = ".localproxy"
_ARCHIVE_EXTS = (".zip", ".tar", ".tgz", ".gz", ".rar", ".7z")
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp")
_DOC_EXTS = (".docx", ".pdf", ".xlsx", ".md", ".xls", ".doc")
_SKIP_INNER = ("__macosx", ".ds_store", "thumbs.db")


def strip_localproxy(name: str) -> str:
    """去掉本地 MinIO 代理为绕开 nginx deny 加上的 .localproxy 后缀。"""
    n = name or ""
    if n.lower().endswith(_LOCALPROXY):
        return n[: -len(_LOCALPROXY)]
    return n


def normalize_attachment(att) -> Optional[dict]:
    """把字符串 object_path 或残缺 dict 收成 {filename, path, object_path}。"""
    if isinstance(att, str):
        s = att.strip()
        if not s:
            return None
        fname = s.rstrip("/").rsplit("/", 1)[-1] or s
        return {"filename": fname, "path": s, "object_path": s}
    if not isinstance(att, dict):
        return None
    obj = str(att.get("object_path") or att.get("path") or att.get("url") or "").strip()
    path = str(att.get("path") or att.get("url") or att.get("object_path") or "").strip()
    fname = str(att.get("filename") or att.get("name") or "").strip()
    if not fname:
        fname = (obj or path).rstrip("/").rsplit("/", 1)[-1] if (obj or path) else ""
    if not path and not obj:
        return None
    out = dict(att)
    out["filename"] = fname or "download.bin"
    out["path"] = path or obj
    out["object_path"] = obj or path
    return out


def is_log_filename(name: str) -> bool:
    n = strip_localproxy((name or "").replace("\\", "/").rsplit("/", 1)[-1]).lower()
    if not n or n.startswith("."):
        return False
    if n.endswith((".log", ".txt", ".csv", ".out", ".err")):
        return True
    if ".log." in n or re.search(r"\.log(\.\d+)?(\.gz)?$", n):
        return True
    return False


def is_archive_filename(name: str) -> bool:
    n = strip_localproxy((name or "").replace("\\", "/").rsplit("/", 1)[-1]).lower()
    return n.endswith(_ARCHIVE_EXTS)


def attachment_kind(filename: str, path: str = "") -> str:
    """按扩展名判断 image / log / doc / other（zip 等压缩包算 log 候选）。"""
    name = strip_localproxy(((filename or path) or "").replace("\\", "/").rsplit("/", 1)[-1]).lower()
    if any(name.endswith(ext) for ext in _IMAGE_EXTS):
        return "image"
    if is_log_filename(name) or is_archive_filename(name) or "log" in name:
        return "log"
    if any(name.endswith(ext) for ext in _DOC_EXTS):
        return "doc"
    return "other"


def _looks_like_minio_key(raw: str) -> bool:
    if not raw or os.path.isabs(raw) or raw.startswith(("http://", "https://")):
        return False
    if os.path.isfile(raw):
        return False
    return "/" in raw.replace("\\", "/")


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


def materialize_path(raw_path: str, tmp_dirs: list, task_id: Optional[str] = None, obj_key: str = "") -> str:
    """本地文件原样返回；MinIO 预签名 URL 或 bucket/object 下载到本地。失败返回空串。"""
    if not raw_path:
        return ""
    if os.path.isfile(raw_path):
        return raw_path
    if not raw_path.startswith(("http://", "https://")):
        key = obj_key or raw_path
        if _looks_like_minio_key(raw_path) or _looks_like_minio_key(key):
            return _download_minio_object(key, tmp_dirs, task_id=task_id)
        logger.warning(f"附件既不是本地文件也不是 MinIO key: {raw_path[:120]}")
        return ""

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
    return _download_minio_object(
        f"{bucket_name}/{object_name}", tmp_dirs, task_id=task_id, obj_key=obj_key
    )


def _download_minio_object(
    object_path: str, tmp_dirs: list, task_id: Optional[str] = None, obj_key: str = ""
) -> str:
    object_path = (object_path or "").strip()
    if not object_path:
        return ""
    try:
        from ai.core.minio_client import minio_client
        local_name = os.path.basename(strip_localproxy(object_path)) or "download.bin"
        if task_id is not None:
            from ai.core.log_cache import get_log_cache_dir
            dest_dir = get_log_cache_dir(task_id, obj_key or object_path)
            local_path = os.path.join(str(dest_dir), local_name)
            try:
                if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
                    logger.info(f"[log_cache] 复用已下载附件: {object_path} -> {local_path}")
                    return local_path
            except Exception:
                pass
        else:
            tmp_dir = tempfile.mkdtemp(prefix="log_dl_")
            tmp_dirs.append(tmp_dir)
            local_path = os.path.join(tmp_dir, local_name)
        if not minio_client.fget_object(object_path, local_path):
            logger.warning(f"MinIO 下载失败 {object_path}")
            return ""
        logger.info(f"附件下载完成: {object_path} -> {local_path}")
        return local_path
    except Exception as e:
        logger.warning(f"MinIO 下载失败 {object_path}: {e}")
        return ""


def _pick_log_files(candidates: list) -> list:
    """优先扩展名像日志的文件；没有则收下压缩包里非图片的正文。"""
    logs = [p for p in candidates if is_log_filename(p)]
    if logs:
        return logs
    fallback = []
    for p in candidates:
        name = os.path.basename(p).lower()
        if any(s in name for s in _SKIP_INNER):
            continue
        if any(name.endswith(ext) for ext in _IMAGE_EXTS + _DOC_EXTS):
            continue
        if os.path.isfile(p) and os.path.getsize(p) > 0:
            fallback.append(p)
    if fallback:
        logger.info(f"[extract_log] 压缩包内无 .log/.txt，回退收录 {len(fallback)} 个文件")
    return fallback


def extract_log_paths(attachments: list, task_id: Optional[str] = None) -> tuple[list, list]:
    """从附件列表中提取日志文件路径（压缩包先解压）。

    传入 task_id 时下载/解压到稳定缓存目录（不重复下载/解压、不随讨论清理，工单关闭才删）；
    未传时回退到 mkdtemp 临时目录。
    """
    log_paths: list = []
    tmp_dirs: list = []
    _ARCHIVE_MAX = 50

    for raw in attachments or []:
        att = normalize_attachment(raw)
        if not att:
            continue
        raw_path = att.get("path") or ""
        obj_key = att.get("object_path") or raw_path
        name = strip_localproxy(att.get("filename") or "").lower()
        if not raw_path:
            logger.warning(f"[extract_log] 跳过无路径附件 filename={att.get('filename')}")
            continue

        path = materialize_path(raw_path, tmp_dirs, task_id=task_id, obj_key=obj_key)
        if not path:
            logger.warning(f"[extract_log] 落地失败 filename={att.get('filename')} path={raw_path[:80]}")
            continue

        if is_log_filename(name) and not is_archive_filename(name):
            log_paths.append(path)
            continue

        if name.endswith((".rar", ".7z")):
            logger.warning(f"[extract_log] 暂不支持 {name}，请改传 zip/log")
            continue

        if not is_archive_filename(name) or not os.path.isfile(path):
            continue

        try:
            if task_id is not None:
                from ai.core.log_cache import get_log_cache_dir
                extract_dir = get_log_cache_dir(task_id, f"{obj_key or raw_path}::extract")
            else:
                extract_dir = _Path(tempfile.mkdtemp(prefix="log_extract_"))
                tmp_dirs.append(str(extract_dir))
            extracted: list = []

            if name.endswith(".zip"):
                with zipfile.ZipFile(path) as zf:
                    for info in zf.infolist()[:_ARCHIVE_MAX]:
                        if info.is_dir():
                            continue
                        inner = os.path.join(str(extract_dir), info.filename)
                        if not os.path.exists(inner):
                            zf.extract(info, str(extract_dir))
                        extracted.append(inner)

            elif name.endswith((".tar", ".tgz", ".gz")):
                raw_bytes = open(path, "rb").read()
                if name.endswith((".tgz", ".gz")):
                    try:
                        raw_bytes = gzip.decompress(raw_bytes)
                    except Exception:
                        pass
                try:
                    with tarfile.open(fileobj=io.BytesIO(raw_bytes), mode="r:*") as tf:
                        for member in tf.getmembers()[:_ARCHIVE_MAX]:
                            if member.isdir():
                                continue
                            inner = os.path.join(str(extract_dir), member.name)
                            if not os.path.exists(inner):
                                tf.extract(member, str(extract_dir))
                            extracted.append(inner)
                except tarfile.TarError:
                    inner = os.path.join(str(extract_dir), strip_localproxy(name).rsplit(".", 1)[0] or "inflated.log")
                    os.makedirs(str(extract_dir), exist_ok=True)
                    with open(inner, "wb") as f:
                        f.write(raw_bytes)
                    extracted.append(inner)

            log_paths.extend(_pick_log_files(extracted))
        except Exception as e:
            logger.warning(f"[extract_log] 解压失败 filename={att.get('filename')}: {e}")

    return log_paths, tmp_dirs
