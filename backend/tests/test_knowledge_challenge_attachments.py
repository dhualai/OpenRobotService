import importlib.util
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from zipfile import ZipFile

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select

from app.core.auth_routes import get_current_active_user_from_token
from app.core.database import get_async_db
from app.models.identity import UserDB
from app.models.knowledge_challenge import KnowledgeChallenge, KnowledgeChallengeAnswer, KnowledgeChallengeAttachment
from app.modules.admin.api.knowledge_challenge import AnswerBody, router
from app.modules.admin.services import knowledge_challenge as challenges
from app.modules.admin.services import knowledge_challenge_attachments as files
from backend.tests.test_knowledge import actor, db
from backend.tests.test_knowledge_challenge import admin, after_deadline, published, submitted


@pytest.fixture
def storage(monkeypatch):
    response = Mock()
    response.stream.side_effect = lambda size: iter([b"document content"])
    client = SimpleNamespace(upload_bytes=Mock(return_value=True), client=SimpleNamespace(get_object=Mock(return_value=response)))
    monkeypatch.setattr(files, "minio_client", client)
    return client


@pytest.fixture
def api(db):
    app = FastAPI()
    app.include_router(router)
    current = {"actor": actor("alice")}

    async def get_db():
        yield db

    app.dependency_overrides[get_async_db] = get_db
    app.dependency_overrides[get_current_active_user_from_token] = lambda: current["actor"]
    with TestClient(app) as client:
        yield client, current


async def upload(db, challenge_id, user_id="alice", name="排查说明.md", raw=b"# troubleshooting"):
    return await files.upload(db, actor(user_id), challenge_id, UploadFile(filename=name, file=BytesIO(raw)))


def zipped(entry, content=b"document"):
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(entry, content)
    return buffer.getvalue()


