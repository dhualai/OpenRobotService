"""界面图鉴 CRUD + 代理 AI 扫难懂区域。"""
from __future__ import annotations

import base64
import mimetypes
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import db_manager
from app.models.ui_atlas import UiAtlasCard
from app.modules.admin.api.auth import require_permission
from app.modules.admin.api.dispatch_dev import PERM, _proxy, ensure_dispatch_dev_permission
from app.modules.admin.schemas.response import DataResponse

router = APIRouter(prefix="/dispatch-dev/ui-atlas", tags=["admin-dispatch-dev-ui-atlas"])

# 本地图鉴根目录：与 kb 同级（OpenRobotService_Data/ui_atlas），不进诊断向量库
# ui_atlas.py → api/admin/modules/app/backend/OpenRobotService → sibling OpenRobotService_Data
_REPO_ROOT = Path(__file__).resolve().parents[5]
_ATLAS_ROOT = (_REPO_ROOT.parent / "OpenRobotService_Data" / "ui_atlas").resolve()
_LOCAL_PREFIX = "local://"
_SAFE_NAME = re.compile(r"[^\w\u4e00-\u9fff\-]+", re.UNICODE)


def _slug(text: str, fallback: str = "x") -> str:
    s = _SAFE_NAME.sub("_", (text or "").strip())[:64].strip("_")
    return s or fallback


def _ensure_atlas_root() -> Path:
    _ATLAS_ROOT.mkdir(parents=True, exist_ok=True)
    return _ATLAS_ROOT


def _local_rel_path(stored: str) -> Optional[Path]:
    """local://rel → 绝对路径；非法或不存在返回 None。"""
    if not stored or not stored.startswith(_LOCAL_PREFIX):
        return None
    rel = stored[len(_LOCAL_PREFIX):].lstrip("/\\")
    if not rel or ".." in Path(rel).parts:
        return None
    full = (_ATLAS_ROOT / rel).resolve()
    try:
        full.relative_to(_ATLAS_ROOT)
    except ValueError:
        return None
    return full if full.is_file() else None


def _file_to_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _resolve_image_for_vlm(image_url: str) -> str:
    """VLM 需要 data URL 或公网 URL；本地文件读盘转 data URL。"""
    raw = (image_url or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="图片为空")
    if raw.startswith("data:"):
        return raw
    local = _local_rel_path(raw)
    if local is not None:
        return _file_to_data_url(local)
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    raise HTTPException(status_code=400, detail="无法解析图片地址（请重新上传到本地图鉴）")


def _save_bytes(product: str, iface: str, data: bytes, filename: str) -> str:
    """写入本地并返回 local:// 相对引用。"""
    root = _ensure_atlas_root()
    ext = Path(filename or "shot.png").suffix.lower() or ".png"
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        ext = ".png"
    rel = Path(_slug(product, "product")) / _slug(iface, "iface") / f"{uuid.uuid4().hex[:12]}{ext}"
    dest = root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return f"{_LOCAL_PREFIX}{rel.as_posix()}"


def _maybe_materialize_data_uri(product: str, iface: str, image_url: str) -> str:
    """建卡时若仍是 data URI，自动落盘，避免 MySQL 塞大图。"""
    raw = (image_url or "").strip()
    if not raw.startswith("data:") or "," not in raw:
        return raw
    header, b64 = raw.split(",", 1)
    try:
        data = base64.b64decode(b64)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"图片 data URI 无效: {e}") from e
    ext = ".png"
    if "jpeg" in header or "jpg" in header:
        ext = ".jpg"
    elif "webp" in header:
        ext = ".webp"
    return _save_bytes(product, iface, data, f"upload{ext}")


