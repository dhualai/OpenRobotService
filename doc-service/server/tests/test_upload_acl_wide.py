from __future__ import annotations

import io
import json
import zipfile

from app.infra.models import Board, Grant, Module, User, UserProduct
from app.services.auth import hash_password
from tests.conftest import SECRET_TITLE, login
from tests.test_agent_pack import _rewrite
from tests.test_upload_acl import _add_eng2, _add_group, _err, _hint, _nest_host, _set_perms


def _token(raw: bytes) -> str:
    return json.loads(zipfile.ZipFile(io.BytesIO(raw)).read("PACK.json"))["edit_token"]


def _make_zip(module_id: str, token: str, body: str, *, pack: dict | None = None, doc_name: str | None = None):
    meta = {"product": "usp", "module_id": module_id, "edit_token": token}
    if pack:
        meta.update(pack)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("PACK.json", json.dumps(meta, ensure_ascii=False))
        from app.services.packs import AGENTS_MD

        z.writestr("AGENTS.md", AGENTS_MD)
        z.writestr(doc_name or f"content/{module_id}/doc.md", body)
    return buf.getvalue()


def _clear_grants(db, username="eng"):
    db.query(Grant).filter_by(username=username).delete()
    db.commit()


def _add_peer(db, username, *, module="secret_mod"):
    if db.get(User, username):
        return
    db.add(User(username=username, password_hash=hash_password(f"{username}-secret"), role="engineer"))
    db.add(UserProduct(username=username, product="usp"))
    db.add(Grant(username=username, product="usp", kind="module", resource_id=module, perm="write"))
    db.commit()


def _nest_grand(db, crypto):
    _nest_host(db, crypto)
    _nest_host(db, crypto, host_id="secret_kid", child_id="secret_grand", rel="boards/secret_kid.json")


def _add_ors_feat(db, crypto, *, grant_eng=False):
    if not db.query(UserProduct).filter_by(username="eng", product="ors").first():
        db.add(UserProduct(username="eng", product="ors"))
    if not db.query(Board).filter_by(product="ors", rel_path="boards/ors.json").first():
        db.add(
            Board(
                product="ors",
                rel_path="boards/ors.json",
                doc={"nodes": [{"id": "ors_feat", "x": 0, "y": 0}], "groups": [], "edges": []},
            )
        )
    if not db.query(Module).filter_by(product="ors", id="ors_feat").first():
        db.add(
            Module(
                id="ors_feat",
                product="ors",
                board_rel="boards/ors.json",
                kind="feature",
                encrypted=False,
                title_blob=crypto.encrypt("ORS功能", aad="module.title:ors:ors_feat"),
                current_version="v1.0",
            )
        )
    if grant_eng and not db.query(Grant).filter_by(username="eng", product="ors", resource_id="ors_feat").first():
        db.add(Grant(username="eng", product="ors", kind="module", resource_id="ors_feat", perm="write"))
    db.commit()


def _rev_count(client, mid="secret_mod") -> int:
    return client.get(f"/api/v1/products/usp/modules/{mid}").json().get("revision_count") or 0


# --- 鉴权 / 角色 / 产品隔离 ---


def test_u51_unauthenticated_pack_401(client):
    assert client.get("/api/v1/products/usp/packs/secret_mod").status_code == 401


def test_u52_unauthenticated_writable_401(client):
    assert client.get("/api/v1/products/usp/writable-modules").status_code == 401


def test_u53_unauthenticated_edit_token_401(client):
    assert client.get("/api/v1/products/usp/packs/secret_mod/edit-token").status_code == 401


def test_u54_unauthenticated_graph_401(client):
    assert client.get("/api/v1/products/usp/graph").status_code == 401


def test_u55_bad_bearer_import_401(client):
    r = client.post(
        "/api/v1/products/usp/packs/secret_mod",
        content=b"PK\x03\x04xxxx",
        headers={"Authorization": "Bearer totally-invalid"},
    )
    assert r.status_code == 401


def test_u56_reissue_agent_token_invalidates_old(client):
    login(client, "eng", "eng-secret")
    old = client.post("/api/v1/account/agent-token").json()["token"]
    new = client.post("/api/v1/account/agent-token").json()["token"]
    client.post("/api/v1/auth/logout")
    stale = client.get(
        "/api/v1/products/usp/packs/secret_mod",
        headers={"Authorization": f"Bearer {old}"},
    )
    fresh = client.get(
        "/api/v1/products/usp/packs/secret_mod",
        headers={"Authorization": f"Bearer {new}"},
    )
    assert stale.status_code == 401
    assert fresh.status_code == 200


def test_u57_live_session_sees_grant_delete(client, db):
    login(client, "eng", "eng-secret")
    assert client.get("/api/v1/products/usp/packs/secret_mod").status_code == 200
    _clear_grants(db)
    r = client.get("/api/v1/products/usp/packs/secret_mod")
    assert r.status_code == 403
    assert _err(r) == "write_revoked"


def test_u58_guest_export_write_revoked(client):
    login(client, "guest", "guest-secret")
    r = client.get("/api/v1/products/usp/packs/biz")
    assert r.status_code == 403
    assert _err(r) == "write_revoked"


def test_u59_pm_with_module_grant_cannot_export(client):
    _set_perms(client, "pm1", {"secret_mod": "write", "biz": "write"}, role="pm")
    login(client, "pm1", "pm-secret")
    r = client.get("/api/v1/products/usp/packs/biz")
    assert r.status_code == 403
    assert _err(r) == "write_revoked"