@pytest.mark.parametrize("name, raw", [
    ("guide.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1document"),
    ("guide.docx", zipped("word/document.xml")),
    ("data.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1spreadsheet"),
    ("data.xlsx", zipped("xl/workbook.xml")),
    ("slides.ppt", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1presentation"),
    ("slides.pptx", zipped("ppt/presentation.xml")),
    ("guide.PDF", b"%PDF-1.7\ncontent"),
    ("guide.md", "# 中文说明".encode()),
    ("guide.markdown", b"# guide"),
    ("guide.txt", "操作步骤".encode("gb18030")),
    ("data.csv", "序号,结果\n1,通过".encode("utf-16")),
    ("guide.rtf", b"{\\rtf1 document}"),
    ("guide.odt", zipped("mimetype", b"application/vnd.oasis.opendocument.text")),
    ("data.ods", zipped("mimetype", b"application/vnd.oasis.opendocument.spreadsheet")),
    ("slides.odp", zipped("mimetype", b"application/vnd.oasis.opendocument.presentation")),
])
def test_common_document_formats(name, raw):
    assert files.document_type(name, raw) == files.DOCUMENT_TYPES[Path(name).suffix.lower()]


@pytest.mark.parametrize("name, raw", [
    ("run.exe", b"MZ"), ("page.html", b"<html>"), ("image.svg", b"<svg>"),
    ("fake.pdf", b"not pdf"), ("fake.doc", b"not word"), ("fake.docx", b"PKbad zip"),
    ("wrong.xlsx", zipped("word/document.xml")), ("fake.odt", zipped("mimetype", b"wrong")),
    ("binary.md", b"\x00\x01\x02"), ("fake.rtf", b"plain text"),
])
def test_documents_reject_unrecognized_formats_or_mismatched_content(name, raw):
    with pytest.raises(HTTPException) as error:
        files.document_type(name, raw)
    assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_upload_stores_metadata_without_exposing_storage_or_uploader(db, storage):
    challenge = await published(db)
    attachment = await upload(db, challenge["id"])
    assert set(attachment) == {"id", "name", "size", "content_type"}
    assert attachment["name"] == "排查说明.md"
    assert len(attachment["id"]) == 32
    record = await db.get(KnowledgeChallengeAttachment, attachment["id"])
    assert record.uploader_id == "alice"
    assert f"/knowledge-challenges/{challenge['id']}/" in record.object_path
    assert record.object_path.endswith(".md")
    assert attachment["id"] not in record.object_path
    storage.upload_bytes.assert_called_once_with(b"# troubleshooting", record.object_path, "text/markdown")


@pytest.mark.asyncio
@pytest.mark.parametrize("name, raw, status", [("../guide.md", b"text", 422), ("bad\r\n.md", b"text", 422),
    ("empty.md", b"", 422), ("large.md", b"x" * (files.MAX_FILE_SIZE + 1), 413)],
    ids=["path-traversal", "control-characters", "empty", "oversize"])
async def test_upload_rejects_unsafe_names_empty_and_oversized_files(db, storage, name, raw, status):
    challenge = await published(db)
    with pytest.raises(HTTPException) as error:
        await upload(db, challenge["id"], name=name, raw=raw)
    assert error.value.status_code == status
    storage.upload_bytes.assert_not_called()


@pytest.mark.asyncio
async def test_upload_failure_does_not_create_a_reference(db, storage):
    challenge = await published(db)
    storage.upload_bytes.return_value = False
    with pytest.raises(HTTPException) as error:
        await upload(db, challenge["id"])
    assert error.value.status_code == 502
    assert not db.session.scalars(select(KnowledgeChallengeAttachment)).all()


@pytest.mark.asyncio
async def test_attachments_remain_private_until_publication_and_follow_edit_history_and_snapshot(db, storage, monkeypatch):
    challenge = await published(db)
    first = await upload(db, challenge["id"])
    second = await upload(db, challenge["id"], name="补充说明.pdf", raw=b"%PDF-1.7\nextra")
    draft = await challenges.write_answer(db, actor("alice"), challenge["id"], "排查步骤", 0, attachments=[first["id"]])
    assert draft["attachments"] == [first]
    assert not (await challenges.detail(db, actor("voter"), challenge["id"]))["answers"]
    with pytest.raises(HTTPException) as error:
        await files.download(db, actor("voter"), challenge["id"], first["id"])
    assert error.value.status_code == 404
    public = await challenges.write_answer(db, actor("alice"), challenge["id"], "排查步骤", draft["version"], submit=True)
    assert public["attachments"] == [first]
    edited = await challenges.write_answer(db, actor("alice"), challenge["id"], "新的步骤", public["version"], attachments=[second["id"]])
    assert (await challenges.detail(db, actor("voter"), challenge["id"]))["answers"][0]["attachments"] == [first]
    with pytest.raises(HTTPException):
        await files.download(db, admin(), challenge["id"], second["id"])
    await challenges.write_answer(db, actor("alice"), challenge["id"], "新的步骤", edited["version"], submit=True)
    answer = db.session.scalar(select(KnowledgeChallengeAnswer))
    assert answer.history[0]["attachments"] == [first["id"]]
    assert answer.draft_attachments is None
    after_deadline(monkeypatch)
    await challenges.settle(db, challenge["id"])
    answer.attachments = [first["id"]]
    await db.commit()
    result = await challenges.community_result(db, actor("voter"), challenge["id"])
    assert result["best_answer"]["attachments"] == [second]
    response = await files.download(db, actor("voter"), challenge["id"], second["id"])
    assert b"".join([chunk async for chunk in response.body_iterator]) == b"document content"
    storage.client.get_object.return_value.close.assert_called_once()
    storage.client.get_object.return_value.release_conn.assert_called_once()
    with pytest.raises(HTTPException):
        await files.download(db, actor("voter"), challenge["id"], first["id"])


@pytest.mark.asyncio
async def test_attachment_omission_preserves_files_and_empty_list_removes_them(db, storage):
    challenge = await published(db)
    attachment = await upload(db, challenge["id"])
    answer = await challenges.write_answer(db, actor("alice"), challenge["id"], "初稿", 0, submit=True, attachments=[attachment["id"]])
    draft = await challenges.write_answer(db, actor("alice"), challenge["id"], "修改", answer["version"])
    assert draft["attachments"] == [attachment]
    cleared = await challenges.write_answer(db, actor("alice"), challenge["id"], "修改", draft["version"], attachments=[])
    assert cleared["attachments"] == []
    assert (await challenges.detail(db, actor("voter"), challenge["id"]))["answers"][0]["attachments"] == [attachment]
    answer = await challenges.write_answer(db, actor("alice"), challenge["id"], "修改", cleared["version"], submit=True)
    assert answer["attachments"] == []
    with pytest.raises(HTTPException):
        await files.download(db, actor("voter"), challenge["id"], attachment["id"])


@pytest.mark.asyncio
async def test_cannot_attach_files_uploaded_by_others_or_to_another_question(db, storage):
    first = await published(db)
    second = await published(db, key="second")
    foreign_author = await upload(db, first["id"], user_id="bob")
    foreign_question = await upload(db, second["id"])
    for attachment_id in (foreign_author["id"], foreign_question["id"], "f" * 32):
        with pytest.raises(HTTPException) as error:
            await challenges.write_answer(db, actor("alice"), first["id"], "答案", 0, attachments=[attachment_id])
        assert error.value.status_code == 422
        await db.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize("condition", ["paused", "deadline", "exhausted", "hidden", "disabled"])
async def test_upload_requires_open_question_and_editable_answer(db, storage, monkeypatch, condition):
    challenge = await published(db)
    if condition == "paused":
        (await db.get(KnowledgeChallenge, challenge["id"])).state = "paused"
        await db.commit()
    elif condition == "deadline":
        after_deadline(monkeypatch)
    elif condition == "disabled":
        monkeypatch.setattr(challenges.settings, "KNOWLEDGE_CHALLENGES_ENABLED", False)
    else:
        answer = await submitted(db, challenge["id"])
        if condition == "exhausted":
            await challenges.write_answer(db, actor("alice"), challenge["id"], "修改", answer.version, submit=True)
        else:
            answer.hidden = True
            await db.commit()
    with pytest.raises(HTTPException) as error:
        await upload(db, challenge["id"])
    assert error.value.status_code == 409
    storage.upload_bytes.assert_not_called()


def test_attachment_payload_validates_ids_deduplicates_and_does_not_limit_count():
    ids = [f"{index:032x}" for index in range(20)]
    assert AnswerBody(content="内容", version=0, attachments=[*ids, ids[0]]).attachments == ids
    for invalid in ("https://example.com/doc.pdf", "../secret", "not-an-id"):
        with pytest.raises(ValueError):
            AnswerBody(content="内容", version=0, attachments=[invalid])


def test_api_zero_bounty_document_upload_publish_and_authenticated_download(db, storage, api):
    client, current = api
    current["actor"] = admin()
    response = client.post("/knowledge/challenges", json={"request_key": "docs-api", "title": "文档问题",
        "content": "请附文档", "bounty_fen": 0, "ends_at": "2026-11-21T00:00:00Z"})
    assert response.status_code == 201
    challenge = response.json()
    assert challenge["bounty_fen"] == 0
    base = f"/knowledge/challenges/{challenge['id']}"
    assert client.post(f"{base}/publish", json={"version": challenge["version"]}).status_code == 200
    current["actor"] = actor("alice")
    response = client.post(f"{base}/answer-attachments", files={"file": ("说明.md", b"# guide", "text/markdown")})
    assert response.status_code == 201
    attachment = response.json()
    download = f"{base}/answer-attachments/{attachment['id']}"
    owner = client.get(download)
    assert owner.status_code == 200
    assert owner.headers["content-disposition"].startswith("attachment; filename*=UTF-8''")
    assert owner.headers["x-content-type-options"] == "nosniff"
    current["actor"] = actor("voter")
    assert client.get(download).status_code == 404
    current["actor"] = actor("alice")
    body = {"content": "排查步骤", "version": 0, "attachments": [attachment["id"]]}
    saved = client.put(f"{base}/my-answer", json=body)
    assert saved.json()["attachments"] == [attachment]
    response = client.post(f"{base}/my-answer/submit", json={**body, "version": saved.json()["version"]})
    assert response.status_code == 200
    current["actor"] = actor("voter")
    assert client.get(base).json()["answers"][0]["attachments"] == [attachment]
    assert client.get(download).content == b"document content"
    client.app.dependency_overrides.pop(get_current_active_user_from_token)
    assert client.get(download).status_code == 401
    assert client.post(f"{base}/answer-attachments", files={"file": ("guide.md", b"text")}).status_code == 401


@pytest.mark.asyncio
async def test_hidden_answers_and_questions_do_not_expose_document_downloads(db, storage):
    challenge = await published(db)
    attachment = await upload(db, challenge["id"])
    await challenges.write_answer(db, actor("alice"), challenge["id"], "步骤", 0, submit=True, attachments=[attachment["id"]])
    answer = db.session.scalar(select(KnowledgeChallengeAnswer))
    answer.hidden = True
    await db.commit()
    with pytest.raises(HTTPException) as error:
        await files.download(db, actor("voter"), challenge["id"], attachment["id"])
    assert error.value.status_code == 404
    answer.hidden = False
    (await db.get(KnowledgeChallenge, challenge["id"])).hidden = True
    await db.commit()
    with pytest.raises(HTTPException) as error:
        await files.download(db, actor("voter"), challenge["id"], attachment["id"])
    assert error.value.status_code == 404
    storage.client.get_object.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("privilege", [{"is_admin": True}, {"permissions": ["admin"]}, {"can_manage_knowledge_challenges": True}, {}])
async def test_publisher_username_only_in_administrator_payloads(db, monkeypatch, privilege):
    challenge = await published(db)
    reader = {**actor("voter"), **privilege}
    responses = [(await challenges.listing(db, reader))["items"][0], await challenges.detail(db, reader, challenge["id"])]
    await submitted(db, challenge["id"])
    after_deadline(monkeypatch)
    await challenges.settle(db, challenge["id"])
    responses.append(await challenges.community_result(db, reader, challenge["id"]))
    for response in responses:
        if privilege:
            assert response["publisher_username"] == "reviewer-account"
        else:
            assert "publisher_username" not in response
            assert "reviewer-account" not in json.dumps(response)
        assert "publisher_id" not in response


def test_publisher_is_filtered_in_list_overview_and_detail_api(db, api):
    client, current = api
    current["actor"] = admin()
    response = client.post("/knowledge/challenges", json={"request_key": "publisher-api", "title": "问题",
        "content": "内容", "bounty_fen": 0, "ends_at": "2026-11-21T00:00:00Z"}).json()
    base = f"/knowledge/challenges/{response['id']}"
    client.post(f"{base}/publish", json={"version": response["version"]})
    for administrator in (False, True):
        current["actor"] = {**actor("voter"), "can_manage_knowledge_challenges": administrator}
        for path in ("/knowledge/challenges", "/knowledge/challenges/overview", base):
            payload = client.get(path).json()
            assert ("reviewer-account" in json.dumps(payload)) is administrator


@pytest.mark.asyncio
async def test_missing_publisher_does_not_break_administrator_display(db):
    challenge = await published(db)
    db.session.delete(await db.get(UserDB, "reviewer"))
    await db.commit()
    assert (await challenges.detail(db, {**actor("voter"), "is_admin": True}, challenge["id"]))["publisher_username"] is None


def test_attachment_migration_preserves_existing_answers_and_is_idempotent():
    path = Path(__file__).parents[1] / "alembic/versions/a4c8e2f6b901_challenge_answer_attachments.py"
    spec = importlib.util.spec_from_file_location("challenge_attachment_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE knowledge_challenge_answers (id INTEGER PRIMARY KEY, content TEXT)")
        connection.exec_driver_sql("INSERT INTO knowledge_challenge_answers VALUES (1, 'legacy answer')")
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
            assert connection.exec_driver_sql("SELECT content, attachments, draft_attachments FROM knowledge_challenge_answers").one() == ("legacy answer", None, None)
            assert "knowledge_challenge_attachments" in inspect(connection).get_table_names()
            migration.downgrade()
            migration.downgrade()
            assert connection.exec_driver_sql("SELECT content FROM knowledge_challenge_answers").scalar_one() == "legacy answer"
            assert "knowledge_challenge_attachments" not in inspect(connection).get_table_names()
    engine.dispose()
