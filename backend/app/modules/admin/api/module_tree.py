"""admin「产品→界面→功能」责任模块树 API。

前端责任模块树维护页面的后端，负责：
- GET   获取全部产品树 / 产品列表 / 可选工程师候选 / 当前用户编辑权限
- PUT   整体保存树（写 DB + 导出 config.yaml + 通知 AI 热更新，含他人负责模块校验）
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
    """返回 {产品: 产品树哈希}，前端加载时记录为‘本地基准版本’。”"""
    return module_tree_service.get_product_hashes()


@router.get("/func-hashes", summary="获取各功能节点哈希（功能级冲突基准）")
async def get_func_hashes(
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
) -> Dict[str, Any]:
    """返回 {产品: {'界面名||功能名': 功能哈希}}，前端记录各功能本地版本用于功能级冲突检测。"""
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
            # 主动/在职且有关键信息
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
        # 按部门、姓名排序
        result.sort(key=lambda x: (x["department"], x["name"]))
        return result
    finally:
        db.close()


@router.put("/", summary="保存 产品→界面→功能 树（节点级增量 + 功能级冲突检测）")
async def save_module_tree(
    payload: Dict[str, Any] = Body(..., description="{products:{产品:树}, removed?:[产品], per_product?:{产品:{changed_funcs,new_interfaces,removed_interfaces,removed_funcs}}, func_hashes?:{产品:{'界面||功能':哈希}}, force?:[产品]}"),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
) -> Dict[str, Any]:
    """保存树，导出 config.yaml + 通知 AI 热更新。

    入参：
    - products: 提交的产品树 {产品:树}
    - removed: 待删除的产品名列表
    - per_product: 每产品本次改动范围（节点级增量）。changed_funcs:['界面名||功能名']；
      new_interfaces:[界面名]；removed_interfaces/removed_funcs:[key或name]
    - func_hashes: 前端记录的“各功能加载时哈希”，用于功能级冲突检测
    - force: 强制覆盖的产品（跳过功能冲突检测）
    只覆盖 per_product 点名的功能节点，其它节点保留 DB 最新（多人并发安全）；
    同一功能被双方修改时返回 conflicted_funcs。
    """
    # 解析增量 / 旧式
    if isinstance(payload, dict) and ("products" in payload or "removed" in payload or "per_product" in payload):
        products = payload.get("products") or {}
        removed = payload.get("removed") or []
        per_product = payload.get("per_product") or {}
        func_hashes = payload.get("func_hashes") or {}
        force = payload.get("force") or []
        if not isinstance(removed, list):
            removed = [removed]
        if not isinstance(force, list):
            force = [force]
    else:
        products = payload
        removed = []
        per_product = {}
        func_hashes = {}
        force = []

    # 校验结构基本合法
    for product, tree in products.items():
        if not isinstance(tree, dict):
            raise HTTPException(status_code=400, detail=f"产品 {product} 的树结构必须是对象")
        if "interfaces" not in tree:
            tree["interfaces"] = []

    # 统一保存（节点级增量 + 功能级冲突检测）：写 DB + 同步画像 + 导出 config
    result = module_tree_service.save_trees(
        products, removed=removed, per_product=per_product, func_hashes=func_hashes, force=force)
    if result.get("rejected") == "empty":
        raise HTTPException(status_code=400, detail="提交内容为空，拒绝保存（防止清空责任模块树）")
    if not result["db"]:
        raise HTTPException(status_code=500, detail="保存到数据库失败")
    if not result["export"]:
        raise HTTPException(status_code=500, detail="保存成功但导出 config.yaml 失败")

    conflicted_funcs = result.get("conflicted_funcs") or []

    # 通知 AI 热更新（尽力而为，失败不阻断）
    reload_msg = None
    try:
        import httpx
        from app.core.config import settings
        ai_url = settings.AI_SERVICE_URL.rstrip("/")
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{ai_url}/api/ai/assigner/reload")
            if resp.status_code == 200:
                reload_msg = "ok"
    except Exception as e:
        reload_msg = f"AI 热更新失败: {e}"

    return {
        "code": 0,
        "message": "保存成功" if not conflicted_funcs else "保存成功，部分功能存在冲突",
        "synced_users": result["synced"],
        "conflicted_funcs": conflicted_funcs,
        "ai_reload": reload_msg,
    }


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

    - 可直改（admin/特殊权限/本人负责/待分配）→ 直接应用写 DB + 导出 config。
    - 他人负责 → 创建审批单，返回 {created_edit: true}，等待原负责人同意。
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
    """返回与当前用户相关的审批单：待处理时返回（作为负责人）待我审批的 + 我发起的，其余状态返回我发起的。"""
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

