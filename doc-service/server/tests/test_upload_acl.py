from __future__ import annotations

import io
import json
import zipfile

from app.infra.models import Board, Grant, Module, User, UserProduct
from app.services.auth import hash_password
from tests.conftest import SECRET_BODY, login
from tests.test_agent_pack import _rewrite


def _err(r):
    d = r.json().get("detail")
    if isinstance(d, dict):
        return d.get("error")
    return d


def _hint(r):
    d = r.json().get("detail")
    return d.get("hint") if isinstance(d, dict) else None


def _set_perms(client, username, perms, *, role="engineer", products=None, password=None):
    login(client, "admin", "admin-secret")
    body = {
        "username": username,
        "role": role,
        "products": ["usp"] if products is None else products,
        "module_perms": perms,
    }
    if password:
        body["password"] = password
    r = client.post("/api/v1/admin/users", json=body)
    assert r.status_code in (200, 201), r.text
    client.post("/api/v1/auth/logout")


def _add_eng2(db):
    if not db.get(User, "eng2"):
        db.add(User(username="eng2", password_hash=hash_password("eng2-secret"), role="engineer"))
        db.add(UserProduct(username="eng2", product="usp"))
        db.add(Grant(username="eng2", product="usp", kind="module", resource_id="secret_mod", perm="write"))
        db.commit()


def _nest_host(db, crypto, host_id="secret_mod", child_id="secret_kid", rel="boards/secret_mod.json"):
    host = db.query(Module).filter_by(product="usp", id=host_id).one()
    host.nested_board = rel
    if not db.query(Board).filter_by(product="usp", rel_path=rel).first():
        db.add(
            Board(
                product="usp",
                rel_path=rel,
                doc={"nodes": [{"id": child_id, "x": 0, "y": 0}], "groups": [], "edges": []},
            )
        )
    if not db.query(Module).filter_by(product="usp", id=child_id).first():
        db.add(
            Module(
                id=child_id,
                product="usp",
                board_rel=rel,
                kind="feature",
                parent_id=host_id,
                title_blob=crypto.encrypt("子模块", aad=f"module.title:usp:{child_id}"),
                current_version="v1.0",
            )
        )
    db.commit()


def _add_group(db, crypto, gid: str, members: list[str], title: str = "族"):
    from sqlalchemy.orm.attributes import flag_modified

    b = db.query(Board).filter_by(product="usp", rel_path="board.json").one()
    doc = dict(b.doc)
    groups = list(doc.get("groups") or [])
    groups.append({"id": gid, "title": title, "x": 0, "y": 0, "w": 200, "h": 80, "members": members})
    doc["groups"] = groups
    b.doc = doc
    flag_modified(b, "doc")
    if not db.query(Module).filter_by(product="usp", id=gid).first():
        db.add(
            Module(
                id=gid,
                product="usp",
                board_rel="board.json",
                kind="group",
                title_blob=crypto.encrypt(title, aad=f"module.title:usp:{gid}"),
                current_version="v1.0",
            )
        )
    db.commit()


# --- 多用户 / edit_token ---


def test_u01_export_returns_edit_token(client):
    login(client, "eng", "eng-secret")
    z = client.get("/api/v1/products/usp/packs/secret_mod")
    assert z.status_code == 200
    pack = json.loads(zipfile.ZipFile(io.BytesIO(z.content)).read("PACK.json"))
    assert pack["edit_token"]
    assert pack["module_id"] == "secret_mod"


def test_u02_edit_token_since_current_unchanged(client):
    login(client, "eng", "eng-secret")
    z = client.get("/api/v1/products/usp/packs/secret_mod")
    token = json.loads(zipfile.ZipFile(io.BytesIO(z.content)).read("PACK.json"))["edit_token"]
    r = client.get(f"/api/v1/products/usp/packs/secret_mod/edit-token?since={token}")
    assert r.status_code == 200
    body = r.json()
    assert body["unchanged"] is True
    assert body["export_required"] is False
    assert body["edit_token"] == token


def test_u03_edit_token_since_stale_requires_export(client):
    login(client, "eng", "eng-secret")
    r = client.get("/api/v1/products/usp/packs/secret_mod/edit-token?since=not-the-token")
    assert r.status_code == 200
    assert r.json()["unchanged"] is False
    assert r.json()["export_required"] is True


def test_u04_edit_token_without_since(client):
    login(client, "eng", "eng-secret")
    r = client.get("/api/v1/products/usp/packs/secret_mod/edit-token")
    assert r.status_code == 200
    assert r.json()["unchanged"] is None
    assert r.json()["export_required"] is None
    assert r.json()["edit_token"]


