"""admin「产品→界面→功能」责任模块树 API。

前端责任模块树维护页面的后端，负责：
- GET   获取全部产品树 / 产品列表 / 可选工程师候选 / 当前用户编辑权限
- PUT/DELETE /node  单功能行新增/更新/删除（并发安全）
- POST  /submit-edit 提交对某个功能的修改（直改或创建审批单）
- GET/POST /edits* 审批单查询与审批
"""
from fastapi import APIRouter, Depends, HTTPException, Body
from typing import Dict, Any, List, Optional

from app.core.database import db_manager
from app.models.identity import UserDB
from app.modules.admin.api.auth import get_current_active_user_from_token
from app.modules.admin.services import module_tree_service
from app.modules.admin.services import module_tree_edit_service
from app.modules.admin.api.module_tree_ws import ws_broadcast_module_tree_updated

router = APIRouter(prefix="/module-tree", tags=["admin-module-tree"])


@router.get("/", summary="获取全部产品→界面→功能 树")
async def get_module_tree(
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
) -> Dict[str, Any]:
    """返回 {产品: {"interfaces": [...]}}。"""
    return module_tree_service.get_all_trees()


@router.get("/products", summary="获取产品列表")
async def get_products(
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
) -> list:
    """返回已有产品名列表。"""
    trees = module_tree_service.get_all_trees()
    return sorted(trees.keys())


@router.get("/hashes", summary="获取各产品树的版本哈希（乐观锁基准）")
async def get_product_hashes(
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
) -> Dict[str, str]:
    """返回 {产品: 产品树哈希}（乐观锁基准）。"""
    return module_tree_service.get_product_hashes()


@router.get("/func-hashes", summary="获取各功能节点哈希（功能级冲突基准）")
async def get_func_hashes(
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
) -> Dict[str, Any]:
    """返回 {产品: {'界面名||功能名': 功能哈希}}（功能级冲突基准）。"""
    return module_tree_service.get_func_hashes()


@router.get("/candidates", summary="获取可选工程师候选")
async def get_candidates(
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
) -> List[Dict[str, Any]]:
    """返回可作为功能负责人的工程师候选（id/name/department/duty_text）。"""
    db = db_manager.get_db()
    try:
        users = db.query(UserDB).filter(UserDB.status == "active").all()
        result = []
        for u in users:
            if not u.id or not (u.name or u.username):
                continue
            dept = u.department or ""
            result.append({
                "id": u.id,
                "username": u.username,
                "name": u.name or u.username,
                "department": dept,
                "job_level": u.job_level,
                "duty_text": u.duty_text or "",
            })
        result.sort(key=lambda x: (x["department"], x["name"]))
        return result
    finally:
        db.close()


@router.put("/node", summary="按行 id 新增/更新单个功能（并发安全）")
async def upsert_node(
    payload: Dict[str, Any] = Body(..., description="{id?, product, iface_name, iface_order, func_name, func_order, keywords, anchor, engineers}"),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
) -> Dict[str, Any]:
    """单功能行新增/更新：id 有则 update，否则 insert。返回该行 id 供前端绑定。"""
    node_id = payload.get("id")
    product = payload.get("product") or ""
    if not product:
        raise HTTPException(status_code=400, detail="缺少 product")
    if node_id:
        node_id = int(node_id)
    res = module_tree_service.upsert_node(
        product=product,
        iface_name=payload.get("iface_name") or "",
        iface_order=int(payload.get("iface_order") or 0),
        func_name=payload.get("func_name") or "",
        func_order=int(payload.get("func_order") or 0),
        keywords=payload.get("keywords") or [],
        anchor=payload.get("anchor") or "",
        engineers=payload.get("engineers") or [],
        node_id=node_id,
    )
    if res.get("id") is None:
        raise HTTPException(status_code=400, detail="保存失败：行不存在或写入失败")
    await ws_broadcast_module_tree_updated(product, str(current_user.get("username") or ""))
    return {"code": 0, "id": res["id"], "message": "已保存", "synced_users": res.get("synced", 0)}


@router.delete("/node", summary="按行 id 批量删除功能")
async def delete_node(
    payload: Dict[str, Any] = Body(..., description="{ids: [行id]}"),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
) -> Dict[str, Any]:
    """按行 id 批量删除功能行。"""
    ids = payload.get("ids") or []
    if isinstance(ids, (int, str)):
        ids = [ids]
    ids = [int(x) for x in ids if str(x).isdigit()]
    if not ids:
        raise HTTPException(status_code=400, detail="缺少待删除的 id 列表")
    res = module_tree_service.delete_nodes(ids)
    for p in res.get("products", []) or []:
        await ws_broadcast_module_tree_updated(p, str(current_user.get("username") or ""))
    return {"code": 0, "deleted": res.get("deleted", 0), "message": "已删除", "synced_users": res.get("synced", 0)}