def test_u60_pjm_cannot_export_with_grant(client):
    _set_perms(client, "pjm1", {"biz": "write"}, role="pjm", password="pjm1-secret")
    login(client, "pjm1", "pjm1-secret")
    r = client.get("/api/v1/products/usp/packs/biz")
    assert r.status_code == 403
    assert _err(r) == "write_revoked"


def test_u61_tester_with_write_can_import(client):
    _set_perms(client, "qa1", {"secret_mod": "write"}, role="tester", password="qa1-secret1")
    login(client, "qa1", "qa1-secret1")
    raw = client.get("/api/v1/products/usp/packs/secret_mod").content
    r = client.post("/api/v1/products/usp/packs/secret_mod", content=_rewrite(raw, doc="qa-wrote"))
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/products/usp/modules/secret_mod").json()["markdown"] == "qa-wrote"


def test_u62_tester_product_all_root_can_edit(client):
    _set_perms(client, "qa1", {"usp:*": "write"}, role="tester", password="qa1-secret1")
    login(client, "qa1", "qa1-secret1")
    assert client.get("/api/v1/products/usp/graph").json()["can_edit"] is True


def test_u63_demote_to_guest_loses_edit(client):
    _set_perms(client, "eng", {"secret_mod": "write"}, role="guest")
    login(client, "eng", "eng-secret")
    r = client.get("/api/v1/products/usp/packs/secret_mod")
    assert r.status_code == 403
    assert _err(r) == "write_revoked"


def test_u64_engineer_without_product_forbidden(client):
    _set_perms(client, "eng", {"secret_mod": "write"}, products=[])
    login(client, "eng", "eng-secret")
    assert client.get("/api/v1/products/usp/graph").status_code == 403
    assert client.get("/api/v1/products/usp/packs/secret_mod").status_code == 403


def test_u65_supervisor_without_product_forbidden(client, db):
    db.add(User(username="boss0", password_hash=hash_password("boss0-secret"), role="supervisor"))
    db.commit()
    login(client, "boss0", "boss0-secret")
    assert client.get("/api/v1/products/usp/graph").status_code == 403


def test_u66_admin_exports_without_explicit_grant(client):
    login(client, "admin", "admin-secret")
    r = client.get("/api/v1/products/usp/packs/biz")
    assert r.status_code == 200
    assert _token(r.content)


def test_u67_unknown_module_pack_404(client):
    login(client, "eng", "eng-secret")
    r = client.get("/api/v1/products/usp/packs/no_such_mod")
    assert r.status_code == 404


def test_u68_unknown_module_edit_token_404(client):
    login(client, "eng", "eng-secret")
    r = client.get("/api/v1/products/usp/packs/no_such_mod/edit-token")
    assert r.status_code == 404


def test_u69_bad_product_400(client):
    login(client, "eng", "eng-secret")
    assert client.get("/api/v1/products/xyz/packs/secret_mod").status_code == 400


# --- 包格式 / 冲突交叉 / 修订 ---


def test_u70_base_edit_token_alias(client):
    login(client, "eng", "eng-secret")
    raw = client.get("/api/v1/products/usp/packs/secret_mod").content
    token = _token(raw)
    r = client.post(
        "/api/v1/products/usp/packs/secret_mod",
        content=_make_zip("secret_mod", "", "via-base", pack={"edit_token": "", "base_edit_token": token}),
    )
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/products/usp/modules/secret_mod").json()["markdown"] == "via-base"


def test_u71_doc_md_at_zip_root(client):
    login(client, "eng", "eng-secret")
    raw = client.get("/api/v1/products/usp/packs/secret_mod").content
    r = client.post(
        "/api/v1/products/usp/packs/secret_mod",
        content=_make_zip("secret_mod", _token(raw), "root-doc", doc_name="doc.md"),
    )
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/products/usp/modules/secret_mod").json()["markdown"] == "root-doc"


def test_u72_empty_markdown_import(client):
    login(client, "eng", "eng-secret")
    raw = client.get("/api/v1/products/usp/packs/secret_mod").content
    r = client.post("/api/v1/products/usp/packs/secret_mod", content=_rewrite(raw, doc=""))
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/products/usp/modules/secret_mod").json()["markdown"] == ""


def test_u73_unicode_body_roundtrip(client):
    body = "中文标题 😀\n\n公式 $x^2$"
    login(client, "eng", "eng-secret")
    raw = client.get("/api/v1/products/usp/packs/secret_mod").content
    r = client.post("/api/v1/products/usp/packs/secret_mod", content=_rewrite(raw, doc=body))
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/products/usp/modules/secret_mod").json()["markdown"] == body


def test_u74_stale_pack_after_revoke_is_write_revoked(client, db):
    login(client, "eng", "eng-secret")
    stale = client.get("/api/v1/products/usp/packs/secret_mod").content
    client.put("/api/v1/products/usp/modules/secret_mod/document", json={"markdown": "changed-then-revoked"})
    _clear_grants(db)
    r = client.post("/api/v1/products/usp/packs/secret_mod", content=_rewrite(stale, doc="late"))
    assert r.status_code == 403
    assert _err(r) == "write_revoked"
    login(client, "admin", "admin-secret")
    assert client.get("/api/v1/products/usp/modules/secret_mod").json()["markdown"] == "changed-then-revoked"


