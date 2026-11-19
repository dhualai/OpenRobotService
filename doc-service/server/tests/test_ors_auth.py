"""账号统一通道（app/services/ors_auth.py）的单测。

这组用例**不需要 Postgres**：只覆盖权限码匹配、角色映射，以及
``verify_ors_token`` 对摇人吧响应的各种失败处理（httpx.get 被打桩）。

注意：conftest 为了跑带库的用例会把 ``verify_ors_token`` 换成 fake，
本文件用 autouse fixture 先恢复真实实现，保证测的是真代码。
"""

from __future__ import annotations

import pytest

from app.services import ors_auth
from app.services.ors_auth import OrsUser


@pytest.fixture(autouse=True)
def _restore_real_verify(monkeypatch):
    real = getattr(ors_auth, "_real_verify_ors_token", None)
    if real is not None:
        monkeypatch.setattr(ors_auth, "verify_ors_token", real)
    yield


# ---------- 权限码段匹配（星号在用户串侧） ----------

@pytest.mark.parametrize(
    "user_perm,required,expected",
    [
        ("frontend:kb:write", "frontend:kb:write", True),
        ("frontend:kb:read", "frontend:kb:write", False),
        ("frontend:kb:manage", "frontend:kb:write", False),
        ("frontend:kb:*", "frontend:kb:write", True),
        ("frontend:*:write", "frontend:kb:write", True),
        ("*", "frontend:kb:write", False),  # 段数不同；通配直达在 _perm_matches_any 层
        ("frontend:kb", "frontend:kb:write", False),  # 段数不同
        ("backend:kb:write", "frontend:kb:write", False),
        ("admin", "frontend:kb:write", False),
        ("", "frontend:kb:write", False),
    ],
)
def test_perm_matches(user_perm, required, expected):
    assert ors_auth._perm_matches(user_perm, required) is expected


# ---------- 角色映射 ----------

@pytest.mark.parametrize(
    "perms,expected",
    [
        (("admin",), "admin"),
        (("*",), "admin"),
        (("frontend:kb:write",), "engineer"),
        (("frontend:kb:*",), "engineer"),
        (("backend:project:all",), "guest"),
        ((), "guest"),
    ],
)
def test_role_for(perms, expected):
    assert ors_auth.role_for(perms) == expected


def test_admin_and_kb_flags():
    assert ors_auth.is_ors_admin(("admin",)) is True
    assert ors_auth.is_ors_admin(("frontend:kb:write",)) is False
    assert ors_auth.has_kb_write(("frontend:kb:write",)) is True
    assert ors_auth.has_kb_write(("frontend:kb:read",)) is False
    assert ors_auth.has_kb_write(()) is False


def test_perm_matches_any_wildcard_and_empty():
    # 单独的 "*" 权限直达（与摇人吧 hasPermission 一致）
    assert ors_auth._perm_matches_any(("*",), "frontend:kb:write") is True
    assert ors_auth.has_kb_write(("*",)) is True
    assert ors_auth.is_ors_admin(("*",)) is True
    assert ors_auth._perm_matches_any((), "frontend:kb:write") is False


# ---------- verify_ors_token：对摇人吧响应的容错 ----------

class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def test_verify_ok(monkeypatch):
    captured = {}

    def _get(url, **kw):
        captured["url"] = url
        captured.update(kw)
        return _Resp(200, {"username": "zhangsan", "name": "张三", "permissions": ["frontend:kb:write"]})

    monkeypatch.setattr(ors_auth.httpx, "get", _get)
    u = ors_auth.verify_ors_token("tok")
    assert u == OrsUser("zhangsan", "张三", ("frontend:kb:write",))
    assert captured["url"].endswith("/api/auth/me")
    assert captured["headers"]["Authorization"] == "Bearer tok"
    assert captured["timeout"] == ors_auth._VERIFY_TIMEOUT


@pytest.mark.parametrize("status", [401, 403, 500, 502])
def test_verify_non_200_returns_none(monkeypatch, status):
    monkeypatch.setattr(ors_auth.httpx, "get", lambda url, **kw: _Resp(status, {"detail": "x"}))
    assert ors_auth.verify_ors_token("tok") is None


def test_verify_network_error_returns_none(monkeypatch):
    def _boom(url, **kw):
        raise OSError("connection refused")

    monkeypatch.setattr(ors_auth.httpx, "get", _boom)
    assert ors_auth.verify_ors_token("tok") is None


def test_verify_bad_json_returns_none(monkeypatch):
    monkeypatch.setattr(ors_auth.httpx, "get", lambda url, **kw: _Resp(200, None))
    assert ors_auth.verify_ors_token("tok") is None


def test_verify_non_dict_payload_returns_none(monkeypatch):
    monkeypatch.setattr(ors_auth.httpx, "get", lambda url, **kw: _Resp(200, ["not", "a", "dict"]))
    assert ors_auth.verify_ors_token("tok") is None


def test_verify_missing_username_returns_none(monkeypatch):
    monkeypatch.setattr(ors_auth.httpx, "get", lambda url, **kw: _Resp(200, {"username": "  ", "permissions": []}))
    assert ors_auth.verify_ors_token("tok") is None


def test_verify_permissions_not_list(monkeypatch):
    monkeypatch.setattr(
        ors_auth.httpx,
        "get",
        lambda url, **kw: _Resp(200, {"username": "u1", "permissions": "oops"}),
    )
    u = ors_auth.verify_ors_token("tok")
    assert u is not None and u.permissions == ()


def test_verify_empty_token_short_circuits(monkeypatch):
    def _boom(url, **kw):  # 空 token 不应发起请求
        raise AssertionError("should not call httpx")

    monkeypatch.setattr(ors_auth.httpx, "get", _boom)
    assert ors_auth.verify_ors_token("") is None
    assert ors_auth.verify_ors_token(None) is None


# ---------- principal_from_ors：Bearer 解析 ----------

def test_principal_from_ors_bad_bearer(monkeypatch):
    def _boom(url, **kw):
        raise AssertionError("should not call httpx")

    monkeypatch.setattr(ors_auth.httpx, "get", _boom)
    assert ors_auth.principal_from_ors(None, None) is None
    assert ors_auth.principal_from_ors(None, "Basic xyz") is None
    assert ors_auth.principal_from_ors(None, "Bearer ") is None
