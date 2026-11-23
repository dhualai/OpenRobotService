from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.infra.models import DocumentView, VisitLog
from tests.conftest import login


def test_module_views_admin_only_and_cooldown(client, seed):
    login(client, "eng", "eng-secret")
    a = client.post("/api/v1/products/usp/modules/biz/views")
    assert a.status_code == 200, a.text
    b = client.post("/api/v1/products/usp/modules/biz/views")
    assert b.status_code == 200
    denied = client.get("/api/v1/products/usp/modules/biz/views")
    assert denied.status_code == 403

    login(client, "admin", "admin-secret")
    listed = client.get("/api/v1/products/usp/modules/biz/views")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["total_views"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["username"] == "eng"
    assert body["items"][0]["count"] == 1

    client.post("/api/v1/products/usp/modules/biz/views")
    again = client.get("/api/v1/products/usp/modules/biz/views").json()
    by = {x["username"]: x["count"] for x in again["items"]}
    assert by["eng"] == 1
    assert "admin" not in by
    assert again["total_views"] == 1
    names = [x["username"] for x in again["items"]]
    assert names == ["eng"]


def test_requirement_views_admin_list(client, seed):
    login(client, "eng", "eng-secret")
    created = client.post("/api/v1/products/usp/requirements", json={"title": "浏览记录用例"})
    assert created.status_code == 201, created.text
    rid = created.json()["id"]
    assert client.post(f"/api/v1/products/usp/requirements/{rid}/views").status_code == 200
    assert client.get(f"/api/v1/products/usp/requirements/{rid}/views").status_code == 403

    login(client, "admin", "admin-secret")
    listed = client.get(f"/api/v1/products/usp/requirements/{rid}/views")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["total_views"] == 1
    assert body["items"][0]["username"] == "eng"


def test_admin_visits_timeline_login_and_pages(client, seed):
    login(client, "eng", "eng-secret")
    assert client.post("/api/v1/products/usp/modules/biz/views").status_code == 200
    assert client.post("/api/v1/products/usp/visits").status_code == 200
    denied = client.get("/api/v1/admin/visits")
    assert denied.status_code == 403

    login(client, "admin", "admin-secret")
    listed = client.get("/api/v1/admin/visits")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["days"] == 7
    kinds = [x["kind"] for x in body["items"]]
    assert "login" in kinds
    assert "module" in kinds
    assert "home" in kinds
    assert all(x["username"] != "admin" for x in body["items"])
    times = [x["at"] for x in body["items"] if x["at"]]
    assert times == sorted(times, reverse=True)
    biz = next(x for x in body["items"] if x["kind"] == "module")
    assert biz["href"] and biz["href"].startswith("/usp/m/biz")
    assert biz["name"]


def test_home_visit_cooldown_matches_document_views(client, seed, db):
    login(client, "eng", "eng-secret")
    assert client.post("/api/v1/products/usp/visits").status_code == 200
    assert client.post("/api/v1/products/usp/visits").status_code == 200

    login(client, "admin", "admin-secret")
    homes = [x for x in client.get("/api/v1/admin/visits").json()["items"] if x["kind"] == "home"]
    assert len(homes) == 1

    now = datetime.now(timezone.utc)
    db.expire_all()
    row = db.scalar(
        select(DocumentView).where(
            DocumentView.kind == "home",
            DocumentView.product == "usp",
            DocumentView.username == "eng",
        )
    )
    assert row is not None
    row.last_seen_at = now - timedelta(seconds=90)
    log = db.scalar(select(VisitLog).where(VisitLog.id == homes[0]["id"]))
    assert log is not None
    log.at = now - timedelta(seconds=150)
    db.commit()

    login(client, "eng", "eng-secret")
    assert client.post("/api/v1/products/usp/visits").status_code == 200
    login(client, "admin", "admin-secret")
    homes_after = [x for x in client.get("/api/v1/admin/visits").json()["items"] if x["kind"] == "home"]
    assert len(homes_after) == 1
    assert homes_after[0]["id"] == homes[0]["id"]


def test_admin_visits_rolls_after_seven_days(client, db):
    login(client, "admin", "admin-secret")
    stale = VisitLog(
        username="eng",
        kind="login",
        title="",
        at=datetime.now(timezone.utc) - timedelta(days=8),
    )
    fresh = VisitLog(
        username="eng",
        kind="home",
        product="usp",
        title="USP",
        at=datetime.now(timezone.utc) - timedelta(days=6),
    )
    db.add_all([stale, fresh])
    db.commit()
    stale_id, fresh_id = stale.id, fresh.id
    body = client.get("/api/v1/admin/visits").json()
    ids = [i["id"] for i in body["items"]]
    assert stale_id not in ids
    assert fresh_id in ids
    db.expire_all()
    assert db.get(VisitLog, stale_id) is None
    assert db.get(VisitLog, fresh_id) is not None