def test_u05_import_matching_token_ok(client):
    login(client, "eng", "eng-secret")
    z = client.get("/api/v1/products/usp/packs/secret_mod")
    r = client.post("/api/v1/products/usp/packs/secret_mod", content=z.content)
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_u06_import_after_other_user_change_conflict(client, db):
    _add_eng2(db)
    login(client, "eng", "eng-secret")
    stale = client.get("/api/v1/products/usp/packs/secret_mod").content
    login(client, "eng2", "eng2-secret")
    client.put("/api/v1/products/usp/modules/secret_mod/document", json={"markdown": "eng2-wrote"})
    login(client, "eng", "eng-secret")
    r = client.post("/api/v1/products/usp/packs/secret_mod", content=_rewrite(stale, doc="eng-late"))
    assert r.status_code == 409
    assert _err(r) == "edit_conflict"
    assert "重新" in (_hint(r) or "")
    assert client.get("/api/v1/products/usp/modules/secret_mod").json()["markdown"] == "eng2-wrote"


def test_u07_import_stale_token_same_body_idempotent(client, db):
    _add_eng2(db)
    login(client, "eng", "eng-secret")
    stale = client.get("/api/v1/products/usp/packs/secret_mod").content
    login(client, "eng2", "eng2-secret")
    client.put("/api/v1/products/usp/modules/secret_mod/document", json={"markdown": "same-now"})
    login(client, "eng", "eng-secret")
    r = client.post("/api/v1/products/usp/packs/secret_mod", content=_rewrite(stale, doc="same-now"))
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_u08_two_exports_second_import_wins(client, db):
    _add_eng2(db)
    login(client, "eng", "eng-secret")
    a = client.get("/api/v1/products/usp/packs/secret_mod").content
    login(client, "eng2", "eng2-secret")
    b = client.get("/api/v1/products/usp/packs/secret_mod").content
    r2 = client.post("/api/v1/products/usp/packs/secret_mod", content=_rewrite(b, doc="from-eng2"))
    assert r2.status_code == 200
    login(client, "eng", "eng-secret")
    r1 = client.post("/api/v1/products/usp/packs/secret_mod", content=_rewrite(a, doc="from-eng"))
    assert r1.status_code == 409
    assert _err(r1) == "edit_conflict"


def test_u09_import_without_token_requires_export(client):
    login(client, "eng", "eng-secret")
    raw = client.get("/api/v1/products/usp/packs/secret_mod").content
    r = client.post(
        "/api/v1/products/usp/packs/secret_mod",
        content=_rewrite(raw, doc="legacy-no-token", pack={"edit_token": ""}),
    )
    assert r.status_code == 409
    assert _err(r) == "edit_conflict"
    assert "工程包" in (_hint(r) or "")


def test_u10_page_edit_then_old_pack_conflicts(client):
    login(client, "eng", "eng-secret")
    stale = client.get("/api/v1/products/usp/packs/secret_mod").content
    client.put("/api/v1/products/usp/modules/secret_mod/document", json={"markdown": "page-now"})
    r = client.post("/api/v1/products/usp/packs/secret_mod", content=_rewrite(stale, doc="agent-old"))
    assert r.status_code == 409
    assert _err(r) == "edit_conflict"
    assert client.get("/api/v1/products/usp/modules/secret_mod").json()["markdown"] == "page-now"


def test_u11_successful_import_returns_new_token(client):
    login(client, "eng", "eng-secret")
    raw = client.get("/api/v1/products/usp/packs/secret_mod").content
    old = json.loads(zipfile.ZipFile(io.BytesIO(raw)).read("PACK.json"))["edit_token"]
    r = client.post("/api/v1/products/usp/packs/secret_mod", content=_rewrite(raw, doc="new-body-token"))
    assert r.status_code == 200
    assert r.json()["edit_token"]
    assert r.json()["edit_token"] != old


def test_u12_edit_token_without_write_revoked(client):
    login(client, "eng", "eng-secret")
    r = client.get("/api/v1/products/usp/packs/biz/edit-token")
    assert r.status_code == 403
    assert _err(r) == "write_revoked"
    assert "写权限" in (_hint(r) or "")


# --- 权限移除 ---


def test_u13_export_after_revoke_write_revoked(client):
    _set_perms(client, "eng", {})
    login(client, "eng", "eng-secret")
    r = client.get("/api/v1/products/usp/packs/secret_mod")
    assert r.status_code == 403
    assert _err(r) == "write_revoked"


