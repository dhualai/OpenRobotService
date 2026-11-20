"""USP 测试管家 SSO. Admin license stays on this server; never sent to the browser."""

from __future__ import annotations

import json
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request

from app.settings import Settings, get_settings

_USER_RE = re.compile(r"^[A-Za-z0-9._-]{2,32}$")
_TIMEOUT = 8


class ButlerError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def configured(s: Settings | None = None) -> bool:
    s = s or get_settings()
    return bool(s.usp_test_base_url and s.usp_test_admin_license)


def butler_username(raw: str) -> str:
    u = (raw or "").strip()
    if _USER_RE.fullmatch(u):
        return u
    raise ButlerError(400, "bad_username")


def _base(s: Settings) -> str:
    return s.usp_test_base_url.rstrip("/")


def _call(s: Settings, method: str, path: str, *, query: dict | None = None, body: dict | None = None) -> dict:
    q = urllib.parse.urlencode(query or {}, safe="")
    url = f"{_base(s)}{path}"
    if q:
        url = f"{url}?{q}"
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        detail = "butler_error"
        try:
            payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
            if isinstance(payload, dict):
                d = payload.get("detail")
                if isinstance(d, str) and d.strip():
                    detail = d.strip()
                elif isinstance(d, dict) and isinstance(d.get("error"), str):
                    detail = d["error"]
        except Exception:
            pass
        if e.code == 401:
            raise ButlerError(502, "butler_auth") from None
        raise ButlerError(502 if e.code >= 500 else e.code, detail) from None
    except urllib.error.URLError:
        raise ButlerError(502, "butler_unreachable") from None


def _list_names(s: Settings) -> set[str]:
    out = _call(s, "GET", "/api/external/users", query={"license": s.usp_test_admin_license})
    rows = out.get("users") if isinstance(out, dict) else None
    names: set[str] = set()
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and row.get("username"):
                names.add(str(row["username"]))
    return names


def _ensure_user(s: Settings, username: str) -> None:
    if username in _list_names(s):
        return
    try:
        _call(
            s,
            "POST",
            "/api/external/users",
            body={
                "license": s.usp_test_admin_license,
                "username": username,
                "password": secrets.token_urlsafe(12),
                "scope": "full",
            },
        )
    except ButlerError as e:
        if e.status == 400:
            return
        raise


def issue_sso(username: str, s: Settings | None = None) -> dict:
    s = s or get_settings()
    if not configured(s):
        raise ButlerError(503, "unconfigured")
    name = butler_username(username)
    _ensure_user(s, name)
    out = _call(
        s,
        "POST",
        "/api/external/sso",
        body={"license": s.usp_test_admin_license, "username": name},
    )
    token = (out.get("sso_token") or "").strip() if isinstance(out, dict) else ""
    if not token:
        raise ButlerError(502, "butler_error")
    q = urllib.parse.urlencode({"sso_token": token})
    return {"url": f"{_base(s)}/?{q}", "username": name}
