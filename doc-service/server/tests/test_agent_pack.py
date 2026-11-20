from __future__ import annotations

import io
import json
import zipfile

from tests.conftest import SECRET_BODY, login

PACK = "/api/v1/products/usp/packs/secret_mod"
AGENT_BODY = "AGENT_PACK_IMPORT_BODY"


def _bearer(client, user="eng", password="eng-secret") -> dict[str, str]:
    login(client, user, password)
    issued = client.post("/api/v1/account/agent-token")
    assert issued.status_code == 200, issued.text
    token = issued.json()["token"]
    client.post("/api/v1/auth/logout")
    return {"Authorization": f"Bearer {token}"}


def _export(client, headers: dict[str, str] | None = None, module: str = "secret_mod") -> bytes:
    r = client.get(f"/api/v1/products/usp/packs/{module}", headers=headers or {})
    assert r.status_code == 200, r.text
    return r.content


def _rewrite(
    raw: bytes,
    *,
    doc: str | None = None,
    pack: dict | None = None,
    drop: set[str] | None = None,
) -> bytes:
    skip = drop or set()
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as src, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst:
        for name in src.namelist():
            if name in skip:
                continue
            data = src.read(name)
            if name == "PACK.json" and pack is not None:
                meta = json.loads(data.decode("utf-8"))
                meta.update(pack)
                data = json.dumps(meta, ensure_ascii=False).encode("utf-8")
            if doc is not None and (name.endswith("/doc.md") or name == "doc.md"):
                data = doc.encode("utf-8")
            dst.writestr(name, data)
    return buf.getvalue()


def test_agent_bearer_import_writes_body(client):
    headers = _bearer(client)
    raw = _export(client, headers)
    r = client.post(PACK, content=_rewrite(raw, doc=AGENT_BODY), headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    login(client, "eng", "eng-secret")
    page = client.get("/api/v1/products/usp/modules/secret_mod")
    assert page.status_code == 200
    assert page.json()["markdown"] == AGENT_BODY


def test_agent_bad_token_401(client):
    r = client.post(PACK, content=b"PK\x03\x04", headers={"Authorization": "Bearer not-a-token"})
    assert r.status_code == 401


def test_agent_import_not_zip(client):
    headers = _bearer(client)
    r = client.post(PACK, content=b"not-a-zip", headers=headers)
    assert r.status_code == 400
    assert r.json()["detail"] == "not_zip"


def test_agent_import_missing_pack_json(client):
    headers = _bearer(client)
    raw = _export(client, headers)
    r = client.post(PACK, content=_rewrite(raw, drop={"PACK.json"}), headers=headers)
    assert r.status_code == 400
    assert r.json()["detail"] == "missing_pack"


def test_agent_import_product_mismatch(client):
    headers = _bearer(client)
    raw = _export(client, headers)
    r = client.post(PACK, content=_rewrite(raw, pack={"product": "ors"}), headers=headers)
    assert r.status_code == 400
    assert r.json()["detail"] == "product_mismatch"


def test_agent_put_document_must_use_pack(client):
    headers = _bearer(client)
    r = client.put(
        "/api/v1/products/usp/modules/secret_mod/document",
        json={"markdown": "nope"},
        headers=headers,
    )
    assert r.status_code == 409
    raw = _export(client, headers)
    ok = client.post(PACK, content=_rewrite(raw, doc=AGENT_BODY), headers=headers)
    assert ok.status_code == 200, ok.text


def test_agent_import_module_mismatch(client):
    headers = _bearer(client)
    raw = _export(client, headers)
    r = client.post(PACK, content=_rewrite(raw, pack={"module_id": "biz"}), headers=headers)
    assert r.status_code == 400
    assert r.json()["detail"] == "module_mismatch"


def test_agent_import_missing_doc(client):
    headers = _bearer(client)
    raw = _export(client, headers)
    r = client.post(PACK, content=_rewrite(raw, drop={"content/secret_mod/doc.md"}), headers=headers)
    assert r.status_code == 400
    assert r.json()["detail"] == "missing_doc"


def test_agent_import_stale_edit_token(client):
    headers = _bearer(client)
    stale = _export(client, headers)
    login(client, "eng", "eng-secret")
    put = client.put(
        "/api/v1/products/usp/modules/secret_mod/document",
        json={"markdown": "page-changed-after-export"},
    )
    assert put.status_code == 200, put.text
    client.post("/api/v1/auth/logout")
    r = client.post(PACK, content=_rewrite(stale, doc=AGENT_BODY), headers=headers)
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert (detail == "edit_conflict") or (isinstance(detail, dict) and detail.get("error") == "edit_conflict")
    login(client, "eng", "eng-secret")
    assert client.get("/api/v1/products/usp/modules/secret_mod").json()["markdown"] == "page-changed-after-export"


def test_agent_import_forbidden_without_module_write(client):
    login(client, "admin", "admin-secret")
    biz = _export(client, module="biz")
    client.post("/api/v1/auth/logout")
    headers = _bearer(client)
    r = client.post(
        "/api/v1/products/usp/packs/biz",
        content=_rewrite(biz, doc="should-not-land"),
        headers=headers,
    )
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert (detail == "write_revoked") or (isinstance(detail, dict) and detail.get("error") == "write_revoked")
    login(client, "admin", "admin-secret")
    assert client.get("/api/v1/products/usp/modules/biz").json()["markdown"] != "should-not-land"


def test_agent_import_revision_and_audit(client):
    headers = _bearer(client)
    raw = _export(client, headers)
    same = client.post(PACK, content=raw, headers=headers)
    assert same.status_code == 200, same.text
    login(client, "eng", "eng-secret")
    empty = client.get("/api/v1/products/usp/modules/secret_mod/revisions")
    assert empty.status_code == 200
    assert empty.json()["items"] == []
    client.post("/api/v1/auth/logout")
    changed = client.post(PACK, content=_rewrite(raw, doc=AGENT_BODY), headers=headers)
    assert changed.status_code == 200, changed.text
    login(client, "eng", "eng-secret")
    revs = client.get("/api/v1/products/usp/modules/secret_mod/revisions").json()["items"]
    assert revs
    assert revs[0]["author"]
    page = client.get("/api/v1/products/usp/modules/secret_mod").json()
    assert page["revision_count"] == len(revs)
    login(client, "admin", "admin-secret")
    audit = client.get("/api/v1/admin/audit").json()
    hits = [i for i in audit["items"] if i["action"] == "pack.import"]
    assert hits
    assert any(i["actor"] == "eng" for i in hits)
    assert all("json" not in (i["title"] or "").lower() for i in hits)