def _card_dict(row: UiAtlasCard) -> Dict[str, Any]:
    stored = row.image_url or ""
    display = stored
    if stored.startswith(_LOCAL_PREFIX):
        display = f"/api/admin/dispatch-dev/ui-atlas/cards/{row.id}/image"
    return {
        "id": row.id,
        "product": row.product,
        "iface_name": row.iface_name,
        "image_url": display,
        "image_stored": stored,
        "storage": "local" if stored.startswith(_LOCAL_PREFIX) else (
            "data" if stored.startswith("data:") else "url"
        ),
        "page_caption": row.page_caption,
        "source": row.source,
        "kb_path": row.kb_path,
        "status": row.status,
        "regions": row.regions or [],
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


class RegionIn(BaseModel):
    id: Optional[str] = None
    x: float
    y: float
    w: float
    h: float
    question: str = ""
    answer: Optional[str] = None
    status: str = "pending"  # pending|answered|skipped
    label_x: Optional[float] = None
    label_y: Optional[float] = None


class CardCreate(BaseModel):
    product: str = Field(..., min_length=1, max_length=64)
    iface_name: str = Field(..., min_length=1, max_length=128)
    image_url: str = Field(..., min_length=1)
    source: str = "upload"
    kb_path: Optional[str] = None
    page_caption: Optional[str] = None


class CardUpdate(BaseModel):
    product: Optional[str] = None
    iface_name: Optional[str] = None
    image_url: Optional[str] = None
    page_caption: Optional[str] = None
    status: Optional[str] = None
    regions: Optional[List[RegionIn]] = None
    source: Optional[str] = None
    kb_path: Optional[str] = None


def _norm_regions(regions: List[Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for i, r in enumerate(regions or []):
        if isinstance(r, RegionIn):
            r = r.model_dump()
        if not isinstance(r, dict):
            continue
        rid = str(r.get("id") or f"r{uuid.uuid4().hex[:8]}")
        try:
            x, y, w, h = float(r["x"]), float(r["y"]), float(r["w"]), float(r["h"])
        except (KeyError, TypeError, ValueError):
            continue
        status = str(r.get("status") or "pending")
        if status not in ("pending", "answered", "skipped"):
            status = "pending"
        answer = r.get("answer")
        if answer is not None:
            answer = str(answer).strip() or None
        question = str(r.get("question") or "").strip()
        out.append({
            "id": rid,
            "x": max(0.0, min(1.0, x)),
            "y": max(0.0, min(1.0, y)),
            "w": max(0.01, min(1.0, w)),
            "h": max(0.01, min(1.0, h)),
            "question": question,
            "answer": answer,
            "status": status,
        })
        if out[-1]["status"] not in ("pending", "answered", "skipped"):
            out[-1]["status"] = "pending"
        # 有答案但显式 pending：视为「AI 草稿 / 待人工确认」，不自动变绿
        try:
            if r.get("label_x") is not None and r.get("label_y") is not None:
                out[-1]["label_x"] = max(0.0, min(0.92, float(r["label_x"])))
                out[-1]["label_y"] = max(0.0, min(0.95, float(r["label_y"])))
        except (TypeError, ValueError):
            pass
    return out


def _derive_status(regions: List[Dict[str, Any]], current: str) -> str:
    if not regions:
        return "published" if current in ("pending_answers", "published", "draft") else current
    if any(r.get("status") == "pending" for r in regions):
        return "pending_answers"
    return "published"


@router.get("/cards", response_model=DataResponse, summary="图鉴卡列表")
async def list_cards(
    product: Optional[str] = None,
    status: Optional[str] = None,
    current_user: Dict[str, Any] = require_permission(PERM),
):
    ensure_dispatch_dev_permission()
    db: Session = db_manager.get_db()
    try:
        q = db.query(UiAtlasCard).order_by(UiAtlasCard.id.desc())
        if product:
            q = q.filter(UiAtlasCard.product == product.strip())
        if status:
            q = q.filter(UiAtlasCard.status == status.strip())
        rows = q.limit(200).all()
        return DataResponse(code=0, message="success", data=[_card_dict(r) for r in rows])
    finally:
        db.close()


@router.post("/cards", response_model=DataResponse, summary="新建图鉴卡")
async def create_card(
    body: CardCreate,
    current_user: Dict[str, Any] = require_permission(PERM),
):
    ensure_dispatch_dev_permission()
    stored = _maybe_materialize_data_uri(body.product, body.iface_name, body.image_url)
    db: Session = db_manager.get_db()
    try:
        row = UiAtlasCard(
            product=body.product.strip(),
            iface_name=body.iface_name.strip(),
            image_url=stored,
            page_caption=(body.page_caption or "").strip() or None,
            source=(body.source or "upload").strip() or "upload",
            kb_path=(body.kb_path or "").strip() or None,
            status="draft",
            regions=[],
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return DataResponse(code=0, message="success", data=_card_dict(row))
    finally:
        db.close()


@router.post("/cards/upload", response_model=DataResponse, summary="上传标准图并建卡（本地落盘）")
async def upload_card(
    file: UploadFile = File(...),
    product: str = Form(...),
    iface_name: str = Form(...),
    current_user: Dict[str, Any] = require_permission(PERM),
):
    """multipart 上传 → OpenRobotService_Data/ui_atlas/{product}/{iface}/xxx.png"""
    ensure_dispatch_dev_permission()
    product = (product or "").strip()
    iface_name = (iface_name or "").strip()
    if not product or not iface_name:
        raise HTTPException(status_code=422, detail="product / iface_name 必填")
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="空文件")
    if len(raw) > 12 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="图片请小于 12MB")
    ctype = (file.content_type or "").lower()
    if ctype and not ctype.startswith("image/"):
        raise HTTPException(status_code=400, detail="请上传图片文件")
    stored = _save_bytes(product, iface_name, raw, file.filename or "shot.png")
    db: Session = db_manager.get_db()
    try:
        row = UiAtlasCard(
            product=product,
            iface_name=iface_name,
            image_url=stored,
            source="upload",
            status="draft",
            regions=[],
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return DataResponse(code=0, message="success", data=_card_dict(row))
    finally:
        db.close()


@router.get("/cards/{card_id}/image", summary="读取本地标准图")
async def get_card_image(
    card_id: int,
    current_user: Dict[str, Any] = require_permission(PERM),
):
    ensure_dispatch_dev_permission()
    db: Session = db_manager.get_db()
    try:
        row = db.query(UiAtlasCard).filter(UiAtlasCard.id == card_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="图鉴卡不存在")
        stored = row.image_url or ""
    finally:
        db.close()

    local = _local_rel_path(stored)
    if local is not None:
        mime = mimetypes.guess_type(str(local))[0] or "image/png"
        return FileResponse(local, media_type=mime, filename=local.name)
    if stored.startswith("data:") and "," in stored:
        header, b64 = stored.split(",", 1)
        mime = "image/png"
        if header.startswith("data:") and ";" in header:
            mime = header[5:].split(";", 1)[0] or mime
        try:
            data = base64.b64decode(b64)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"data URI 损坏: {e}") from e
        from fastapi.responses import Response
        return Response(content=data, media_type=mime)
    raise HTTPException(status_code=404, detail="本地图片不存在，请重新上传")


@router.get("/cards/{card_id}", response_model=DataResponse, summary="图鉴卡详情")
async def get_card(
    card_id: int,
    current_user: Dict[str, Any] = require_permission(PERM),
):
    ensure_dispatch_dev_permission()
    db: Session = db_manager.get_db()
    try:
        row = db.query(UiAtlasCard).filter(UiAtlasCard.id == card_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="图鉴卡不存在")
        return DataResponse(code=0, message="success", data=_card_dict(row))
    finally:
        db.close()


@router.put("/cards/{card_id}", response_model=DataResponse, summary="更新图鉴卡")
async def update_card(
    card_id: int,
    body: CardUpdate,
    current_user: Dict[str, Any] = require_permission(PERM),
):
    ensure_dispatch_dev_permission()
    db: Session = db_manager.get_db()
    try:
        row = db.query(UiAtlasCard).filter(UiAtlasCard.id == card_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="图鉴卡不存在")
        if body.product is not None:
            row.product = body.product.strip()
        if body.iface_name is not None:
            row.iface_name = body.iface_name.strip()
        if body.image_url is not None:
            row.image_url = body.image_url.strip()
        if body.page_caption is not None:
            row.page_caption = body.page_caption.strip() or None
        if body.source is not None:
            row.source = body.source.strip() or row.source
        if body.kb_path is not None:
            row.kb_path = body.kb_path.strip() or None
        if body.regions is not None:
            row.regions = _norm_regions(body.regions)
            row.status = _derive_status(row.regions, row.status)
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(row, "regions")
        if body.status is not None:
            row.status = body.status.strip() or row.status
        db.commit()
        db.refresh(row)
        return DataResponse(code=0, message="success", data=_card_dict(row))
    finally:
        db.close()


@router.delete("/cards/{card_id}", response_model=DataResponse, summary="删除图鉴卡")
async def delete_card(
    card_id: int,
    current_user: Dict[str, Any] = require_permission(PERM),
):
    ensure_dispatch_dev_permission()
    db: Session = db_manager.get_db()
    try:
        row = db.query(UiAtlasCard).filter(UiAtlasCard.id == card_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="图鉴卡不存在")
        db.delete(row)
        db.commit()
        return DataResponse(code=0, message="success", data={"id": card_id})
    finally:
        db.close()


def _iou_local(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    ax2, ay2 = a["x"] + a["w"], a["y"] + a["h"]
    bx2, by2 = b["x"] + b["w"], b["y"] + b["h"]
    ix1, iy1 = max(a["x"], b["x"]), max(a["y"], b["y"])
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union > 0 else 0.0


def _clone_region(r: Dict[str, Any]) -> Dict[str, Any]:
    item = {
        "id": str(r.get("id") or f"r{uuid.uuid4().hex[:8]}"),
        "x": float(r["x"]),
        "y": float(r["y"]),
        "w": float(r["w"]),
        "h": float(r["h"]),
        "question": str(r.get("question") or "").strip(),
        "answer": (str(r["answer"]).strip() if r.get("answer") is not None else None) or None,
        "status": str(r.get("status") or "pending"),
    }
    if item["status"] not in ("pending", "answered", "skipped"):
        item["status"] = "pending"
    # 保留 pending+answer（人工核对前的 AI 草稿）
    try:
        if r.get("label_x") is not None and r.get("label_y") is not None:
            item["label_x"] = max(0.0, min(0.92, float(r["label_x"])))
            item["label_y"] = max(0.0, min(0.95, float(r["label_y"])))
    except (TypeError, ValueError):
        pass
    return item


def _merge_base(db_regions: List[Any], client_regions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """以客户端当前框为主（含刚确认的答案），再并入库里未重叠的旧框。"""
    base: List[Dict[str, Any]] = []
    for c in client_regions or []:
        try:
            base.append(_clone_region(c))
        except (KeyError, TypeError, ValueError):
            continue
    if not base:
        for e in db_regions or []:
            if not isinstance(e, dict):
                continue
            try:
                base.append(_clone_region(e))
            except (KeyError, TypeError, ValueError):
                continue
        return base
    for e in db_regions or []:
        if not isinstance(e, dict):
            continue
        try:
            er = _clone_region(e)
        except (KeyError, TypeError, ValueError):
            continue
        hit = None
        for i, c in enumerate(base):
            if _iou_local(er, c) >= 0.35:
                hit = i
                break
        if hit is None:
            base.append(er)
        elif er.get("answer") and not base[hit].get("answer"):
            base[hit] = er
    return base


def _merge_regions_local(
    existing: List[Dict[str, Any]], newly: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """保留全部已有框，只追加重叠不高的新框。"""
    kept: List[Dict[str, Any]] = []
    for e in existing or []:
        if not isinstance(e, dict):
            continue
        try:
            kept.append(_clone_region(e))
        except (KeyError, TypeError, ValueError):
            continue
    for n in newly or []:
        if not isinstance(n, dict):
            continue
        try:
            item = _clone_region(n)
        except (KeyError, TypeError, ValueError):
            continue
        if not item.get("answer"):
            item["status"] = "pending"
        if any(_iou_local(item, e) >= 0.35 for e in kept):
            continue
        item["id"] = f"n{uuid.uuid4().hex[:8]}"
        kept.append(item)
    return kept


class ScanBody(BaseModel):
    client_regions: Optional[List[RegionIn]] = None


@router.post("/cards/{card_id}/scan", response_model=DataResponse, summary="AI 扫难懂点（增量）")
async def scan_card(
    card_id: int,
    body: ScanBody = ScanBody(),
    current_user: Dict[str, Any] = require_permission(PERM),
):
    ensure_dispatch_dev_permission()
    db: Session = db_manager.get_db()
    try:
        row = db.query(UiAtlasCard).filter(UiAtlasCard.id == card_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="图鉴卡不存在")
        image_url = row.image_url
        db_regions = list(row.regions or [])
    finally:
        db.close()

    vlm_image = _resolve_image_for_vlm(image_url)
    client = _norm_regions(body.client_regions) if body.client_regions is not None else []
    existing = _merge_base(db_regions, client)
    kept_n = len(existing)
    answered_before = sum(1 for r in existing if r.get("status") == "answered")

    data = await _proxy(
        "POST",
        "/api/ai/assigner/debug/ui-atlas/scan",
        timeout=120.0,
        json={"image_url": vlm_image, "existing_regions": existing},
    )
    newly = _norm_regions(data.get("regions") or [])
    regions = _merge_regions_local(existing, newly)
    caption = data.get("page_caption")
    added_n = max(0, len(regions) - kept_n)

    db = db_manager.get_db()
    try:
        row = db.query(UiAtlasCard).filter(UiAtlasCard.id == card_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="图鉴卡不存在")
        if caption and not row.page_caption:
            row.page_caption = str(caption).strip() or None
        row.regions = regions
        row.status = _derive_status(regions, row.status or "pending_answers")
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(row, "regions")
        db.commit()
        db.refresh(row)
        payload = _card_dict(row)
        payload["_scan_meta"] = {
            "kept": kept_n,
            "added": added_n,
            "answered": sum(1 for r in regions if r.get("status") == "answered"),
            "pending": sum(1 for r in regions if r.get("status") == "pending"),
            "answered_before": answered_before,
        }
        return DataResponse(code=0, message="success", data=payload)
    finally:
        db.close()


class ExplainBody(BaseModel):
    x: float
    y: float
    w: float
    h: float


@router.post("/cards/{card_id}/explain", response_model=DataResponse, summary="解释手动画框")
async def explain_region(
    card_id: int,
    body: ExplainBody,
    current_user: Dict[str, Any] = require_permission(PERM),
):
    ensure_dispatch_dev_permission()
    db: Session = db_manager.get_db()
    try:
        row = db.query(UiAtlasCard).filter(UiAtlasCard.id == card_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="图鉴卡不存在")
        image_url = row.image_url
    finally:
        db.close()

    vlm_image = _resolve_image_for_vlm(image_url)
    data = await _proxy(
        "POST",
        "/api/ai/assigner/debug/ui-atlas/explain",
        timeout=90.0,
        json={
            "image_url": vlm_image,
            "box": body.model_dump(),
        },
    )
    return DataResponse(code=0, message="success", data=data)


class CompareChatBody(BaseModel):
    """用正式对话看图 prompt 对照：不带图鉴 vs 带当前卡的已确认标注。"""
    image_data_url: str = Field(..., min_length=32, description="相似图 data URL")
    dialog_context: str = ""
    image_name: str = "similar_shot.png"


@router.post("/cards/{card_id}/compare-chat-vlm", response_model=DataResponse, summary="对话看图对照（±图鉴）")
async def compare_chat_vlm(
    card_id: int,
    body: CompareChatBody,
    current_user: Dict[str, Any] = require_permission(PERM),
):
    """
    模拟用户在真实对话里上传相似截图时的 VLM 效果：
    同一张图、同一套 router 上传看图文案，分别不带/带上本图鉴卡已确认标注。
    """
    ensure_dispatch_dev_permission()
    db: Session = db_manager.get_db()
    try:
        row = db.query(UiAtlasCard).filter(UiAtlasCard.id == card_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="图鉴卡不存在")
        product = row.product
        iface_name = row.iface_name
        page_caption = row.page_caption
        regions = list(row.regions or [])
        stored = row.image_url or ""
    finally:
        db.close()

    raw = (body.image_data_url or "").strip()
    if not raw.startswith("data:"):
        raise HTTPException(status_code=400, detail="请上传相似图（data URL）")
    try:
        standard_image = _resolve_image_for_vlm(stored)
    except HTTPException:
        standard_image = ""

    data = await _proxy(
        "POST",
        "/api/ai/assigner/debug/ui-atlas/compare-chat-vlm",
        timeout=180.0,
        json={
            "image_url": raw,
            "standard_image_url": standard_image,
            "product": product,
            "iface_name": iface_name,
            "page_caption": page_caption,
            "regions": regions,
            "dialog_context": body.dialog_context or "",
            "image_name": body.image_name or "similar_shot.png",
        },
    )
    return DataResponse(code=0, message="success", data=data)