def test_u14_import_after_revoke_body_unchanged(client):
    login(client, "eng", "eng-secret")
    raw = client.get("/api/v1/products/usp/packs/secret_mod").content
    _set_perms(client, "eng", {})
    login(client, "eng", "eng-secret")
    r = client.post("/api/v1/products/usp/packs/secret_mod", content=_rewrite(raw, doc="should-fail"))
    assert r.status_code == 403
    assert _err(r) == "write_revoked"
    login(client, "admin", "admin-secret")
    assert client.get("/api/v1/products/usp/modules/secret_mod").json()["markdown"] == SECRET_BODY


def test_u15_revoke_hint_mentions_permission(client):
    _set_perms(client, "eng", {})
    login(client, "eng", "eng-secret")
    r = client.get("/api/v1/products/usp/packs/secret_mod")
    assert "写权限" in (_hint(r) or "")


def test_u16_edit_token_after_revoke_403(client):
    _set_perms(client, "eng", {})
    login(client, "eng", "eng-secret")
    r = client.get("/api/v1/products/usp/packs/secret_mod/edit-token")
    assert r.status_code == 403
    assert _err(r) == "write_revoked"


def test_u17_module_can_edit_false_after_revoke(client):
    _set_perms(client, "eng", {})
    login(client, "eng", "eng-secret")
    page = client.get("/api/v1/products/usp/modules/secret_mod")
    assert page.status_code == 403


def test_u18_writable_modules_omits_revoked(client):
    login(client, "eng", "eng-secret")
    before = {i["id"] for i in client.get("/api/v1/products/usp/writable-modules").json()["items"]}
    assert "secret_mod" in before
    _set_perms(client, "eng", {})
    login(client, "eng", "eng-secret")
    after = {i["id"] for i in client.get("/api/v1/products/usp/writable-modules").json()["items"]}
    assert "secret_mod" not in after


def test_u19_encrypted_hidden_after_revoke(client):
    _set_perms(client, "eng", {})
    login(client, "eng", "eng-secret")
    g = client.get("/api/v1/products/usp/graph").json()
    assert "secret_mod" not in {n["id"] for n in g["nodes"]}


def test_u20_graph_elides_encrypted_after_revoke(client):
    _set_perms(client, "eng", {})
    login(client, "eng", "eng-secret")
    g = client.get("/api/v1/products/usp/graph").json()
    assert g["elided"] >= 1


# --- 权限授予 ---


def test_u21_grant_then_writable_lists_module(client):
    login(client, "eng", "eng-secret")
    assert "biz" not in {i["id"] for i in client.get("/api/v1/products/usp/writable-modules").json()["items"]}
    _set_perms(client, "eng", {"secret_mod": "write", "biz": "write"})
    login(client, "eng", "eng-secret")
    ids = {i["id"] for i in client.get("/api/v1/products/usp/writable-modules").json()["items"]}
    assert "biz" in ids


def test_u22_grant_then_can_edit_true(client):
    _set_perms(client, "eng", {"secret_mod": "write", "biz": "write"})
    login(client, "eng", "eng-secret")
    assert client.get("/api/v1/products/usp/modules/biz").json()["can_edit"] is True


def test_u23_grant_then_export_ok(client):
    _set_perms(client, "eng", {"secret_mod": "write", "biz": "write"})
    login(client, "eng", "eng-secret")
    assert client.get("/api/v1/products/usp/packs/biz").status_code == 200


def test_u24_grant_then_import_ok(client):
    _set_perms(client, "eng", {"secret_mod": "write", "biz": "write"})
    login(client, "eng", "eng-secret")
    raw = client.get("/api/v1/products/usp/packs/biz").content
    r = client.post("/api/v1/products/usp/packs/biz", content=_rewrite(raw, doc="granted-import"))
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/products/usp/modules/biz").json()["markdown"] == "granted-import"


def test_u25_grant_encrypted_becomes_browsable(client):
    _set_perms(client, "eng", {})
    login(client, "eng", "eng-secret")
    assert client.get("/api/v1/products/usp/modules/secret_mod").status_code == 403
    _set_perms(client, "eng", {"secret_mod": "write"})
    login(client, "eng", "eng-secret")
    assert client.get("/api/v1/products/usp/modules/secret_mod").status_code == 200


def test_u26_same_agent_token_after_grant(client):
    headers = {}
    login(client, "eng", "eng-secret")
    issued = client.post("/api/v1/account/agent-token")
    token = issued.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/api/v1/auth/logout")
    _set_perms(client, "eng", {"secret_mod": "write", "biz": "write"})
    r = client.get("/api/v1/products/usp/packs/biz", headers=headers)
    assert r.status_code == 200


