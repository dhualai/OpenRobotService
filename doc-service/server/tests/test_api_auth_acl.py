from __future__ import annotations

from sqlalchemy import select

from app.infra.models import Document
from tests.conftest import PUBLIC_BODY, SECRET_BODY, SECRET_TITLE, login


def test_health(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_me_requires_login(client):
    r = client.get("/api/v1/me")
    assert r.status_code == 401


def test_bad_password(client):
    r = client.post("/api/v1/auth/login", json={"username": "admin", "password": "nope"})
    assert r.status_code == 401


def test_login_me_logout(client):
    login(client, "admin", "admin-secret")
    me = client.get("/api/v1/me")
    assert me.status_code == 200
    assert me.json()["user"]["role"] == "admin"
    client.post("/api/v1/auth/logout")
    assert client.get("/api/v1/me").status_code == 401


def test_graph_hides_encrypted_from_guest(client):
    login(client, "guest", "guest-secret")
    r = client.get("/api/v1/products/usp/graph")
    assert r.status_code == 200
    ids = {n["id"] for n in r.json()["nodes"]}
    assert "biz" in ids
    assert "secret_mod" not in ids
    assert SECRET_TITLE not in r.text
    assert r.json()["elided"] >= 1
    assert r.json()["board"] == "board.json"
    assert any(d["id"] == "algo" for d in r.json()["domains"])


def test_graph_omits_empty_nested(client, db):
    from app.infra.models import Board, Module

    login(client, "admin", "admin-secret")
    m = db.query(Module).filter_by(id="biz", product="usp").one()
    m.nested_board = "boards/empty.json"
    db.add(Board(product="usp", rel_path="boards/empty.json", doc={"nodes": [], "groups": [], "edges": []}))
    db.commit()
    r = client.get("/api/v1/products/usp/graph")
    biz = next(n for n in r.json()["nodes"] if n["id"] == "biz")
    assert not biz.get("nested_board")


def test_ors_graph_defaults_to_ors_board(client, db, crypto):
    from app.infra.models import Board, Module, UserProduct

    login(client, "admin", "admin-secret")
    if not db.query(UserProduct).filter_by(username="admin", product="ors").first():
        db.add(UserProduct(username="admin", product="ors"))
    db.add(
        Board(
            product="ors",
            rel_path="boards/ors.json",
            doc={"node_w": 158, "node_h": 46, "nodes": [{"id": "ors_x", "x": 0, "y": 0}], "groups": [], "edges": []},
        )
    )
    db.add(
        Module(
            id="ors_x",
            product="ors",
            board_rel="boards/ors.json",
            kind="feature",
            title_blob=crypto.encrypt("ORS根", aad="module.title:ors:ors_x"),
            current_version="v1.0",
        )
    )
    db.commit()
    r = client.get("/api/v1/products/ors/graph")
    assert r.status_code == 200
    assert r.json()["board"] == "boards/ors.json"
    ids = {n["id"] for n in r.json()["nodes"]}
    assert "ors_x" in ids


def test_graph_shows_encrypted_to_grantee(client):
    login(client, "eng", "eng-secret")
    r = client.get("/api/v1/products/usp/graph")
    ids = {n["id"] for n in r.json()["nodes"]}
    assert "secret_mod" in ids


def test_forbidden_module_has_no_title(client):
    login(client, "guest", "guest-secret")
    r = client.get("/api/v1/products/usp/modules/secret_mod")
    assert r.status_code == 403
    assert SECRET_TITLE not in r.text
    assert SECRET_BODY not in r.text


def test_module_ok_for_grantee(client):
    login(client, "eng", "eng-secret")
    r = client.get("/api/v1/products/usp/modules/secret_mod")
    assert r.status_code == 200
    assert r.json()["markdown"] == SECRET_BODY


def test_pack_requires_write(client):
    login(client, "guest", "guest-secret")
    r = client.get("/api/v1/products/usp/packs/biz")
    assert r.status_code == 403


def test_pack_zip_for_editor(client):
    login(client, "eng", "eng-secret")
    r = client.get("/api/v1/products/usp/packs/secret_mod")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/zip")


def test_agent_token_pack(client):
    login(client, "eng", "eng-secret")
    issued = client.post("/api/v1/account/agent-token")
    assert issued.status_code == 200
    token = issued.json()["token"]
    client.post("/api/v1/auth/logout")
    assert client.get("/api/v1/me").status_code == 401
    r = client.get(
        "/api/v1/products/usp/packs/secret_mod",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/zip")


def test_auth_pack_zip(client):
    import io
    import json
    import zipfile

    login(client, "eng", "eng-secret")
    r = client.get("/api/v1/account/auth-pack")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/zip")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(z.namelist())
    assert "auth.json" in names
    assert "modules.json" in names
    auth = json.loads(z.read("auth.json"))
    assert auth["username"] == "eng"
    assert auth["token"]
    login(client, "guest", "guest-secret")
    assert client.get("/api/v1/account/auth-pack").status_code == 403


def test_auth_pack_uses_page_origin(client):
    import io
    import json
    import zipfile

    login(client, "eng", "eng-secret")
    r = client.get(
        "/api/v1/account/auth-pack",
        headers={"X-Topo-Origin": "http://127.0.0.1:5173"},
    )
    assert r.status_code == 200, r.text
    auth = json.loads(zipfile.ZipFile(io.BytesIO(r.content)).read("auth.json"))
    assert auth["base_url"] == "http://127.0.0.1:5173"
    assert "{base_url}" in auth["apis"]["import"]


def test_auth_pack_public_url_wins(client, monkeypatch):
    import io
    import json
    import zipfile

    from app.settings import get_settings

    monkeypatch.setenv("TOPO_PUBLIC_URL", "http://10.10.10.202:8766")
    get_settings.cache_clear()
    try:
        login(client, "eng", "eng-secret")
        r = client.get(
            "/api/v1/account/auth-pack",
            headers={"X-Topo-Origin": "http://127.0.0.1:5173"},
        )
        assert r.status_code == 200, r.text
        auth = json.loads(zipfile.ZipFile(io.BytesIO(r.content)).read("auth.json"))
        assert auth["base_url"] == "http://10.10.10.202:8766"
    finally:
        get_settings.cache_clear()


def test_db_blob_not_plaintext(db, seed):
    blob = db.scalar(select(Document.body_blob).where(Document.module_id == "secret_mod"))
    assert blob is not None
    assert SECRET_BODY.encode() not in blob
    assert PUBLIC_BODY.encode() not in db.scalar(
        select(Document.body_blob).where(Document.module_id == "biz")
    )
