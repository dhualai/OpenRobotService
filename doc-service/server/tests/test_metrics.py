from app.infra.models import Module
from app.services.metrics import _NH, _NW, _POS, seed_product_metrics
from tests.conftest import login


def test_metrics_layout_boxes_do_not_overlap():
    items = list(_POS.items())
    for i, (a, (ax, ay)) in enumerate(items):
        for b, (bx, by) in items[i + 1 :]:
            hit = ax < bx + _NW and bx < ax + _NW and ay < by + _NH and by < ay + _NH
            assert not hit, f"{a} overlaps {b}"


def test_graph_metrics_id_absent_without_host(client):
    login(client, "admin", "admin-secret")
    g = client.get("/api/v1/products/usp/graph").json()
    hub = next(x for x in g["groups"] if x["id"] == "usp_algo")
    assert not hub.get("metrics_id")


def test_usp_metrics_seed_graph_and_docs(client, db, crypto):
    login(client, "admin", "admin-secret")
    out = seed_product_metrics(db, crypto, "usp", overwrite=True)
    db.commit()
    assert out["host"] == "usp_metrics"
    assert out["children"] == 18
    g = client.get("/api/v1/products/usp/graph").json()
    hub = next(x for x in g["groups"] if x["id"] == "usp_algo")
    assert hub["metrics_id"] == "usp_metrics"
    page = client.get("/api/v1/products/usp/modules/usp_metrics").json()
    assert page["kind"] == "metrics"
    assert page["title"] == "指标"
    assert "吞吐量" in page["markdown"]
    assert page["hours"] is None
    assert page["can_log_hours"] is False
    assert page["can_edit_lifecycle"] is False
    nest = client.get("/api/v1/products/usp/modules/usp_metrics/nested-graph").json()
    assert nest["skip"] is False
    assert nest["empty"] is False
    ids = {n["id"] for n in nest["nodes"]}
    assert "usp_m_theory" in ids
    assert "usp_m_mapf" in ids
    assert "usp_m_biz" in ids
    child = client.get("/api/v1/products/usp/modules/usp_m_mapf").json()
    assert child["kind"] == "metrics"
    assert "/usp/m/usp_m_lmapf" in child["markdown"]
    cat = client.get("/api/v1/admin/catalog?product=usp").json()
    algo = next(b for b in cat["tree"] if b["id"] == "algo")
    tree_ids = {n["id"] for n in algo["nodes"]}
    assert "usp_metrics" not in tree_ids
    assert "usp_m_theory" not in tree_ids
    host = db.query(Module).filter_by(product="usp", id="usp_metrics").one()
    assert host.parent_id == "usp_algo"


def test_ors_metrics_host_empty(client, db, crypto):
    login(client, "admin", "admin-secret")
    seed_product_metrics(db, crypto, "ors", overwrite=True)
    db.commit()
    page = client.get("/api/v1/products/ors/modules/ors_metrics")
    assert page.status_code == 200, page.text
    body = page.json()
    assert body["kind"] == "metrics"
    assert body["title"] == "指标"
    nest = client.get("/api/v1/products/ors/modules/ors_metrics/nested-graph").json()
    assert nest["skip"] is False
    assert nest["empty"] is True
    assert nest["nodes"] == []
