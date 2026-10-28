import asyncio
import io
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.wechat_qrcode import WechatQrcode
from app.modules.admin.api import auth as admin_auth
from app.modules.admin.api import qrcode as qrcode_api


PNG = b"\x89PNG\r\n\x1a\nimage"
ENDPOINT = "/api/admin/qrcodes/batch-download"


@pytest.fixture
def setup(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    WechatQrcode.__table__.create(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        db.add_all([
            WechatQrcode(id=1, name="项目A", ticket="ticket-1", status="published", type="permanent"),
            WechatQrcode(id=2, name="项目B", ticket="ticket-2", status="entering", type="temporary"),
            WechatQrcode(id=3, name="未生成", status="init", type="permanent"),
            WechatQrcode(id=4, name='../项目:A\\B*?', ticket="ticket-4", status="published", type="permanent"),
        ])
        db.commit()
    monkeypatch.setattr(qrcode_api, "db_manager", SimpleNamespace(get_db=sessions))
    fetch_image = AsyncMock(return_value=(PNG, "png"))
    monkeypatch.setattr(qrcode_api, "_fetch_qrcode_image", fetch_image)
    application = FastAPI()
    application.include_router(qrcode_api.router, prefix="/api/admin")
    application.dependency_overrides[admin_auth.get_current_active_user_from_token] = lambda: {
        "username": "tester", "permissions": ["frontend:admin:other:show"],
    }
    with TestClient(application) as client:
        yield SimpleNamespace(client=client, app=application, sessions=sessions, fetch_image=fetch_image)
    engine.dispose()


def image_ids(response):
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert "attachment;" in response.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        return {int(name.split("_")[0]) for name in archive.namelist() if name.endswith(".png")}


@pytest.mark.parametrize("scope", ["selected", "page"])
def test_explicit_ids_only_and_deduplicate(setup, scope):
    response = setup.client.post(ENDPOINT, json={"scope": scope, "qid_list": [1, 2, 1], "status": "init"})
    assert image_ids(response) == {1, 2}
    assert setup.fetch_image.await_count == 2


def test_filtered_scope_covers_all_matches_not_just_page(setup):
    response = setup.client.post(ENDPOINT, json={"scope": "filtered", "status": "published", "qrcode_type": "permanent"})
    assert image_ids(response) == {1, 4}


@pytest.mark.parametrize("keyword, expected", [("项目B", {2}), ("2", {2})])
def test_keyword_matches_name_or_exact_id(setup, keyword, expected):
    response = setup.client.post(ENDPOINT, json={"scope": "filtered", "keyword": keyword})
    assert image_ids(response) == expected


def test_id_range_is_inclusive_and_ignores_filters(setup):
    response = setup.client.post(ENDPOINT, json={"scope": "range", "id_start": 2, "id_end": 4, "status": "init"})
    assert image_ids(response) == {2, 4}


def test_all_ignores_filters_and_reports_skipped_rows(setup):
    response = setup.client.post(ENDPOINT, json={"scope": "all", "status": "init", "qid_list": [3]})
    assert image_ids(response) == {1, 2, 4}
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        summary = archive.read("下载说明.txt").decode("utf-8-sig")
        assert "成功 3 条" in summary
        assert "ID 3：未生成 ticket" in summary
        for name in archive.namelist():
            assert not any(char in name for char in '\\/:*?"<>|')
    with setup.sessions() as db:
        assert db.get(WechatQrcode, 3).ticket is None
        assert db.get(WechatQrcode, 1).status == "published"


@pytest.mark.parametrize("payload, status", [
    ({"scope": "selected"}, 400),
    ({"scope": "page", "qid_list": []}, 400),
    ({"scope": "range", "id_start": 2, "id_end": 1}, 400),
    ({"scope": "range", "id_start": 1}, 400),
    ({"scope": "range", "id_start": 0, "id_end": 2}, 422),
    ({"scope": "range", "id_start": 1.5, "id_end": 2}, 422),
    ({"scope": "selected", "qid_list": [3]}, 400),
    ({"scope": "selected", "qid_list": [999]}, 404),
    ({"scope": "filtered", "keyword": "不存在"}, 404),
    ({"scope": "unknown"}, 422),
    ({}, 422),
    ({"scope": "selected", "qid_list": list(range(1, 502))}, 422),
])
def test_invalid_empty_or_ungenerated_range_does_not_download(setup, payload, status):
    assert setup.client.post(ENDPOINT, json=payload).status_code == status
    setup.fetch_image.assert_not_awaited()


def test_server_rejects_over_500_without_truncating(setup):
    with setup.sessions() as db:
        db.add_all(WechatQrcode(id=qid, name="额外记录", ticket="ticket") for qid in range(5, 502))
        db.commit()
    response = setup.client.post(ENDPOINT, json={"scope": "all"})
    assert response.status_code == 400
    assert "500" in response.json()["detail"]
    setup.fetch_image.assert_not_awaited()


def test_exactly_500_records_can_be_downloaded(setup):
    with setup.sessions() as db:
        db.add_all(WechatQrcode(id=qid, name="额外记录", ticket="ticket") for qid in range(5, 501))
        db.commit()
    response = setup.client.post(ENDPOINT, json={"scope": "all"})
    assert len(image_ids(response)) == 499
    assert setup.fetch_image.await_count == 499


def test_download_filters_match_list_filters_including_batch(setup):
    with setup.sessions() as db:
        db.get(WechatQrcode, 1).batch_id = "batch-a"
        db.commit()
    filters = {"status": "published", "qrcode_type": "permanent", "keyword": "项目", "batch_id": "batch-a"}
    listing = setup.client.get("/api/admin/qrcodes", params=filters)
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    response = setup.client.post(ENDPOINT, json={"scope": "filtered", **filters})
    assert image_ids(response) == {item["id"] for item in listing.json()["items"]} == {1}


def test_partial_upstream_failure_still_delivers_successful_images(setup):
    setup.fetch_image.side_effect = [(PNG, "png"), httpx.ReadTimeout("private-ticket-must-not-leak")]
    response = setup.client.post(ENDPOINT, json={"scope": "selected", "qid_list": [1, 2]})
    assert image_ids(response) == {1}
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        summary = archive.read("下载说明.txt").decode("utf-8-sig")
        assert "ID 2：图片下载失败" in summary
        assert "private-ticket" not in summary


def test_all_upstream_failures_return_error_not_empty_zip(setup):
    setup.fetch_image.side_effect = httpx.ReadTimeout("timeout")
    assert setup.client.post(ENDPOINT, json={"scope": "selected", "qid_list": [1]}).status_code == 502


@pytest.mark.parametrize("logged_in, expected", [(False, 401), (True, 403)])
def test_requires_admin_permission(setup, logged_in, expected):
    if logged_in:
        setup.app.dependency_overrides[admin_auth.get_current_active_user_from_token] = lambda: {"permissions": []}
    else:
        setup.app.dependency_overrides.clear()
    assert setup.client.post(ENDPOINT, json={"scope": "all"}).status_code == expected
    setup.fetch_image.assert_not_awaited()


@pytest.mark.parametrize("content, expected", [
    (PNG, "png"), (b"\xff\xd8\xffjpeg", "jpg"), (b'{"errcode":40001}', None),
    (PNG + b"x" * (2 * 1024 * 1024), None),
], ids=["png", "jpeg", "invalid-response", "oversized-image"])
def test_image_fetch_uses_fixed_wechat_host_and_validates_content(content, expected):
    async def run():
        def handle(request):
            assert request.url.host == "mp.weixin.qq.com"
            assert request.url.params["ticket"] == "ticket&with+special=?"
            return httpx.Response(200, content=content)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            if expected:
                image, extension = await qrcode_api._fetch_qrcode_image(client, "ticket&with+special=?")
                assert image == content
                assert extension == expected
            else:
                with pytest.raises(ValueError):
                    await qrcode_api._fetch_qrcode_image(client, "ticket&with+special=?")

    asyncio.run(run())