def test_u27_domain_grant_lists_domain_modules(client):
    _set_perms(client, "eng", {"usp:algo:*": "write"})
    login(client, "eng", "eng-secret")
    ids = {i["id"] for i in client.get("/api/v1/products/usp/writable-modules").json()["items"]}
    assert "biz" in ids
    assert "secret_mod" in ids


def test_u28_can_edit_query_without_auth_pack_snapshot(client):
    login(client, "eng", "eng-secret")
    assert client.get("/api/v1/products/usp/modules/biz").json()["can_edit"] is False
    _set_perms(client, "eng", {"secret_mod": "write", "biz": "write"})
    login(client, "eng", "eng-secret")
    assert client.get("/api/v1/products/usp/modules/biz").json()["can_edit"] is True


# --- 新增子模块级联 ---


def test_u29_host_write_can_edit_new_child(client, db, crypto):
    _nest_host(db, crypto)
    login(client, "eng", "eng-secret")
    page = client.get("/api/v1/products/usp/modules/secret_kid")
    assert page.status_code == 200
    assert page.json()["can_edit"] is True


def test_u30_writable_includes_new_child(client, db, crypto):
    _nest_host(db, crypto)
    login(client, "eng", "eng-secret")
    ids = {i["id"] for i in client.get("/api/v1/products/usp/writable-modules").json()["items"]}
    assert "secret_kid" in ids


def test_u31_import_child_pack_ok(client, db, crypto):
    _nest_host(db, crypto)
    login(client, "admin", "admin-secret")
    client.put("/api/v1/products/usp/modules/secret_kid/document", json={"markdown": "kid-src"})
    login(client, "eng", "eng-secret")
    raw = client.get("/api/v1/products/usp/packs/secret_kid").content
    r = client.post("/api/v1/products/usp/packs/secret_kid", content=_rewrite(raw, doc="kid-by-eng"))
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/products/usp/modules/secret_kid").json()["markdown"] == "kid-by-eng"


def test_u32_without_host_write_cannot_edit_child(client, db, crypto):
    _nest_host(db, crypto)
    _set_perms(client, "eng", {"biz": "write"})
    login(client, "eng", "eng-secret")
    assert client.get("/api/v1/products/usp/packs/secret_kid").status_code == 403


def test_u33_group_grant_cascades_to_new_member(client, db, crypto):
    _add_group(db, crypto, "g_up", ["biz"])
    _set_perms(client, "eng", {"g_up": "write"})
    login(client, "eng", "eng-secret")
    r = client.put("/api/v1/products/usp/modules/biz/document", json={"markdown": "from-new-group"})
    assert r.status_code == 200, r.text


def test_u34_nested_create_sets_parent_id(client, db, crypto):
    host = db.query(Module).filter_by(product="usp", id="secret_mod").one()
    host.nested_board = "boards/secret_mod.json"
    db.commit()
    login(client, "eng", "eng-secret")
    r = client.put(
        "/api/v1/products/usp/boards/boards/secret_mod.json",
        json={"doc": {"nodes": [{"id": "born_kid", "x": 1, "y": 2, "title": "新生"}], "groups": [], "edges": []}},
    )
    assert r.status_code == 200, r.text
    db.expire_all()
    kid = db.query(Module).filter_by(product="usp", id="born_kid").one()
    assert kid.parent_id == "secret_mod"
    assert kid.encrypted is True


def test_u35_product_all_can_edit_new_child(client, db, crypto):
    _nest_host(db, crypto)
    _set_perms(client, "eng", {"usp:*": "write"})
    login(client, "eng", "eng-secret")
    assert client.get("/api/v1/products/usp/modules/secret_kid").json()["can_edit"] is True


def test_u36_revoke_host_loses_child_write(client, db, crypto):
    _nest_host(db, crypto)
    _set_perms(client, "eng", {})
    login(client, "eng", "eng-secret")
    assert client.get("/api/v1/products/usp/packs/secret_kid").status_code == 403


def test_u37_domain_grant_covers_nested_child(client, db, crypto):
    _nest_host(db, crypto)
    _set_perms(client, "eng", {"usp:algo:*": "write"})
    login(client, "eng", "eng-secret")
    assert client.get("/api/v1/products/usp/modules/secret_kid").json()["can_edit"] is True


