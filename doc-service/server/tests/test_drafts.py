from __future__ import annotations

import io
import json
import zipfile

from app.infra.models import Draft
from tests.conftest import login


def test_draft_own_only_and_encrypted(client, db, crypto):
    login(client, "eng", "eng-secret")
    assert client.get("/api/v1/drafts?tag=draft").json()["items"] == []
    assert client.get("/api/v1/drafts?tag=public").json()["items"] == []
    bad = client.post("/api/v1/drafts", json={"title": "  "})
    assert bad.status_code == 400
    marker = "DRAFT_SECRET_TEXT"
    created = client.post("/api/v1/drafts", json={"title": "笔记", "markdown": marker})
    assert created.status_code == 201, created.text
    did = created.json()["id"]
    page = client.get(f"/api/v1/drafts/{did}").json()
    assert page["title"] == "笔记"
    assert page["status"] == "draft"
    assert page["can_publish"] is True
    assert page["markdown"] == marker
    assert marker in page["html"]
    row = db.get(Draft, did)
    assert row is not None
    assert marker.encode() not in row.body_blob
    assert crypto.decrypt_text(row.body_blob, aad=f"draft:{did}") == marker
    listing = client.get("/api/v1/drafts?tag=draft").json()
    assert [i["id"] for i in listing["items"]] == [did]
    assert client.get("/api/v1/drafts?tag=public").json()["items"] == []
    login(client, "pm1", "pm-secret")
    assert client.get("/api/v1/drafts?tag=draft").json()["items"] == []
    assert client.get(f"/api/v1/drafts/{did}").status_code == 404
    assert client.patch(f"/api/v1/drafts/{did}", json={"title": "偷"}).status_code == 404
    assert client.delete(f"/api/v1/drafts/{did}").status_code == 404
    login(client, "admin", "admin-secret")
    assert client.get(f"/api/v1/drafts/{did}").status_code == 404
    login(client, "eng", "eng-secret")
    patched = client.patch(f"/api/v1/drafts/{did}", json={"markdown": "改过"})
    assert patched.status_code == 200
    assert patched.json()["markdown"] == "改过"
    gone = client.delete(f"/api/v1/drafts/{did}")
    assert gone.status_code == 200
    assert client.get(f"/api/v1/drafts/{did}").status_code == 404


def test_draft_pagination_and_guest(client):
    login(client, "guest", "guest-secret")
    ids = []
    for i in range(3):
        r = client.post("/api/v1/drafts", json={"title": f"g{i}"})
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    page1 = client.get("/api/v1/drafts?tag=draft&page=1&size=2").json()
    assert page1["total"] == 3
    assert page1["pages"] == 2
    assert page1["size"] == 2
    assert len(page1["items"]) == 2
    page2 = client.get("/api/v1/drafts?tag=draft&page=2&size=2").json()
    assert len(page2["items"]) == 1
    seen = {i["id"] for i in page1["items"] + page2["items"]}
    assert seen == set(ids)
    client.post("/api/v1/auth/logout")
    assert client.get("/api/v1/drafts").status_code == 401
    assert client.post("/api/v1/drafts", json={"title": "x"}).status_code == 401


def test_draft_publish_audience(client):
    login(client, "eng", "eng-secret")
    people = client.get("/api/v1/drafts/people").json()["items"]
    names = {u["username"] for u in people}
    assert {"eng", "pm1", "admin", "guest"} <= names
    all_open = client.post("/api/v1/drafts", json={"title": "全员", "markdown": "open"}).json()["id"]
    listed = client.post("/api/v1/drafts", json={"title": "点名", "markdown": "secret"}).json()["id"]
    pub_all = client.post(f"/api/v1/drafts/{all_open}/publish", json={"usernames": list(names)})
    assert pub_all.status_code == 200, pub_all.text
    assert pub_all.json()["audience"] == "all"
    assert client.get(f"/api/v1/drafts/{all_open}").json()["can_publish"] is False
    pub_pm = client.post(f"/api/v1/drafts/{listed}/publish", json={"usernames": ["pm1"]})
    assert pub_pm.status_code == 200, pub_pm.text
    assert pub_pm.json()["audience"] == "listed"
    assert client.get("/api/v1/drafts?tag=draft").json()["items"] == []
    again = client.post(f"/api/v1/drafts/{all_open}/publish", json={"usernames": list(names)})
    assert again.status_code == 400
    login(client, "pm1", "pm-secret")
    public = {i["id"] for i in client.get("/api/v1/drafts?tag=public").json()["items"]}
    assert all_open in public
    assert listed in public
    listed_page = client.get(f"/api/v1/drafts/{listed}").json()
    assert listed_page["html"]
    assert listed_page["markdown"] == "secret"
    assert listed_page["can_edit"] is True
    assert listed_page["can_delete"] is False
    assert listed_page["can_publish"] is False
    assert client.patch(f"/api/v1/drafts/{listed}", json={"markdown": "pm改了"}).status_code == 200
    assert client.get(f"/api/v1/drafts/{listed}").json()["markdown"] == "pm改了"
    assert client.delete(f"/api/v1/drafts/{listed}").status_code == 404
    login(client, "guest", "guest-secret")
    public_g = {i["id"] for i in client.get("/api/v1/drafts?tag=public").json()["items"]}
    assert all_open in public_g
    assert listed not in public_g
    assert client.get(f"/api/v1/drafts/{listed}").status_code == 404
    open_page = client.get(f"/api/v1/drafts/{all_open}").json()
    assert open_page["can_edit"] is True
    assert client.patch(f"/api/v1/drafts/{all_open}", json={"markdown": "guest改"}).status_code == 200


