"""双树试做：把浏览器里的组织树/技能树另存一份到本机 `.local/责任双树/` 快照。

试做页的树只写在浏览器 localStorage，清缓存、换端口、换浏览器都会读不回来。
这里给页面一个读写本地快照的口子：只存这份试做数据，不入库、不进 git
（`.local/` 已在 .gitignore）。快照文件与页面「导出 JSON」同格式，可以直接互用。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from app.modules.admin.api.auth import get_current_active_user_from_token
from app.modules.admin.schemas.response import DataResponse

router = APIRouter(prefix="/dual-tree-trial", tags=["admin-dual-tree-trial"])

# backend/app/modules/admin/api/dual_tree_trial.py → 仓库根 = parents[5]
SNAPSHOT_REL = (".local", "责任双树", "dual-tree-trial.snapshot.json")
SNAPSHOT_FORMAT = "dual-tree-trial"
MAX_NODES = 5000
MAX_MOUNTS = 20000
TZ_CN = timezone(timedelta(hours=8))


def _now_cn() -> str:
    return datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")


def snapshot_path() -> Path:
    return Path(__file__).resolve().parents[5].joinpath(*SNAPSHOT_REL)


def _validate(payload: Dict[str, Any]) -> Dict[str, Any]:
    """只要 {nodes: [...], mounts: [...]}，节点/挂人都得是对象。"""
    body = payload if isinstance(payload, dict) else {}
    raw = body.get("snapshot") if isinstance(body.get("snapshot"), dict) else body
    nodes = raw.get("nodes")
    mounts = raw.get("mounts")
    if not isinstance(nodes, list) or not isinstance(mounts, list):
        raise HTTPException(status_code=400, detail="快照格式不对：需要 {nodes: [], mounts: []}")
    if len(nodes) > MAX_NODES or len(mounts) > MAX_MOUNTS:
        raise HTTPException(status_code=400, detail="快照太大：节点或挂人数量超出上限")
    if any(not isinstance(item, dict) for item in nodes) or any(not isinstance(item, dict) for item in mounts):
        raise HTTPException(status_code=400, detail="快照格式不对：nodes / mounts 里的每个元素都要是对象")
    return {
        "format": SNAPSHOT_FORMAT,
        "version": 1,
        "saved_at": str(raw.get("saved_at") or _now_cn()),
        "saved_by": str(body.get("saved_by") or "")[:64],
        "nodes": nodes,
        "mounts": mounts,
    }


def _snapshot_info(file_data: Dict[str, Any], exists: bool) -> Dict[str, Any]:
    nodes = file_data.get("nodes") if isinstance(file_data.get("nodes"), list) else []
    mounts = file_data.get("mounts") if isinstance(file_data.get("mounts"), list) else []
    return {
        "exists": exists,
        "path": str(snapshot_path()),
        "saved_at": file_data.get("saved_at"),
        "saved_by": file_data.get("saved_by"),
        "nodes": len(nodes),
        "mounts": len(mounts),
    }


@router.get("/snapshot", response_model=DataResponse, summary="读取双树试做本机快照")
async def get_dual_tree_trial_snapshot(
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """没有快照文件时 exists=False，不算错误。"""
    path = snapshot_path()
    if not path.exists():
        return DataResponse(code=0, message="success", data=_snapshot_info({}, False))
    try:
        file_data = json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取双树快照失败: {e}") from e
    if not isinstance(file_data, dict):
        raise HTTPException(status_code=500, detail="读取双树快照失败: 文件内容不是对象")
    info = _snapshot_info(file_data, True)
    info["snapshot"] = file_data
    return DataResponse(code=0, message="success", data=info)


@router.put("/snapshot", response_model=DataResponse, summary="保存双树试做本机快照")
async def save_dual_tree_trial_snapshot(
    payload: Dict[str, Any],
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """写 `.local/责任双树/dual-tree-trial.snapshot.json`（gitignore 目录）。"""
    snapshot = _validate(payload or {})
    snapshot["saved_by"] = snapshot["saved_by"] or str(current_user.get("username") or "")
    path = snapshot_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存双树快照失败: {e}") from e
    return DataResponse(code=0, message="success", data=_snapshot_info(snapshot, True))
