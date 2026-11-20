from io import BytesIO
from pathlib import PurePath
from urllib.parse import quote
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from minio.error import S3Error
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.models.knowledge_challenge import KnowledgeChallengeAnswer, KnowledgeChallengeAttachment
from app.modules.admin.services import knowledge_service as knowledge
from app.utils.minio_client import minio_client


MAX_FILE_SIZE = 20 * 1024 * 1024
DOCUMENT_TYPES = {
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".pdf": "application/pdf",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".rtf": "application/rtf",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".odp": "application/vnd.oasis.opendocument.presentation",
}
OFFICE_ENTRIES = {".docx": "word/document.xml", ".xlsx": "xl/workbook.xml", ".pptx": "ppt/presentation.xml"}


def document_type(filename, raw):
    extension = PurePath(filename).suffix.lower()
    if extension not in DOCUMENT_TYPES:
        raise HTTPException(422, "仅支持 Word、Markdown、Excel、PDF、PPT、TXT、CSV、RTF 和 OpenDocument 文档")
    valid = False
    if extension == ".pdf":
        valid = raw.startswith(b"%PDF-")
    elif extension in (".doc", ".xls", ".ppt"):
        valid = raw.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    elif extension in OFFICE_ENTRIES or extension in (".odt", ".ods", ".odp"):
        try:
            with ZipFile(BytesIO(raw)) as archive:
                if extension in OFFICE_ENTRIES:
                    valid = OFFICE_ENTRIES[extension] in archive.namelist()
                else:
                    entry = archive.getinfo("mimetype")
                    valid = entry.file_size < 128 and archive.read(entry).decode("ascii") == DOCUMENT_TYPES[extension]
        except (BadZipFile, KeyError, UnicodeError, RuntimeError, NotImplementedError):
            valid = False
    elif extension == ".rtf":
        valid = raw.startswith(b"{\\rtf")
    else:
        for encoding in (("utf-16",) if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else ("utf-8-sig", "gb18030")):
            try:
                text = raw.decode(encoding)
                valid = not any(ord(character) < 32 and character not in "\t\r\n" for character in text)
                break
            except UnicodeError:
                continue
    if not valid:
        raise HTTPException(422, "文档内容与文件格式不符，请上传有效文档")
    return DOCUMENT_TYPES[extension]


def attachment_info(attachment):
    return {"id": attachment.id, "name": attachment.filename,
        "size": attachment.size, "content_type": attachment.content_type}


async def attachment_details(db, attachment_ids):
    if not attachment_ids:
        return []
    rows = (await db.execute(select(KnowledgeChallengeAttachment).where(
        KnowledgeChallengeAttachment.id.in_(attachment_ids)))).scalars().all()
    records = {row.id: row for row in rows}
    return [attachment_info(records[attachment_id]) for attachment_id in attachment_ids if attachment_id in records]


async def validate_attachment_ids(db, challenge_id, user_id, attachment_ids):
    attachment_ids = list(dict.fromkeys(attachment_ids))
    if attachment_ids:
        owned = set((await db.execute(select(KnowledgeChallengeAttachment.id).where(
            KnowledgeChallengeAttachment.id.in_(attachment_ids),
            KnowledgeChallengeAttachment.challenge_id == challenge_id,
            KnowledgeChallengeAttachment.uploader_id == user_id))).scalars().all())
        if owned != set(attachment_ids):
            raise HTTPException(422, "请使用本人在本题上传成功的附件")
    return attachment_ids


async def upload(db, actor, challenge_id, file):
    from app.modules.admin.services import knowledge_challenge as challenges

    user_id = await knowledge.actor_id(db, actor)
    challenge = await challenges.get_challenge(db, challenge_id, actor, lock=True)
    challenges.ensure_open(challenge)
    answer = (await db.execute(select(KnowledgeChallengeAnswer).where(
        KnowledgeChallengeAnswer.challenge_id == challenge_id,
        KnowledgeChallengeAnswer.author_id == user_id))).scalar_one_or_none()
    if answer and (answer.hidden or (answer.submitted_at is not None and challenges.remaining_answer_edits(answer) == 0)):
        raise HTTPException(409, "本题答案不能继续编辑或上传附件")
    filename = (file.filename or "").strip()
    if not filename or len(filename) > 255 or any(ord(character) < 32 or character in "/\\" for character in filename):
        raise HTTPException(422, "无效的附件文件名")
    raw = await file.read(MAX_FILE_SIZE + 1)
    if not raw:
        raise HTTPException(422, "不能上传空文件")
    if len(raw) > MAX_FILE_SIZE:
        raise HTTPException(413, "单个文档不能超过20MB")
    content_type = document_type(filename, raw)
    attachment_id = uuid4().hex
    path = f"{settings.COMMENT_BUCKET}/knowledge-challenges/{challenge_id}/{uuid4().hex}{PurePath(filename).suffix.lower()}"
    success = await run_in_threadpool(minio_client.upload_bytes, raw, path, content_type)
    if not success:
        raise HTTPException(502, "附件上传失败，请稍后重试")
    challenges.ensure_open(challenge)
    attachment = KnowledgeChallengeAttachment(id=attachment_id, challenge_id=challenge_id,
        uploader_id=user_id, filename=filename, size=len(raw), content_type=content_type,
        object_path=path, created_at=knowledge.utcnow())
    db.add(attachment)
    await db.commit()
    return attachment_info(attachment)


async def download(db, actor, challenge_id, attachment_id):
    from app.modules.admin.services import knowledge_challenge as challenges

    user_id = await knowledge.actor_id(db, actor)
    challenge = await challenges.get_challenge(db, challenge_id, actor)
    attachment = await db.get(KnowledgeChallengeAttachment, attachment_id)
    if not attachment or attachment.challenge_id != challenge_id:
        raise HTTPException(404, "附件不存在或暂不可见")
    if attachment.uploader_id != user_id:
        answers = (await db.execute(select(KnowledgeChallengeAnswer).where(
            KnowledgeChallengeAnswer.challenge_id == challenge_id,
            KnowledgeChallengeAnswer.submitted_at.is_not(None), KnowledgeChallengeAnswer.hidden.is_(False)))).scalars().all()
        frozen = {entry["id"]: entry for entry in (challenge.snapshot or {}).get("answers", [])}
        visible = {file_id for answer in answers for file_id in (
            frozen[answer.id].get("attachments", []) if answer.id in frozen else (answer.attachments or []))}
        if attachment_id not in visible:
            raise HTTPException(404, "附件不存在或暂不可见")
    bucket, object_name = attachment.object_path.split("/", 1)
    try:
        response = await run_in_threadpool(minio_client.client.get_object, bucket, object_name)
    except S3Error as error:
        raise HTTPException(404 if error.code in ("NoSuchKey", "NoSuchBucket") else 502, "附件暂时无法下载") from error

    def chunks():
        try:
            yield from response.stream(64 * 1024)
        finally:
            response.close()
            response.release_conn()

    return StreamingResponse(chunks(), media_type="application/octet-stream", headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(attachment.filename, safe='')}",
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})