def test_draft_comments_and_views(client):
    login(client, "eng", "eng-secret")
    did = client.post("/api/v1/drafts", json={"title": "帖", "markdown": "正文"}).json()["id"]
    people = {u["username"] for u in client.get("/api/v1/drafts/people").json()["items"]}
    assert client.post(f"/api/v1/drafts/{did}/publish", json={"usernames": list(people)}).status_code == 200
    empty = client.post(f"/api/v1/drafts/{did}/comments", json={"body": "  "})
    assert empty.status_code == 400
    posted = client.post(f"/api/v1/drafts/{did}/comments", json={"body": "说一句"})
    assert posted.status_code == 201, posted.text
    assert client.post(f"/api/v1/drafts/{did}/views").status_code == 200
    page = client.get(f"/api/v1/drafts/{did}").json()
    assert page["can_comment"] is True
    assert [c["body"] for c in page["comments"]] == ["说一句"]
    assert client.get(f"/api/v1/drafts/{did}/views").status_code == 403

    login(client, "guest", "guest-secret")
    assert client.post(f"/api/v1/drafts/{did}/comments", json={"body": "guest说"}).status_code == 201
    gpage = client.get(f"/api/v1/drafts/{did}").json()
    assert [c["body"] for c in gpage["comments"]] == ["说一句", "guest说"]
    hidden = client.post("/api/v1/drafts", json={"title": "别人的"}).json()["id"]
    login(client, "eng", "eng-secret")
    assert client.post(f"/api/v1/drafts/{hidden}/comments", json={"body": "偷评"}).status_code == 404
    assert client.post(f"/api/v1/drafts/{hidden}/views").status_code == 404

    login(client, "admin", "admin-secret")
    listed = client.get(f"/api/v1/drafts/{did}/views")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["total_views"] == 1
    assert body["items"][0]["username"] == "eng"
    assert "admin" not in {x["username"] for x in body["items"]}
    visits = client.get("/api/v1/admin/visits").json()["items"]
    draft_hits = [x for x in visits if x["kind"] == "draft"]
    assert draft_hits
    assert draft_hits[0]["href"] == f"/usp/drafts/{did}"
    assert draft_hits[0]["title"] == "帖"


def test_draft_pack_roundtrip_and_ack(client):
    login(client, "eng", "eng-secret")
    did = client.post("/api/v1/drafts", json={"title": "包", "markdown": "初稿"}).json()["id"]
    people = {u["username"] for u in client.get("/api/v1/drafts/people").json()["items"]}
    assert client.post(f"/api/v1/drafts/{did}/publish", json={"usernames": list(people)}).status_code == 200
    raw = client.get(f"/api/v1/drafts/{did}/pack").content
    z = zipfile.ZipFile(io.BytesIO(raw))
    md = z.read("AGENTS.md").decode("utf-8")
    assert md.strip().splitlines()[-1].startswith("校验码 ")
    pack = json.loads(z.read("PACK.json"))
    assert pack["kind"] == "draft"
    from tests.test_agent_pack import _rewrite

    r = client.post(f"/api/v1/drafts/{did}/pack", content=_rewrite(raw, doc="改过的讨论稿"))
    assert r.status_code == 200, r.text
    assert client.get(f"/api/v1/drafts/{did}").json()["markdown"] == "改过的讨论稿"
    stale_ack = md.rsplit("\n", 1)[0] + "\n校验码 0-deadbeef\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as src, zipfile.ZipFile(buf, "w") as dst:
        for name in src.namelist():
            data = src.read(name) if name != "AGENTS.md" else stale_ack.encode("utf-8")
            dst.writestr(name, data)
    bad = client.post(f"/api/v1/drafts/{did}/pack", content=buf.getvalue())
    assert bad.status_code == 409
    login(client, "pm1", "pm-secret")
    raw2 = client.get(f"/api/v1/drafts/{did}/pack").content
    ok = client.post(f"/api/v1/drafts/{did}/pack", content=_rewrite(raw2, doc="pm改"))
    assert ok.status_code == 200, ok.text


def test_draft_agent_must_use_pack(client):
    from tests.test_agent_pack import _bearer

    login(client, "eng", "eng-secret")
    did = client.post("/api/v1/drafts", json={"title": "agent稿"}).json()["id"]
    headers = _bearer(client)
    denied = client.patch(f"/api/v1/drafts/{did}", json={"markdown": "偷改"}, headers=headers)
    assert denied.status_code == 409
    created = client.post("/api/v1/drafts", json={"title": "带正文", "markdown": "不行"}, headers=headers)
    assert created.status_code == 409
    raw = client.get(f"/api/v1/drafts/{did}/pack", headers=headers).content
    from tests.test_agent_pack import _rewrite

    r = client.post(f"/api/v1/drafts/{did}/pack", content=_rewrite(raw, doc="agent正文"), headers=headers)
    assert r.status_code == 200, r.text
    login(client, "eng", "eng-secret")
    assert client.get(f"/api/v1/drafts/{did}").json()["markdown"] == "agent正文"
