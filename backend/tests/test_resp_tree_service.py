"""责任双树服务层：落库后的节点 / 挂载 / 乐观锁行为。

被测：`app.modules.admin.services.resp_tree_service`（三张表唯一读写入口）。

口径：
  - 空库 ensure_trees 会建 org / skill 两棵；
  - 写操作 bump revision；带 expected_revision 且不符 → RevisionConflict；
  - 删节点软删子树，挂载硬删（可再挂回同人）。
用内存 SQLite（aiosqlite），不连开发库。
"""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.resp_tree import RespNode, RespNodeLink, RespTree
from app.modules.admin.services import resp_tree_service as svc

# 只建这三张表，避免 Base.metadata 里整库建表
_TABLES = [RespTree.__table__, RespNode.__table__, RespNodeLink.__table__]


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=_TABLES))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_ensure_trees_建出_org_与_skill(db):
    trees = await svc.ensure_trees(db)
    kinds = sorted(t.kind for t in trees)
    assert kinds == ["org", "skill"]
    listed = await svc.list_trees(db)
    assert {row["kind"] for row in listed} == {"org", "skill"}
    # 再调一次不重复造
    again = await svc.ensure_trees(db)
    assert len(again) == 2


@pytest.mark.asyncio
async def test_建节点_bump_revision_且乐观锁冲突(db):
    trees = await svc.ensure_trees(db)
    skill = next(t for t in trees if t.kind == "skill")
    assert int(skill.revision or 0) == 0

    created = await svc.create_node(db, {"tree_id": skill.id, "name": "车体硬件", "node_kind": "umbrella"})
    assert created["node"]["name"] == "车体硬件"
    assert created["revision"] == 1

    with pytest.raises(svc.RevisionConflict) as raised:
        await svc.create_node(
            db,
            {"tree_id": skill.id, "name": "车端软件", "node_kind": "umbrella"},
            expected_revision=0,
        )
    assert raised.value.expected == 0 and raised.value.actual == 1

    ok = await svc.create_node(
        db,
        {"tree_id": skill.id, "name": "车端软件", "node_kind": "umbrella"},
        expected_revision=1,
    )
    assert ok["revision"] == 2


@pytest.mark.asyncio
async def test_删节点软删_挂载硬删可再挂回(db):
    trees = await svc.ensure_trees(db)
    org = next(t for t in trees if t.kind == "org")
    parent = await svc.create_node(db, {"tree_id": org.id, "name": "中力", "node_kind": "org", "org_kind": "company"})
    child = await svc.create_node(
        db,
        {"tree_id": org.id, "parent_id": parent["node"]["id"], "name": "硬件组", "node_kind": "org",
         "org_kind": "department"},
        expected_revision=parent["revision"],
    )
    linked = await svc.put_link(
        db,
        {"node_id": child["node"]["id"], "user_id": "u-bob", "role": "specialist", "link_type": "solid"},
        expected_revision=child["revision"],
    )
    assert linked["link"]["user_id"] == "u-bob"

    deleted = await svc.delete_node(db, parent["node"]["id"], expected_revision=linked["revision"])
    assert set(deleted["deleted"]) == {parent["node"]["id"], child["node"]["id"]}

    payload = await svc.get_tree_payload(db, org.id)
    assert payload["nodes"] == []
    assert payload["links"] == []

    # 子节点已软删，不能再挂人
    with pytest.raises(LookupError):
        await svc.put_link(db, {"node_id": child["node"]["id"], "user_id": "u-bob"})

    # 新节点上同人可再挂（证明挂载是硬删，唯一键没被软删行占住）
    fresh = await svc.create_node(
        db, {"tree_id": org.id, "name": "硬件组", "node_kind": "org"}, expected_revision=deleted["revision"]
    )
    again = await svc.put_link(
        db,
        {"node_id": fresh["node"]["id"], "user_id": "u-bob", "role": "member"},
        expected_revision=fresh["revision"],
    )
    assert again["link"]["role"] == "member"


@pytest.mark.asyncio
async def test_import_trial_snapshot_splits_trees_and_force_overwrite(db):
    snap = {
        "nodes": [
            {"id": "d1", "parentId": None, "kind": "domain", "name": "调度系统",
             "description": "DISP", "vehicleModels": ""},
            {"id": "s1", "parentId": "d1", "kind": "skill", "name": "任务调度",
             "description": "", "vehicleModels": ""},
            {"id": "o1", "parentId": "d1", "kind": "org", "orgKind": "company",
             "name": "中力", "description": "", "vehicleModels": "XQE"},
        ],
        "mounts": [
            {"nodeId": "s1", "userId": "u1", "role": "specialist"},
            {"nodeId": "o1", "userId": "u2", "role": "owner"},
        ],
    }
    first = await svc.import_trial_snapshot(db, snap, force=False)
    assert first["domains"] == 1 and first["skills"] == 1 and first["orgs"] == 1
    assert first["mounts"] == 2

    skill_payload = await svc.get_tree_payload(db, first["skill_tree_id"])
    org_payload = await svc.get_tree_payload(db, first["org_tree_id"])
    assert any(n["name"] == "调度系统" and n["node_kind"] == "umbrella" for n in skill_payload["nodes"])
    assert any(n["name"] == "中力" and n["org_kind"] == "company" for n in org_payload["nodes"])

    with pytest.raises(ValueError):
        await svc.import_trial_snapshot(db, snap, force=False)

    again = await svc.import_trial_snapshot(db, snap, force=True)
    assert again["domains"] == 1
    assert again["skill_revision"] >= first["skill_revision"]
