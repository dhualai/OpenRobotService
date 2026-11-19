from __future__ import annotations

from app.doc_updates import doc_update_closed, migrated_notice, migrated_url
from app.settings import get_settings
from tests.conftest import login


def test_closed_paths_leave_login_read_and_test_jump(tmp_path, monkeypatch):
    marker = tmp_path / "migrated.url"
    marker.write_text("http://203.0.113.5:18080\n", encoding="utf-8")
    monkeypatch.delenv("TOPO_MIGRATED_URL", raising=False)
    monkeypatch.setenv("TOPO_MIGRATED_URL_FILE", str(marker))
    get_settings.cache_clear()
    assert migrated_url() == "http://203.0.113.5:18080"
    assert migrated_notice() == "本站已迁移至 http://203.0.113.5:18080，请登录新地址更新。"
    assert doc_update_closed("POST", "/api/v1/products/usp/packs/secret_mod")
    assert doc_update_closed("POST", "/api/v1/products/usp/requirements/req_x/pack")
    assert doc_update_closed("POST", "/api/v1/drafts/d1/pack")
    assert doc_update_closed("PUT", "/api/v1/products/usp/modules/secret_mod/document")
    assert doc_update_closed("POST", "/api/v1/products/usp/modules/secret_mod/revisions/restore")
    assert doc_update_closed("POST", "/api/v1/products/usp/requirements/req_x/revisions/restore")
    assert doc_update_closed("PUT", "/api/v1/products/usp/boards/board.json")
    assert not doc_update_closed("GET", "/api/v1/products/usp/packs/secret_mod")
    assert not doc_update_closed("POST", "/api/v1/products/usp/test-sso")
    assert not doc_update_closed("POST", "/api/v1/auth/login")
    assert not doc_update_closed("POST", "/api/v1/products/usp/visits")
    assert not doc_update_closed("PUT", "/api/v1/products/usp/hours")


def test_open_when_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("TOPO_MIGRATED_URL", raising=False)
    monkeypatch.setenv("TOPO_MIGRATED_URL_FILE", str(tmp_path / "absent"))
    get_settings.cache_clear()
    assert migrated_url() == ""
    assert not doc_update_closed("POST", "/api/v1/products/usp/packs/secret_mod")
    assert not doc_update_closed("PUT", "/api/v1/products/usp/modules/secret_mod/document")


def test_api_refuses_doc_write_and_keeps_test_jump(client, tmp_path, monkeypatch):
    marker = tmp_path / "migrated.url"
    marker.write_text("http://203.0.113.5:18080", encoding="utf-8")
    monkeypatch.delenv("TOPO_MIGRATED_URL", raising=False)
    monkeypatch.setenv("TOPO_MIGRATED_URL_FILE", str(marker))
    get_settings.cache_clear()
    login(client, "eng", "eng-secret")
    denied = client.post("/api/v1/products/usp/packs/secret_mod", content=b"not-a-zip")
    assert denied.status_code == 403
    assert denied.json()["detail"] == "本站已迁移至 http://203.0.113.5:18080，请登录新地址更新。"
    page = client.get("/api/v1/products/usp/modules/secret_mod")
    assert page.status_code == 200
    jump = client.post("/api/v1/products/usp/test-sso")
    assert jump.status_code != 403
    me = client.get("/api/v1/me")
    assert me.status_code == 200
    assert me.json()["migrated_url"] == "http://203.0.113.5:18080"
