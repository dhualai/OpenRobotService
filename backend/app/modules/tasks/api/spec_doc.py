"""工单「问题文档」API：读取 / 保存（md 在线编辑）/ 上传解析 / 图片上传。

路由（挂在 /api/tasks 下）：
- GET  /{task_id}/spec-doc   读文档（无则 exists=false）
- PUT  /{task_id}/spec-doc   保存正文（乐观锁 revision，冲突返回 409）
- POST /spec-doc/parse       上传 .md/.doc/.docx 解析为 markdown（并保留原文件到 MinIO）
- POST /spec-doc/image       上传图片（编辑器粘贴/插入图片用），返回代理 URL

鉴权：GET 需登录；PUT 需登录 + 属主/接单人/管理员；parse/image 需登录。
安全：上传走扩展名白名单 + 魔数 + 大小上限（见 spec_doc_parser）；
     原文件/图片 object_path 均为服务端 uuid 命名，规避路径穿越与覆盖。
"""
import logging
import os
import re
import uuid
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.auth_routes import get_current_active_user_from_token
from app.core.config import settings
from app.core.database import get_async_db as get_db
from app.core.user_identity import actor_username, is_admin_user, user_matches
from app.models.task import Task, TaskSpecDoc
from app.modules.tasks.schemas.spec_doc import (
    SpecDocImageResult,
    SpecDocParseResult,
    SpecDocResponse,
    SpecDocUpdate,
)
from app.utils.minio_client import minio_client
from app.utils.spec_doc_parser import (
    MAX_DOC_SIZE,
    SpecDocParseError,
    ext_of,
    parse_spec_document,
)

router = APIRouter(tags=["task-spec-doc"])
logger = logging.getLogger(__name__)

_UNSAFE_FILENAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

# 编辑器图片上传：扩展名白名单 + 大小上限
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
MAX_IMAGE_SIZE = 10 * 1024 * 1024
# 魔数 → content_type（RIFF 需再校验 WEBP 标识）
_IMAGE_MAGICS: List[tuple] = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
]
# mammoth image_handler 的 content_type → 扩展名（emf/wmf 转存为 png 后缀名不合适，按原样保存）
_CONTENT_TYPE_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
    "image/x-emf": ".emf",
    "image/x-wmf": ".wmf",
}


def _sniff_image_mime(raw: bytes) -> str:
    """按魔数嗅探图片 MIME；RIFF 容器须为 WEBP。非图片返回空串。"""
    for magic, mime in _IMAGE_MAGICS:
        if raw.startswith(magic):
            return mime
    if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        return "image/webp"
    return ""


def _safe_filename(name: str) -> str:
    """安全化用户文件名（去目录、过滤危险字符），避免路径穿越。"""
    base = os.path.basename((name or "").strip()) or "document"
    base = _UNSAFE_FILENAME_RE.sub("_", base)
    base = base.lstrip(".") or "document"
    return base[:120]


async def _load_task_or_404(db: AsyncSession, task_id: int) -> Task:
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务未找到")
    return task


async def _load_doc(db: AsyncSession, task_id: int):
    result = await db.execute(select(TaskSpecDoc).where(TaskSpecDoc.task_id == task_id))
    return result.scalar_one_or_none()


def _can_edit(current_user: Any, task: Task) -> bool:
    if is_admin_user(current_user):
        return True
    return user_matches(current_user, task.created_by, task.assigned_to, task.customer)


def _resolve_name(username: str) -> str:
    if not username:
        return ""
    try:
        from app.services.user_service import user_service
        umap = user_service.get_user_map() or {}
        return umap.get(username) or username
    except Exception:
        return username


