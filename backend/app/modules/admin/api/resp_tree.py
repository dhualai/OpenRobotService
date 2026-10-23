"""责任双树接口：两棵树（org / skill）的节点与人挂载。

取代 `dual_tree_trial.py` 的「整份 JSON 快照」—— 那套把树交给浏览器 localStorage 与
一个 JSON 文件，加载时多份副本互相合并，2026-10-15、10-16 各丢过一次组织树（车体硬件
7 个组织清零）。这里落库做唯一真相源，接口按 `.local/责任双树/责任双树实现计划.md`
§2 的路径表，采用节点级 CRUD。

并发：写操作可带 `expected_revision`；与库中不符返回 409，前端据此刷新而不静默覆盖。

权限：登录即可（与页面上已有的 `/admin/module-tree/`、`/admin/dual-tree-trial/` 同口径）。
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_db as get_db
from app.modules.admin.api.auth import get_current_active_user_from_token
from app.modules.admin.schemas.response import DataResponse
from app.modules.admin.services import resp_tree_service as svc

router = APIRouter(tags=["admin-resp-tree"])


def _ok(data: Any) -> DataResponse:
    return DataResponse(code=0, message="success", data=data)


def _fail(error: Exception) -> HTTPException:
    """服务层异常 → HTTP 状态：乐观锁冲突 409 / 找不到 404 / 入参 400。"""
    if isinstance(error, svc.RevisionConflict):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, LookupError):
        return HTTPException(status_code=404, detail=str(error))
    return HTTPException(status_code=400, detail=str(error))


async def _run(coro):
    """统一把服务层的领域异常翻成 HTTP。"""
    try:
        return await coro
    except HTTPException:
        raise
    except (svc.RevisionConflict, LookupError, ValueError) as error:
        raise _fail(error) from error


# ── 树 ──────────────────────────────────────────────────────

@router.get("/resp-trees", response_model=DataResponse, summary="责任双树：树列表")
async def list_resp_trees(
    kind: Optional[str] = Query(None, description="org / skill；不传返回全部"),
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    return _ok(await _run(svc.list_trees(db, kind)))


@router.post("/resp-trees/seed-defaults", response_model=DataResponse,
             summary="责任双树：确保 org / skill 两棵树存在（空库初始化）")
async def seed_resp_trees(
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    trees = await _run(svc.ensure_trees(db))
    return _ok({"trees": [{"id": t.id, "kind": t.kind, "name": t.name,
                           "status": t.status, "revision": int(t.revision or 0)} for t in trees]})


@router.get("/resp-trees/{tree_id}/nodes", response_model=DataResponse,
            summary="责任双树：整树节点 + 挂载（含 revision）")
async def get_resp_tree_nodes(
    tree_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    return _ok(await _run(svc.get_tree_payload(db, tree_id)))


# ── 节点 ────────────────────────────────────────────────────

@router.post("/resp-nodes", response_model=DataResponse, summary="责任双树：新建节点")
async def create_resp_node(
    body: Dict[str, Any] = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    expected = body.get("expected_revision")
    return _ok(await _run(svc.create_node(db, body, expected)))


@router.patch("/resp-nodes/{node_id}", response_model=DataResponse,
              summary="责任双树：改节点（改名 / 描述 / 换父 dock）")
async def patch_resp_node(
    node_id: int,
    body: Dict[str, Any] = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    expected = body.get("expected_revision")
    patch = {k: v for k, v in (body or {}).items() if k != "expected_revision"}
    return _ok(await _run(svc.patch_node(db, node_id, patch, expected)))


@router.delete("/resp-nodes/{node_id}", response_model=DataResponse,
               summary="责任双树：递归软删子树（连挂载一起清）")
async def delete_resp_node(
    node_id: int,
    expected_revision: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    return _ok(await _run(svc.delete_node(db, node_id, expected_revision)))


@router.put("/resp-nodes/{node_id}/owners", response_model=DataResponse,
            summary="责任双树：设置编制负责人")
async def put_resp_node_owners(
    node_id: int,
    body: Dict[str, Any] = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    owners = body.get("owners")
    if not isinstance(owners, list):
        raise HTTPException(status_code=400, detail="owners 必须是数组")
    return _ok(await _run(
        svc.set_owners(db, node_id, owners, body.get("expected_revision"))))


@router.post("/resp-nodes/batch", response_model=DataResponse,
             summary="责任双树：按自然键幂等批量补节点")
async def batch_resp_nodes(
    body: Dict[str, Any] = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    tree_id = int(body.get("tree_id") or 0)
    nodes: List[Dict[str, Any]] = body.get("nodes") or []
    if not tree_id:
        raise HTTPException(status_code=400, detail="tree_id 必填")
    return _ok(await _run(
        svc.batch_create_nodes(db, tree_id, nodes, body.get("expected_revision"))))


@router.post("/resp-trees/{tree_id}/import", response_model=DataResponse,
             summary="责任双树：整树替换（导入 JSON）")
async def import_resp_tree(
    tree_id: int,
    body: Dict[str, Any] = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    return _ok(await _run(svc.replace_tree(
        db, tree_id, body.get("nodes") or [], body.get("links") or [],
        body.get("expected_revision"))))


# ── 挂载 ────────────────────────────────────────────────────

@router.get("/resp-links", response_model=DataResponse, summary="责任双树：人挂载查询")
async def list_resp_links(
    node_id: Optional[int] = Query(None),
    user_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    return _ok(await _run(svc.list_links(db, node_id, user_id)))


@router.put("/resp-links", response_model=DataResponse, summary="责任双树：挂人 / 改角色")
async def put_resp_links(
    body: Dict[str, Any] = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """单个：{user_id, node_id, role, ...}；批量：{links: [{...}, ...]}。"""
    links = body.get("links")
    if isinstance(links, list):
        return _ok(await _run(svc.put_links(db, links, body.get("expected_revision"))))
    return _ok(await _run(svc.put_link(db, body, body.get("expected_revision"))))


@router.delete("/resp-links", response_model=DataResponse, summary="责任双树：取消挂人")
async def delete_resp_link(
    node_id: int = Query(...),
    user_id: str = Query(...),
    expected_revision: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    return _ok(await _run(svc.delete_link(db, node_id, user_id, expected_revision)))
