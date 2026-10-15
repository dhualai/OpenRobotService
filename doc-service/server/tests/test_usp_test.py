from __future__ import annotations

import json
from io import BytesIO
from urllib.error import HTTPError, URLError

from app.services import usp_test
from tests.conftest import login


class _Resp:
    def __init__(self, payload, status=200):
        self.status = status
        self._raw = json.dumps(payload).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_graph_test_sso_on_usp_hub(client):
    login(client, "admin", "admin-secret")
    g = client.get("/api/v1/products/usp/graph").json()
    hub = next(x for x in g["groups"] if x["id"] == "usp_algo")
    assert hub["test_sso"] is True


def test_test_sso_requires_login(client):
    r = client.post("/api/v1/products/usp/test-sso")
    assert r.status_code == 401


def test_test_sso_ors_rejected(client):
    login(client, "admin", "admin-secret")
    r = client.post("/api/v1/products/ors/test-sso")
    assert r.status_code == 400
    assert r.json()["detail"] == "bad_product"


def test_test_sso_unconfigured(client, monkeypatch):
    monkeypatch.setenv("USP_TEST_ADMIN_LICENSE", "")
    from app.settings import get_settings

    get_settings.cache_clear()
    login(client, "guest", "guest-secret")
    r = client.post("/api/v1/products/usp/test-sso")
    assert r.status_code == 503
    assert r.json()["detail"] == "unconfigured"
    get_settings.cache_clear()


def test_guest_can_open_test_sso(client, monkeypatch):
    monkeypatch.setenv("USP_TEST_BASE_URL", "http://butler.test")
    monkeypatch.setenv("USP_TEST_ADMIN_LICENSE", "lic-admin-test")
    from app.settings import get_settings

    get_settings.cache_clear()
    seen: list[tuple[str, str]] = []

    def fake_open(req, timeout=8):
        url = req.full_url
        method = req.get_method()
        seen.append((method, url))
        if "/api/external/users" in url and method == "GET":
            return _Resp({"users": []})
        if "/api/external/users" in url and method == "POST":
            return _Resp({"username": "guest", "role": "user", "license_code": "lic-u", "license_scope": "full"})
        if url.endswith("/api/external/sso"):
            return _Resp({"sso_token": "sso-abc", "expires_in": 60, "username": "guest"})
        raise AssertionError(url)

    monkeypatch.setattr(usp_test.urllib.request, "urlopen", fake_open)
    login(client, "guest", "guest-secret")
    r = client.post("/api/v1/products/usp/test-sso")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["url"] == "http://butler.test/?sso_token=sso-abc"
    assert body["username"] == "guest"
    assert any(m == "POST" and u.endswith("/api/external/sso") for m, u in seen)
    get_settings.cache_clear()


def test_test_sso_existing_user_skips_create(client, monkeypatch):
    monkeypatch.setenv("USP_TEST_BASE_URL", "http://butler.test")
    monkeypatch.setenv("USP_TEST_ADMIN_LICENSE", "lic-admin-test")
    from app.settings import get_settings

    get_settings.cache_clear()
    posts = []

    def fake_open(req, timeout=8):
        url = req.full_url
        method = req.get_method()
        posts.append((method, url))
        if "/api/external/users" in url and method == "GET":
            return _Resp({"users": [{"username": "eng", "role": "user", "enabled": True}]})
        if url.endswith("/api/external/sso"):
            return _Resp({"sso_token": "sso-eng", "expires_in": 60, "username": "eng"})
        raise AssertionError((method, url))

    monkeypatch.setattr(usp_test.urllib.request, "urlopen", fake_open)
    login(client, "eng", "eng-secret")
    r = client.post("/api/v1/products/usp/test-sso")
    assert r.status_code == 200, r.text
    assert r.json()["url"] == "http://butler.test/?sso_token=sso-eng"
    assert not any(m == "POST" and "/api/external/users" in u for m, u in posts)
    get_settings.cache_clear()


def test_test_sso_butler_down(client, monkeypatch):
    monkeypatch.setenv("USP_TEST_BASE_URL", "http://butler.test")
    monkeypatch.setenv("USP_TEST_ADMIN_LICENSE", "lic-admin-test")
    from app.settings import get_settings

    get_settings.cache_clear()

    def fake_open(req, timeout=8):
        raise URLError("down")

    monkeypatch.setattr(usp_test.urllib.request, "urlopen", fake_open)
    login(client, "admin", "admin-secret")
    r = client.post("/api/v1/products/usp/test-sso")
    assert r.status_code == 502
    assert r.json()["detail"] == "butler_unreachable"
    get_settings.cache_clear()


def test_test_sso_bad_license(client, monkeypatch):
    monkeypatch.setenv("USP_TEST_BASE_URL", "http://butler.test")
    monkeypatch.setenv("USP_TEST_ADMIN_LICENSE", "lic-dead")
    from app.settings import get_settings

    get_settings.cache_clear()

    def fake_open(req, timeout=8):
        raise HTTPError(req.full_url, 401, "unauthorized", hdrs=None, fp=BytesIO(b'{"detail":"invalid license"}'))

    monkeypatch.setattr(usp_test.urllib.request, "urlopen", fake_open)
    login(client, "admin", "admin-secret")
    r = client.post("/api/v1/products/usp/test-sso")
    assert r.status_code == 502
    assert r.json()["detail"] == "butler_auth"
    get_settings.cache_clear()
