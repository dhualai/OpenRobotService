from __future__ import annotations

import json

from app.infra.models import AuditLog, Board, Document, HourAllocation, Module, ModuleVersion, Requirement, RequirementDoc, User, UserProduct
from app.services.auth import hash_password
from app.services.hours import iso_week_id, week_bounds
from tests.conftest import SECRET_BODY, login


def test_hours_hard_cap(client, db):
    login(client, "eng", "eng-secret")
    week = iso_week_id()
    r = client.put(
        "/api/v1/products/usp/hours",
        json={
            "feature_id": "secret_mod",
            "version": "v1.0",
            "expected_hours": 64,
            "hours": 41,
            "week": week,
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "hard_cap"


def test_hours_ok_then_settled(client, db):
    login(client, "eng", "eng-secret")
    week = iso_week_id()
    r = client.put(
        "/api/v1/products/usp/hours",
        json={
            "feature_id": "secret_mod",
            "version": "v1.0",
            "expected_hours": 8,
            "hours": 2,
            "week": week,
        },
    )
    assert r.status_code == 200
    db.expire_all()
    mv = db.query(ModuleVersion).filter_by(module_id="secret_mod", semver="v1.0").one()
    mv.lifecycle_band = "done"
    db.commit()
    r2 = client.put(
        "/api/v1/products/usp/hours",
        json={"feature_id": "secret_mod", "version": "v1.0", "hours": 3, "week": week},
    )
    assert r2.status_code == 403
    assert r2.json()["detail"] == "settled"


def test_hours_expected_max_64(client):
    login(client, "eng", "eng-secret")
    bad = client.put(
        "/api/v1/products/usp/hours",
        json={"feature_id": "biz", "version": "v1.0", "expected_hours": 65},
    )
    assert bad.status_code == 400
    ok = client.put(
        "/api/v1/products/usp/hours",
        json={"feature_id": "biz", "version": "v1.0", "expected_hours": 28},
    )
    assert ok.status_code == 200, ok.text
    page = client.get("/api/v1/products/usp/modules/biz").json()
    assert page["hours"]["expected_hours"] == 28
    assert any(u["user"] == "eng" and u["hours"] == 0 and u["expected_hours"] == 28 for u in page["hours"]["by_user"])
    logged = client.put(
        "/api/v1/products/usp/hours",
        json={"feature_id": "biz", "version": "v1.0", "hours": 3},
    )
    assert logged.status_code == 200, logged.text
    page2 = client.get("/api/v1/products/usp/modules/biz").json()
    assert page2["hours"]["week_invested"] == 3
    assert page2["hours"]["expected_hours"] == 28
    assert page2["hours"]["invested"] == 0
    assert any(u["user"] == "eng" and u["hours"] == 3 and u["expected_hours"] == 28 for u in page2["hours"]["by_user"])
    gone = client.put(
        "/api/v1/products/usp/hours",
        json={"feature_id": "biz", "version": "v1.0", "hours": 0},
    )
    assert gone.status_code == 200, gone.text
    page3 = client.get("/api/v1/products/usp/modules/biz").json()
    assert any(u["user"] == "eng" and u["hours"] == 0 and u["expected_hours"] == 28 for u in page3["hours"]["by_user"])
    assert page3["hours"]["week_invested"] == 0
    assert page3["hours"]["expected_hours"] == 28


def test_hours_singleton_module_expected(client, db):
    from app.services.hours import seed_singleton_expectations

    login(client, "eng", "eng-secret")
    db.add(
        HourAllocation(
            week=iso_week_id(),
            username="eng",
            product="usp",
            feature_id="biz",
            version="v1.0",
            hours=3,
        )
    )
    db.commit()
    assert seed_singleton_expectations(db) == 1
    db.commit()
    page = client.get("/api/v1/products/usp/modules/biz").json()
    mine = next(u for u in page["hours"]["by_user"] if u["user"] == "eng")
    assert mine["expected_hours"] == 24
    assert page["hours"]["expected_hours"] == 24
    login(client, "admin", "admin-secret")
    r2 = client.put(
        "/api/v1/products/usp/hours",
        json={"feature_id": "biz", "version": "v1.0", "expected_hours": 8, "hours": 2},
    )
    assert r2.status_code == 200, r2.text
    page2 = client.get("/api/v1/products/usp/modules/biz").json()
    by_user = {u["user"]: u for u in page2["hours"]["by_user"]}
    assert by_user["eng"]["expected_hours"] == 24
    assert by_user["admin"]["expected_hours"] == 8
    assert page2["hours"]["expected_hours"] == 32


def test_hours_zero_expectation_ignored_in_sum(client, db):
    from app.infra.models import HourExpectation

    db.add(
        HourExpectation(
            username="pm1",
            product="usp",
            feature_id="biz",
            version="v1.0",
            expected_hours=0,
        )
    )
    db.commit()
    login(client, "eng", "eng-secret")
    r = client.put(
        "/api/v1/products/usp/hours",
        json={"feature_id": "biz", "version": "v1.0", "expected_hours": 10, "hours": 3},
    )
    assert r.status_code == 200, r.text
    page = client.get("/api/v1/products/usp/modules/biz").json()
    assert page["hours"]["expected_hours"] == 10
    mine = next(u for u in page["hours"]["by_user"] if u["user"] == "eng")
    assert mine["expected_hours"] == 10
    assert not any(u["user"] == "pm1" for u in page["hours"]["by_user"])


def test_covers_hours(client, db):
    login(client, "eng", "eng-secret")
    week = iso_week_id()
    client.put(
        "/api/v1/products/usp/hours",
        json={"feature_id": "biz", "version": "v1.0", "expected_hours": 24, "hours": 5, "week": week},
    )
    r = client.get("/api/v1/products/usp/releases/usp_v100/hours")
    assert r.status_code == 200
    body = r.json()
    assert body["hours_mode"] == "covers_aggregate"
    assert body["week_invested"] >= 5
    assert body["invested"] == 0
    assert body["invested_total"] == 0
    assert "biz" in body["changelog_cover_ids"]
    assert body["remaining_hours"] == 24.0
    assert body["expected_hours"] == 24.0


def test_req_comment_gate(client):
    login(client, "pm1", "pm-secret")
    c = client.post("/api/v1/products/usp/requirements", json={"title": "t1", "markdown": "body"})
    assert c.status_code == 201
    rid = c.json()["id"]
    assert client.post(
        f"/api/v1/products/usp/requirements/{rid}/comments", json={"body": "please fix"}
    ).status_code == 201
    bad = client.patch(
        f"/api/v1/products/usp/requirements/{rid}", json={"markdown": "new"}
    )
    assert bad.status_code == 409


def test_req_pack_roundtrip_and_ack(client):
    import io
    import json
    import zipfile

    from tests.test_agent_pack import _rewrite

    login(client, "eng", "eng-secret")
    rid = client.post("/api/v1/products/usp/requirements", json={"title": "包需求"}).json()["id"]
    raw = client.get(f"/api/v1/products/usp/requirements/{rid}/pack").content
    z = zipfile.ZipFile(io.BytesIO(raw))
    md = z.read("AGENTS.md").decode("utf-8")
    assert md.strip().splitlines()[-1].startswith("校验码 ")
    pack = json.loads(z.read("PACK.json"))
    assert pack["kind"] == "requirement"
    assert pack["requirement_id"] == rid
    stale = md.rsplit("\n", 1)[0] + "\n校验码 0-deadbeef\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst:
        for name in z.namelist():
            dst.writestr(name, stale.encode("utf-8") if name == "AGENTS.md" else z.read(name))
    bad = client.post(f"/api/v1/products/usp/requirements/{rid}/pack", content=buf.getvalue())
    assert bad.status_code == 409
    ok = client.post(
        f"/api/v1/products/usp/requirements/{rid}/pack",
        content=_rewrite(raw, doc="包改"),
    )
    assert ok.status_code == 200, ok.text
    assert client.get(f"/api/v1/products/usp/requirements/{rid}").json()["markdown"] == "包改"


def test_req_agent_must_use_pack(client):
    from tests.test_agent_pack import _bearer, _rewrite

    login(client, "eng", "eng-secret")
    rid = client.post("/api/v1/products/usp/requirements", json={"title": "agent需"}).json()["id"]
    headers = _bearer(client)
    denied = client.patch(
        f"/api/v1/products/usp/requirements/{rid}", json={"markdown": "偷改"}, headers=headers
    )
    assert denied.status_code == 409
    created = client.post(
        "/api/v1/products/usp/requirements",
        json={"title": "带正文", "markdown": "不行"},
        headers=headers,
    )
    assert created.status_code == 409
    raw = client.get(f"/api/v1/products/usp/requirements/{rid}/pack", headers=headers).content
    r = client.post(
        f"/api/v1/products/usp/requirements/{rid}/pack",
        content=_rewrite(raw, doc="agent正文"),
        headers=headers,
    )
    assert r.status_code == 200, r.text
    login(client, "eng", "eng-secret")
    assert client.get(f"/api/v1/products/usp/requirements/{rid}").json()["markdown"] == "agent正文"


def test_req_status_machine(client):
    login(client, "pm1", "pm-secret")
    rid = client.post("/api/v1/products/usp/requirements", json={"title": "t2"}).json()["id"]
    r = client.patch(f"/api/v1/products/usp/requirements/{rid}", json={"status": "done"})
    assert r.status_code == 400


def test_req_review_all_pass_then_pending_link(client, db):
    from app.infra.models import User, UserProduct
    from app.services.auth import hash_password

    db.add(User(username="qa1", password_hash=hash_password("qa-secret"), role="tester"))
    db.add(UserProduct(username="qa1", product="usp"))
    db.commit()
    login(client, "pm1", "pm-secret")
    rid = client.post("/api/v1/products/usp/requirements", json={"title": "rev"}).json()["id"]
    people = client.get("/api/v1/products/usp/review-people").json()["items"]
    assert any(u["username"] == "eng" for u in people)
    bad = client.post(f"/api/v1/products/usp/requirements/{rid}/review", json={"usernames": []})
    assert bad.status_code == 400
    start = client.post(
        f"/api/v1/products/usp/requirements/{rid}/review", json={"usernames": ["eng", "qa1"]}
    )
    assert start.status_code == 200, start.text
    assert start.json()["status"] == "review"
    login(client, "eng", "eng-secret")
    inbox = client.get("/api/v1/inbox/reviews").json()
    assert any(i["id"] == rid for i in inbox["items"])
    no_reason = client.post(
        f"/api/v1/products/usp/requirements/{rid}/verdict", json={"verdict": "reject"}
    )
    assert no_reason.status_code == 400
    ok = client.post(f"/api/v1/products/usp/requirements/{rid}/verdict", json={"verdict": "pass"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "review"
    login(client, "qa1", "qa-secret")
    done = client.post(f"/api/v1/products/usp/requirements/{rid}/verdict", json={"verdict": "pass"})
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "pending_link"
    listing = client.get("/api/v1/products/usp/requirements").json()
    item = next(i for i in listing["items"] if i["id"] == rid)
    assert item["status"] == "pending_link"
    assert item["features"] == []
    empty = client.get("/api/v1/inbox/reviews").json()
    assert not any(i["id"] == rid for i in empty["items"])
    login(client, "pm1", "pm-secret")
    detail = client.get(f"/api/v1/products/usp/requirements/{rid}").json()
    assert detail["can_skip_link"] is True
    wait_link = client.get("/api/v1/inbox/pending-links").json()
    assert any(i["id"] == rid and i.get("can_skip") for i in wait_link["items"])
    login(client, "qa1", "qa-secret")
    qa_links = client.get("/api/v1/inbox/pending-links").json()
    assert not any(i["id"] == rid for i in qa_links["items"])
    login(client, "eng", "eng-secret")
    eng_links = client.get("/api/v1/inbox/pending-links").json()
    assert any(i["id"] == rid for i in eng_links["items"])
    assert not next(i for i in eng_links["items"] if i["id"] == rid).get("can_skip")
    denied = client.post(f"/api/v1/products/usp/requirements/{rid}/skip-link")
    assert denied.status_code == 403
    login(client, "pm1", "pm-secret")
    skipped = client.post(f"/api/v1/products/usp/requirements/{rid}/skip-link")
    assert skipped.status_code == 200, skipped.text
    assert skipped.json()["status"] == "done"
    gone = client.get("/api/v1/inbox/pending-links").json()
    assert not any(i["id"] == rid for i in gone["items"])
    again = client.post(f"/api/v1/products/usp/requirements/{rid}/skip-link")
    assert again.status_code == 400


def test_req_review_reject_returns_draft(client):
    login(client, "pm1", "pm-secret")
    rid = client.post("/api/v1/products/usp/requirements", json={"title": "no"}).json()["id"]
    client.post(f"/api/v1/products/usp/requirements/{rid}/review", json={"usernames": ["eng"]})
    login(client, "eng", "eng-secret")
    r = client.post(
        f"/api/v1/products/usp/requirements/{rid}/verdict",
        json={"verdict": "clarify", "reason": "缺接口"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "draft"
    login(client, "pm1", "pm-secret")
    detail = client.get(f"/api/v1/products/usp/requirements/{rid}").json()
    assert detail["status"] == "draft"
    assert detail["reviewers"][0]["reason"] == "缺接口"
    assert detail["review_rounds"][0]["outcome"] == "fail"
    assert detail["review_rounds"][0]["reviewers"][0]["reason"] == "缺接口"
    client.post(f"/api/v1/products/usp/requirements/{rid}/review", json={"usernames": ["eng"]})
    again = client.get(f"/api/v1/products/usp/requirements/{rid}").json()
    assert again["status"] == "review"
    assert len(again["review_rounds"]) == 2
    assert again["review_rounds"][0]["outcome"] == "fail"
    assert again["review_rounds"][0]["reviewers"][0]["reason"] == "缺接口"
    assert again["review_rounds"][1]["outcome"] is None
    assert again["review_rounds"][1]["reviewers"][0]["verdict"] is None


def test_req_inline_media_and_omit_markdown(client):
    png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    login(client, "pm1", "pm-secret")
    rid = client.post(
        "/api/v1/products/usp/requirements",
        json={"title": "shot", "markdown": f'<svg viewBox="0 0 1 1"><image href="data:image/png;base64,{png}"></image></svg>'},
    ).json()["id"]
    lite = client.get(f"/api/v1/products/usp/requirements/{rid}?include_markdown=false")
    assert lite.status_code == 200
    body = lite.json()
    assert body["markdown"] == ""
    assert "data:image/png;base64," not in body["html"]
    assert "/api/v1/inline-media/" in body["html"]
    sha = body["html"].split("/api/v1/inline-media/")[1].split('"')[0]
    media = client.get(f"/api/v1/inline-media/{sha}")
    assert media.status_code == 200
    assert media.content[:8] == b"\x89PNG\r\n\x1a\n"
    full = client.get(f"/api/v1/products/usp/requirements/{rid}").json()
    assert "data:image/png;base64," in full["markdown"]
    missing = client.get("/api/v1/inline-media/" + "a" * 64)
    assert missing.status_code == 404


def test_req_document_revision(client):
    login(client, "pm1", "pm-secret")
    rid = client.post("/api/v1/products/usp/requirements", json={"title": "rev", "markdown": "v1"}).json()["id"]
    empty = client.get(f"/api/v1/products/usp/requirements/{rid}/revisions")
    assert empty.status_code == 200
    assert empty.json()["items"] == []
    assert client.get(f"/api/v1/products/usp/requirements/{rid}").json()["revision_count"] == 0
    same = client.patch(f"/api/v1/products/usp/requirements/{rid}", json={"markdown": "v1"})
    assert same.status_code == 200
    assert client.get(f"/api/v1/products/usp/requirements/{rid}/revisions").json()["items"] == []
    changed = client.patch(f"/api/v1/products/usp/requirements/{rid}", json={"markdown": "v2"})
    assert changed.status_code == 200, changed.text
    revs = client.get(f"/api/v1/products/usp/requirements/{rid}/revisions")
    items = revs.json()["items"]
    assert len(items) == 1
    assert items[0]["created_at"]
    assert "T" in items[0]["created_at"]
    assert client.get(f"/api/v1/products/usp/requirements/{rid}").json()["revision_count"] == 1
    page = client.get(f"/api/v1/products/usp/requirements/{rid}").json()
    assert page["markdown"] == "v2"
    restore = client.post(
        f"/api/v1/products/usp/requirements/{rid}/revisions/restore",
        json={"revision_id": items[0]["id"]},
    )
    assert restore.status_code == 200, restore.text
    assert client.get(f"/api/v1/products/usp/requirements/{rid}").json()["markdown"] == "v1"
    after = client.get(f"/api/v1/products/usp/requirements/{rid}/revisions").json()["items"]
    assert len(after) == 2


def test_req_link_and_complete_and_hours(client, db):
    login(client, "pm1", "pm-secret")
    rid = client.post("/api/v1/products/usp/requirements", json={"title": "feat"}).json()["id"]
    client.post(f"/api/v1/products/usp/requirements/{rid}/review", json={"usernames": ["eng"]})
    login(client, "eng", "eng-secret")
    passed = client.post(f"/api/v1/products/usp/requirements/{rid}/verdict", json={"verdict": "pass"})
    assert passed.json()["status"] == "pending_link"
    wait_link = client.get("/api/v1/inbox/pending-links").json()
    assert any(i["id"] == rid for i in wait_link["items"])
    linked = client.put(
        "/api/v1/products/usp/features/secret_mod/requirements",
        json={"requirement_ids": [rid]},
    )
    assert linked.status_code == 200, linked.text
    gone = client.get("/api/v1/inbox/pending-links").json()
    assert not any(i["id"] == rid for i in gone["items"])
    listing = client.get("/api/v1/products/usp/requirements").json()
    item = next(i for i in listing["items"] if i["id"] == rid)
    assert item["status"] == "developing"
    assert any(f["id"] == "secret_mod" for f in item["features"])
    assert item["hours_ratio"] == 0
    assert item["hours_week"] == 0
    week = iso_week_id()
    client.put(
        "/api/v1/products/usp/hours",
        json={"feature_id": "secret_mod", "version": "v1.0", "expected_hours": 10, "hours": 2, "week": week},
    )
    listing2 = client.get("/api/v1/products/usp/requirements").json()
    item2 = next(i for i in listing2["items"] if i["id"] == rid)
    assert abs(item2["hours_ratio"] - 0.2) < 1e-6
    assert item2["hours_week"] == 2
    lc = client.put(
        "/api/v1/products/usp/modules/secret_mod/lifecycle",
        json={"band": "done", "progress": 100, "version": "v1.0"},
    )
    assert lc.status_code == 200, lc.text
    listing3 = client.get("/api/v1/products/usp/requirements").json()
    item3 = next(i for i in listing3["items"] if i["id"] == rid)
    assert item3["status"] == "done"
    drop = client.put(
        "/api/v1/products/usp/features/secret_mod/requirements",
        json={"requirement_ids": []},
    )
    assert drop.status_code == 200
    still = client.get("/api/v1/products/usp/features/secret_mod/requirements").json()["items"]
    assert any(x["id"] == rid for x in still)


def test_req_cannot_link_draft(client):
    login(client, "pm1", "pm-secret")
    rid = client.post("/api/v1/products/usp/requirements", json={"title": "drafty"}).json()["id"]
    login(client, "eng", "eng-secret")
    client.put("/api/v1/products/usp/features/secret_mod/requirements", json={"requirement_ids": [rid]})
    links = client.get("/api/v1/products/usp/features/secret_mod/requirements").json()["items"]
    assert not any(x["id"] == rid for x in links)


def test_req_body_encrypted(client, db, crypto):
    login(client, "pm1", "pm-secret")
    marker = "REQ_SECRET_TEXT"
    rid = client.post(
        "/api/v1/products/usp/requirements", json={"title": "t3", "markdown": marker}
    ).json()["id"]
    doc = db.get(RequirementDoc, rid)
    assert marker.encode() not in doc.body_blob
    assert crypto.decrypt_text(doc.body_blob, aad=f"requirement:{rid}") == marker


def test_req_list_meta_and_priority(client):
    login(client, "pm1", "pm-secret")
    rid = client.post("/api/v1/products/usp/requirements", json={"title": "prio"}).json()["id"]
    listing = client.get("/api/v1/products/usp/requirements").json()
    item = next(i for i in listing["items"] if i["id"] == rid)
    assert item["priority"] == 2
    assert item["created_by"] == "pm1"
    assert item["updated_at"]
    assert item["can_edit"] is True
    ok = client.patch(f"/api/v1/products/usp/requirements/{rid}", json={"priority": 0})
    assert ok.status_code == 200, ok.text
    assert ok.json()["priority"] == 0
    bad_n = client.patch(f"/api/v1/products/usp/requirements/{rid}", json={"priority": 9})
    assert bad_n.status_code == 400
    login(client, "eng", "eng-secret")
    listing2 = client.get("/api/v1/products/usp/requirements").json()
    item2 = next(i for i in listing2["items"] if i["id"] == rid)
    assert item2["priority"] == 0
    assert item2["can_edit"] is False
    denied = client.patch(f"/api/v1/products/usp/requirements/{rid}", json={"priority": 1})
    assert denied.status_code == 403


def test_req_delete_creator_and_admin(client):
    login(client, "pm1", "pm-secret")
    own = client.post("/api/v1/products/usp/requirements", json={"title": "mine"}).json()["id"]
    listing = client.get("/api/v1/products/usp/requirements").json()
    item = next(i for i in listing["items"] if i["id"] == own)
    assert item["can_delete"] is True
    login(client, "eng", "eng-secret")
    listing2 = client.get("/api/v1/products/usp/requirements").json()
    item2 = next(i for i in listing2["items"] if i["id"] == own)
    assert item2["can_delete"] is False
    assert client.delete(f"/api/v1/products/usp/requirements/{own}").status_code == 403
    login(client, "admin", "admin-secret")
    listing3 = client.get("/api/v1/products/usp/requirements").json()
    item3 = next(i for i in listing3["items"] if i["id"] == own)
    assert item3["can_delete"] is True
    gone = client.delete(f"/api/v1/products/usp/requirements/{own}")
    assert gone.status_code == 200, gone.text
    assert client.get(f"/api/v1/products/usp/requirements/{own}").status_code == 404
    login(client, "eng", "eng-secret")
    own2 = client.post("/api/v1/products/usp/requirements", json={"title": "eng-own"}).json()["id"]
    ok = client.delete(f"/api/v1/products/usp/requirements/{own2}")
    assert ok.status_code == 200
    assert client.get(f"/api/v1/products/usp/requirements/{own2}").status_code == 404


def test_req_tag_from_creator_role(client, db):
    from app.infra.models import User, UserProduct
    from app.services.auth import hash_password

    db.add(User(username="pjm1", password_hash=hash_password("pjm-secret"), role="pjm"))
    db.add(UserProduct(username="pjm1", product="usp"))
    db.commit()
    login(client, "pjm1", "pjm-secret")
    rid = client.post("/api/v1/products/usp/requirements", json={"title": "proj"}).json()["id"]
    item = next(i for i in client.get("/api/v1/products/usp/requirements").json()["items"] if i["id"] == rid)
    assert item["tag"] == "project"
    login(client, "pm1", "pm-secret")
    rid2 = client.post("/api/v1/products/usp/requirements", json={"title": "prod"}).json()["id"]
    item2 = next(i for i in client.get("/api/v1/products/usp/requirements").json()["items"] if i["id"] == rid2)
    assert item2["tag"] == "product"


def test_supervisor_can_create_own_requirement(client, db):
    import io
    import json
    import zipfile

    from tests.test_agent_pack import _rewrite

    db.add(User(username="boss", password_hash=hash_password("boss-secret"), role="supervisor"))
    db.add(UserProduct(username="boss", product="usp"))
    db.commit()
    login(client, "boss", "boss-secret")
    me = client.get("/api/v1/me").json()
    assert me["can_edit_requirements"] is True
    created = client.post("/api/v1/products/usp/requirements", json={"title": "主管需求"})
    assert created.status_code == 201, created.text
    rid = created.json()["id"]
    item = next(i for i in client.get("/api/v1/products/usp/requirements").json()["items"] if i["id"] == rid)
    assert item["tag"] == "product"
    assert item["can_edit"] is True
    assert item["can_delete"] is True
    raw = client.get(f"/api/v1/products/usp/requirements/{rid}/pack").content
    z = zipfile.ZipFile(io.BytesIO(raw))
    pack = json.loads(z.read("PACK.json"))
    assert pack["requirement_id"] == rid
    ok = client.post(
        f"/api/v1/products/usp/requirements/{rid}/pack",
        content=_rewrite(raw, doc="主管改"),
    )
    assert ok.status_code == 200, ok.text
    assert client.get(f"/api/v1/products/usp/requirements/{rid}").json()["markdown"] == "主管改"
    login(client, "pm1", "pm-secret")
    other = client.post("/api/v1/products/usp/requirements", json={"title": "产品需求"}).json()["id"]
    login(client, "boss", "boss-secret")
    denied = client.patch(f"/api/v1/products/usp/requirements/{other}", json={"markdown": "nope"})
    assert denied.status_code == 403
    assert client.get(f"/api/v1/products/usp/requirements/{other}/pack").status_code == 403


def test_covers_forbidden_for_guest(client):
    login(client, "guest", "guest-secret")
    r = client.put(
        "/api/v1/products/usp/releases/usp_v100/covers",
        json={"covers": [{"module_id": "biz", "version": "v1.0"}]},
    )
    assert r.status_code == 403


def test_covers_ok_engineer_dev(client):
    login(client, "eng", "eng-secret")
    r = client.put(
        "/api/v1/products/usp/releases/usp_v100/covers",
        json={"covers": [{"module_id": "secret_mod", "version": "v1.0"}]},
    )
    assert r.status_code == 200
    listing = client.get("/api/v1/products/usp/releases")
    covers = listing.json()["versions"][0]["covers"]
    assert any(c["module_id"] == "secret_mod" for c in covers)


def test_release_notes_roundtrip(client, db, crypto):
    login(client, "eng", "eng-secret")
    r = client.put(
        "/api/v1/products/usp/releases/usp_v100/notes",
        json={"added": "NOTE_PLAINTEXT_MARKER", "fixed": "", "remark": "r"},
    )
    assert r.status_code == 200
    got = client.get("/api/v1/products/usp/releases/usp_v100")
    assert got.json()["added"] == "NOTE_PLAINTEXT_MARKER"
    from app.infra.models import ProductRelease

    row = db.get(ProductRelease, "usp_v100")
    assert row.added_blob is not None
    assert b"NOTE_PLAINTEXT_MARKER" not in row.added_blob


def test_hours_list(client):
    login(client, "eng", "eng-secret")
    week = iso_week_id()
    client.put(
        "/api/v1/products/usp/hours",
        json={"feature_id": "biz", "version": "v1.0", "hours": 1, "expected_hours": 24, "week": week},
    )
    r = client.get("/api/v1/products/usp/hours")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert "week" in body
    assert body["can_log"] is True
    assert body["cap"] == 40
    assert any(f["id"] == "biz" and f["title"] == "业务分配" for f in body["features"])
    hit = next(i for i in body["items"] if i["feature_id"] == "biz")
    assert hit["title"] == "业务分配"


def test_hours_history(client):
    login(client, "eng", "eng-secret")
    week = iso_week_id()
    client.put(
        "/api/v1/products/usp/hours",
        json={"feature_id": "biz", "version": "v1.0", "hours": 4, "expected_hours": 24, "week": week},
    )
    r = client.get("/api/v1/products/usp/hours/history")
    assert r.status_code == 200
    body = r.json()
    assert body["week"] == week
    assert body["cap"] == 40
    cur = next(i for i in body["items"] if i["week"] == week)
    assert cur["open"] is True
    assert cur["total"] == 4
    assert cur["by_user"][0]["user"] == "eng"
    assert cur["by_user"][0]["hours"] == 4
    feat = cur["by_user"][0]["features"][0]
    assert feat["id"] == "biz"
    assert feat["title"] == "业务分配"
    assert feat["version"] == "v1.0"
    assert feat["hours"] == 4
    start, end = week_bounds(week)
    assert cur["start"] == start.isoformat()
    assert cur["end"] == end.isoformat()


def test_release_tags_use_semver(client):
    login(client, "admin", "admin-secret")
    r = client.get("/api/v1/products/usp/releases")
    assert r.status_code == 200
    tags = r.json()["tags"]
    assert tags["dev"]["id"] == "usp_v100"
    assert tags["dev"]["semver"] == "v1.0.0"


def test_hours_register_and_week_save(client):
    login(client, "eng", "eng-secret")
    week = iso_week_id()
    r = client.get("/api/v1/products/usp/hours/register")
    assert r.status_code == 200
    ids = {f["id"] for f in r.json()["features"]}
    assert "biz" in ids
    assert "secret_mod" in ids
    client.put(
        "/api/v1/products/usp/hours",
        json={"feature_id": "biz", "version": "v1.0", "expected_hours": 24},
    )
    r2 = client.put(
        "/api/v1/products/usp/hours/week",
        json={
            "week": week,
            "allocations": [{"feature_id": "biz", "version": "v1.0", "hours": 3}],
            "remaining": [{"feature_id": "biz", "version": "v1.0", "remaining_hours": 10}],
        },
    )
    assert r2.status_code == 200, r2.text
    reg = client.get(f"/api/v1/products/usp/hours/register?week={week}").json()
    biz = next(f for f in reg["features"] if f["id"] == "biz")
    assert biz["my_week"] == 3
    assert biz["remaining_hours"] == 10.0


def test_lifecycle_save(client):
    login(client, "eng", "eng-secret")
    r = client.put(
        "/api/v1/products/usp/modules/secret_mod/lifecycle",
        json={"band": "test", "progress": 40},
    )
    assert r.status_code == 200, r.text
    page = client.get("/api/v1/products/usp/modules/secret_mod").json()
    assert page["lifecycle"]["band"] == "test"
    assert page["can_edit_lifecycle"] is True
    login(client, "guest", "guest-secret")
    r2 = client.put(
        "/api/v1/products/usp/modules/biz/lifecycle",
        json={"band": "dev", "progress": 10},
    )
    assert r2.status_code == 403


def test_admin_users_forbidden(client):
    login(client, "eng", "eng-secret")
    assert client.get("/api/v1/admin/users").status_code == 403


def test_supervisor_cannot_manage_users_or_audit(client, db):
    db.add(User(username="boss", password_hash=hash_password("boss-secret"), role="supervisor"))
    db.add(UserProduct(username="boss", product="usp"))
    db.commit()
    login(client, "boss", "boss-secret")
    me = client.get("/api/v1/me").json()
    assert me["can_manage_users"] is False
    assert client.get("/api/v1/admin/users").status_code == 403
    assert client.get("/api/v1/admin/audit").status_code == 403
    assert client.get("/api/v1/admin/visits").status_code == 403


def test_admin_users_grant(client):
    login(client, "admin", "admin-secret")
    r = client.get("/api/v1/admin/users")
    assert r.status_code == 200
    names = {u["username"] for u in r.json()["users"]}
    assert "eng" in names
    cat = client.get("/api/v1/admin/catalog?product=usp")
    assert cat.status_code == 200
    assert any(m["id"] == "biz" for m in cat.json()["modules"])
    r2 = client.post(
        "/api/v1/admin/users",
        json={
            "username": "eng",
            "role": "engineer",
            "products": ["usp", "ors"],
            "module_perms": {"usp:*": "write"},
        },
    )
    assert r2.status_code == 200, r2.text
    assert "ors" in r2.json()["user"]["products"]
    assert r2.json()["user"]["module_perms"].get("usp:*") == "write"
    created = client.post(
        "/api/v1/admin/users",
        json={
            "username": "neweng",
            "password": "neweng-secret",
            "role": "engineer",
            "products": ["usp"],
            "module_perms": {"biz": "write"},
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["user"]["username"] == "neweng"


def test_admin_catalog_tree_hides_product_hub(client):
    login(client, "admin", "admin-secret")
    cat = client.get("/api/v1/admin/catalog?product=usp").json()
    algo = next(b for b in cat["tree"] if b["id"] == "algo")
    ids = {n["id"] for n in algo["nodes"]}
    assert "biz" in ids
    assert "secret_mod" in ids
    assert "usp_algo" not in ids
    assert "usp_dev" not in ids


def test_admin_catalog_tree_nests_children(client, db, crypto):
    login(client, "admin", "admin-secret")
    host = db.query(Module).filter_by(product="usp", id="biz").one()
    host.nested_board = "boards/biz.json"
    db.add(
        Board(
            product="usp",
            rel_path="boards/biz.json",
            doc={"nodes": [{"id": "biz_kid", "x": 0, "y": 0}], "groups": [], "edges": []},
        )
    )
    db.add(
        Module(
            id="biz_kid",
            product="usp",
            board_rel="boards/biz.json",
            kind="feature",
            title_blob=crypto.encrypt("子", aad="module.title:usp:biz_kid"),
            current_version="v1.0",
        )
    )
    db.commit()
    cat = client.get("/api/v1/admin/catalog?product=usp").json()
    algo = next(b for b in cat["tree"] if b["id"] == "algo")
    biz = next(n for n in algo["nodes"] if n["id"] == "biz")
    assert {c["id"] for c in biz["children"]} == {"biz_kid"}


def test_group_grant_cascades_write(client, db, crypto):
    from sqlalchemy.orm.attributes import flag_modified

    login(client, "eng", "eng-secret")
    denied = client.put("/api/v1/products/usp/modules/biz/document", json={"markdown": "nope"})
    assert denied.status_code == 403
    login(client, "admin", "admin-secret")
    b = db.query(Board).filter_by(product="usp", rel_path="board.json").one()
    doc = dict(b.doc)
    groups = list(doc.get("groups") or [])
    groups.append({"id": "g_family", "title": "族", "x": 0, "y": 0, "w": 200, "h": 80, "members": ["biz"]})
    doc["groups"] = groups
    b.doc = doc
    flag_modified(b, "doc")
    db.add(
        Module(
            id="g_family",
            product="usp",
            board_rel="board.json",
            kind="group",
            title_blob=crypto.encrypt("族", aad="module.title:usp:g_family"),
            current_version="v1.0",
        )
    )
    db.commit()
    granted = client.post(
        "/api/v1/admin/users",
        json={
            "username": "eng",
            "role": "engineer",
            "products": ["usp"],
            "module_perms": {"g_family": "write"},
        },
    )
    assert granted.status_code == 200, granted.text
    assert granted.json()["user"]["module_perms"].get("g_family") == "write"
    login(client, "eng", "eng-secret")
    ok = client.put("/api/v1/products/usp/modules/biz/document", json={"markdown": "from-group"})
    assert ok.status_code == 200, ok.text


def test_pack_import_roundtrip(client):
    login(client, "eng", "eng-secret")
    z = client.get("/api/v1/products/usp/packs/secret_mod")
    assert z.status_code == 200
    r = client.post(
        "/api/v1/products/usp/packs/secret_mod",
        content=z.content,
        headers={"Content-Type": "application/zip"},
    )
    assert r.status_code == 200, r.text
    login(client, "guest", "guest-secret")
    r2 = client.post(
        "/api/v1/products/usp/packs/secret_mod",
        content=z.content,
        headers={"Content-Type": "application/zip"},
    )
    assert r2.status_code == 403


def test_admin_updates_pack_and_lifecycle(client, db):
    login(client, "eng", "eng-secret")
    lc = client.put(
        "/api/v1/products/usp/modules/secret_mod/lifecycle",
        json={"band": "test", "progress": 40},
    )
    assert lc.status_code == 200, lc.text
    z = client.get("/api/v1/products/usp/packs/secret_mod")
    assert z.status_code == 200
    imp = client.post(
        "/api/v1/products/usp/packs/secret_mod",
        content=z.content,
        headers={"Content-Type": "application/zip"},
    )
    assert imp.status_code == 200, imp.text
    login(client, "guest", "guest-secret")
    assert client.get("/api/v1/admin/audit").status_code == 403
    login(client, "admin", "admin-secret")
    body = client.get("/api/v1/admin/audit").json()
    actions = {i["action"] for i in body["items"]}
    assert "module.lifecycle" in actions
    assert "pack.import" in actions
    hit = next(i for i in body["items"] if i["action"] == "module.lifecycle")
    assert hit["action_label"] == "状态/进度"
    assert hit["title"]
    pack = next(i for i in body["items"] if i["action"] == "pack.import")
    assert pack["action_label"] == "导入工程包"
    g0 = client.get("/api/v1/products/usp/graph").json()
    nodes = list(g0["nodes"]) + [{"id": "new_feat", "x": 0, "y": 80, "title": "新功能"}]
    saved = client.put(
        "/api/v1/products/usp/boards/board.json",
        json={"doc": {"node_w": 158, "node_h": 46, "nodes": nodes, "groups": g0["groups"], "edges": g0["edges"]}},
    )
    assert saved.status_code == 200, saved.text
    be = client.put(
        "/api/v1/products/usp/boards/boards/usp_backend.json",
        json={"doc": {"nodes": [{"id": "be_a", "x": 0, "y": 0, "title": "接口"}], "groups": [], "edges": []}},
    )
    assert be.status_code == 200, be.text
    host = db.query(Module).filter_by(product="usp", id="biz").one()
    host.nested_board = "boards/biz.json"
    db.add(
        Board(
            product="usp",
            rel_path="boards/biz.json",
            doc={"nodes": [{"id": "child_a", "x": 0, "y": 0}], "groups": [], "edges": []},
        )
    )
    db.add(
        AuditLog(
            actor="admin",
            action="board.save",
            product="usp",
            resource="board.json",
            detail={"title": "board.json", "summary": "boards/board.json"},
        )
    )
    db.commit()
    nest = client.put(
        "/api/v1/products/usp/boards/boards/biz.json",
        json={
            "doc": {
                "nodes": [
                    {"id": "child_a", "x": 0, "y": 0, "title": "子项"},
                    {"id": "child_b", "x": 40, "y": 0, "title": "另一项"},
                ],
                "groups": [],
                "edges": [{"from": "child_a", "to": "child_b"}],
            }
        },
    )
    assert nest.status_code == 200, nest.text
    body2 = client.get("/api/v1/admin/audit").json()
    blob = json.dumps(body2["items"], ensure_ascii=False)
    assert "board.json" not in blob
    assert ".json" not in blob
    assert "boards/" not in blob
    assert "resource" not in body2["items"][0]
    topo_items = [i for i in body2["items"] if i["action"] == "board.save"]
    assert all(i["title"] and "json" not in (i["title"] + (i["href"] or "")).lower() for i in topo_items)
    algo_rows = [i for i in topo_items if i["href"] == "/usp/editor"]
    assert algo_rows
    assert all(i["title"] == "USP · 算法" for i in algo_rows)
    assert any("新增" in (i["summary"] or "") for i in algo_rows)
    backend = next(i for i in topo_items if i["href"] == "/usp/editor?domain=backend")
    assert backend["title"] == "USP · 后端"
    nested = next(i for i in topo_items if i["href"] == "/usp/m/biz")
    assert nested["title"] == "业务分配"
    assert "新增" in nested["summary"]


def test_audit_rolls_after_seven_days(client, db):
    from datetime import datetime, timedelta, timezone

    login(client, "admin", "admin-secret")
    stale = AuditLog(
        actor="admin",
        action="module.lifecycle",
        product="usp",
        resource="stale_keep_check",
        detail={"title": "过期记录"},
        at=datetime.now(timezone.utc) - timedelta(days=8),
    )
    fresh = AuditLog(
        actor="admin",
        action="module.lifecycle",
        product="usp",
        resource="fresh_keep_check",
        detail={"title": "一周内记录"},
        at=datetime.now(timezone.utc) - timedelta(days=6),
    )
    db.add_all([stale, fresh])
    db.commit()
    stale_id, fresh_id = stale.id, fresh.id
    body = client.get("/api/v1/admin/audit").json()
    assert body["days"] == 7
    titles = [i["title"] for i in body["items"]]
    assert "过期记录" not in titles
    assert "一周内记录" in titles
    db.expire_all()
    assert db.get(AuditLog, stale_id) is None
    assert db.get(AuditLog, fresh_id) is not None


def test_document_revision(client):
    login(client, "eng", "eng-secret")
    r = client.put(
        "/api/v1/products/usp/modules/secret_mod/document",
        json={"markdown": "page-edit-body"},
    )
    assert r.status_code == 200
    empty = client.get("/api/v1/products/usp/modules/secret_mod/revisions")
    assert empty.status_code == 200
    assert empty.json()["items"] == []
    assert client.get("/api/v1/products/usp/modules/secret_mod").json()["revision_count"] == 0
    z = client.get("/api/v1/products/usp/packs/secret_mod")
    assert z.status_code == 200
    imp = client.post(
        "/api/v1/products/usp/packs/secret_mod",
        content=z.content,
        headers={"Content-Type": "application/zip"},
    )
    assert imp.status_code == 200, imp.text
    still = client.get("/api/v1/products/usp/modules/secret_mod/revisions")
    assert still.json()["items"] == []
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        with zipfile.ZipFile(io.BytesIO(z.content)) as src:
            for name in src.namelist():
                data = src.read(name)
                if name.endswith("/doc.md") or name == "doc.md":
                    data = b"agent-upload-body"
                zf.writestr(name, data)
    uploaded = client.post(
        "/api/v1/products/usp/packs/secret_mod",
        content=buf.getvalue(),
        headers={"Content-Type": "application/zip"},
    )
    assert uploaded.status_code == 200, uploaded.text
    revs = client.get("/api/v1/products/usp/modules/secret_mod/revisions")
    assert revs.status_code == 200
    items = revs.json()["items"]
    assert items
    page = client.get("/api/v1/products/usp/modules/secret_mod").json()
    assert page["revision_count"] == len(items)
    restore = client.post(
        "/api/v1/products/usp/modules/secret_mod/revisions/restore",
        json={"revision_id": items[0]["id"]},
    )
    assert restore.status_code == 200, restore.text
    page = client.get("/api/v1/products/usp/modules/secret_mod").json()
    assert page["markdown"] == "page-edit-body"
    after = client.get("/api/v1/products/usp/modules/secret_mod/revisions")
    assert len(after.json()["items"]) == len(items)
    assert client.get("/api/v1/products/usp/modules/secret_mod").json()["revision_count"] == len(items)


def test_document_snapshot_creates_revision(client):
    login(client, "eng", "eng-secret")
    r = client.put(
        "/api/v1/products/usp/modules/secret_mod/document",
        json={"markdown": "fig-edit-body", "snapshot": True},
    )
    assert r.status_code == 200
    items = client.get("/api/v1/products/usp/modules/secret_mod/revisions").json()["items"]
    assert len(items) == 1


def test_guest_cannot_assign_tags(client):
    login(client, "guest", "guest-secret")
    r = client.put("/api/v1/products/usp/tags", json={"stable": "usp_v100"})
    assert r.status_code == 403


def test_tag_assign_updates_graph(client):
    login(client, "admin", "admin-secret")
    g = client.get("/api/v1/products/usp/graph").json()
    ids = {n["id"] for n in g["nodes"]}
    assert "usp_dev" in ids
    assert "usp_stable" in ids
    assert "usp_v100" not in ids
    dev = next(n for n in g["nodes"] if n["id"] == "usp_dev")
    assert dev["title"] == "DEV"
    assert dev["version"] == "v1.0.0"
    assert not dev["tag_unassigned"]
    st = next(n for n in g["nodes"] if n["id"] == "usp_stable")
    assert st["title"] == "STABLE"
    assert st["tag_unassigned"] is True
    assert not st["version"]
    r = client.put("/api/v1/products/usp/tags", json={"stable": "usp_v100", "dev": "usp_v100"})
    assert r.status_code == 200, r.text
    g2 = client.get("/api/v1/products/usp/graph").json()
    st2 = next(n for n in g2["nodes"] if n["id"] == "usp_stable")
    assert st2["tag_unassigned"] is False
    assert st2["version"] == "v1.0.0"


def test_product_page_follows_tag(client):
    login(client, "eng", "eng-secret")
    client.put(
        "/api/v1/products/usp/releases/usp_v100/notes",
        json={"added": "REL_ADDED", "fixed": "REL_FIXED", "remark": "REL_NOTE"},
    )
    page = client.get("/api/v1/products/usp/modules/usp_dev").json()
    assert page["kind"] == "product_release"
    assert page["tag"] == "dev"
    assert page["release_id"] == "usp_v100"
    assert page["added"] == "REL_ADDED"
    assert page["hours"]["hours_mode"] == "covers_aggregate"
    assert page["can_log_hours"] is False
    assert page["can_edit_covers"] is True
    assert page["can_publish"] is False
    assert page["lifecycle"]["band"] == "dev"
    assert page["lifecycle"]["label"] == "开发中"
    biz = next(c for c in page["cover_items"] if c["module_id"] == "biz")
    assert biz["week_invested"] == 0
    week = iso_week_id()
    logged = client.put(
        "/api/v1/products/usp/hours",
        json={"feature_id": "biz", "version": "v1.0", "expected_hours": 24, "hours": 8, "week": week},
    )
    assert logged.status_code == 200, logged.text
    page2 = client.get("/api/v1/products/usp/modules/usp_dev").json()
    biz2 = next(c for c in page2["cover_items"] if c["module_id"] == "biz")
    assert biz2["week_invested"] == 8
    empty = client.get("/api/v1/products/usp/modules/usp_stable").json()
    assert empty["tag_unassigned"] is True
    assert not empty["release_id"]


def test_cover_tree_groups_and_nested(client, db, crypto):
    from sqlalchemy.orm.attributes import flag_modified

    login(client, "eng", "eng-secret")
    b = db.query(Board).filter_by(product="usp", rel_path="board.json").one()
    doc = dict(b.doc)
    nodes = list(doc.get("nodes") or [])
    if not any(n.get("id") == "nest_host" for n in nodes):
        nodes.append({"id": "nest_host", "x": 40, "y": 40})
    doc["nodes"] = nodes
    groups = list(doc.get("groups") or [])
    groups.append({"id": "g_line", "title": "线类", "surface": "line", "members": ["biz", "nest_host"]})
    doc["groups"] = groups
    b.doc = doc
    flag_modified(b, "doc")
    db.add(
        Module(
            id="nest_host",
            product="usp",
            board_rel="board.json",
            kind="feature",
            nested_board="boards/nest.json",
            title_blob=crypto.encrypt("嵌套宿主", aad="module.title:usp:nest_host"),
            current_version="v1.0",
        )
    )
    db.add(ModuleVersion(product="usp", module_id="nest_host", semver="v1.0", lifecycle_band="dev"))
    db.add(
        Board(
            product="usp",
            rel_path="boards/nest.json",
            doc={"nodes": [{"id": "nest_child", "x": 0, "y": 0}], "groups": [], "edges": []},
        )
    )
    db.add(
        Module(
            id="nest_child",
            product="usp",
            board_rel="boards/nest.json",
            kind="feature",
            title_blob=crypto.encrypt("嵌套子", aad="module.title:usp:nest_child"),
            current_version="v1.0",
        )
    )
    db.add(ModuleVersion(product="usp", module_id="nest_child", semver="v1.0", lifecycle_band="dev"))
    db.commit()
    tree = client.get("/api/v1/products/usp/modules/usp_dev").json()["cover_tree"]
    g = next(n for n in tree if n["id"] == "g_line")
    child_ids = [c["id"] for c in g["children"]]
    assert "biz" in child_ids
    host = next(c for c in g["children"] if c["id"] == "nest_host")
    assert host["coverable"] is True
    assert any(c["id"] == "nest_child" for c in host["children"])
    assert g["coverable"] is False


def test_cover_tree_group_host_is_coverable(client, db, crypto):
    from sqlalchemy.orm.attributes import flag_modified

    login(client, "eng", "eng-secret")
    b = db.query(Board).filter_by(product="usp", rel_path="board.json").one()
    doc = dict(b.doc)
    nodes = list(doc.get("nodes") or [])
    if not any(n.get("id") == "zone_host" for n in nodes):
        nodes.append({"id": "zone_host", "x": 48, "y": 48})
    doc["nodes"] = nodes
    b.doc = doc
    flag_modified(b, "doc")
    db.add(
        Module(
            id="zone_host",
            product="usp",
            board_rel="board.json",
            kind="group",
            nested_board="boards/zone_host.json",
            title_blob=crypto.encrypt("库区实体", aad="module.title:usp:zone_host"),
            current_version="v1.0",
        )
    )
    db.add(ModuleVersion(product="usp", module_id="zone_host", semver="v1.0", lifecycle_band="dev"))
    db.add(ModuleVersion(product="usp", module_id="zone_host", semver="v1.1", lifecycle_band="dev"))
    db.add(
        Board(
            product="usp",
            rel_path="boards/zone_host.json",
            doc={"nodes": [{"id": "zone_kid", "x": 0, "y": 0}], "groups": [], "edges": []},
        )
    )
    db.add(
        Module(
            id="zone_kid",
            product="usp",
            board_rel="boards/zone_host.json",
            kind="feature",
            title_blob=crypto.encrypt("库位配置", aad="module.title:usp:zone_kid"),
            current_version="v1.0",
        )
    )
    db.add(ModuleVersion(product="usp", module_id="zone_kid", semver="v1.0", lifecycle_band="dev"))
    db.commit()
    tree = client.get("/api/v1/products/usp/modules/usp_dev").json()["cover_tree"]

    def find(nodes, mid):
        for n in nodes or []:
            if n["id"] == mid:
                return n
            hit = find(n.get("children"), mid)
            if hit:
                return hit
        return None

    host = find(tree, "zone_host")
    assert host is not None
    assert host["kind"] == "group"
    assert host["coverable"] is True
    assert "v1.1" in host["versions"]
    r = client.put(
        "/api/v1/products/usp/releases/usp_v100/covers",
        json={"covers": [{"module_id": "zone_host", "version": "v1.1"}]},
    )
    assert r.status_code == 200, r.text
    page = client.get("/api/v1/products/usp/modules/usp_dev").json()
    assert any(c["module_id"] == "zone_host" and c["version"] == "v1.1" for c in page["cover_items"])


def test_document_heading_must_match_title(client):
    login(client, "admin", "admin-secret")
    bad = client.put(
        "/api/v1/products/usp/modules/biz/document",
        json={"markdown": "# 库区分支\n\n正文"},
    )
    assert bad.status_code == 400, bad.text
    assert bad.json()["detail"] == "title_mismatch"
    ok = client.put(
        "/api/v1/products/usp/modules/biz/document",
        json={"markdown": "# 功能 · 业务分配\n\n正文"},
    )
    assert ok.status_code == 200, ok.text
    page = client.get("/api/v1/products/usp/modules/biz").json()
    assert page["markdown"].startswith("# 功能 · 业务分配")


def test_changelog_vs_predecessor(client, db):
    from app.infra.models import ProductRelease, ReleaseCover

    login(client, "admin", "admin-secret")
    db.add(ProductRelease(id="usp_v090", product="usp", semver="v0.9.0", sort=-1))
    db.add(ReleaseCover(release_id="usp_v090", module_id="biz", module_version="v1.0"))
    db.commit()
    created = client.post(
        "/api/v1/products/usp/releases",
        json={"semver": "v2.0.0", "copy_from": "usp_v100"},
    )
    assert created.status_code == 201, created.text
    rid = created.json()["id"]
    client.put(
        f"/api/v1/products/usp/releases/{rid}/covers",
        json={"covers": [{"module_id": "biz", "version": "v1.0"}, {"module_id": "secret_mod", "version": "v1.0"}]},
    )
    got = client.get(f"/api/v1/products/usp/releases/{rid}").json()
    mids = {r["module_id"]: r for r in got["changelog"]}
    assert "secret_mod" in mids
    assert mids["secret_mod"]["kind"] == "new"
    notes = client.put(
        f"/api/v1/products/usp/releases/{rid}/changelog-notes",
        json={"notes": [{"module_id": "secret_mod", "version": "v1.0", "note": "CL_NOTE"}]},
    )
    assert notes.status_code == 200, notes.text
    assert any(r["note"] == "CL_NOTE" for r in notes.json()["changelog"])


def test_unpublished_release_can_delete(client, db):
    from app.infra.models import ProductRelease, ProductTag, ReleaseCover, ReleaseNote

    login(client, "guest", "guest-secret")
    created = client.post("/api/v1/products/usp/releases", json={"semver": "v3.0.0"})
    assert created.status_code == 403
    login(client, "admin", "admin-secret")
    listing = client.get("/api/v1/products/usp/releases").json()
    assert listing["can_delete"] is True
    created = client.post("/api/v1/products/usp/releases", json={"semver": "v3.0.0"})
    assert created.status_code == 201, created.text
    rid = created.json()["id"]
    client.put(
        f"/api/v1/products/usp/releases/{rid}/covers",
        json={"covers": [{"module_id": "biz", "version": "v1.0"}]},
    )
    client.put("/api/v1/products/usp/tags", json={"latest": rid})
    db.add(ReleaseNote(release_id=rid, module_id="biz", relative="new"))
    db.commit()
    gone = client.delete(f"/api/v1/products/usp/releases/{rid}")
    assert gone.status_code == 200, gone.text
    ids = {v["id"] for v in client.get("/api/v1/products/usp/releases").json()["versions"]}
    assert rid not in ids
    assert db.get(ProductRelease, rid) is None
    assert db.query(ProductTag).filter_by(release_id=rid).count() == 0
    assert db.query(ReleaseCover).filter_by(release_id=rid).count() == 0
    assert db.query(ReleaseNote).filter_by(release_id=rid).count() == 0
    login(client, "eng", "eng-secret")
    draft = client.post("/api/v1/products/usp/releases", json={"semver": "v3.1.0"})
    assert draft.status_code == 201, draft.text
    rid2 = draft.json()["id"]
    assert client.delete(f"/api/v1/products/usp/releases/{rid2}").status_code == 200
    login(client, "admin", "admin-secret")
    frozen = client.post("/api/v1/products/usp/releases", json={"semver": "v4.0.0"})
    assert frozen.status_code == 201, frozen.text
    rid3 = frozen.json()["id"]
    row = db.get(ProductRelease, rid3)
    from datetime import datetime, timezone

    row.published_at = datetime.now(timezone.utc)
    db.commit()
    blocked = client.delete(f"/api/v1/products/usp/releases/{rid3}")
    assert blocked.status_code == 400
    login(client, "guest", "guest-secret")
    assert client.delete("/api/v1/products/usp/releases/usp_v100").status_code == 403


def test_snake_duration_formula():
    from app.services.hours import snake_duration_sec

    assert snake_duration_sec(24, 0) is None
    assert snake_duration_sec(24, 8) == 1.35
    assert snake_duration_sec(16, 8) == 1.25
    assert snake_duration_sec(None, 4) == 1.25


def test_hours_snake_summary(client):
    login(client, "eng", "eng-secret")
    week = iso_week_id()
    empty = client.get("/api/v1/products/usp/hours/summary").json()
    assert empty["ok"] is True
    assert empty["features"]["biz"]["duration"] is None
    r = client.put(
        "/api/v1/products/usp/hours",
        json={"feature_id": "biz", "version": "v1.0", "expected_hours": 24, "hours": 8, "week": week},
    )
    assert r.status_code == 200, r.text
    s = client.get("/api/v1/products/usp/hours/summary").json()
    biz = s["features"]["biz"]
    assert biz["invested"] == 8
    assert biz["duration"] == 1.25
    assert s["features"]["usp_dev"]["duration"] == 1.25
    login(client, "guest", "guest-secret")
    g = client.get("/api/v1/products/usp/hours/summary").json()
    assert "biz" in g["features"]
    assert "secret_mod" not in g["features"]
    assert g["features"]["biz"]["duration"] == 1.25


def test_nested_host_own_hours_count_in_snake(client, db, crypto):
    from sqlalchemy.orm.attributes import flag_modified

    from app.services.hours import snake_duration_sec

    login(client, "eng", "eng-secret")
    b = db.query(Board).filter_by(product="usp", rel_path="board.json").one()
    doc = dict(b.doc)
    nodes = list(doc.get("nodes") or [])
    if not any(n.get("id") == "snake_host" for n in nodes):
        nodes.append({"id": "snake_host", "x": 12, "y": 12})
    doc["nodes"] = nodes
    b.doc = doc
    flag_modified(b, "doc")
    db.add(
        Module(
            id="snake_host",
            product="usp",
            board_rel="board.json",
            kind="feature",
            nested_board="boards/snake_host.json",
            title_blob=crypto.encrypt("珠子宿主", aad="module.title:usp:snake_host"),
            current_version="v1.0",
        )
    )
    db.add(ModuleVersion(product="usp", module_id="snake_host", semver="v1.0", lifecycle_band="dev", expected_hours=40))
    db.add(
        Board(
            product="usp",
            rel_path="boards/snake_host.json",
            doc={"nodes": [{"id": "snake_child", "x": 0, "y": 0}], "groups": [], "edges": []},
        )
    )
    db.add(
        Module(
            id="snake_child",
            product="usp",
            board_rel="boards/snake_host.json",
            kind="feature",
            title_blob=crypto.encrypt("珠子子", aad="module.title:usp:snake_child"),
            current_version="v1.0",
        )
    )
    db.add(ModuleVersion(product="usp", module_id="snake_child", semver="v1.0", lifecycle_band="dev", expected_hours=24))
    db.commit()
    week = iso_week_id()
    host_log = client.put(
        "/api/v1/products/usp/hours",
        json={"feature_id": "snake_host", "version": "v1.0", "expected_hours": 40, "hours": 8, "week": week},
    )
    assert host_log.status_code == 200, host_log.text
    s = client.get("/api/v1/products/usp/hours/summary").json()
    host = s["features"]["snake_host"]
    assert host["invested"] == 8
    assert host["duration"] == snake_duration_sec(56, 8)
    child_log = client.put(
        "/api/v1/products/usp/hours",
        json={"feature_id": "snake_child", "version": "v1.0", "expected_hours": 24, "hours": 4, "week": week},
    )
    assert child_log.status_code == 200, child_log.text
    s2 = client.get("/api/v1/products/usp/hours/summary").json()
    host2 = s2["features"]["snake_host"]
    assert host2["invested"] == 12
    assert host2["duration"] == snake_duration_sec(52, 12)
    page = client.get("/api/v1/products/usp/modules/snake_host").json()
    assert page["hours"]["week_invested"] == 8
    child = next(c for c in page["hours"]["by_child"] if c["id"] == "snake_child")
    assert child["hours"] == 4


def test_nested_group_own_hours_count_in_snake(client, db, crypto):
    from sqlalchemy.orm.attributes import flag_modified

    from app.services.hours import snake_duration_sec

    login(client, "eng", "eng-secret")
    b = db.query(Board).filter_by(product="usp", rel_path="board.json").one()
    doc = dict(b.doc)
    nodes = list(doc.get("nodes") or [])
    if not any(n.get("id") == "assist_host" for n in nodes):
        nodes.append({"id": "assist_host", "x": 18, "y": 18})
    doc["nodes"] = nodes
    b.doc = doc
    flag_modified(b, "doc")
    db.add(
        Module(
            id="assist_host",
            product="usp",
            board_rel="board.json",
            kind="group",
            nested_board="boards/assist_host.json",
            title_blob=crypto.encrypt("辅助编辑", aad="module.title:usp:assist_host"),
            current_version="v1.0",
        )
    )
    db.add(ModuleVersion(product="usp", module_id="assist_host", semver="v1.0", lifecycle_band="dev", expected_hours=40))
    db.add(
        Board(
            product="usp",
            rel_path="boards/assist_host.json",
            doc={"nodes": [{"id": "assist_child", "x": 0, "y": 0}], "groups": [], "edges": []},
        )
    )
    db.add(
        Module(
            id="assist_child",
            product="usp",
            board_rel="boards/assist_host.json",
            kind="feature",
            title_blob=crypto.encrypt("子功能", aad="module.title:usp:assist_child"),
            current_version="v1.0",
        )
    )
    db.add(ModuleVersion(product="usp", module_id="assist_child", semver="v1.0", lifecycle_band="dev"))
    db.commit()
    week = iso_week_id()
    logged = client.put(
        "/api/v1/products/usp/hours",
        json={"feature_id": "assist_host", "version": "v1.0", "expected_hours": 40, "hours": 6, "week": week},
    )
    assert logged.status_code == 200, logged.text
    s = client.get("/api/v1/products/usp/hours/summary").json()
    assert "usp_algo" not in s["features"]
    host = s["features"]["assist_host"]
    assert host["invested"] == 6
    assert host["duration"] == snake_duration_sec(34, 6)


def test_hours_version_locked(client, db):
    login(client, "eng", "eng-secret")
    past = _closed_week()
    m = db.query(Module).filter_by(id="biz", product="usp").one()
    m.current_version = "v1.1"
    db.add(
        ModuleVersion(
            product="usp",
            module_id="biz",
            semver="v1.1",
            lifecycle_band="dev",
            expected_hours=24,
        )
    )
    db.add(
        HourAllocation(
            week=past,
            username="eng",
            product="usp",
            feature_id="biz",
            version="v1.0",
            hours=3,
        )
    )
    db.commit()
    page = client.get("/api/v1/products/usp/modules/biz").json()
    assert page["version"] == "v1.1"
    assert page["hours"]["week_invested"] == 0
    assert page["hours"]["invested"] == 0
    v10 = client.get("/api/v1/products/usp/modules/biz?version=v1.0").json()
    assert v10["hours"]["invested"] == 3
    assert v10["hours"]["week_invested"] == 0
    snake = client.get("/api/v1/products/usp/hours/summary").json()
    assert snake["features"]["biz"]["invested"] == 0


def test_hours_open_week_stays_on_logged_version(client, db):
    login(client, "eng", "eng-secret")
    m = db.query(Module).filter_by(id="biz", product="usp").one()
    m.current_version = "v1.1"
    db.add(
        ModuleVersion(
            product="usp",
            module_id="biz",
            semver="v1.1",
            lifecycle_band="dev",
            expected_hours=24,
        )
    )
    db.add(
        HourAllocation(
            week=iso_week_id(),
            username="eng",
            product="usp",
            feature_id="biz",
            version="v1.0",
            hours=3,
        )
    )
    db.commit()
    page = client.get("/api/v1/products/usp/modules/biz").json()
    assert page["version"] == "v1.1"
    assert page["hours"]["week_invested"] == 0
    v10 = client.get("/api/v1/products/usp/modules/biz?version=v1.0").json()
    assert v10["hours"]["week_invested"] == 3
    assert any(u["user"] == "eng" and u["hours"] == 3 for u in v10["hours"]["by_user"])
    reg = client.get("/api/v1/products/usp/hours/register").json()
    biz = next(f for f in reg["features"] if f["id"] == "biz")
    assert biz["version"] == "v1.1"
    assert biz["my_week"] == 0
    snake = client.get("/api/v1/products/usp/hours/summary").json()
    assert snake["features"]["biz"]["invested"] == 0


def test_hours_week_cannot_exceed_expected(client):
    login(client, "eng", "eng-secret")
    over = client.put(
        "/api/v1/products/usp/hours",
        json={"feature_id": "biz", "version": "v1.0", "hours": 3},
    )
    assert over.status_code == 400
    assert over.json()["detail"] == "exceeds_expected"
    cap = client.put(
        "/api/v1/products/usp/hours",
        json={"feature_id": "biz", "version": "v1.0", "expected_hours": 4, "hours": 5},
    )
    assert cap.status_code == 400
    assert cap.json()["detail"] == "exceeds_expected"
    ok = client.put(
        "/api/v1/products/usp/hours",
        json={"feature_id": "biz", "version": "v1.0", "expected_hours": 4, "hours": 4},
    )
    assert ok.status_code == 200, ok.text


def test_hours_week_close_settles_into_cumulative(client, db):
    from app.infra.models import HourExpectation

    login(client, "eng", "eng-secret")
    past = _closed_week()
    db.add(
        HourExpectation(
            username="eng",
            product="usp",
            feature_id="biz",
            version="v1.0",
            expected_hours=20,
        )
    )
    db.add(
        HourAllocation(
            week=past,
            username="eng",
            product="usp",
            feature_id="biz",
            version="v1.0",
            hours=6,
        )
    )
    db.commit()
    page = client.get("/api/v1/products/usp/modules/biz").json()
    assert page["hours"]["invested"] == 6
    assert page["hours"]["expected_hours"] == 14
    assert page["hours"]["week_invested"] == 0
    mine = next(u for u in page["hours"]["by_user"] if u["user"] == "eng")
    assert mine["hours"] == 0
    assert mine["expected_hours"] == 14
    over = client.put(
        "/api/v1/products/usp/hours",
        json={"feature_id": "biz", "version": "v1.0", "hours": 15},
    )
    assert over.status_code == 400
    assert over.json()["detail"] == "exceeds_expected"
    ok = client.put(
        "/api/v1/products/usp/hours",
        json={"feature_id": "biz", "version": "v1.0", "hours": 14},
    )
    assert ok.status_code == 200, ok.text
    page2 = client.get("/api/v1/products/usp/modules/biz").json()
    assert page2["hours"]["invested"] == 6
    assert page2["hours"]["expected_hours"] == 14
    assert page2["hours"]["week_invested"] == 14


def test_hours_versions_independent_expected(client, db):
    login(client, "eng", "eng-secret")
    db.add(
        ModuleVersion(
            product="usp",
            module_id="biz",
            semver="v1.1",
            lifecycle_band="dev",
        )
    )
    db.commit()
    v10 = client.put(
        "/api/v1/products/usp/hours",
        json={"feature_id": "biz", "version": "v1.0", "expected_hours": 10, "hours": 4},
    )
    assert v10.status_code == 200, v10.text
    v11 = client.put(
        "/api/v1/products/usp/hours",
        json={"feature_id": "biz", "version": "v1.1", "expected_hours": 20, "hours": 5},
    )
    assert v11.status_code == 200, v11.text
    p10 = client.get("/api/v1/products/usp/modules/biz?version=v1.0").json()["hours"]
    p11 = client.get("/api/v1/products/usp/modules/biz?version=v1.1").json()["hours"]
    assert p10["settled"] is True
    assert p10["expected_hours"] is None
    assert p10["week_invested"] == 4
    assert p11["expected_hours"] == 20
    assert p11["week_invested"] == 5
    assert p11["settled"] is False


def _closed_week() -> str:
    from datetime import date, timedelta

    from app.services.hours import week_is_closed

    d = date.today() - timedelta(days=14)
    y, w, _ = d.isocalendar()
    wid = f"{y}-W{w:02d}"
    assert week_is_closed(wid)
    return wid


def test_covers_hours_settled_excludes_open_week(client, db):
    login(client, "eng", "eng-secret")
    past = _closed_week()
    db.add(
        HourAllocation(
            week=past,
            username="eng",
            product="usp",
            feature_id="biz",
            version="v1.0",
            hours=7,
        )
    )
    db.commit()
    week = iso_week_id()
    client.put(
        "/api/v1/products/usp/hours",
        json={"feature_id": "biz", "version": "v1.0", "expected_hours": 24, "hours": 5, "week": week},
    )
    body = client.get("/api/v1/products/usp/releases/usp_v100/hours").json()
    assert body["invested_total"] == 7
    assert body["invested"] == 7
    assert body["week_invested"] == 5
    assert body["remaining_hours"] == 24.0


def test_covers_hours_changelog_excludes_predecessor(client, db):
    from app.infra.models import ProductRelease, ReleaseCover

    login(client, "eng", "eng-secret")
    db.add(ProductRelease(id="usp_v090", product="usp", semver="v0.9.0", sort=-1))
    db.add(ReleaseCover(release_id="usp_v090", module_id="biz", module_version="v1.0"))
    db.commit()
    week = iso_week_id()
    client.put(
        "/api/v1/products/usp/hours",
        json={"feature_id": "biz", "version": "v1.0", "expected_hours": 24, "hours": 5, "week": week},
    )
    body = client.get("/api/v1/products/usp/releases/usp_v100/hours").json()
    assert body["changelog_cover_ids"] == []
    assert body["week_invested"] == 0
    assert body["invested_total"] == 0


def test_covers_hours_nest_host_rolls_up(client, db, crypto):
    from sqlalchemy.orm.attributes import flag_modified

    login(client, "eng", "eng-secret")
    b = db.query(Board).filter_by(product="usp", rel_path="board.json").one()
    doc = dict(b.doc)
    nodes = list(doc.get("nodes") or [])
    if not any(n.get("id") == "nest_host" for n in nodes):
        nodes.append({"id": "nest_host", "x": 40, "y": 40})
    doc["nodes"] = nodes
    b.doc = doc
    flag_modified(b, "doc")
    db.add(
        Module(
            id="nest_host",
            product="usp",
            board_rel="board.json",
            kind="feature",
            nested_board="boards/nest.json",
            title_blob=crypto.encrypt("嵌套宿主", aad="module.title:usp:nest_host"),
            current_version="v1.0",
        )
    )
    db.add(ModuleVersion(product="usp", module_id="nest_host", semver="v1.0", lifecycle_band="dev", expected_hours=40))
    db.add(
        Board(
            product="usp",
            rel_path="boards/nest.json",
            doc={"nodes": [{"id": "nest_child", "x": 0, "y": 0}], "groups": [], "edges": []},
        )
    )
    db.add(
        Module(
            id="nest_child",
            product="usp",
            board_rel="boards/nest.json",
            kind="feature",
            title_blob=crypto.encrypt("嵌套子", aad="module.title:usp:nest_child"),
            current_version="v1.0",
        )
    )
    db.add(ModuleVersion(product="usp", module_id="nest_child", semver="v1.0", lifecycle_band="dev"))
    db.commit()
    r = client.put(
        "/api/v1/products/usp/releases/usp_v100/covers",
        json={"covers": [{"module_id": "nest_host", "version": "v1.0"}]},
    )
    assert r.status_code == 200, r.text
    week = iso_week_id()
    logged = client.put(
        "/api/v1/products/usp/hours",
        json={"feature_id": "nest_child", "version": "v1.0", "expected_hours": 24, "hours": 4, "week": week},
    )
    assert logged.status_code == 200, logged.text
    page = client.get("/api/v1/products/usp/modules/usp_dev").json()
    host = next(c for c in page["cover_items"] if c["module_id"] == "nest_host")
    assert host["week_invested"] == 4
    hrs = page["hours"]
    assert hrs["week_invested"] == 4
    host_page = client.get("/api/v1/products/usp/modules/nest_host").json()
    assert host_page["hours"]["hours_mode"] == "nested_aggregate"
    assert host_page["hours"]["week_invested"] == 0
    assert host_page["hours"]["can_register"] is True
    assert host_page["can_log_hours"] is True
    kids = host_page["hours"]["by_child"]
    child = next(c for c in kids if c["id"] == "nest_child")
    assert child["hours"] == 4
    assert child["title"] == "嵌套子"
    assert child["band"] == "dev"
    assert host_page["hours"]["invested_total"] == 4
    assert host_page["lifecycle"]["derived"] is False
    assert host_page["lifecycle"]["band"] == "dev"
    host_mv = db.query(ModuleVersion).filter_by(module_id="nest_host", semver="v1.0").one()
    host_mv.lifecycle_band = "done"
    host_mv.progress = 100
    db.commit()
    done_host = client.get("/api/v1/products/usp/modules/nest_host").json()
    assert done_host["hours"]["band"] == "done"
    assert done_host["hours"]["invested_total"] == 4
    assert done_host["lifecycle"]["derived"] is True
    assert done_host["lifecycle"]["band"] == "dev"
    assert hrs["changelog_cover_ids"] == ["nest_host"]
    assert any(u["user"] == "eng" and u["hours"] == 4 for u in hrs["by_user"])


def test_covers_hours_nest_host_own_hours_count(client, db, crypto):
    from sqlalchemy.orm.attributes import flag_modified

    login(client, "eng", "eng-secret")
    b = db.query(Board).filter_by(product="usp", rel_path="board.json").one()
    doc = dict(b.doc)
    nodes = list(doc.get("nodes") or [])
    if not any(n.get("id") == "own_host" for n in nodes):
        nodes.append({"id": "own_host", "x": 44, "y": 44})
    doc["nodes"] = nodes
    b.doc = doc
    flag_modified(b, "doc")
    db.add(
        Module(
            id="own_host",
            product="usp",
            board_rel="board.json",
            kind="feature",
            nested_board="boards/own_host.json",
            title_blob=crypto.encrypt("宿主自记", aad="module.title:usp:own_host"),
            current_version="v1.0",
        )
    )
    db.add(ModuleVersion(product="usp", module_id="own_host", semver="v1.0", lifecycle_band="dev", expected_hours=40))
    db.add(
        Board(
            product="usp",
            rel_path="boards/own_host.json",
            doc={"nodes": [{"id": "own_child", "x": 0, "y": 0}], "groups": [], "edges": []},
        )
    )
    db.add(
        Module(
            id="own_child",
            product="usp",
            board_rel="boards/own_host.json",
            kind="feature",
            title_blob=crypto.encrypt("子模块", aad="module.title:usp:own_child"),
            current_version="v1.0",
        )
    )
    db.add(ModuleVersion(product="usp", module_id="own_child", semver="v1.0", lifecycle_band="dev"))
    db.commit()
    r = client.put(
        "/api/v1/products/usp/releases/usp_v100/covers",
        json={"covers": [{"module_id": "own_host", "version": "v1.0"}]},
    )
    assert r.status_code == 200, r.text
    week = iso_week_id()
    host_log = client.put(
        "/api/v1/products/usp/hours",
        json={"feature_id": "own_host", "version": "v1.0", "expected_hours": 40, "hours": 8, "week": week},
    )
    assert host_log.status_code == 200, host_log.text
    child_log = client.put(
        "/api/v1/products/usp/hours",
        json={"feature_id": "own_child", "version": "v1.0", "expected_hours": 24, "hours": 4, "week": week},
    )
    assert child_log.status_code == 200, child_log.text
    page = client.get("/api/v1/products/usp/modules/usp_dev").json()
    host = next(c for c in page["cover_items"] if c["module_id"] == "own_host")
    assert host["week_invested"] == 12
    hrs = page["hours"]
    assert hrs["week_invested"] == 12
    assert hrs["cover_ids"] == ["own_host"]
    assert any(u["user"] == "eng" and u["hours"] == 12 for u in hrs["by_user"])
    host_page = client.get("/api/v1/products/usp/modules/own_host").json()
    assert host_page["hours"]["hours_mode"] == "nested_aggregate"
    assert host_page["hours"]["week_invested"] == 8
    assert host_page["hours"]["invested_total"] == 12


def test_covers_hours_child_pin_version(client, db, crypto):
    """Child cover pin wins over the host pin when the changelog rolls the child up."""
    from sqlalchemy.orm.attributes import flag_modified

    login(client, "eng", "eng-secret")
    b = db.query(Board).filter_by(product="usp", rel_path="board.json").one()
    doc = dict(b.doc)
    nodes = list(doc.get("nodes") or [])
    if not any(n.get("id") == "pin_host" for n in nodes):
        nodes.append({"id": "pin_host", "x": 48, "y": 48})
    doc["nodes"] = nodes
    b.doc = doc
    flag_modified(b, "doc")
    db.add(
        Module(
            id="pin_host",
            product="usp",
            board_rel="board.json",
            kind="feature",
            nested_board="boards/pin_host.json",
            title_blob=crypto.encrypt("版本宿主", aad="module.title:usp:pin_host"),
            current_version="v1.0",
        )
    )
    db.add(ModuleVersion(product="usp", module_id="pin_host", semver="v1.0", lifecycle_band="dev"))
    db.add(
        Board(
            product="usp",
            rel_path="boards/pin_host.json",
            doc={"nodes": [{"id": "pin_child", "x": 0, "y": 0}], "groups": [], "edges": []},
        )
    )
    db.add(
        Module(
            id="pin_child",
            product="usp",
            board_rel="boards/pin_host.json",
            kind="feature",
            title_blob=crypto.encrypt("版本子模块", aad="module.title:usp:pin_child"),
            current_version="v1.1",
        )
    )
    db.add(ModuleVersion(product="usp", module_id="pin_child", semver="v1.0", lifecycle_band="dev"))
    db.add(ModuleVersion(product="usp", module_id="pin_child", semver="v1.1", lifecycle_band="dev"))
    db.commit()
    r = client.put(
        "/api/v1/products/usp/releases/usp_v100/covers",
        json={
            "covers": [
                {"module_id": "pin_host", "version": "v1.0"},
                {"module_id": "pin_child", "version": "v1.1"},
            ]
        },
    )
    assert r.status_code == 200, r.text
    week = iso_week_id()
    decoy = client.put(
        "/api/v1/products/usp/hours",
        json={"feature_id": "pin_child", "version": "v1.0", "expected_hours": 8, "hours": 1, "week": week},
    )
    assert decoy.status_code == 200, decoy.text
    logged = client.put(
        "/api/v1/products/usp/hours",
        json={"feature_id": "pin_child", "version": "v1.1", "expected_hours": 10, "hours": 6.5, "week": week},
    )
    assert logged.status_code == 200, logged.text
    page = client.get("/api/v1/products/usp/modules/usp_dev").json()
    hrs = page["hours"]
    assert hrs["changelog_cover_ids"] == ["pin_host"]
    assert hrs["week_invested"] == 6.5
    assert hrs["remaining_hours"] == 10
    assert any(u["user"] == "eng" and u["hours"] == 6.5 for u in hrs["by_user"])
    host = next(c for c in page["cover_items"] if c["module_id"] == "pin_host")
    child = next(c for c in page["cover_items"] if c["module_id"] == "pin_child")
    assert host["week_invested"] == 6.5
    assert child["week_invested"] == 6.5


def test_group_host_hours_flattens_descendant_week(client, db, crypto):
    from sqlalchemy.orm.attributes import flag_modified

    from app.infra.models import HourAllocation, ModuleVersion
    from app.services.hours import iso_week_id

    login(client, "admin", "admin-secret")
    b = db.query(Board).filter_by(product="usp", rel_path="board.json").one()
    doc = dict(b.doc)
    nodes = list(doc.get("nodes") or [])
    if not any(n.get("id") == "env_host" for n in nodes):
        nodes.append({"id": "env_host", "x": 8, "y": 8})
    doc["nodes"] = nodes
    b.doc = doc
    flag_modified(b, "doc")
    db.add(
        Module(
            id="env_host",
            product="usp",
            board_rel="board.json",
            kind="group",
            nested_board="boards/env_host.json",
            title_blob=crypto.encrypt("功能性宿主", aad="module.title:usp:env_host"),
            current_version="v1.0",
        )
    )
    db.add(ModuleVersion(product="usp", module_id="env_host", semver="v1.0", lifecycle_band="done", progress=100))
    db.add(
        Board(
            product="usp",
            rel_path="boards/env_host.json",
            doc={"nodes": [{"id": "env_mid", "x": 0, "y": 0}], "groups": [], "edges": []},
        )
    )
    db.add(
        Module(
            id="env_mid",
            product="usp",
            board_rel="boards/env_host.json",
            kind="group",
            nested_board="boards/env_mid.json",
            title_blob=crypto.encrypt("中间包络", aad="module.title:usp:env_mid"),
            current_version="v1.0",
        )
    )
    db.add(ModuleVersion(product="usp", module_id="env_mid", semver="v1.0", lifecycle_band="done", progress=100))
    db.add(
        Board(
            product="usp",
            rel_path="boards/env_mid.json",
            doc={"nodes": [{"id": "env_leaf", "x": 0, "y": 0}], "groups": [], "edges": []},
        )
    )
    db.add(
        Module(
            id="env_leaf",
            product="usp",
            board_rel="boards/env_mid.json",
            kind="feature",
            title_blob=crypto.encrypt("叶子功能", aad="module.title:usp:env_leaf"),
            current_version="v1.0",
        )
    )
    db.add(ModuleVersion(product="usp", module_id="env_leaf", semver="v1.0", lifecycle_band="pre", progress=20))
    db.add(
        HourAllocation(
            week=iso_week_id(),
            username="eng",
            product="usp",
            feature_id="env_leaf",
            version="v1.0",
            hours=7,
        )
    )
    db.commit()
    page = client.get("/api/v1/products/usp/modules/env_host").json()
    hrs = page["hours"]
    assert hrs["band"] == "done"
    assert hrs["settled"] is True
    assert hrs["invested"] == 0
    kids = hrs["by_child"]
    assert [c["id"] for c in kids] == ["env_leaf"]
    leaf = kids[0]
    assert leaf["title"] == "叶子功能"
    assert leaf["hours"] == 7
    assert not any(c["id"] == "env_mid" for c in kids)
    assert hrs["invested_total"] == 7
    assert hrs["can_register"] is False


def test_done_host_omits_child_hours_when_all_zero(client, db, crypto):
    from sqlalchemy.orm.attributes import flag_modified

    login(client, "admin", "admin-secret")
    b = db.query(Board).filter_by(product="usp", rel_path="board.json").one()
    doc = dict(b.doc)
    nodes = list(doc.get("nodes") or [])
    if not any(n.get("id") == "chg_host" for n in nodes):
        nodes.append({"id": "chg_host", "x": 9, "y": 9})
    doc["nodes"] = nodes
    b.doc = doc
    flag_modified(b, "doc")
    db.add(
        Module(
            id="chg_host",
            product="usp",
            board_rel="board.json",
            kind="feature",
            nested_board="boards/chg_host.json",
            title_blob=crypto.encrypt("充电策略", aad="module.title:usp:chg_host"),
            current_version="v1.0",
        )
    )
    db.add(ModuleVersion(product="usp", module_id="chg_host", semver="v1.0", lifecycle_band="done", progress=100))
    db.add(
        Board(
            product="usp",
            rel_path="boards/chg_host.json",
            doc={"nodes": [{"id": "chg_leaf", "x": 0, "y": 0}], "groups": [], "edges": []},
        )
    )
    db.add(
        Module(
            id="chg_leaf",
            product="usp",
            board_rel="boards/chg_host.json",
            kind="feature",
            title_blob=crypto.encrypt("忙时充电", aad="module.title:usp:chg_leaf"),
            current_version="v1.0",
        )
    )
    db.add(ModuleVersion(product="usp", module_id="chg_leaf", semver="v1.0", lifecycle_band="done", progress=100))
    db.commit()
    page = client.get("/api/v1/products/usp/modules/chg_host").json()
    assert page["hours"]["settled"] is True
    assert page["hours"]["invested"] == 0
    assert page["hours"]["by_child"] == []


def test_covers_hours_published_stable_total_only(client, db):
    login(client, "admin", "admin-secret")
    past = _closed_week()
    db.add(
        HourAllocation(
            week=past,
            username="eng",
            product="usp",
            feature_id="biz",
            version="v1.0",
            hours=6,
        )
    )
    mv = db.query(ModuleVersion).filter_by(module_id="biz", semver="v1.0").one()
    mv.lifecycle_band = "done"
    mv.progress = 100
    db.commit()
    pub = client.post("/api/v1/products/usp/releases/usp_v100/publish")
    assert pub.status_code == 200, pub.text
    tags = client.put("/api/v1/products/usp/tags", json={"stable": "usp_v100", "dev": "usp_v100"})
    assert tags.status_code == 200, tags.text
    stable_page = client.get("/api/v1/products/usp/modules/usp_stable").json()
    assert stable_page["lifecycle"]["band"] == "joint_test"
    assert stable_page["hours"].get("hours_view") not in ("total_only", "hidden")
    done = client.put(
        "/api/v1/products/usp/modules/usp_stable/lifecycle",
        json={"band": "done", "progress": 100},
    )
    assert done.status_code == 200, done.text
    stable = client.get("/api/v1/products/usp/modules/usp_stable").json()
    assert stable["lifecycle"]["band"] == "done"
    assert stable["hours"]["hours_view"] == "total_only"
    assert stable["hours"]["invested_total"] == 6
    dev = client.get("/api/v1/products/usp/modules/usp_dev").json()["hours"]
    assert dev.get("hours_view") not in ("total_only", "hidden")
    assert dev["invested_total"] == 6
    assert dev["week_invested"] == 0


def test_product_lifecycle_covers_then_publish(client, db):
    login(client, "admin", "admin-secret")
    g = client.get("/api/v1/products/usp/graph").json()
    dev = next(n for n in g["nodes"] if n["id"] == "usp_dev")
    assert dev["band"] == "dev"
    blocked = client.post("/api/v1/products/usp/releases/usp_v100/publish")
    assert blocked.status_code == 400
    mv = db.query(ModuleVersion).filter_by(module_id="biz", semver="v1.0").one()
    mv.lifecycle_band = "done"
    mv.progress = 100
    db.commit()
    g2 = client.get("/api/v1/products/usp/graph").json()
    dev2 = next(n for n in g2["nodes"] if n["id"] == "usp_dev")
    assert dev2["band"] == "test"
    page = client.get("/api/v1/products/usp/modules/usp_dev").json()
    assert page["lifecycle"]["label"] == "待联调测试"
    assert page["can_publish"] is True
    pub = client.post("/api/v1/products/usp/releases/usp_v100/publish")
    assert pub.status_code == 200, pub.text
    page2 = client.get("/api/v1/products/usp/modules/usp_dev").json()
    assert page2["lifecycle"]["band"] == "joint_test"
    assert page2["lifecycle"]["label"] == "联合测试中"
    assert page2["can_edit_lifecycle"] is True
    g3 = client.get("/api/v1/products/usp/graph").json()
    dev3 = next(n for n in g3["nodes"] if n["id"] == "usp_dev")
    assert dev3["band"] == "joint_test"
    login(client, "eng", "eng-secret")
    denied = client.put(
        "/api/v1/products/usp/modules/usp_dev/lifecycle",
        json={"band": "done"},
    )
    assert denied.status_code == 403


def test_product_lifecycle_progress_follows_bottleneck(client, db):
    from app.infra.models import ReleaseCover

    login(client, "admin", "admin-secret")
    biz = db.query(ModuleVersion).filter_by(module_id="biz", semver="v1.0").one()
    biz.lifecycle_band = "done"
    biz.progress = 100
    late = db.query(ModuleVersion).filter_by(module_id="secret_mod", semver="v1.0").one()
    late.lifecycle_band = "pre"
    late.progress = 10
    db.add(ReleaseCover(release_id="usp_v100", module_id="secret_mod", module_version="v1.0"))
    db.commit()
    page = client.get("/api/v1/products/usp/modules/usp_dev").json()
    assert page["lifecycle"]["band"] == "pre"
    assert page["lifecycle"]["progress"] == 10
    assert page["lifecycle"]["label"] == "设计中"
    g = client.get("/api/v1/products/usp/graph").json()
    dev = next(n for n in g["nodes"] if n["id"] == "usp_dev")
    assert dev["band"] == "pre"
    assert dev["progress"] == 10


def test_product_cover_aggregates_only_selected_children(client, db, crypto):
    from sqlalchemy.orm.attributes import flag_modified

    login(client, "admin", "admin-secret")
    b = db.query(Board).filter_by(product="usp", rel_path="board.json").one()
    doc = dict(b.doc)
    nodes = list(doc.get("nodes") or [])
    if not any(n.get("id") == "nest_host" for n in nodes):
        nodes.append({"id": "nest_host", "x": 40, "y": 40})
    doc["nodes"] = nodes
    b.doc = doc
    flag_modified(b, "doc")
    db.add(
        Module(
            id="nest_host",
            product="usp",
            board_rel="board.json",
            kind="feature",
            nested_board="boards/nest.json",
            title_blob=crypto.encrypt("嵌套宿主", aad="module.title:usp:nest_host"),
            current_version="v1.0",
        )
    )
    db.add(ModuleVersion(product="usp", module_id="nest_host", semver="v1.0", lifecycle_band="done", progress=100))
    db.add(
        Board(
            product="usp",
            rel_path="boards/nest.json",
            doc={
                "nodes": [
                    {"id": "nest_done", "x": 0, "y": 0},
                    {"id": "nest_late", "x": 20, "y": 0},
                ],
                "groups": [],
                "edges": [],
            },
        )
    )
    db.add(
        Module(
            id="nest_done",
            product="usp",
            board_rel="boards/nest.json",
            kind="feature",
            title_blob=crypto.encrypt("已完成子", aad="module.title:usp:nest_done"),
            current_version="v1.0",
        )
    )
    db.add(ModuleVersion(product="usp", module_id="nest_done", semver="v1.0", lifecycle_band="done", progress=100))
    db.add(
        Module(
            id="nest_late",
            product="usp",
            board_rel="boards/nest.json",
            kind="feature",
            title_blob=crypto.encrypt("未完成子", aad="module.title:usp:nest_late"),
            current_version="v1.0",
        )
    )
    db.add(ModuleVersion(product="usp", module_id="nest_late", semver="v1.0", lifecycle_band="pre", progress=10))
    db.commit()
    host_page = client.get("/api/v1/products/usp/modules/nest_host").json()
    assert host_page["lifecycle"]["band"] == "pre"
    assert host_page["lifecycle"]["derived"] is True
    assert (
        client.put(
            "/api/v1/products/usp/releases/usp_v100/covers",
            json={"covers": [{"module_id": "nest_host", "version": "v1.0"}]},
        ).status_code
        == 200
    )
    page = client.get("/api/v1/products/usp/modules/usp_dev").json()
    host = next(c for c in page["cover_items"] if c["module_id"] == "nest_host")
    assert host["band"] == "done"
    assert page["lifecycle"]["label"] == "待联调测试"
    assert page["can_publish"] is True
    assert (
        client.put(
            "/api/v1/products/usp/releases/usp_v100/covers",
            json={
                "covers": [
                    {"module_id": "nest_host", "version": "v1.0"},
                    {"module_id": "nest_done", "version": "v1.0"},
                ]
            },
        ).status_code
        == 200
    )
    page2 = client.get("/api/v1/products/usp/modules/usp_dev").json()
    host2 = next(c for c in page2["cover_items"] if c["module_id"] == "nest_host")
    assert host2["band"] == "done"
    assert page2["can_publish"] is True
    assert (
        client.put(
            "/api/v1/products/usp/releases/usp_v100/covers",
            json={
                "covers": [
                    {"module_id": "nest_host", "version": "v1.0"},
                    {"module_id": "nest_done", "version": "v1.0"},
                    {"module_id": "nest_late", "version": "v1.0"},
                ]
            },
        ).status_code
        == 200
    )
    page3 = client.get("/api/v1/products/usp/modules/usp_dev").json()
    host3 = next(c for c in page3["cover_items"] if c["module_id"] == "nest_host")
    late = next(c for c in page3["cover_items"] if c["module_id"] == "nest_late")
    assert host3["band"] == "pre"
    assert late["band"] == "pre"
    assert page3["lifecycle"]["band"] == "pre"
    assert page3["can_publish"] is False
    blocked = client.post("/api/v1/products/usp/releases/usp_v100/publish")
    assert blocked.status_code == 400
    assert blocked.json()["detail"] == "涵盖功能尚未全部完成"


def test_graph_keeps_edges_to_groups(client, db):
    from sqlalchemy.orm.attributes import flag_modified

    login(client, "admin", "admin-secret")
    b = db.query(Board).filter_by(product="usp", rel_path="board.json").one()
    doc = dict(b.doc)
    groups = list(doc.get("groups") or [])
    groups.append({"id": "g_wrap", "title": "包", "x": 0, "y": 0, "w": 200, "h": 80, "members": ["biz"]})
    doc["groups"] = groups
    edges = list(doc.get("edges") or [])
    edges.append({"from": "biz", "to": "g_wrap", "from_side": "bottom", "to_side": "top", "from_t": 0.4, "to_t": 0.6})
    doc["edges"] = edges
    b.doc = doc
    flag_modified(b, "doc")
    db.commit()
    g = client.get("/api/v1/products/usp/graph").json()
    e = next(x for x in g["edges"] if x["from"] == "biz" and x["to"] == "g_wrap")
    assert e["from_side"] == "bottom"
    assert e["to_side"] == "top"
    assert abs(float(e["from_t"]) - 0.4) < 1e-6


def test_undeclared_nested_file_does_not_drive_host_lifecycle(client, db, crypto):
    login(client, "admin", "admin-secret")
    db.add(
        Board(
            product="usp",
            rel_path="boards/biz.json",
            doc={"nodes": [{"id": "biz_ghost", "x": 0, "y": 0}], "groups": [], "edges": []},
        )
    )
    db.add(
        Module(
            id="biz_ghost",
            product="usp",
            board_rel="boards/biz.json",
            kind="feature",
            title_blob=crypto.encrypt("幽灵子", aad="module.title:usp:biz_ghost"),
            current_version="v1.0",
        )
    )
    db.add(ModuleVersion(product="usp", module_id="biz_ghost", semver="v1.0", lifecycle_band="pre", progress=0))
    host = db.query(Module).filter_by(product="usp", id="biz").one()
    assert not host.nested_board
    db.commit()
    g = client.get("/api/v1/products/usp/graph").json()
    node = next(n for n in g["nodes"] if n["id"] == "biz")
    assert node["band"] == "dev"
    assert node.get("nested_board") == "boards/biz.json"


def test_nest_host_incomplete_keeps_own_band(client, db, crypto):
    from sqlalchemy.orm.attributes import flag_modified

    login(client, "admin", "admin-secret")
    b = db.query(Board).filter_by(product="usp", rel_path="board.json").one()
    doc = dict(b.doc)
    nodes = list(doc.get("nodes") or [])
    if not any(n.get("id") == "nest_host" for n in nodes):
        nodes.append({"id": "nest_host", "x": 40, "y": 40})
    doc["nodes"] = nodes
    b.doc = doc
    flag_modified(b, "doc")
    db.add(
        Module(
            id="nest_host",
            product="usp",
            board_rel="board.json",
            kind="feature",
            nested_board="boards/nest.json",
            title_blob=crypto.encrypt("嵌套宿主", aad="module.title:usp:nest_host"),
            current_version="v1.1",
        )
    )
    db.add(
        ModuleVersion(
            product="usp", module_id="nest_host", semver="v1.1", lifecycle_band="test", progress=50
        )
    )
    db.add(
        Board(
            product="usp",
            rel_path="boards/nest.json",
            doc={"nodes": [{"id": "nest_child", "x": 0, "y": 0}], "groups": [], "edges": []},
        )
    )
    db.add(
        Module(
            id="nest_child",
            product="usp",
            board_rel="boards/nest.json",
            kind="feature",
            title_blob=crypto.encrypt("嵌套子", aad="module.title:usp:nest_child"),
            current_version="v1.0",
        )
    )
    db.add(ModuleVersion(product="usp", module_id="nest_child", semver="v1.0", lifecycle_band="done", progress=100))
    db.commit()
    g = client.get("/api/v1/products/usp/graph").json()
    host = next(n for n in g["nodes"] if n["id"] == "nest_host")
    assert host["band"] == "test"
    assert host["progress"] == 50
    assert host["version"] == "v1.1"
    page = client.get("/api/v1/products/usp/modules/nest_host").json()
    assert page["lifecycle"]["band"] == "test"
    assert page["lifecycle"]["progress"] == 50
    assert page["lifecycle"]["derived"] is False
    assert page["can_edit_lifecycle"] is True
    marked = client.put(
        "/api/v1/products/usp/modules/nest_host/lifecycle",
        json={"band": "done", "progress": 100, "version": "v1.1"},
    )
    assert marked.status_code == 200, marked.text
    done = client.get("/api/v1/products/usp/modules/nest_host").json()
    assert done["lifecycle"]["band"] == "done"
    assert done["lifecycle"]["derived"] is True
    assert done["can_edit_lifecycle"] is False


def test_nest_host_v10_follows_lowest_child_current(client, db, crypto):
    from sqlalchemy.orm.attributes import flag_modified

    login(client, "admin", "admin-secret")
    b = db.query(Board).filter_by(product="usp", rel_path="board.json").one()
    doc = dict(b.doc)
    nodes = list(doc.get("nodes") or [])
    if not any(n.get("id") == "nest_host" for n in nodes):
        nodes.append({"id": "nest_host", "x": 40, "y": 40})
    doc["nodes"] = nodes
    b.doc = doc
    flag_modified(b, "doc")
    db.add(
        Module(
            id="nest_host",
            product="usp",
            board_rel="board.json",
            kind="feature",
            nested_board="boards/nest.json",
            title_blob=crypto.encrypt("嵌套宿主", aad="module.title:usp:nest_host"),
            current_version="v1.0",
        )
    )
    db.add(ModuleVersion(product="usp", module_id="nest_host", semver="v1.0", lifecycle_band="done", progress=100))
    db.add(
        Board(
            product="usp",
            rel_path="boards/nest.json",
            doc={
                "nodes": [
                    {"id": "nest_done", "x": 0, "y": 0},
                    {"id": "nest_late", "x": 20, "y": 0},
                ],
                "groups": [],
                "edges": [],
            },
        )
    )
    db.add(
        Module(
            id="nest_done",
            product="usp",
            board_rel="boards/nest.json",
            kind="feature",
            title_blob=crypto.encrypt("已完成子", aad="module.title:usp:nest_done"),
            current_version="v1.0",
        )
    )
    db.add(ModuleVersion(product="usp", module_id="nest_done", semver="v1.0", lifecycle_band="done", progress=100))
    db.add(
        Module(
            id="nest_late",
            product="usp",
            board_rel="boards/nest.json",
            kind="feature",
            title_blob=crypto.encrypt("升级子", aad="module.title:usp:nest_late"),
            current_version="v1.1",
        )
    )
    db.add(ModuleVersion(product="usp", module_id="nest_late", semver="v1.0", lifecycle_band="done", progress=100))
    db.add(ModuleVersion(product="usp", module_id="nest_late", semver="v1.1", lifecycle_band="pre", progress=10))
    db.commit()
    g = client.get("/api/v1/products/usp/graph").json()
    host = next(n for n in g["nodes"] if n["id"] == "nest_host")
    assert host["band"] == "pre"
    assert host["progress"] == 10
    page = client.get("/api/v1/products/usp/modules/nest_host").json()
    assert page["lifecycle"]["band"] == "pre"
    assert page["lifecycle"]["progress"] == 10
    assert page["lifecycle"]["derived"] is True
    late = client.get("/api/v1/products/usp/modules/nest_late?version=v1.0").json()
    assert late["lifecycle"]["band"] == "done"


def test_nest_host_v11_follows_lowest_child_current(client, db, crypto):
    from sqlalchemy.orm.attributes import flag_modified

    login(client, "admin", "admin-secret")
    b = db.query(Board).filter_by(product="usp", rel_path="board.json").one()
    doc = dict(b.doc)
    nodes = list(doc.get("nodes") or [])
    if not any(n.get("id") == "nest_host" for n in nodes):
        nodes.append({"id": "nest_host", "x": 40, "y": 40})
    doc["nodes"] = nodes
    b.doc = doc
    flag_modified(b, "doc")
    db.add(
        Module(
            id="nest_host",
            product="usp",
            board_rel="board.json",
            kind="feature",
            nested_board="boards/nest.json",
            title_blob=crypto.encrypt("嵌套宿主", aad="module.title:usp:nest_host"),
            current_version="v1.1",
        )
    )
    db.add(
        ModuleVersion(
            product="usp", module_id="nest_host", semver="v1.0", lifecycle_band="done", progress=100
        )
    )
    db.add(
        ModuleVersion(
            product="usp", module_id="nest_host", semver="v1.1", lifecycle_band="dev", progress=50
        )
    )
    db.add(
        Board(
            product="usp",
            rel_path="boards/nest.json",
            doc={
                "nodes": [
                    {"id": "nest_done", "x": 0, "y": 0},
                    {"id": "nest_late", "x": 20, "y": 0},
                ],
                "groups": [],
                "edges": [],
            },
        )
    )
    db.add(
        Module(
            id="nest_done",
            product="usp",
            board_rel="boards/nest.json",
            kind="feature",
            title_blob=crypto.encrypt("已完成子", aad="module.title:usp:nest_done"),
            current_version="v1.0",
        )
    )
    db.add(ModuleVersion(product="usp", module_id="nest_done", semver="v1.0", lifecycle_band="done", progress=100))
    db.add(
        Module(
            id="nest_late",
            product="usp",
            board_rel="boards/nest.json",
            kind="feature",
            title_blob=crypto.encrypt("升级子", aad="module.title:usp:nest_late"),
            current_version="v1.0",
        )
    )
    db.add(ModuleVersion(product="usp", module_id="nest_late", semver="v1.0", lifecycle_band="pre", progress=10))
    db.commit()
    g = client.get("/api/v1/products/usp/graph").json()
    host = next(n for n in g["nodes"] if n["id"] == "nest_host")
    assert host["band"] == "dev"
    assert host["progress"] == 50
    assert host["version"] == "v1.1"
    page = client.get("/api/v1/products/usp/modules/nest_host").json()
    assert page["lifecycle"]["band"] == "dev"
    assert page["lifecycle"]["progress"] == 50
    assert page["lifecycle"]["derived"] is False
    old = client.get("/api/v1/products/usp/modules/nest_host?version=v1.0").json()
    assert old["lifecycle"]["band"] == "done"
    assert old["lifecycle"]["progress"] == 100
    assert old["lifecycle"]["derived"] is False
    assert old["hours"]["settled"] is True


def test_older_nested_version_done_current_still_lists_child_hours(client, db, crypto):
    from sqlalchemy.orm.attributes import flag_modified

    login(client, "admin", "admin-secret")
    b = db.query(Board).filter_by(product="usp", rel_path="board.json").one()
    doc = dict(b.doc)
    nodes = list(doc.get("nodes") or [])
    if not any(n.get("id") == "nest_host" for n in nodes):
        nodes.append({"id": "nest_host", "x": 40, "y": 40})
    doc["nodes"] = nodes
    b.doc = doc
    flag_modified(b, "doc")
    db.add(
        Module(
            id="nest_host",
            product="usp",
            board_rel="board.json",
            kind="group",
            nested_board="boards/nest.json",
            title_blob=crypto.encrypt("嵌套宿主", aad="module.title:usp:nest_host"),
            current_version="v1.2.1",
        )
    )
    db.add(ModuleVersion(product="usp", module_id="nest_host", semver="v1.1", lifecycle_band="pre", progress=0))
    db.add(ModuleVersion(product="usp", module_id="nest_host", semver="v1.2", lifecycle_band="pre", progress=0))
    db.add(ModuleVersion(product="usp", module_id="nest_host", semver="v1.2.1", lifecycle_band="pre", progress=0))
    db.add(
        Board(
            product="usp",
            rel_path="boards/nest.json",
            doc={"nodes": [{"id": "nest_child", "x": 0, "y": 0}], "groups": [], "edges": []},
        )
    )
    db.add(
        Module(
            id="nest_child",
            product="usp",
            board_rel="boards/nest.json",
            kind="feature",
            title_blob=crypto.encrypt("嵌套子", aad="module.title:usp:nest_child"),
            current_version="v1.0",
        )
    )
    db.add(ModuleVersion(product="usp", module_id="nest_child", semver="v1.0", lifecycle_band="test", progress=40))
    db.add(
        HourAllocation(
            week=iso_week_id(),
            username="eng",
            product="usp",
            feature_id="nest_child",
            version="v1.0",
            hours=3,
        )
    )
    db.commit()
    v11 = client.get("/api/v1/products/usp/modules/nest_host?version=v1.1").json()
    assert v11["lifecycle"]["band"] == "done"
    assert v11["lifecycle"]["derived"] is False
    assert v11["hours"]["settled"] is True
    assert v11["hours"]["by_child"][0]["id"] == "nest_child"
    assert v11["hours"]["by_child"][0]["hours"] == 3
    v12 = client.get("/api/v1/products/usp/modules/nest_host?version=v1.2").json()
    assert v12["lifecycle"]["band"] == "done"
    assert v12["hours"]["settled"] is True
    cur = client.get("/api/v1/products/usp/modules/nest_host").json()
    assert cur["version"] == "v1.2.1"
    assert cur["lifecycle"]["band"] == "pre"
    assert cur["lifecycle"]["derived"] is False
    assert cur["hours"]["settled"] is False
    assert cur["hours"]["can_register"] is True
    assert cur["can_log_hours"] is True
    assert cur["hours"]["by_child"]
    child = next(c for c in cur["hours"]["by_child"] if c["id"] == "nest_child")
    assert child["title"] == "嵌套子"
    logged = client.put(
        "/api/v1/products/usp/hours",
        json={"feature_id": "nest_host", "version": "v1.2.1", "expected_hours": 8, "hours": 2},
    )
    assert logged.status_code == 200, logged.text
    filled = client.get("/api/v1/products/usp/modules/nest_host").json()
    assert filled["hours"]["week_invested"] == 2
    assert filled["hours"]["can_register"] is True


def test_nested_graph_group_crop_beats_nested_file(client, db, crypto):
    from sqlalchemy.orm.attributes import flag_modified

    login(client, "admin", "admin-secret")
    b = db.query(Board).filter_by(product="usp", rel_path="board.json").one()
    doc = dict(b.doc)
    groups = list(doc.get("groups") or [])
    groups.append({"id": "g_family", "title": "族", "x": 0, "y": 0, "w": 200, "h": 80, "members": ["biz"]})
    doc["groups"] = groups
    b.doc = doc
    flag_modified(b, "doc")
    db.add(
        Module(
            id="g_family",
            product="usp",
            board_rel="board.json",
            kind="group",
            nested_board="boards/g_family.json",
            title_blob=crypto.encrypt("族", aad="module.title:usp:g_family"),
            current_version="v1.0",
        )
    )
    db.add(
        Board(
            product="usp",
            rel_path="boards/g_family.json",
            doc={"nodes": [{"id": "ghost", "x": 0, "y": 0}], "groups": [], "edges": []},
        )
    )
    db.commit()
    r = client.get("/api/v1/products/usp/modules/g_family/nested-graph")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "group"
    assert {n["id"] for n in body["nodes"]} == {"biz"}
    assert body["empty"] is False
    assert body.get("editor_board") == "board.json"
    assert body.get("editor_group") == "g_family"
    wrap = next(g for g in body["groups"] if g.get("id") == "g_family")
    assert wrap.get("w") == 0
    assert wrap.get("h") == 0


def test_nested_graph_nested_board(client, db, crypto):
    login(client, "admin", "admin-secret")
    host = db.query(Module).filter_by(product="usp", id="biz").one()
    host.nested_board = "boards/biz.json"
    db.add(
        Board(
            product="usp",
            rel_path="boards/biz.json",
            doc={"nodes": [{"id": "biz_kid", "x": 8, "y": 9}], "groups": [], "edges": []},
        )
    )
    db.add(
        Module(
            id="biz_kid",
            product="usp",
            board_rel="boards/biz.json",
            kind="feature",
            title_blob=crypto.encrypt("子", aad="module.title:usp:biz_kid"),
            current_version="v1.0",
        )
    )
    db.add(ModuleVersion(product="usp", module_id="biz_kid", semver="v1.0", lifecycle_band="done", progress=100))
    db.commit()
    r = client.get("/api/v1/products/usp/modules/biz/nested-graph")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "board"
    assert {n["id"] for n in body["nodes"]} == {"biz_kid"}
    wrap = next((g for g in body["groups"] if g.get("id") == "biz"), None)
    assert wrap is not None
    assert wrap.get("members") == ["biz_kid"]


def test_nested_graph_empty_leaf_and_skip_hub(client):
    login(client, "admin", "admin-secret")
    leaf = client.get("/api/v1/products/usp/modules/biz/nested-graph")
    assert leaf.status_code == 200
    assert leaf.json()["empty"] is True
    assert leaf.json()["nodes"] == []
    hub = client.get("/api/v1/products/usp/modules/usp_algo/nested-graph")
    assert hub.status_code == 200
    assert hub.json()["skip"] is True


def test_nested_graph_forbidden(client):
    login(client, "guest", "guest-secret")
    r = client.get("/api/v1/products/usp/modules/secret_mod/nested-graph")
    assert r.status_code == 403


def test_board_save_adds_node(client):
    login(client, "admin", "admin-secret")
    g0 = client.get("/api/v1/products/usp/graph").json()
    nodes = list(g0["nodes"])
    nodes.append({"id": "usp_new1", "x": 12, "y": 24, "title": "新功能"})
    r = client.put(
        "/api/v1/products/usp/boards/board.json",
        json={"doc": {"node_w": 158, "node_h": 46, "nodes": nodes, "groups": g0["groups"], "edges": g0["edges"]}},
    )
    assert r.status_code == 200, r.text
    g = client.get("/api/v1/products/usp/graph").json()
    n = next(x for x in g["nodes"] if x["id"] == "usp_new1")
    assert n["title"] == "新功能"
    assert n["band"] == "pre"


def test_board_save_omitted_hidden_release_not_delete_audit(client, db):
    login(client, "admin", "admin-secret")
    g0 = client.get("/api/v1/products/usp/graph").json()
    assert not any(n["id"] == "usp_v100" for n in g0["nodes"])
    before = db.query(AuditLog).filter_by(action="board.save").count()
    r = client.put(
        "/api/v1/products/usp/boards/board.json",
        json={"doc": {"node_w": 158, "node_h": 46, "nodes": g0["nodes"], "groups": g0["groups"], "edges": g0["edges"]}},
    )
    assert r.status_code == 200, r.text
    db.expire_all()
    saved = db.query(Board).filter_by(product="usp", rel_path="board.json").one()
    assert "usp_v100" in {n["id"] for n in saved.doc["nodes"]}
    assert db.query(AuditLog).filter_by(action="board.save").count() == before
    body = client.get("/api/v1/admin/audit").json()
    assert not any("删除" in (i.get("summary") or "") for i in body["items"] if i["action"] == "board.save")


def test_board_save_own_module_delete_is_audited(client):
    login(client, "admin", "admin-secret")
    g0 = client.get("/api/v1/products/usp/graph").json()
    created = list(g0["nodes"]) + [{"id": "tmp_del_audit", "x": 12, "y": 24, "title": "临时删除"}]
    add = client.put(
        "/api/v1/products/usp/boards/board.json",
        json={"doc": {"node_w": 158, "node_h": 46, "nodes": created, "groups": g0["groups"], "edges": g0["edges"]}},
    )
    assert add.status_code == 200, add.text
    g1 = client.get("/api/v1/products/usp/graph").json()
    assert any(n["id"] == "tmp_del_audit" for n in g1["nodes"])
    leftover = [n for n in g1["nodes"] if n["id"] != "tmp_del_audit"]
    r = client.put(
        "/api/v1/products/usp/boards/board.json",
        json={"doc": {"node_w": 158, "node_h": 46, "nodes": leftover, "groups": g1["groups"], "edges": g1["edges"]}},
    )
    assert r.status_code == 200, r.text
    body = client.get("/api/v1/admin/audit").json()
    rows = [i for i in body["items"] if i["action"] == "board.save"]
    assert any("删除1个功能" in (i.get("summary") or "") for i in rows)


def test_board_save_promote_node_keeps_module(client, db):
    login(client, "admin", "admin-secret")
    g0 = client.get("/api/v1/products/usp/graph").json()
    nodes = [n for n in g0["nodes"] if n["id"] != "biz"]
    groups = list(g0["groups"])
    groups.append({"id": "biz", "x": 0, "y": 0, "w": 0, "h": 0, "title": "业务分配", "members": ["secret_mod"]})
    r = client.put(
        "/api/v1/products/usp/boards/board.json",
        json={"doc": {"node_w": 158, "node_h": 46, "nodes": nodes, "groups": groups, "edges": []}},
    )
    assert r.status_code == 200, r.text
    db.expire_all()
    m = db.query(Module).filter_by(product="usp", id="biz").one()
    assert m.kind == "group"
    kid = db.query(Module).filter_by(product="usp", id="secret_mod").one()
    assert kid.parent_id == "biz"
    g = client.get("/api/v1/products/usp/graph").json()
    assert all(n["id"] != "biz" for n in g["nodes"])
    wrap = next(x for x in g["groups"] if x["id"] == "biz")
    assert "secret_mod" in wrap["members"]


def test_board_save_move_node_to_nested_board(client, db, crypto):
    login(client, "admin", "admin-secret")
    host = db.query(Module).filter_by(product="usp", id="biz").one()
    host.nested_board = "boards/biz.json"
    db.add(
        Board(
            product="usp",
            rel_path="boards/biz.json",
            doc={"nodes": [{"id": "biz_kid", "x": 8, "y": 9}], "groups": [], "edges": []},
        )
    )
    db.add(
        Module(
            id="biz_kid",
            product="usp",
            board_rel="boards/biz.json",
            kind="feature",
            title_blob=crypto.encrypt("子", aad="module.title:usp:biz_kid"),
            current_version="v1.0",
            parent_id="biz",
        )
    )
    db.add(ModuleVersion(product="usp", module_id="biz_kid", semver="v1.0", lifecycle_band="done", progress=100))
    db.commit()
    g0 = client.get("/api/v1/products/usp/graph").json()
    nodes = list(g0["nodes"])
    nodes.append({"id": "usp_moved", "x": 12, "y": 24, "title": "新功能"})
    assert client.put(
        "/api/v1/products/usp/boards/board.json",
        json={"doc": {"node_w": 158, "node_h": 46, "nodes": nodes, "groups": g0["groups"], "edges": g0["edges"]}},
    ).status_code == 200
    nested_put = client.put(
        "/api/v1/products/usp/boards/boards/biz.json",
        json={
            "doc": {
                "node_w": 158,
                "node_h": 46,
                "nodes": [
                    {"id": "biz_kid", "x": 8, "y": 9, "title": "子"},
                    {"id": "usp_moved", "x": 40, "y": 40, "title": "新功能"},
                ],
                "groups": [],
                "edges": [],
            }
        },
    )
    assert nested_put.status_code == 200, nested_put.text
    parent_nodes = [n for n in nodes if n["id"] != "usp_moved"]
    parent_put = client.put(
        "/api/v1/products/usp/boards/board.json",
        json={"doc": {"node_w": 158, "node_h": 46, "nodes": parent_nodes, "groups": g0["groups"], "edges": g0["edges"]}},
    )
    assert parent_put.status_code == 200, parent_put.text
    db.expire_all()
    moved = db.query(Module).filter_by(product="usp", id="usp_moved").one()
    assert moved.board_rel == "boards/biz.json"
    assert moved.parent_id == "biz"
    root = client.get("/api/v1/products/usp/graph").json()
    assert all(n["id"] != "usp_moved" for n in root["nodes"])
    nest = client.get("/api/v1/products/usp/graph?board=boards/biz.json").json()
    assert any(n["id"] == "usp_moved" for n in nest["nodes"])


def test_lifecycle_cannot_regress(client):
    login(client, "eng", "eng-secret")
    r = client.put(
        "/api/v1/products/usp/modules/secret_mod/lifecycle",
        json={"band": "pre", "progress": 0},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "cannot_regress"


def test_agent_cannot_set_lifecycle(client):
    from tests.test_agent_pack import _bearer

    headers = _bearer(client, "eng", "eng-secret")
    r = client.put(
        "/api/v1/products/usp/modules/secret_mod/lifecycle",
        json={"band": "test", "progress": 40},
        headers=headers,
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "page_only"
    login(client, "eng", "eng-secret")
    page = client.get("/api/v1/products/usp/modules/secret_mod").json()
    assert page["lifecycle"]["band"] == "dev"


def test_new_module_starts_designing(client):
    login(client, "admin", "admin-secret")
    g0 = client.get("/api/v1/products/usp/graph").json()
    nodes = list(g0["nodes"]) + [
        {
            "id": "born_pre",
            "x": 0,
            "y": 120,
            "title": "新模块",
            "band": "done",
            "progress": 100,
            "lifecycle": {"band": "done", "progress": 100},
        }
    ]
    saved = client.put(
        "/api/v1/products/usp/boards/board.json",
        json={"doc": {"node_w": 158, "node_h": 46, "nodes": nodes, "groups": g0["groups"], "edges": g0["edges"]}},
    )
    assert saved.status_code == 200, saved.text
    page = client.get("/api/v1/products/usp/modules/born_pre").json()
    assert page["lifecycle"]["band"] == "pre"
    assert page["lifecycle"]["progress"] == 0


def test_admin_can_reopen_done_module(client):
    login(client, "eng", "eng-secret")
    done = client.put(
        "/api/v1/products/usp/modules/secret_mod/lifecycle",
        json={"band": "done", "progress": 100},
    )
    assert done.status_code == 200, done.text
    locked = client.get("/api/v1/products/usp/modules/secret_mod").json()
    assert locked["can_edit_lifecycle"] is False
    back = client.put(
        "/api/v1/products/usp/modules/secret_mod/lifecycle",
        json={"band": "dev", "progress": 0},
    )
    assert back.status_code == 403
    login(client, "admin", "admin-secret")
    page = client.get("/api/v1/products/usp/modules/secret_mod").json()
    assert page["can_edit_lifecycle"] is True
    reopen = client.put(
        "/api/v1/products/usp/modules/secret_mod/lifecycle",
        json={"band": "dev", "progress": 0},
    )
    assert reopen.status_code == 200, reopen.text
    assert client.get("/api/v1/products/usp/modules/secret_mod").json()["lifecycle"]["band"] == "dev"


def test_lifecycle_done_not_editable(client):
    login(client, "eng", "eng-secret")
    r = client.put(
        "/api/v1/products/usp/modules/secret_mod/lifecycle",
        json={"band": "done", "progress": 100},
    )
    assert r.status_code == 200, r.text
    page = client.get("/api/v1/products/usp/modules/secret_mod").json()
    assert page["lifecycle"]["band"] == "done"
    assert page["can_edit_lifecycle"] is False
    again = client.put(
        "/api/v1/products/usp/modules/secret_mod/lifecycle",
        json={"band": "done", "progress": 100},
    )
    assert again.status_code == 403


def test_module_version_switch(client, db, crypto):
    login(client, "admin", "admin-secret")
    biz = db.query(Module).filter_by(product="usp", id="biz").one()
    biz.current_version = "v1.1"
    db.add(
        ModuleVersion(
            product="usp", module_id="biz", semver="v1.1", lifecycle_band="done", progress=100
        )
    )
    doc10 = db.query(Document).filter_by(product="usp", module_id="biz", version="v1.0").one()
    doc10.body_blob = crypto.encrypt("DOC_V10", aad="document:usp:biz:v1.0")
    doc10.content_hash = crypto.content_hash("DOC_V10")
    db.add(
        Document(
            product="usp",
            module_id="biz",
            version="v1.1",
            body_blob=crypto.encrypt("DOC_V11", aad="document:usp:biz:v1.1"),
            content_hash=crypto.content_hash("DOC_V11"),
            updated_by="t",
        )
    )
    db.commit()
    latest = client.get("/api/v1/products/usp/modules/biz").json()
    assert latest["version"] == "v1.1"
    assert latest["latest_version"] == "v1.1"
    assert latest["versions"] == ["v1.0", "v1.1"]
    assert "DOC_V11" in latest["markdown"]
    old = client.get("/api/v1/products/usp/modules/biz?version=v1.0").json()
    assert old["version"] == "v1.0"
    assert "DOC_V10" in old["markdown"]
    assert old["lifecycle"]["band"] == "done"
    assert old["lifecycle"]["derived"] is False
    assert old["can_edit_lifecycle"] is False
    assert old["can_edit"] is False
    assert latest["can_edit"] is True
    bad = client.get("/api/v1/products/usp/modules/biz?version=v9.9")
    assert bad.status_code == 404
    assert bad.json()["detail"] == "unknown_version"


def test_module_html_rewrites_legacy_version_href(client):
    login(client, "eng", "eng-secret")
    r = client.put(
        "/api/v1/products/usp/modules/secret_mod/document",
        json={"markdown": "> 仍以 [v1.0](./secret_mod-v1.0.html) 为准。\n"},
    )
    assert r.status_code == 200, r.text
    page = client.get("/api/v1/products/usp/modules/secret_mod").json()
    assert "/usp/m/secret_mod?v=v1.0" in page["html"]
    assert "secret_mod-v1.0.html" not in page["html"]


def test_module_html_rewrites_version_file_alias(client, db, crypto):
    login(client, "admin", "admin-secret")
    biz = db.query(Module).filter_by(product="usp", id="biz").one()
    biz.current_version = "v1.1"
    db.add(ModuleVersion(product="usp", module_id="biz", semver="v1.1", lifecycle_band="dev"))
    body = "> 完整说明见 [上版本-v1.0](./um_6e3357f6.html)。\n"
    db.add(
        Document(
            product="usp",
            module_id="biz",
            version="v1.1",
            body_blob=crypto.encrypt(body, aad="document:usp:biz:v1.1"),
            content_hash=crypto.content_hash(body),
            updated_by="t",
        )
    )
    db.commit()
    page = client.get("/api/v1/products/usp/modules/biz").json()
    assert page["version"] == "v1.1"
    assert "/usp/m/biz?v=v1.0" in page["html"]
    assert "/usp/m/um_6e3357f6" not in page["html"]
    old = client.get("/api/v1/products/usp/modules/biz?version=v1.0")
    assert old.status_code == 200
    assert old.json()["markdown"]


def test_module_html_keeps_tex(client, db, crypto):
    login(client, "admin", "admin-secret")
    doc10 = db.query(Document).filter_by(product="usp", module_id="biz", version="v1.0").one()
    tex = "给定 \\(\\mathcal{T}\\) 与 \\(\\mathcal{R}\\)\n\n\\[\n\\mu:\\mathcal{T}'\\rightarrow\\mathcal{R}\n\\]\n"
    doc10.body_blob = crypto.encrypt(tex, aad="document:usp:biz:v1.0")
    doc10.content_hash = crypto.content_hash(tex)
    db.commit()
    page = client.get("/api/v1/products/usp/modules/biz?version=v1.0").json()
    assert "\\(\\mathcal{T}\\)" in page["html"]
    assert "\\[" in page["html"]
    assert "<em>" not in page["html"]


def test_non_admin_cannot_protect_module(client):
    login(client, "eng", "eng-secret")
    r = client.put("/api/v1/products/usp/modules/secret_mod/protect", json={"protected": True})
    assert r.status_code == 403


def test_admin_protect_blocks_module_delete(client, db):
    login(client, "admin", "admin-secret")
    assert client.put("/api/v1/products/usp/modules/biz/protect", json={"protected": True}).status_code == 200
    g0 = client.get("/api/v1/products/usp/graph").json()
    biz = next(n for n in g0["nodes"] if n["id"] == "biz")
    assert biz["admin_protected"] is True
    assert biz["can_delete"] is False
    nodes = [n for n in g0["nodes"] if n["id"] != "biz"]
    bad = client.put(
        "/api/v1/products/usp/boards/board.json",
        json={"doc": {"node_w": 158, "node_h": 46, "nodes": nodes, "groups": g0["groups"], "edges": g0["edges"]}},
    )
    assert bad.status_code == 403
    assert bad.json()["detail"] == "protected"
    assert client.put("/api/v1/products/usp/modules/biz/protect", json={"protected": False}).status_code == 200
    ok = client.put(
        "/api/v1/products/usp/boards/board.json",
        json={"doc": {"node_w": 158, "node_h": 46, "nodes": nodes, "groups": g0["groups"], "edges": g0["edges"]}},
    )
    assert ok.status_code == 200, ok.text
    db.expire_all()
    assert db.query(Module).filter_by(product="usp", id="biz").one_or_none() is None


def test_admin_protect_blocks_group_delete(client):
    login(client, "admin", "admin-secret")
    g0 = client.get("/api/v1/products/usp/graph").json()
    nodes = [n for n in g0["nodes"] if n["id"] != "biz"]
    groups = list(g0["groups"])
    groups.append({"id": "biz", "x": 0, "y": 0, "w": 0, "h": 0, "title": "业务分配", "members": ["secret_mod"]})
    assert (
        client.put(
            "/api/v1/products/usp/boards/board.json",
            json={"doc": {"node_w": 158, "node_h": 46, "nodes": nodes, "groups": groups, "edges": []}},
        ).status_code
        == 200
    )
    assert client.put("/api/v1/products/usp/modules/biz/protect", json={"protected": True}).status_code == 200
    g1 = client.get("/api/v1/products/usp/graph").json()
    wrap = next(x for x in g1["groups"] if x["id"] == "biz")
    assert wrap["admin_protected"] is True
    assert wrap["can_delete"] is False
    gone = [g for g in g1["groups"] if g["id"] != "biz"]
    bad = client.put(
        "/api/v1/products/usp/boards/board.json",
        json={"doc": {"node_w": 158, "node_h": 46, "nodes": g1["nodes"], "groups": gone, "edges": g1["edges"]}},
    )
    assert bad.status_code == 403
    assert bad.json()["detail"] == "protected"


def test_parent_protect_does_not_block_child_delete(client, db):
    login(client, "admin", "admin-secret")
    g0 = client.get("/api/v1/products/usp/graph").json()
    nodes = [n for n in g0["nodes"] if n["id"] != "biz"]
    groups = list(g0["groups"])
    groups.append({"id": "biz", "x": 0, "y": 0, "w": 0, "h": 0, "title": "业务分配", "members": ["secret_mod"]})
    assert (
        client.put(
            "/api/v1/products/usp/boards/board.json",
            json={"doc": {"node_w": 158, "node_h": 46, "nodes": nodes, "groups": groups, "edges": []}},
        ).status_code
        == 200
    )
    assert client.put("/api/v1/products/usp/modules/biz/protect", json={"protected": True}).status_code == 200
    g1 = client.get("/api/v1/products/usp/graph").json()
    child = next(n for n in g1["nodes"] if n["id"] == "secret_mod")
    assert child["can_delete"] is True
    nodes = [n for n in g1["nodes"] if n["id"] != "secret_mod"]
    groups = [
        {**g, "members": [m for m in (g.get("members") or []) if m != "secret_mod"]} for g in g1["groups"]
    ]
    ok = client.put(
        "/api/v1/products/usp/boards/board.json",
        json={"doc": {"node_w": 158, "node_h": 46, "nodes": nodes, "groups": groups, "edges": []}},
    )
    assert ok.status_code == 200, ok.text
    db.expire_all()
    assert db.query(Module).filter_by(product="usp", id="biz").one() is not None
    assert db.query(Module).filter_by(product="usp", id="secret_mod").one_or_none() is None


def test_module_protect_locks_oldest_version(client, db):
    login(client, "admin", "admin-secret")
    db.add(ModuleVersion(product="usp", module_id="secret_mod", semver="v1.1", lifecycle_band="pre", progress=0))
    db.commit()
    assert client.put("/api/v1/products/usp/modules/secret_mod/protect", json={"protected": True}).status_code == 200
    old = client.get("/api/v1/products/usp/modules/secret_mod?version=v1.0").json()
    new = client.get("/api/v1/products/usp/modules/secret_mod?version=v1.1").json()
    assert old["admin_protected"] is True
    assert old["version_protected"] is True
    assert new["admin_protected"] is True
    assert new["version_protected"] is False
    deny = client.put(
        "/api/v1/products/usp/modules/secret_mod/versions/v1.0/protect", json={"protected": False}
    )
    assert deny.status_code == 403
    assert deny.json()["detail"] == "last_protected_version"
    assert client.put(
        "/api/v1/products/usp/modules/secret_mod/versions/v1.1/protect", json={"protected": True}
    ).status_code == 200
    assert (
        client.put(
            "/api/v1/products/usp/modules/secret_mod/versions/v1.0/protect", json={"protected": False}
        ).status_code
        == 200
    )


def test_edit_rights_can_delete_unprotected_version(client, db):
    login(client, "admin", "admin-secret")
    db.add(ModuleVersion(product="usp", module_id="secret_mod", semver="v1.1", lifecycle_band="pre", progress=0))
    db.commit()
    login(client, "eng", "eng-secret")
    page = client.get("/api/v1/products/usp/modules/secret_mod").json()
    assert page["can_delete_version"] is True
    r = client.delete("/api/v1/products/usp/modules/secret_mod/versions/v1.1")
    assert r.status_code == 200, r.text
    left = client.get("/api/v1/products/usp/modules/secret_mod").json()
    assert left["versions"] == ["v1.0"]
    last = client.delete("/api/v1/products/usp/modules/secret_mod/versions/v1.0")
    assert last.status_code == 400
    assert last.json()["detail"] == "last_version"


def test_protected_version_cannot_delete(client, db):
    login(client, "admin", "admin-secret")
    db.add(ModuleVersion(product="usp", module_id="secret_mod", semver="v1.1", lifecycle_band="pre", progress=0))
    db.commit()
    assert client.put(
        "/api/v1/products/usp/modules/secret_mod/versions/v1.1/protect", json={"protected": True}
    ).status_code == 200
    login(client, "eng", "eng-secret")
    page = client.get("/api/v1/products/usp/modules/secret_mod?version=v1.1").json()
    assert page["version_protected"] is True
    assert page["can_delete_version"] is False
    r = client.delete("/api/v1/products/usp/modules/secret_mod/versions/v1.1")
    assert r.status_code == 403
    assert r.json()["detail"] == "protected"


def test_create_feature_version_switches_latest_and_copies_doc(client):
    login(client, "eng", "eng-secret")
    blocked = client.post(
        "/api/v1/products/usp/modules/secret_mod/versions",
        json={"semver": "v1.0.1"},
    )
    assert blocked.status_code == 400
    assert blocked.json()["detail"] == "not_done"
    done = client.put(
        "/api/v1/products/usp/modules/secret_mod/lifecycle",
        json={"band": "done", "progress": 100},
    )
    assert done.status_code == 200, done.text
    r = client.post(
        "/api/v1/products/usp/modules/secret_mod/versions",
        json={"semver": "v1.0.1"},
    )
    assert r.status_code == 200, r.text
    latest = client.get("/api/v1/products/usp/modules/secret_mod").json()
    assert latest["version"] == "v1.0.1"
    assert latest["latest_version"] == "v1.0.1"
    assert latest["can_edit"] is True
    assert SECRET_BODY in latest["markdown"]
    assert latest["lifecycle"]["band"] == "pre"
    old = client.get("/api/v1/products/usp/modules/secret_mod?version=v1.0").json()
    assert old["can_edit"] is False
    assert old["can_edit_lifecycle"] is False
    assert old["lifecycle"]["band"] == "done"
    assert old["lifecycle"]["derived"] is False
    frozen = client.put(
        "/api/v1/products/usp/modules/secret_mod/document",
        json={"markdown": "NO", "version": "v1.0"},
    )
    assert frozen.status_code == 403
    assert frozen.json()["detail"] == "frozen"
    lc_old = client.put(
        "/api/v1/products/usp/modules/secret_mod/lifecycle",
        json={"band": "test", "progress": 10, "version": "v1.0"},
    )
    assert lc_old.status_code == 403
    assert lc_old.json()["detail"] == "frozen"
    again = client.post(
        "/api/v1/products/usp/modules/secret_mod/versions",
        json={"semver": "v1.0.1"},
    )
    assert again.status_code == 400
    low = client.post(
        "/api/v1/products/usp/modules/secret_mod/versions",
        json={"semver": "v0.9"},
    )
    assert low.status_code == 400


def test_hours_done_keeps_open_week_and_locks_edits(client):
    login(client, "eng", "eng-secret")
    week = iso_week_id()
    r = client.put(
        "/api/v1/products/usp/hours",
        json={
            "feature_id": "secret_mod",
            "version": "v1.0",
            "expected_hours": 8,
            "hours": 3,
            "week": week,
        },
    )
    assert r.status_code == 200, r.text
    done = client.put(
        "/api/v1/products/usp/modules/secret_mod/lifecycle",
        json={"band": "done", "progress": 100},
    )
    assert done.status_code == 200, done.text
    page = client.get("/api/v1/products/usp/modules/secret_mod").json()
    assert page["lifecycle"]["band"] == "done"
    assert page["hours"]["week_invested"] == 3
    assert page["hours"]["invested"] == 0
    assert page["hours"]["can_register"] is False
    assert page["hours"]["settled"] is True
    assert page["hours"]["expected_hours"] is None
    assert any(u["user"] == "eng" and u["hours"] == 3 for u in page["hours"]["by_user"])
    bad = client.put(
        "/api/v1/products/usp/hours",
        json={"feature_id": "secret_mod", "version": "v1.0", "hours": 4, "week": week},
    )
    assert bad.status_code == 403
    assert bad.json()["detail"] == "settled"
    exp = client.put(
        "/api/v1/products/usp/hours",
        json={"feature_id": "secret_mod", "version": "v1.0", "expected_hours": 10},
    )
    assert exp.status_code == 403
    assert exp.json()["detail"] == "settled"


def test_guest_cannot_create_feature_version(client):
    login(client, "guest", "guest-secret")
    r = client.post(
        "/api/v1/products/usp/modules/secret_mod/versions",
        json={"semver": "v1.0.1"},
    )
    assert r.status_code == 403


def test_board_save_does_not_clobber_current_version(client, db):
    login(client, "admin", "admin-secret")
    m = db.query(Module).filter_by(product="usp", id="secret_mod").one()
    assert m.current_version == "v1.0"
    g0 = client.get("/api/v1/products/usp/graph").json()
    nodes = [{**n, "version": "v1.0.2"} if n["id"] == "secret_mod" else n for n in g0["nodes"]]
    r = client.put(
        "/api/v1/products/usp/boards/board.json",
        json={"doc": {"node_w": 158, "node_h": 46, "nodes": nodes, "groups": g0["groups"], "edges": g0["edges"]}},
    )
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.query(Module).filter_by(product="usp", id="secret_mod").one().current_version == "v1.0"
    page = client.get("/api/v1/products/usp/modules/secret_mod").json()
    assert page["version"] == "v1.0"
    assert page["latest_version"] == "v1.0"


def test_dangling_pointer_without_version_row_snaps_back(client, db, crypto):
    login(client, "admin", "admin-secret")
    m = db.query(Module).filter_by(product="usp", id="secret_mod").one()
    m.current_version = "v1.0.2"
    db.add(
        ModuleVersion(
            product="usp", module_id="secret_mod", semver="v1.0.1", lifecycle_band="done", progress=100
        )
    )
    db.add(
        Document(
            product="usp",
            module_id="secret_mod",
            version="v1.0.1",
            body_blob=crypto.encrypt("DOC_V101", aad="document:usp:secret_mod:v1.0.1"),
            content_hash=crypto.content_hash("DOC_V101"),
            updated_by="t",
        )
    )
    db.commit()
    page = client.get("/api/v1/products/usp/modules/secret_mod")
    assert page.status_code == 200, page.text
    body = page.json()
    assert body["version"] == "v1.0.1"
    assert body["latest_version"] == "v1.0.1"
    assert "DOC_V101" in body["markdown"]
    db.expire_all()
    assert db.query(Module).filter_by(product="usp", id="secret_mod").one().current_version == "v1.0.1"
    assert db.query(Document).filter_by(product="usp", module_id="secret_mod", version="v1.0.2").first() is None


def test_dangling_newer_version_becomes_latest(client, db):
    login(client, "admin", "admin-secret")
    db.add(
        ModuleVersion(
            product="usp", module_id="secret_mod", semver="v1.0.2", lifecycle_band="pre", progress=0
        )
    )
    db.commit()
    g = client.get("/api/v1/products/usp/graph").json()
    node = next(n for n in g["nodes"] if n["id"] == "secret_mod")
    assert node["version"] == "v1.0.2"
    page = client.get("/api/v1/products/usp/modules/secret_mod").json()
    assert page["version"] == "v1.0.2"
    assert page["latest_version"] == "v1.0.2"
    assert page["can_edit"] is True
    assert SECRET_BODY in page["markdown"]
    old = client.get("/api/v1/products/usp/modules/secret_mod?version=v1.0").json()
    assert old["can_edit"] is False


def test_module_page_exposes_board_rel(client, db, crypto):
    login(client, "admin", "admin-secret")
    db.add(
        Board(
            product="usp",
            rel_path="boards/usp_backend.json",
            doc={"nodes": [{"id": "be_mod", "x": 1, "y": 1}], "groups": [], "edges": []},
        )
    )
    db.add(
        Module(
            id="be_mod",
            product="usp",
            board_rel="boards/usp_backend.json",
            kind="feature",
            title_blob=crypto.encrypt("后端模块", aad="module.title:usp:be_mod"),
            current_version="v1.0",
        )
    )
    db.add(ModuleVersion(product="usp", module_id="be_mod", semver="v1.0", lifecycle_band="pre", progress=0))
    db.commit()
    page = client.get("/api/v1/products/usp/modules/be_mod").json()
    assert page["board_rel"] == "boards/usp_backend.json"
    root = client.get("/api/v1/products/usp/modules/secret_mod").json()
    assert root["board_rel"] == "board.json"
    dev = client.get("/api/v1/products/usp/modules/usp_dev").json()
    assert dev["board_rel"] == "board.json"