@router.get("/permission", summary="获取当前用户对模块树的编辑权限信息")
async def get_edit_permission(
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
) -> Dict[str, Any]:
    """前端据此渲染每个功能的可编辑状态：is_privileged 免审批，user_id 用于匹配负责人。"""
    perms = current_user.get("permissions") or []
    return {
        "user_id": str(current_user.get("id") or current_user.get("user_id") or ""),
        "username": current_user.get("username") or "",
        "name": current_user.get("name") or current_user.get("username") or "",
        "is_admin": "admin" in perms,
        "is_privileged": module_tree_edit_service.is_admin_or_special(current_user),
        "special_perm": module_tree_edit_service.SPECIAL_PERM,
    }


@router.post("/submit-edit", summary="提交对某个功能节点的修改")
async def submit_edit(
    payload: Dict[str, Any] = Body(..., description="product/iface_key/func_key/new(新功能节点)"),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
) -> Dict[str, Any]:
    """对一个功能节点的修改提交。

    - 可直改（admin/特殊权限/本人负责/待分配）→ 直接应用写 DB。
    - 他人负责 → 创建审批单，返回 {edit_id}，等待原负责人同意。
    """
    product = payload.get("product") or ""
    iface_key = payload.get("iface_key") or ""
    func_key = payload.get("func_key") or ""
    new_json = payload.get("new") or {}
    if not product or not iface_key or not func_key:
        raise HTTPException(status_code=400, detail="缺少 product/iface_key/func_key")

    trees = module_tree_service.get_all_trees()
    found = module_tree_service.find_function(trees, product, iface_key, func_key)
    if not found:
        raise HTTPException(status_code=404, detail="功能节点不存在")
    _, iface, fn, iface_idx, fn_idx = found

    owners = [str(e) for e in (fn.get("engineers") or [])]
    if module_tree_edit_service.can_direct_edit(current_user, owners):
        ok = module_tree_service.apply_function_change(product, iface_key, func_key, new_json)
        if not ok:
            raise HTTPException(status_code=500, detail="应用修改失败")
        await ws_broadcast_module_tree_updated(product, str(current_user.get("username") or ""))
        return {"code": 0, "direct": True, "message": "修改已生效"}

    # 需要审批
    edit_id = module_tree_edit_service.create_edit(
        product=product, iface_key=iface_key, func_key=func_key,
        old_json=dict(fn), new_json=dict(new_json),
        requester=current_user, owner_ids=owners,
    )
    if edit_id is None:
        raise HTTPException(status_code=500, detail="创建审批单失败")
    return {"code": 0, "direct": False, "edit_id": edit_id,
            "message": f"该模块由他人负责，已创建审批单 #{edit_id}，等待负责人同意"}


@router.get("/edits", summary="查看审批单")
async def list_edits(
    status: str = "pending",
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
) -> List[Dict[str, Any]]:
    """返回与当前用户相关的审批单：待处理时含待我审批的 + 我发起的，其余状态只含我发起的。"""
    user_id = str(current_user.get("id") or current_user.get("user_id") or "")
    return module_tree_edit_service.list_edits(status=status, user_id=user_id)


@router.post("/edits/{edit_id}/approve", summary="批准审批单")
async def approve_edit(
    edit_id: int,
    payload: Optional[Dict[str, Any]] = Body(default=None),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
) -> Dict[str, Any]:
    result = module_tree_edit_service.decide_edit(edit_id, "approve", current_user, (payload or {}).get("note"))
    if result is None:
        raise HTTPException(status_code=403, detail="不可审批（不存在/已处理/无权限）")
    if result.get("applied") and result.get("product"):
        await ws_broadcast_module_tree_updated(str(result["product"]), str(current_user.get("username") or ""))
    return {"code": 0, "message": "已批准并应用", "edit": result}


@router.post("/edits/{edit_id}/reject", summary="驳回审批单")
async def reject_edit(
    edit_id: int,
    payload: Optional[Dict[str, Any]] = Body(default=None),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
) -> Dict[str, Any]:
    result = module_tree_edit_service.decide_edit(edit_id, "reject", current_user, (payload or {}).get("note"))
    if result is None:
        raise HTTPException(status_code=403, detail="不可审批（不存在/已处理/无权限）")
    return {"code": 0, "message": "已驳回", "edit": result}

