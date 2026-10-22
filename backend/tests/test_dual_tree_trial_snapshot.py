"""双树试做本机快照接口（读写仓库 `.local/责任双树/dual-tree-trial.snapshot.json`）。

被测：`GET/PUT /api/admin/dual-tree-trial/snapshot`（见 app/modules/admin/api/dual_tree_trial.py）。

口径：
  - 登录即可（与页面上已有的 `/admin/module-tree/` 同口径），不额外要后台权限；
  - 快照只落在 gitignore 的 `.local/`，不入库、不进 git，所以用例必须 monkeypatch
    `snapshot_path`，别写到真仓库目录里；
  - 没有快照文件 → exists=False 的 200（不算错误，页面据此不显示「恢复」）；
  - 保存要 {nodes: [...], mounts: [...]}，坏形状 → 400；文件内容坏了 → 500；
  - 文件写 UTF-8 且不转义中文（人得能直接看）。
"""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.admin.api import dual_tree_trial as api
from app.modules.admin.api.auth import get_current_active_user_from_token
from app.modules.admin.api.dual_tree_trial import router as dual_tree_router

LOGIN_USER = {"id": "u-1", "username": "bob", "name": "Bob", "permissions": [], "roles": {}}

NODES = [
    {"id": "dom", "parentId": None, "kind": "domain", "name": "车体硬件", "description": "", "vehicleModels": ""},
    {"id": "sk", "parentId": "dom", "kind": "skill", "name": "机械", "description": "", "vehicleModels": ""},
]
MOUNTS = [{"nodeId": "sk", "userId": "u1", "role": "specialist"}]


@pytest.fixture
def snapshot_file(tmp_path, monkeypatch):
    """快照落到临时目录，绝不碰仓库 .local。"""
    path = tmp_path / ".local" / "责任双树" / "dual-tree-trial.snapshot.json"
    monkeypatch.setattr(api, "snapshot_path", lambda: path)
    return path


@pytest.fixture
def app(snapshot_file):
    application = FastAPI()
    application.include_router(dual_tree_router, prefix="/api/admin")
    return application


@pytest.fixture
def client(app):
    app.dependency_overrides[get_current_active_user_from_token] = lambda: dict(LOGIN_USER)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_没有快照文件时返回_exists_false(client, snapshot_file):
    resp = client.get("/api/admin/dual-tree-trial/snapshot")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["exists"] is False
    assert data["nodes"] == 0 and data["mounts"] == 0
    assert data["path"] == str(snapshot_file)


def test_保存后能原样读回_且中文不转义(client, snapshot_file):
    resp = client.put(
        "/api/admin/dual-tree-trial/snapshot",
        json={"snapshot": {"nodes": NODES, "mounts": MOUNTS, "saved_at": "2026-10-14 15:52:07"}},
    )
    assert resp.status_code == 200
    saved = resp.json()["data"]
    assert saved["exists"] is True
    assert (saved["nodes"], saved["mounts"]) == (2, 1)
    assert saved["saved_at"] == "2026-10-14 15:52:07"
    assert saved["saved_by"] == "bob"

    raw = snapshot_file.read_text(encoding="utf-8")
    assert "车体硬件" in raw and "\\u" not in raw
    on_disk = json.loads(raw)
    assert on_disk["format"] == api.SNAPSHOT_FORMAT
    assert on_disk["nodes"] == NODES and on_disk["mounts"] == MOUNTS

    back = client.get("/api/admin/dual-tree-trial/snapshot").json()["data"]
    assert back["exists"] is True
    assert back["snapshot"]["nodes"] == NODES
    assert back["snapshot"]["mounts"] == MOUNTS


def test_只给_nodes_mounts_也认_服务端补保存时间(client, snapshot_file):
    resp = client.put("/api/admin/dual-tree-trial/snapshot", json={"nodes": NODES, "mounts": MOUNTS})
    assert resp.status_code == 200
    assert resp.json()["data"]["saved_at"]
    assert json.loads(snapshot_file.read_text(encoding="utf-8"))["nodes"] == NODES


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"snapshot": {"nodes": NODES}},
        {"snapshot": {"nodes": {}, "mounts": []}},
        {"snapshot": {"nodes": ["不是对象"], "mounts": []}},
        {"snapshot": {"nodes": [], "mounts": [1]}},
    ],
)
def test_坏形状返回400且不落盘(client, snapshot_file, body):
    resp = client.put("/api/admin/dual-tree-trial/snapshot", json=body)
    assert resp.status_code == 400
    assert not snapshot_file.exists()


def test_节点数超上限返回400(client, snapshot_file, monkeypatch):
    monkeypatch.setattr(api, "MAX_NODES", 1)
    resp = client.put("/api/admin/dual-tree-trial/snapshot", json={"nodes": NODES, "mounts": MOUNTS})
    assert resp.status_code == 400
    assert "太大" in resp.json()["detail"]
    assert not snapshot_file.exists()


def test_文件内容坏了返回500(client, snapshot_file):
    snapshot_file.parent.mkdir(parents=True, exist_ok=True)
    snapshot_file.write_text("{不是 json", encoding="utf-8")
    assert client.get("/api/admin/dual-tree-trial/snapshot").status_code == 500


def test_快照固定落在_gitignore_的_local_目录():
    path = api.snapshot_path().as_posix()
    assert path.endswith("/.local/责任双树/dual-tree-trial.snapshot.json")