def test_u75_conflict_does_not_create_revision(client, db):
    _add_eng2(db)
    login(client, "eng", "eng-secret")
    stale = client.get("/api/v1/products/usp/packs/secret_mod").content
    before = _rev_count(client)
    login(client, "eng2", "eng2-secret")
    client.put("/api/v1/products/usp/modules/secret_mod/document", json={"markdown": "eng2-page"})
    login(client, "eng", "eng-secret")
    r = client.post("/api/v1/products/usp/packs/secret_mod", content=_rewrite(stale, doc="eng-late"))
    assert r.status_code == 409
    assert _rev_count(client) == before


def test_u76_changing_import_creates_revision(client):
    login(client, "eng", "eng-secret")
    raw = client.get("/api/v1/products/usp/packs/secret_mod").content
    before = _rev_count(client)
    r = client.post("/api/v1/products/usp/packs/secret_mod", content=_rewrite(raw, doc="rev-bump"))
    assert r.status_code == 200
    assert _rev_count(client) == before + 1


def test_u77_idempotent_import_no_revision(client):
    login(client, "eng", "eng-secret")
    raw = client.get("/api/v1/products/usp/packs/secret_mod").content
    before = _rev_count(client)
    r = client.post("/api/v1/products/usp/packs/secret_mod", content=raw)
    assert r.status_code == 200
    assert _rev_count(client) == before


def test_u78_reexport_after_conflict_then_import_ok(client, db):
    _add_eng2(db)
    login(client, "eng", "eng-secret")
    stale = client.get("/api/v1/products/usp/packs/secret_mod").content
    login(client, "eng2", "eng2-secret")
    client.put("/api/v1/products/usp/modules/secret_mod/document", json={"markdown": "taken"})
    login(client, "eng", "eng-secret")
    assert client.post("/api/v1/products/usp/packs/secret_mod", content=_rewrite(stale, doc="late")).status_code == 409
    fresh = client.get("/api/v1/products/usp/packs/secret_mod").content
    r = client.post("/api/v1/products/usp/packs/secret_mod", content=_rewrite(fresh, doc="recovered"))
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/products/usp/modules/secret_mod").json()["markdown"] == "recovered"


def test_u79_edit_token_after_other_user_requires_export(client, db):
    _add_eng2(db)
    login(client, "eng", "eng-secret")
    old = _token(client.get("/api/v1/products/usp/packs/secret_mod").content)
    login(client, "eng2", "eng2-secret")
    client.put("/api/v1/products/usp/modules/secret_mod/document", json={"markdown": "now-other"})
    login(client, "eng", "eng-secret")
    r = client.get(f"/api/v1/products/usp/packs/secret_mod/edit-token?since={old}")
    assert r.status_code == 200
    body = r.json()
    assert body["unchanged"] is False
    assert body["export_required"] is True
    assert body["edit_token"] != old


def test_u80_three_users_last_reexport_wins(client, db):
    _add_eng2(db)
    _add_peer(db, "eng3")
    login(client, "eng", "eng-secret")
    a = client.get("/api/v1/products/usp/packs/secret_mod").content
    login(client, "eng2", "eng2-secret")
    b = client.get("/api/v1/products/usp/packs/secret_mod").content
    r2 = client.post("/api/v1/products/usp/packs/secret_mod", content=_rewrite(b, doc="from-2"))
    assert r2.status_code == 200
    login(client, "eng3", "eng3-secret")
    assert client.post("/api/v1/products/usp/packs/secret_mod", content=_rewrite(a, doc="from-3")).status_code == 409
    c = client.get("/api/v1/products/usp/packs/secret_mod").content
    r3 = client.post("/api/v1/products/usp/packs/secret_mod", content=_rewrite(c, doc="from-3-fresh"))
    assert r3.status_code == 200
    assert client.get("/api/v1/products/usp/modules/secret_mod").json()["markdown"] == "from-3-fresh"


