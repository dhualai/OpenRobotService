"""admin「产品→界面→功能」责任模块树 API。

前端责任模块树维护页面的后端，负责：
- GET   获取全部产品树 / 产品列表 / 可选工程师候选
- PUT   整体保存树（写 DB + 导出 config.yaml + 通知 AI 热更新）
"""
from fastapi import APIRouter, Depends, HTTPException, Body
from typing import Dict, Any, List

from app.core.database import db_manager
from app.models.identity import UserDB
from app.modules.admin.api.auth import require_permission
from app.modules.admin.services import module_tree_service

router = APIRouter(prefix="/module-tree", tags=["admin-module-tree"])


@router.get("/", summary="获取全部产品→界面→功能 树")
async def get_module_tree(
    current_user: Dict[str, Any] = require_permission("backend:module-tree:base:read"),
) -> Dict[str, Any]:
    """返回 {产品: {"interfaces": [...]}}。"""
    return module_tree_service.get_all_trees()


@router.get("/products", summary="获取产品列表")
async def get_products(
    current_user: Dict[str, Any] = require_permission("backend:module-tree:base:read"),
) -> list:
    """返回已有产品名列表。"""
    trees = module_tree_service.get_all_trees()
    return sorted(trees.keys())


@router.get("/candidates", summary="获取可选工程师候选")
async def get_candidates(
    current_user: Dict[str, Any] = require_permission("backend:module-tree:base:read"),
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


@router.put("/", summary="整体保存 产品→界面→功能 树")
async def save_module_tree(
    trees: Dict[str, Any] = Body(..., description="完整树 {产品: {interfaces:[...]}}"),
    current_user: Dict[str, Any] = require_permission("backend:module-tree:base:write"),
) -> Dict[str, Any]:
    """整体覆盖所有产品树，并导出到 config.yaml + 通知 AI 热更新。"""
    # 校验结构基本合法
    for product, tree in trees.items():
        if not isinstance(tree, dict):
            raise HTTPException(status_code=400, detail=f"产品 {product} 的树结构必须是对象")
        if "interfaces" not in tree:
            tree["interfaces"] = []

    # 统一保存：写 DB + 覆盖同步用户画像 + 导出 config
    result = module_tree_service.save_trees(trees)
    if not result["db"]:
        raise HTTPException(status_code=500, detail="保存到数据库失败")
    if not result["export"]:
        raise HTTPException(status_code=500, detail="保存成功但导出 config.yaml 失败")

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

    return {"code": 0, "message": "保存成功", "synced_users": result["synced"], "ai_reload": reload_msg}