def _to_response(task_id: int, doc) -> SpecDocResponse:
    if not doc:
        return SpecDocResponse(exists=False, task_id=int(task_id))
    return SpecDocResponse(
        exists=True,
        task_id=int(doc.task_id),
        content=doc.content or "",
        content_type=doc.content_type or "markdown",
        source=doc.source,
        source_files=doc.source_files or [],
        revision=int(doc.revision or 1),
        created_by=doc.created_by,
        updated_by=doc.updated_by,
        updated_by_name=_resolve_name(doc.updated_by or ""),
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


@router.get("/{task_id}/spec-doc", response_model=SpecDocResponse)
async def get_spec_doc(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """读取工单的问题文档（无文档返回 exists=false，前端据此显示空态）。"""
    await _load_task_or_404(db, task_id)
    doc = await _load_doc(db, task_id)
    return _to_response(task_id, doc)


@router.put("/{task_id}/spec-doc", response_model=SpecDocResponse)
async def upsert_spec_doc(
    task_id: int,
    payload: SpecDocUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """创建/更新问题文档正文。

    乐观锁：payload.revision 与库中不一致 → 409（说明他人已改过），前端提示刷新。
    不传 revision 则强制覆盖（用于首次创建/明确覆盖）。
    """
    task = await _load_task_or_404(db, task_id)
    if not _can_edit(current_user, task):
        raise HTTPException(status_code=403, detail="无权限编辑此工单文档")

    username = actor_username(current_user)
    doc = await _load_doc(db, task_id)

    if doc is None:
        doc = TaskSpecDoc(
            task_id=task_id,
            content=payload.content,
            content_type="markdown",
            source=payload.source or "inline",
            source_files=payload.source_files or [],
            revision=1,
            created_by=username,
            updated_by=username,
        )
        db.add(doc)
    else:
        if payload.revision is not None and int(payload.revision) != int(doc.revision or 1):
            raise HTTPException(
                status_code=409, detail="文档已被他人更新，请刷新后重试"
            )
        doc.content = payload.content
        if payload.source:
            doc.source = payload.source
        if payload.source_files is not None:
            doc.source_files = payload.source_files
        doc.revision = int(doc.revision or 1) + 1
        doc.updated_by = username

    await db.commit()
    await db.refresh(doc)
    return _to_response(task_id, doc)


@router.post("/spec-doc/parse", response_model=SpecDocParseResult)
async def parse_spec_doc(
    file: UploadFile = File(...),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """上传 .md/.markdown/.txt/.doc/.docx → 解析为 markdown；原文件保留到 MinIO。

    Word 内嵌图片外置到 MinIO（markdown 只存代理 URL，治本 base64 内联），
    外置失败自动降级内联 base64（见 spec_doc_parser._mammoth_image_converter）。
    mammoth/soffice/minio 均为阻塞调用，走线程池避免卡事件循环。
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="文件内容为空")
    if len(raw) > MAX_DOC_SIZE:
        raise HTTPException(status_code=400, detail="文件过大，上限 5MB")

    def _upload_image(data: bytes, content_type: str) -> str:
        """mammoth image_handler：内嵌图片落 MinIO，返回 /api/tasks/files 代理 URL。"""
        ct = (content_type or "").strip().lower() or "image/png"
        ext = _CONTENT_TYPE_EXT.get(ct, ".png")
        object_path = f"{settings.COMMENT_BUCKET}/spec-doc/images/{uuid.uuid4().hex}{ext}"
        if not minio_client.upload_bytes(data, object_path, ct):
            return ""  # 空串 → parser 侧降级 base64 内联
        return f"/api/tasks/files/{object_path}"

    filename = file.filename or "document"
    try:
        content = await run_in_threadpool(parse_spec_document, filename, raw, _upload_image)
    except SpecDocParseError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 原始文件落 MinIO（保留原件可下载）；失败降级为空 object_path，不阻塞解析结果
    object_path = ""
    try:
        safe_name = _safe_filename(filename)
        object_path = f"{settings.COMMENT_BUCKET}/spec-doc/{uuid.uuid4().hex}/{safe_name}"
        ok = await run_in_threadpool(
            minio_client.upload_bytes,
            raw,
            object_path,
            file.content_type or "application/octet-stream",
        )
        if not ok:
            logger.warning("[spec_doc] 原始文件上传 MinIO 返回失败: %s", object_path)
            object_path = ""
    except Exception as e:  # noqa: BLE001 - 存储失败不影响解析结果
        logger.warning("[spec_doc] 原始文件上传异常: %s", e)
        object_path = ""

    return SpecDocParseResult(
        content=content, filename=filename, size=len(raw), object_path=object_path
    )


@router.post("/spec-doc/image", response_model=SpecDocImageResult)
async def upload_spec_doc_image(
    file: UploadFile = File(...),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """上传单张图片（问题文档编辑器「插入图片」/粘贴/拖拽用），返回代理 URL。

    安全：扩展名白名单 + 魔数校验 + 10MB 上限 + 服务端 uuid 命名，
    markdown 引用 /api/tasks/files/{object_path}（附件代理，免预签名 host 问题）。
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="文件内容为空")
    if len(raw) > MAX_IMAGE_SIZE:
        raise HTTPException(status_code=400, detail="图片过大，上限 10MB")

    filename = file.filename or "image.png"
    ext = ext_of(filename)
    if ext not in _IMAGE_EXTS:
        raise HTTPException(
            status_code=400, detail="仅支持 png / jpg / jpeg / gif / webp / bmp 图片"
        )
    content_type = _sniff_image_mime(raw)
    if not content_type:
        raise HTTPException(status_code=400, detail="文件不是有效的图片（魔数校验失败）")

    object_path = f"{settings.COMMENT_BUCKET}/spec-doc/images/{uuid.uuid4().hex}{ext}"
    try:
        ok = await run_in_threadpool(minio_client.upload_bytes, raw, object_path, content_type)
    except Exception as e:  # noqa: BLE001
        logger.warning("[spec_doc] 图片上传异常: %s", e)
        ok = False
    if not ok:
        raise HTTPException(status_code=500, detail="图片上传失败，请稍后重试")

    return SpecDocImageResult(url=f"/api/tasks/files/{object_path}")