def test_u81_same_user_page_then_agent_conflicts(client):
    login(client, "eng", "eng-secret")
    issued = client.post("/api/v1/account/agent-token")
    token = issued.json()["token"]
    stale = client.get("/api/v1/products/usp/packs/secret_mod").content
    client.put("/api/v1/products/usp/modules/secret_mod/document", json={"markdown": "from-page"})
    r = client.post(
        "/api/v1/products/usp/packs/secret_mod",
        content=_rewrite(stale, doc="from-agent"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 409
    assert _err(r) == "edit_conflict"
    assert client.get("/api/v1/products/usp/modules/secret_mod").json()["markdown"] == "from-page"


def test_u82_matching_token_overwrites_body(client):
    login(client, "eng", "eng-secret")
    raw = client.get("/api/v1/products/usp/packs/secret_mod").content
    r = client.post("/api/v1/products/usp/packs/secret_mod", content=_rewrite(raw, doc="overwrite-ok"))
    assert r.status_code == 200
    assert client.get("/api/v1/products/usp/modules/secret_mod").json()["markdown"] == "overwrite-ok"


def test_u83_invalid_pack_json_400(client):
    login(client, "eng", "eng-secret")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("PACK.json", "{not-json")
        z.writestr("content/secret_mod/doc.md", "x")
    r = client.post("/api/v1/products/usp/packs/secret_mod", content=buf.getvalue())
    assert r.status_code == 400
    assert _err(r) == "missing_pack"


def test_u84_empty_zip_missing_pack(client):
    login(client, "eng", "eng-secret")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("README.txt", "no pack")
    r = client.post("/api/v1/products/usp/packs/secret_mod", content=buf.getvalue())
    assert r.status_code == 400
    assert _err(r) == "missing_pack"


def test_u85_module_page_edit_token_matches_pack(client):
    login(client, "eng", "eng-secret")
    page = client.get("/api/v1/products/usp/modules/secret_mod").json()
    raw = client.get("/api/v1/products/usp/packs/secret_mod").content
    assert page["edit_token"] == _token(raw)
    from app.services.packs import compose_edit_token

    assert page["edit_token"] == compose_edit_token(page["content_hash"])


def test_u85b_rules_rev_invalidates_bare_content_hash(client):
    login(client, "eng", "eng-secret")
    page = client.get("/api/v1/products/usp/modules/secret_mod").json()
    r = client.get(f"/api/v1/products/usp/packs/secret_mod/edit-token?since={page['content_hash']}")
    assert r.status_code == 200
    assert r.json()["unchanged"] is False
    assert r.json()["export_required"] is True


def test_u85c_old_rules_rev_rejected_even_if_body_unchanged(client):
    login(client, "eng", "eng-secret")
    from app.services.packs import AGENTS_REV

    raw = client.get("/api/v1/products/usp/packs/secret_mod").content
    token = _token(raw)
    assert token.endswith("." + AGENTS_REV) or token == AGENTS_REV
    stale = f"{token.rsplit('.', 1)[0]}.0" if "." in token else "0"
    before = client.get("/api/v1/products/usp/modules/secret_mod").json()["markdown"]
    r = client.post(
        "/api/v1/products/usp/packs/secret_mod",
        content=_rewrite(raw, pack={"edit_token": stale}),
    )
    assert r.status_code == 409
    assert _err(r) == "edit_conflict"
    assert "AGENTS.md 已经更新" in (_hint(r) or "")
    assert "行文" in (_hint(r) or "")
    assert client.get("/api/v1/products/usp/modules/secret_mod").json()["markdown"] == before


def test_u85d_import_requires_current_agents_ack(client):
    login(client, "eng", "eng-secret")
    raw = client.get("/api/v1/products/usp/packs/secret_mod").content
    missing = client.post(
        "/api/v1/products/usp/packs/secret_mod",
        content=_rewrite(raw, drop={"AGENTS.md"}),
    )
    assert missing.status_code == 400
    assert _err(missing) == "missing_agents"
    md = zipfile.ZipFile(io.BytesIO(raw)).read("AGENTS.md").decode("utf-8")
    stale = md.rsplit("\n", 1)[0] + "\n校验码 0-deadbeef\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as src, zipfile.ZipFile(buf, "w") as dst:
        for name in src.namelist():
            data = src.read(name) if name != "AGENTS.md" else stale.encode("utf-8")
            dst.writestr(name, data)
    r = client.post("/api/v1/products/usp/packs/secret_mod", content=buf.getvalue())
    assert r.status_code == 409
    assert _err(r) == "agents_stale"
    hint = _hint(r) or ""
    assert "AGENTS.md 已经更新" in hint
    assert "全文阅读" in hint
    assert "行文" in hint
    assert r.json()["detail"].get("message") == hint


def test_u86_edit_token_blank_since_treated_as_absent(client):
    login(client, "eng", "eng-secret")
    r = client.get("/api/v1/products/usp/packs/secret_mod/edit-token?since=%20%20")
    assert r.status_code == 200
    assert r.json()["unchanged"] is None
    assert r.json()["export_required"] is None


def test_u87_export_agents_mentions_writable(client):
    login(client, "eng", "eng-secret")
    raw = client.get("/api/v1/products/usp/packs/secret_mod").content
    md = zipfile.ZipFile(io.BytesIO(raw)).read("AGENTS.md").decode("utf-8")
    assert "writable-modules" in md
    assert "write_revoked" in md
    assert "句式过碎" in md
    assert "语句不连贯" in md
    assert "拍板" in md
    assert "定死" in md
    assert "规则版本 9" in md
    assert "行文（强制" in md
    assert "行文优先" in md
    assert "doc-tip" in md
    assert "💡" in md
    assert "校验码" in md
    assert md.strip().splitlines()[-1].startswith("校验码 ")
    assert "SVG" in md
    assert "相互支撑" in md


def test_u88_auth_pack_modules_omit_hubs(client):
    login(client, "admin", "admin-secret")
    raw = client.get("/api/v1/account/auth-pack").content
    mods = json.loads(zipfile.ZipFile(io.BytesIO(raw)).read("modules.json"))
    ids = {m["id"] for m in mods["modules_by_product"]["usp"]}
    assert "usp_algo" not in ids
    assert "usp_dev" not in ids
    assert "biz" in ids
    assert "secret_mod" in ids


def test_u89_writable_omits_hub_and_release(client):
    login(client, "admin", "admin-secret")
    ids = {i["id"] for i in client.get("/api/v1/products/usp/writable-modules").json()["items"]}
    assert "usp_algo" not in ids
    assert "usp_dev" not in ids
    assert "usp_v100" not in ids


def test_u90_writable_title_decrypted(client):
    login(client, "eng", "eng-secret")
    items = client.get("/api/v1/products/usp/writable-modules").json()["items"]
    hit = next(i for i in items if i["id"] == "secret_mod")
    assert hit["title"] == SECRET_TITLE


def test_u91_guest_writable_empty(client):
    login(client, "guest", "guest-secret")
    assert client.get("/api/v1/products/usp/writable-modules").json()["items"] == []


def test_u92_pm_writable_empty(client):
    login(client, "pm1", "pm-secret")
    assert client.get("/api/v1/products/usp/writable-modules").json()["items"] == []


def test_u93_supervisor_writable_includes_encrypted(client, db):
    db.add(User(username="boss", password_hash=hash_password("boss-secret"), role="supervisor"))
    db.add(UserProduct(username="boss", product="usp"))
    db.commit()
    login(client, "boss", "boss-secret")
    ids = {i["id"] for i in client.get("/api/v1/products/usp/writable-modules").json()["items"]}
    assert "secret_mod" in ids
    assert "biz" in ids


def test_u94_admin_writable_includes_features(client):
    login(client, "admin", "admin-secret")
    ids = {i["id"] for i in client.get("/api/v1/products/usp/writable-modules").json()["items"]}
    assert "biz" in ids
    assert "secret_mod" in ids


def test_u95_read_perm_cannot_export(client):
    _set_perms(client, "eng", {"secret_mod": "read"})
    login(client, "eng", "eng-secret")
    r = client.get("/api/v1/products/usp/packs/secret_mod")
    assert r.status_code == 403
    assert _err(r) == "write_revoked"


def test_u96_read_perm_encrypted_still_hidden(client):
    _set_perms(client, "eng", {"secret_mod": "read"})
    login(client, "eng", "eng-secret")
    assert client.get("/api/v1/products/usp/modules/secret_mod").status_code == 403


def test_u97_usp_all_does_not_open_ors(client, db, crypto):
    _add_ors_feat(db, crypto)
    _set_perms(client, "eng", {"usp:*": "write"}, products=["usp"])
    login(client, "eng", "eng-secret")
    assert client.get("/api/v1/products/ors/packs/ors_feat").status_code == 403


def test_u98_ors_public_not_editable_without_grant(client, db, crypto):
    _add_ors_feat(db, crypto)
    login(client, "eng", "eng-secret")
    page = client.get("/api/v1/products/ors/modules/ors_feat")
    assert page.status_code == 200
    assert page.json()["can_edit"] is False


def test_u99_ors_write_allows_export(client, db, crypto):
    _add_ors_feat(db, crypto, grant_eng=True)
    login(client, "eng", "eng-secret")
    r = client.get("/api/v1/products/ors/packs/ors_feat")
    assert r.status_code == 200


def test_u100_star_key_grants_product_all(client):
    _set_perms(client, "eng", {"*": "write"})
    login(client, "eng", "eng-secret")
    assert client.get("/api/v1/products/usp/graph").json()["can_edit"] is True
    assert client.get("/api/v1/products/usp/modules/biz").json()["can_edit"] is True


# --- 级联深度 / 拓扑展开交叉 ---


def test_u101_grandchild_can_edit_via_host(client, db, crypto):
    _nest_grand(db, crypto)
    login(client, "eng", "eng-secret")
    page = client.get("/api/v1/products/usp/modules/secret_grand")
    assert page.status_code == 200
    assert page.json()["can_edit"] is True


def test_u102_grandchild_in_writable(client, db, crypto):
    _nest_grand(db, crypto)
    login(client, "eng", "eng-secret")
    ids = {i["id"] for i in client.get("/api/v1/products/usp/writable-modules").json()["items"]}
    assert "secret_grand" in ids
    assert "secret_kid" in ids


def test_u103_revoke_host_loses_grandchild(client, db, crypto):
    _nest_grand(db, crypto)
    _set_perms(client, "eng", {})
    login(client, "eng", "eng-secret")
    assert client.get("/api/v1/products/usp/packs/secret_grand").status_code == 403


def test_u104_direct_child_grant_parent_not_editable(client, db, crypto):
    _nest_host(db, crypto)
    _set_perms(client, "eng", {"secret_kid": "write"})
    login(client, "eng", "eng-secret")
    assert client.get("/api/v1/products/usp/modules/secret_kid").json()["can_edit"] is True
    assert client.get("/api/v1/products/usp/packs/secret_mod").status_code == 403


def test_u105_child_grant_cannot_save_parent_nested_board(client, db, crypto):
    _nest_host(db, crypto)
    _set_perms(client, "eng", {"secret_kid": "write"})
    login(client, "eng", "eng-secret")
    r = client.put(
        "/api/v1/products/usp/boards/boards/secret_mod.json",
        json={"doc": {"nodes": [{"id": "secret_kid", "x": 0, "y": 0, "title": "改"}], "groups": [], "edges": []}},
    )
    assert r.status_code == 403


def test_u106_child_grant_can_save_own_nested_board(client, db, crypto):
    _nest_host(db, crypto)
    _set_perms(client, "eng", {"secret_kid": "write"})
    login(client, "eng", "eng-secret")
    r = client.put(
        "/api/v1/products/usp/boards/boards/secret_kid.json",
        json={"doc": {"nodes": [{"id": "own_n", "x": 0, "y": 0, "title": "自己的子图"}], "groups": [], "edges": []}},
    )
    assert r.status_code == 200, r.text


def test_u107_cascade_page_put_child_document(client, db, crypto):
    _nest_host(db, crypto)
    login(client, "eng", "eng-secret")
    r = client.put("/api/v1/products/usp/modules/secret_kid/document", json={"markdown": "cascade-put"})
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/products/usp/modules/secret_kid").json()["markdown"] == "cascade-put"


def test_u108_nested_graph_encrypted_host_after_revoke_403(client, db, crypto):
    _nest_host(db, crypto)
    _set_perms(client, "eng", {})
    login(client, "eng", "eng-secret")
    assert client.get("/api/v1/products/usp/modules/secret_mod/nested-graph").status_code == 403


def test_u109_empty_nested_can_edit_for_host_writer(client):
    login(client, "eng", "eng-secret")
    g = client.get("/api/v1/products/usp/modules/secret_mod/nested-graph").json()
    assert g.get("empty") is True
    assert g["can_edit"] is True


def test_u110_empty_nested_cannot_edit_without_write(client):
    login(client, "eng", "eng-secret")
    g = client.get("/api/v1/products/usp/modules/biz/nested-graph").json()
    assert g["can_edit"] is False


def test_u111_nested_editor_board_is_nested_rel(client, db, crypto):
    _nest_host(db, crypto)
    login(client, "eng", "eng-secret")
    g = client.get("/api/v1/products/usp/modules/secret_mod/nested-graph").json()
    assert g["can_edit"] is True
    assert g.get("editor_board") == "boards/secret_mod.json"


def test_u112_grandchild_create_inherits_encrypted(client, db, crypto):
    _nest_host(db, crypto)
    kid = db.query(Module).filter_by(product="usp", id="secret_kid").one()
    kid.encrypted = True
    kid.nested_board = "boards/secret_kid.json"
    db.commit()
    login(client, "eng", "eng-secret")
    r = client.put(
        "/api/v1/products/usp/boards/boards/secret_kid.json",
        json={"doc": {"nodes": [{"id": "born_g", "x": 0, "y": 0, "title": "孙"}], "groups": [], "edges": []}},
    )
    assert r.status_code == 200, r.text
    db.expire_all()
    born = db.query(Module).filter_by(product="usp", id="born_g").one()
    assert born.parent_id == "secret_kid"
    assert born.encrypted is True


def test_u113_two_children_both_cascade(client, db, crypto):
    _nest_host(db, crypto)
    host = db.query(Module).filter_by(product="usp", id="secret_mod").one()
    rel = host.nested_board
    b = db.query(Board).filter_by(product="usp", rel_path=rel).one()
    doc = dict(b.doc)
    nodes = list(doc.get("nodes") or [])
    nodes.append({"id": "secret_sib", "x": 40, "y": 0})
    doc["nodes"] = nodes
    b.doc = doc
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(b, "doc")
    db.add(
        Module(
            id="secret_sib",
            product="usp",
            board_rel=rel,
            kind="feature",
            parent_id="secret_mod",
            title_blob=crypto.encrypt("兄弟", aad="module.title:usp:secret_sib"),
            current_version="v1.0",
        )
    )
    db.commit()
    login(client, "eng", "eng-secret")
    ids = {i["id"] for i in client.get("/api/v1/products/usp/writable-modules").json()["items"]}
    assert "secret_kid" in ids
    assert "secret_sib" in ids


def test_u114_group_not_in_writable(client, db, crypto):
    _add_group(db, crypto, "g_hide", ["biz"])
    _set_perms(client, "eng", {"g_hide": "write"})
    login(client, "eng", "eng-secret")
    ids = {i["id"] for i in client.get("/api/v1/products/usp/writable-modules").json()["items"]}
    assert "g_hide" not in ids
    assert "biz" in ids


def test_u115_group_write_does_not_unlock_root_expand(client, db, crypto):
    _add_group(db, crypto, "g_root", ["biz"])
    _set_perms(client, "eng", {"g_root": "write"})
    login(client, "eng", "eng-secret")
    assert client.get("/api/v1/products/usp/graph").json()["can_edit"] is False


def test_u116_algo_domain_can_edit_root(client):
    _set_perms(client, "eng", {"usp:algo:*": "write"})
    login(client, "eng", "eng-secret")
    assert client.get("/api/v1/products/usp/graph").json()["can_edit"] is True


def test_u117_algo_domain_can_save_root_board(client):
    _set_perms(client, "eng", {"usp:algo:*": "write"})
    login(client, "eng", "eng-secret")
    g0 = client.get("/api/v1/products/usp/graph").json()
    r = client.put(
        "/api/v1/products/usp/boards/board.json",
        json={"doc": {"node_w": 158, "node_h": 46, "nodes": g0["nodes"], "groups": g0["groups"], "edges": g0["edges"]}},
    )
    assert r.status_code == 200, r.text


def test_u118_backend_cannot_save_frontend_board(client):
    _set_perms(client, "eng", {"usp:backend:*": "write"})
    login(client, "eng", "eng-secret")
    r = client.put(
        "/api/v1/products/usp/boards/boards/usp_frontend.json",
        json={"doc": {"nodes": [{"id": "fe_x", "x": 0, "y": 0, "title": "前端"}], "groups": [], "edges": []}},
    )
    assert r.status_code == 403


def test_u119_frontend_domain_can_save_frontend_board(client):
    _set_perms(client, "eng", {"usp:frontend:*": "write"})
    login(client, "eng", "eng-secret")
    r = client.put(
        "/api/v1/products/usp/boards/boards/usp_frontend.json",
        json={"doc": {"nodes": [{"id": "fe_ok", "x": 0, "y": 0, "title": "前端"}], "groups": [], "edges": []}},
    )
    assert r.status_code == 200, r.text


def test_u120_backend_board_graph_can_edit(client):
    _set_perms(client, "eng", {"usp:backend:*": "write"})
    login(client, "eng", "eng-secret")
    r = client.put(
        "/api/v1/products/usp/boards/boards/usp_backend.json",
        json={"doc": {"nodes": [{"id": "be_g", "x": 0, "y": 0, "title": "接口"}], "groups": [], "edges": []}},
    )
    assert r.status_code == 200, r.text
    g = client.get("/api/v1/products/usp/graph?board=boards/usp_backend.json").json()
    assert g["can_edit"] is True


def test_u121_backend_grant_root_graph_cannot_edit(client):
    _set_perms(client, "eng", {"usp:backend:*": "write"})
    login(client, "eng", "eng-secret")
    assert client.get("/api/v1/products/usp/graph?board=board.json").json()["can_edit"] is False


def test_u122_product_all_can_save_frontend_board(client):
    _set_perms(client, "eng", {"usp:*": "write"})
    login(client, "eng", "eng-secret")
    r = client.put(
        "/api/v1/products/usp/boards/boards/usp_frontend.json",
        json={"doc": {"nodes": [{"id": "fe_all", "x": 0, "y": 0, "title": "前端"}], "groups": [], "edges": []}},
    )
    assert r.status_code == 200, r.text


def test_u123_nested_graph_skip_product_hub(client):
    login(client, "admin", "admin-secret")
    g = client.get("/api/v1/products/usp/modules/usp_algo/nested-graph").json()
    assert g.get("skip") is True
    assert g.get("can_edit") is False


def test_u124_nested_graph_skip_product_release(client):
    login(client, "admin", "admin-secret")
    g = client.get("/api/v1/products/usp/modules/usp_dev/nested-graph").json()
    assert g.get("skip") is True


def test_u125_guest_nested_graph_encrypted_403(client):
    login(client, "guest", "guest-secret")
    assert client.get("/api/v1/products/usp/modules/secret_mod/nested-graph").status_code == 403


def test_u126_pm_nested_graph_cannot_edit(client):
    login(client, "pm1", "pm-secret")
    g = client.get("/api/v1/products/usp/modules/biz/nested-graph").json()
    assert g["can_edit"] is False


def test_u127_tester_host_write_nested_can_edit(client, db, crypto):
    _nest_host(db, crypto)
    _set_perms(client, "qa1", {"secret_mod": "write"}, role="tester", password="qa1-secret1")
    login(client, "qa1", "qa1-secret1")
    assert client.get("/api/v1/products/usp/modules/secret_mod/nested-graph").json()["can_edit"] is True


def test_u128_without_biz_write_cannot_save_biz_nested_file(client):
    login(client, "eng", "eng-secret")
    r = client.put(
        "/api/v1/products/usp/boards/boards/biz.json",
        json={"doc": {"nodes": [{"id": "bx", "x": 0, "y": 0, "title": "x"}], "groups": [], "edges": []}},
    )
    assert r.status_code == 403


def test_u129_biz_write_can_save_own_nested_file(client):
    _set_perms(client, "eng", {"biz": "write"})
    login(client, "eng", "eng-secret")
    r = client.put(
        "/api/v1/products/usp/boards/boards/biz.json",
        json={"doc": {"nodes": [{"id": "bx", "x": 0, "y": 0, "title": "x"}], "groups": [], "edges": []}},
    )
    assert r.status_code == 200, r.text


def test_u130_supervisor_can_save_root_board(client, db):
    db.add(User(username="boss", password_hash=hash_password("boss-secret"), role="supervisor"))
    db.add(UserProduct(username="boss", product="usp"))
    db.commit()
    login(client, "boss", "boss-secret")
    g0 = client.get("/api/v1/products/usp/graph").json()
    r = client.put(
        "/api/v1/products/usp/boards/board.json",
        json={"doc": {"node_w": 158, "node_h": 46, "nodes": g0["nodes"], "groups": g0["groups"], "edges": g0["edges"]}},
    )
    assert r.status_code == 200, r.text


def test_u131_guest_cannot_save_nested_board(client, db, crypto):
    _nest_host(db, crypto)
    login(client, "guest", "guest-secret")
    r = client.put(
        "/api/v1/products/usp/boards/boards/secret_mod.json",
        json={"doc": {"nodes": [{"id": "hack", "x": 0, "y": 0, "title": "x"}], "groups": [], "edges": []}},
    )
    assert r.status_code == 403


def test_u132_pm_cannot_save_nested_even_if_public(client):
    login(client, "pm1", "pm-secret")
    r = client.put(
        "/api/v1/products/usp/boards/boards/biz.json",
        json={"doc": {"nodes": [{"id": "p", "x": 0, "y": 0, "title": "x"}], "groups": [], "edges": []}},
    )
    assert r.status_code == 403


def test_u133_guest_nested_board_graph_cannot_edit(client, db, crypto):
    _nest_host(db, crypto)
    login(client, "guest", "guest-secret")
    g = client.get("/api/v1/products/usp/graph?board=boards/secret_mod.json").json()
    assert g["can_edit"] is False


def test_u134_guest_sees_public_child_on_nested_board(client, db, crypto):
    _nest_host(db, crypto)
    login(client, "guest", "guest-secret")
    g = client.get("/api/v1/products/usp/graph?board=boards/secret_mod.json").json()
    assert "secret_kid" in {n["id"] for n in g["nodes"]}


def test_u135_guest_hides_encrypted_child_on_nested_board(client, db, crypto):
    _nest_host(db, crypto)
    kid = db.query(Module).filter_by(product="usp", id="secret_kid").one()
    kid.encrypted = True
    db.commit()
    login(client, "guest", "guest-secret")
    g = client.get("/api/v1/products/usp/graph?board=boards/secret_mod.json").json()
    assert "secret_kid" not in {n["id"] for n in g["nodes"]}


def test_u136_role_change_to_tester_keeps_write(client):
    _set_perms(client, "eng", {"secret_mod": "write"}, role="tester")
    login(client, "eng", "eng-secret")
    assert client.get("/api/v1/products/usp/packs/secret_mod").status_code == 200


def test_u137_switch_grant_loses_old_module(client):
    _set_perms(client, "eng", {"biz": "write"})
    login(client, "eng", "eng-secret")
    assert client.get("/api/v1/products/usp/packs/secret_mod").status_code == 403
    assert client.get("/api/v1/products/usp/packs/biz").status_code == 200


def test_u138_downgrade_all_to_module_loses_root_expand(client):
    _set_perms(client, "eng", {"usp:*": "write"})
    login(client, "eng", "eng-secret")
    assert client.get("/api/v1/products/usp/graph").json()["can_edit"] is True
    _set_perms(client, "eng", {"secret_mod": "write"})
    login(client, "eng", "eng-secret")
    assert client.get("/api/v1/products/usp/graph").json()["can_edit"] is False


def test_u139_revoke_domain_drops_all_domain_writes(client):
    _set_perms(client, "eng", {"usp:algo:*": "write"})
    login(client, "eng", "eng-secret")
    assert "biz" in {i["id"] for i in client.get("/api/v1/products/usp/writable-modules").json()["items"]}
    _set_perms(client, "eng", {})
    login(client, "eng", "eng-secret")
    assert client.get("/api/v1/products/usp/writable-modules").json()["items"] == []


def test_u140_auth_pack_after_revoke_omits_module(client):
    _set_perms(client, "eng", {})
    login(client, "eng", "eng-secret")
    raw = client.get("/api/v1/account/auth-pack").content
    mods = json.loads(zipfile.ZipFile(io.BytesIO(raw)).read("modules.json"))
    ids = {m["id"] for m in mods["modules_by_product"].get("usp") or []}
    assert "secret_mod" not in ids


def test_u141_auth_pack_after_grant_includes_biz(client):
    _set_perms(client, "eng", {"secret_mod": "write", "biz": "write"})
    login(client, "eng", "eng-secret")
    raw = client.get("/api/v1/account/auth-pack").content
    mods = json.loads(zipfile.ZipFile(io.BytesIO(raw)).read("modules.json"))
    ids = {m["id"] for m in mods["modules_by_product"]["usp"]}
    assert "biz" in ids


def test_u142_agent_token_after_revoke_write_revoked(client):
    login(client, "eng", "eng-secret")
    token = client.post("/api/v1/account/agent-token").json()["token"]
    client.post("/api/v1/auth/logout")
    _set_perms(client, "eng", {})
    r = client.get(
        "/api/v1/products/usp/packs/secret_mod",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403
    assert _err(r) == "write_revoked"


def test_u143_page_put_after_revoke_403(client):
    _set_perms(client, "eng", {})
    login(client, "eng", "eng-secret")
    r = client.put("/api/v1/products/usp/modules/secret_mod/document", json={"markdown": "nope"})
    assert r.status_code == 403


def test_u144_lifecycle_after_revoke_403(client):
    _set_perms(client, "eng", {})
    login(client, "eng", "eng-secret")
    r = client.put("/api/v1/products/usp/modules/secret_mod/lifecycle", json={"band": "dev", "progress": 10})
    assert r.status_code == 403


def test_u145_public_module_get_can_edit_false_without_grant(client):
    login(client, "eng", "eng-secret")
    page = client.get("/api/v1/products/usp/modules/biz")
    assert page.status_code == 200
    assert page.json()["can_edit"] is False


def test_u146_writable_product_field(client):
    login(client, "eng", "eng-secret")
    body = client.get("/api/v1/products/usp/writable-modules").json()
    assert body["product"] == "usp"
    assert all(i["product"] == "usp" for i in body["items"])


def test_u147_ors_without_product_403(client):
    login(client, "eng", "eng-secret")
    assert client.get("/api/v1/products/ors/writable-modules").status_code == 403


def test_u148_ors_product_no_grant_writable_empty(client, db, crypto):
    _add_ors_feat(db, crypto)
    login(client, "eng", "eng-secret")
    items = client.get("/api/v1/products/ors/writable-modules").json()["items"]
    assert items == []


def test_u149_empty_product_in_pack_allowed(client):
    login(client, "eng", "eng-secret")
    raw = client.get("/api/v1/products/usp/packs/secret_mod").content
    r = client.post(
        "/api/v1/products/usp/packs/secret_mod",
        content=_rewrite(raw, doc="no-product-field", pack={"product": ""}),
    )
    assert r.status_code == 200, r.text


def test_u150_write_revoked_detail_has_module_id(client):
    login(client, "eng", "eng-secret")
    r = client.get("/api/v1/products/usp/packs/biz")
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert isinstance(detail, dict)
    assert detail.get("module_id") == "biz"
    assert detail.get("hint")
    assert _err(r) == "write_revoked"