def test_u38_encrypted_child_browsable_via_cascade(client, db, crypto):
    _nest_host(db, crypto)
    kid = db.query(Module).filter_by(product="usp", id="secret_kid").one()
    kid.encrypted = True
    db.commit()
    login(client, "eng", "eng-secret")
    page = client.get("/api/v1/products/usp/modules/secret_kid")
    assert page.status_code == 200
    assert page.json()["can_edit"] is True


# --- 拓扑展开 / can_edit ---


def test_u39_module_write_root_graph_cannot_edit(client):
    login(client, "eng", "eng-secret")
    g = client.get("/api/v1/products/usp/graph").json()
    assert g["can_edit"] is False


def test_u40_product_all_root_graph_can_edit(client):
    _set_perms(client, "eng", {"usp:*": "write"})
    login(client, "eng", "eng-secret")
    assert client.get("/api/v1/products/usp/graph").json()["can_edit"] is True


def test_u41_admin_root_graph_can_edit(client):
    login(client, "admin", "admin-secret")
    assert client.get("/api/v1/products/usp/graph").json()["can_edit"] is True


def test_u42_supervisor_root_graph_can_edit(client, db):
    db.add(User(username="boss", password_hash=hash_password("boss-secret"), role="supervisor"))
    db.add(UserProduct(username="boss", product="usp"))
    db.commit()
    login(client, "boss", "boss-secret")
    assert client.get("/api/v1/products/usp/graph").json()["can_edit"] is True


def test_u43_host_write_nested_graph_can_edit(client, db, crypto):
    _nest_host(db, crypto)
    login(client, "eng", "eng-secret")
    g = client.get("/api/v1/products/usp/modules/secret_mod/nested-graph").json()
    assert g["can_edit"] is True


def test_u44_unrelated_module_nested_cannot_edit(client):
    login(client, "eng", "eng-secret")
    g = client.get("/api/v1/products/usp/modules/biz/nested-graph").json()
    assert g.get("can_edit") is False


def test_u45_guest_root_cannot_edit(client):
    login(client, "guest", "guest-secret")
    assert client.get("/api/v1/products/usp/graph").json()["can_edit"] is False


def test_u46_pm_cannot_edit_board(client):
    login(client, "pm1", "pm-secret")
    g = client.get("/api/v1/products/usp/graph").json()
    assert g["can_edit"] is False
    r = client.put(
        "/api/v1/products/usp/boards/board.json",
        json={"doc": {"nodes": [{"id": "biz", "x": 0, "y": 0}], "groups": [], "edges": []}},
    )
    assert r.status_code == 403


def test_u47_host_write_can_save_nested_board(client, db, crypto):
    host = db.query(Module).filter_by(product="usp", id="secret_mod").one()
    host.nested_board = "boards/secret_mod.json"
    db.commit()
    login(client, "eng", "eng-secret")
    r = client.put(
        "/api/v1/products/usp/boards/boards/secret_mod.json",
        json={"doc": {"nodes": [{"id": "nk", "x": 0, "y": 0, "title": "子"}], "groups": [], "edges": []}},
    )
    assert r.status_code == 200, r.text


def test_u48_module_write_cannot_save_root_board(client):
    login(client, "eng", "eng-secret")
    g0 = client.get("/api/v1/products/usp/graph").json()
    r = client.put(
        "/api/v1/products/usp/boards/board.json",
        json={"doc": {"node_w": 158, "node_h": 46, "nodes": g0["nodes"], "groups": g0["groups"], "edges": g0["edges"]}},
    )
    assert r.status_code == 403


def test_u49_domain_grant_domain_board_not_root(client):
    _set_perms(client, "eng", {"usp:backend:*": "write"})
    login(client, "eng", "eng-secret")
    assert client.get("/api/v1/products/usp/graph").json()["can_edit"] is False
    r = client.put(
        "/api/v1/products/usp/boards/boards/usp_backend.json",
        json={"doc": {"nodes": [{"id": "be_x", "x": 0, "y": 0, "title": "接口"}], "groups": [], "edges": []}},
    )
    assert r.status_code == 200, r.text


def test_u50_auth_pack_lists_live_apis(client):
    login(client, "eng", "eng-secret")
    r = client.get("/api/v1/account/auth-pack")
    assert r.status_code == 200
    auth = json.loads(zipfile.ZipFile(io.BytesIO(r.content)).read("auth.json"))
    apis = auth["apis"]
    assert "edit_token" in apis
    assert "writable" in apis
    assert "module" in apis
    md = zipfile.ZipFile(io.BytesIO(r.content)).read("AGENTS.md").decode("utf-8")
    assert "write_revoked" in md
    assert "edit_token" in md
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
    assert "draft_export" in apis
    assert "req_export" in apis
