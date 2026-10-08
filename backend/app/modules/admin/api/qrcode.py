"""二维码管理路由 —— 带参数二维码的 CRUD、批量创建、状态流转。

生命周期：init → entering → published → deprecated
- init: 场景值已定义，尚未调微信接口
- entering: 已调微信创建 ticket
- published: 对外使用中
- deprecated: 停止使用

录入信息行（project_code 非空）保存后直接 published（2026-09-30 用户口径）。

录入信息（「新建项目 → 录入信息」页）：项目名称/项目编号/项目地点/客户名称/车型
五个字段一条信息落成本表一行（和行 id 同行存），见下方 project-info 两个接口。
项目id 就是行 id（str(id)，2026-09-30 口径，不再单独存列）；项目编号在 wechat_qrcodes
表里不唯一——同一项目可录多张不同二维码（不同点位/场景）；project 表里项目编号仍唯一。

项目名同步 project 表（2026-09-30 用户口径）：录入信息页的项目名可以从 project 表
拉取选择、也可以直接手输——手输的新名字由 _ensure_project_row 用「项目编号」当新
项目的 id/code 补进 project 表；编号已被占用则 400（detail 直接给前端 Toast）。

权限：by-scene、GET /{id}、POST/PUT project-info、confirm 五个接口「登录即可」
（2026-09-30 用户口径：所有人扫码都能录入信息并确认）；其余管理端接口仍要
frontend:admin:other:show。

权限依赖写法：current_user=require_permission(...)——require_permission 本身已经
返回 Depends(permission_dependency)，不能再套一层 Depends(...)，否则 FastAPI 0.14x
在注册路由时会抛 "Depends(...) is not a callable object"，应用启动即失败。

永久码上限 10 万，批量生成有速率限制。
"""
import time
import uuid
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import db_manager
from app.core.auth_routes import get_current_active_user_from_token
from app.models.delivery import Project, PROJECT_DELETED
from app.models.wechat_qrcode import WechatQrcode, QrcodeStatus, QrcodeType
from app.modules.admin.api.auth import require_permission
from app.wechat.services.wechat_service import wechat_service

router = APIRouter(prefix="/qrcodes", tags=["admin-qrcodes"], redirect_slashes=False)

logger = logging.getLogger(__name__)


# ── 请求/响应 Schema（用 dict，避免额外 schemas 文件） ──

def _to_dict(q: WechatQrcode) -> dict:
    return {
        "id": q.id,
        # 场景值 = str(id)（2026-09-30 口径：扫码 scene 自动等于行 id）
        "scene_str": q.scene_str,
        "name": q.name,
        "description": q.description,
        "ticket": q.ticket,
        "url": q.url,
        "qrcode_image_url": q.qrcode_image_url,
        "type": q.type,
        "expire_seconds": q.expire_seconds,
        "status": q.status,
        "batch_id": q.batch_id,
        # 项目名（录入信息行自带；普通码行该列为 NULL）
        "project_name": q.project_name,
        "project_code": q.project_code,
        "project_location": q.project_location,
        "customer_name": q.customer_name,
        "vehicle_model": q.vehicle_model,
        "redirect_url": q.redirect_url,
        "created_by": q.created_by,
        "published_by": q.published_by,
        "deprecated_by": q.deprecated_by,
        "ticket_created_at": q.ticket_created_at.isoformat() if q.ticket_created_at else None,
        "created_at": q.created_at.isoformat() if q.created_at else None,
        "updated_at": q.updated_at.isoformat() if q.updated_at else None,
    }


# ── 列表 ──

@router.get("", summary="获取二维码列表")
async def list_qrcodes(
    status: Optional[str] = Query(None, description="按状态过滤"),
    qrcode_type: Optional[str] = Query(None, description="按类型过滤: temporary/permanent"),
    keyword: Optional[str] = Query(None, description="按 name 模糊 / id 精确搜索"),
    batch_id: Optional[str] = Query(None, description="按批次过滤"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    current_user=require_permission("frontend:admin:other:show"),
):
    db: Session = db_manager.get_db()
    try:
        query = db.query(WechatQrcode)
        if status:
            query = query.filter(WechatQrcode.status == status)
        if qrcode_type:
            query = query.filter(WechatQrcode.type == qrcode_type)
        if batch_id:
            query = query.filter(WechatQrcode.batch_id == batch_id)
        if keyword:
            kw = f"%{keyword}%"
            # scene_str 已改为 str(id)，不可再 like 查询；keyword 同时匹配 name 与 id（精确）
            try:
                kw_id = int(keyword.strip())
                query = query.filter(or_(
                    WechatQrcode.name.like(kw),
                    WechatQrcode.id == kw_id,
                ))
            except (ValueError, TypeError):
                query = query.filter(WechatQrcode.name.like(kw))

        total = query.count()
        items = query.order_by(WechatQrcode.created_at.desc()).offset(skip).limit(limit).all()
        return {"total": total, "items": [_to_dict(q) for q in items]}
    finally:
        db.close()


# ── 按场景值查单条（扫码落地页用） ──

@router.get("/by-scene/{scene}", summary="按场景值查一条二维码（扫码落地页用：登录即可）")
async def get_qrcode_by_scene(
    scene: str,
    current_user=Depends(get_current_active_user_from_token),
):
    """扫码进入链路：按 scene_str（=str(id)）精确取那一行。

    摇人页从跳转链接（`/app/call?scene=xxx`）拿到场景值后来这里取码信息：录入信息行
    自带 项目名/客户名/车型（_to_dict 里行自带优先），因此这一条响应就够弹确认弹窗。

    设计要点：
    - 路径为双段（/by-scene/{scene}），与单段 GET /{qid} 不冲突（同 /stats/summary 先例）；
    - 权限「登录即可」而非 admin——摇人页是 C 端，普通客服没有 frontend:admin:other:show；
    - scene 即 str(id)，直接解析为 int 当主键查库；
    - 只读，不写库，可安全重复调用。
    """
    try:
        qid = int(scene)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="scene_str 必须是数字（= str(id)）")

    db: Session = db_manager.get_db()
    try:
        q = db.query(WechatQrcode).filter(WechatQrcode.id == qid).first()
        if not q:
            raise HTTPException(status_code=404, detail="二维码不存在")
        return _to_dict(q)
    finally:
        db.close()


# ── 单条 ──

@router.get("/{qid}", summary="获取二维码详情")
async def get_qrcode(qid: int, current_user=Depends(get_current_active_user_from_token)):
    # 权限「登录即可」：扫码链路（录入信息页按 :id 兜底加载、OAuth 回跳）普通用户也要用
    db: Session = db_manager.get_db()
    try:
        q = db.query(WechatQrcode).filter(WechatQrcode.id == qid).first()
        if not q:
            raise HTTPException(status_code=404, detail="二维码不存在")
        return _to_dict(q)
    finally:
        db.close()


# ── 创建（单条） ──

@router.post("", summary="创建二维码记录（init 状态，不调微信接口）")
async def create_qrcode(
    name: str = Body("", embed=True),
    description: Optional[str] = Body(None, embed=True),
    qrcode_type: str = Body(QrcodeType.PERMANENT, embed=True),
    redirect_url: Optional[str] = Body(None, embed=True),
    current_user=require_permission("frontend:admin:other:show"),
):
    db: Session = db_manager.get_db()
    try:
        q = WechatQrcode(
            name=name or "",
            description=description,
            type=qrcode_type,
            redirect_url=redirect_url,
            created_by=current_user.get("username") if isinstance(current_user, dict) else str(current_user),
        )
        db.add(q)
        db.commit()
        db.refresh(q)
        return _to_dict(q)
    finally:
        db.close()


# ── 批量创建（只落库，不调微信） ──

@router.post("/batch", summary="批量创建二维码记录（init 状态）")
async def batch_create_qrcodes(
    count: int = Body(..., embed=True, ge=1, le=500, description="创建数量"),
    name_prefix: str = Body("", embed=True),
    qrcode_type: str = Body(QrcodeType.PERMANENT, embed=True),
    redirect_url: Optional[str] = Body(None, embed=True, description="扫码跳转 URL；留空由扫码链路默认跳录入信息页"),
    current_user=require_permission("frontend:admin:other:show"),
):
    db: Session = db_manager.get_db()
    batch_id = f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    created_by = current_user.get("username") if isinstance(current_user, dict) else str(current_user)
    # 留空就存 NULL，跳转交给扫码链路算（wechat.py::_send_scan_redirect_card：DB 有记录
    # 且没配 redirect_url → /app/admin/info-entry/{id}，2026-09-30 合入的默认规则）。
    # 这里不预填死 URL：建行时 id 还没生成，写不出带 id 的地址；而未登录扫码走微信 OAuth
    # 回跳只保留 pathname（见前端 buildStateFromPath），带 ?scene= 的地址会丢掉行上下文、
    # 落到空白录入页——带 id 的 path 才是 OAuth 安全的那一个。
    redirect_url = (redirect_url or "").strip() or None

    results = {"batch_id": batch_id, "created": []}

    try:
        for i in range(count):
            q = WechatQrcode(
                name=f"{name_prefix.rstrip('-').rstrip()}-{i + 1}" if name_prefix.strip() else "",
                type=qrcode_type,
                redirect_url=redirect_url,
                batch_id=batch_id,
                created_by=created_by,
            )
            db.add(q)
            results["created"].append(i + 1)

        db.commit()
        results["created_count"] = len(results["created"])
        results["skipped_count"] = 0
        return results
    finally:
        db.close()


# ── 生成 ticket（调微信接口） ──

PERMANENT_QRCODE_MAX = 100_000
BATCH_THROTTLE_SECONDS = 0.5  # 每次调用间隔，避免限流

@router.post("/{qid}/generate", summary="调微信接口生成 ticket")
async def generate_qrcode_ticket(qid: int, current_user=require_permission("frontend:admin:other:show")):
    db: Session = db_manager.get_db()
    try:
        q = db.query(WechatQrcode).filter(WechatQrcode.id == qid).first()
        if not q:
            raise HTTPException(status_code=404, detail="二维码不存在")
        if q.status not in (QrcodeStatus.INIT, QrcodeStatus.ENTERING):
            raise HTTPException(status_code=400, detail=f"当前状态 {q.status} 不可生成 ticket")

        is_perm = q.type == QrcodeType.PERMANENT
        if is_perm:
            perm_count = db.query(WechatQrcode).filter(WechatQrcode.type == QrcodeType.PERMANENT).count()
            if perm_count >= PERMANENT_QRCODE_MAX:
                raise HTTPException(status_code=400, detail=f"永久码已达上限 {PERMANENT_QRCODE_MAX}")

        svc = wechat_service
        result = svc.create_qrcode_ticket(
            scene_str=q.scene_str,
            is_permanent=is_perm,
            expire_seconds=q.expire_seconds or 2592000,
        )
        if not result:
            raise HTTPException(status_code=502, detail="微信接口调用失败")

        q.ticket = result.get("ticket")
        q.url = result.get("url")
        q.expire_seconds = result.get("expire_seconds")
        q.status = QrcodeStatus.ENTERING
        q.ticket_created_at = datetime.utcnow()
        db.commit()
        db.refresh(q)
        return _to_dict(q)
    finally:
        db.close()


@router.post("/batch-generate", summary="批量生成 ticket（循环调微信接口）")
async def batch_generate_tickets(
    batch_id: Optional[str] = Body(None, embed=True, description="按 batch_id 筛选 init 状态记录"),
    qid_list: Optional[List[int]] = Body(None, embed=True, description="指定 ID 列表（优先级高于 batch_id）"),
    only_init: bool = Body(True, embed=True, description="只处理 init 状态"),
    current_user=require_permission("frontend:admin:other:show"),
):
    db: Session = db_manager.get_db()
    try:
        query = db.query(WechatQrcode)
        if qid_list:
            query = query.filter(WechatQrcode.id.in_(qid_list))
        elif batch_id:
            query = query.filter(WechatQrcode.batch_id == batch_id)
        else:
            raise HTTPException(status_code=400, detail="必须提供 batch_id 或 qid_list")
        if only_init:
            query = query.filter(WechatQrcode.status.in_([QrcodeStatus.INIT, QrcodeStatus.ENTERING]))

        records = query.all()
        results = {"total": len(records), "success": [], "failed": []}

        svc = wechat_service
        perm_count = db.query(WechatQrcode).filter(WechatQrcode.type == QrcodeType.PERMANENT).count()

        for q in records:
            is_perm = q.type == QrcodeType.PERMANENT
            if is_perm and perm_count >= PERMANENT_QRCODE_MAX:
                results["failed"].append({"id": q.id, "scene_str": q.scene_str, "reason": "永久码配额已达上限"})
                continue

            try:
                wx_result = svc.create_qrcode_ticket(
                    scene_str=q.scene_str,
                    is_permanent=is_perm,
                    expire_seconds=q.expire_seconds or 2592000,
                )
                if wx_result:
                    q.ticket = wx_result.get("ticket")
                    q.url = wx_result.get("url")
                    q.expire_seconds = wx_result.get("expire_seconds")
                    q.status = QrcodeStatus.ENTERING
                    q.ticket_created_at = datetime.utcnow()
                    results["success"].append({"id": q.id, "scene_str": q.scene_str})
                    if is_perm:
                        perm_count += 1
                else:
                    results["failed"].append({"id": q.id, "scene_str": q.scene_str, "reason": "微信接口返回空"})
            except Exception as e:
                results["failed"].append({"id": q.id, "scene_str": q.scene_str, "reason": str(e)})

            time.sleep(BATCH_THROTTLE_SECONDS)

        db.commit()
        return results
    finally:
        db.close()


# ── 更新字段 ──

@router.put("/{qid}", summary="更新二维码字段")
async def update_qrcode(
    qid: int,
    name: Optional[str] = Body(None, embed=True),
    description: Optional[str] = Body(None, embed=True),
    redirect_url: Optional[str] = Body(None, embed=True),
    current_user=require_permission("frontend:admin:other:show"),
):
    db: Session = db_manager.get_db()
    try:
        q = db.query(WechatQrcode).filter(WechatQrcode.id == qid).first()
        if not q:
            raise HTTPException(status_code=404, detail="二维码不存在")

        if name is not None:
            q.name = name
        if description is not None:
            q.description = description
        if redirect_url is not None:
            q.redirect_url = redirect_url

        db.commit()
        db.refresh(q)
        return _to_dict(q)
    finally:
        db.close()


# 录入信息（「其他项目登记」） ──
#
# 「新建项目 → 录入信息」一条录入 = wechat_qrcodes 一行：项目名称/项目编号/项目地点/
# 客户名称/车型五个字段和行 id 同行存（2026-09-29 用户口径），码记录名跟随项目名。
# 项目id 不再单独存列：就是行 id（str(id)，2026-09-30 口径，扫码 scene 自动等于它），
# 也不需要唯一性校验——主键天然唯一。项目编号在 wechat_qrcodes 表里**不唯一**：
# 同一项目可以录入多张不同的二维码（不同点位/场景），一张码 = wechat_qrcodes 一行。
# 项目名同时同步 project 表（project 表仍然按项目编号唯一，见 _ensure_project_row）。

_INFO_FIELD_MAX = {
    "project_code": 64, "project_name": 128,
    "project_location": 128, "customer_name": 128, "vehicle_model": 128,
}
_INFO_FIELD_LABEL = {
    "project_code": "项目编号", "project_name": "项目名称",
    "project_location": "项目地点", "customer_name": "客户名称", "vehicle_model": "车型",
}


def _clean_info_value(value: Optional[str], field: str) -> Optional[str]:
    """strip 后返回；空白 → None（选填字段清空）；超长 → 400。"""
    if value is None:
        return None
    v = str(value).strip()
    if len(v) > _INFO_FIELD_MAX[field]:
        raise HTTPException(status_code=400, detail=f"{_INFO_FIELD_LABEL[field]}最长 {_INFO_FIELD_MAX[field]} 字符")
    return v or None


def _check_info_unique(db: Session, field: str, value: Optional[str], exclude_id: Optional[int] = None) -> None:
    """录入信息行内查重（见本段注释：范围只限 project_code 非空的行）。"""
    if not value:
        return
    query = db.query(WechatQrcode.id).filter(
        getattr(WechatQrcode, field) == value,
        WechatQrcode.project_code.isnot(None),
    )
    if exclude_id is not None:
        query = query.filter(WechatQrcode.id != exclude_id)
    if query.first():
        raise HTTPException(status_code=400, detail=f"{_INFO_FIELD_LABEL[field]}已存在：{value}")


def _ensure_project_row(db: Session, name: str, code: Optional[str]) -> None:
    """录入信息保存时把「项目名」同步进 project 表（2026-09-30 用户口径）。

    录入信息页的项目名支持从 project 表模糊挑选，也支持手输新名字：
    - 名字已在 project 表（未删除）→ 直接复用，不动原行（原行可能有完整台账数据）；
    - 名字不在表里 → 用表单「项目编号」当新项目的 id/code 建一行
      （id 与 code 一致，见 Project 模型注释）；此时编号必填（接口层已保证非空）；
    - 编号已被占用 → 400：软删行给「不可复用」（避免复活已删项目的编号），
      其余给出占用它的项目名，detail 直接 Toast 给用户；
    - 并发下同时建同名项目 → 唯一键兜底，IntegrityError 后复查，名字已落库即视为成功。

    调用点已确认 name 非空；只 flush 不 commit，随调用方的事务一起提交/回滚。
    """
    if not name:
        return
    exists = (
        db.query(Project.id)
        .filter(Project.name == name, Project.status != PROJECT_DELETED)
        .first()
    )
    if exists:
        return
    if not code:
        raise HTTPException(status_code=400, detail=f"新项目「{name}」必须在 project 表登记项目编号")

    taken = db.query(Project).filter(or_(Project.code == code, Project.id == code)).first()
    if taken:
        if taken.name == name:
            # 同名行不是 active（如已删除）：不重建，交人工在项目台账里处理
            raise HTTPException(status_code=400, detail=f"项目「{name}」已存在但已删除，请先恢复或改用其他项目编号")
        if taken.status == PROJECT_DELETED:
            raise HTTPException(status_code=400, detail=f"项目编号 {code} 属于已删除项目「{taken.name}」，不可复用")
        raise HTTPException(status_code=400, detail=f"项目编号 {code} 已被项目「{taken.name}」占用")

    try:
        # savepoint：并发兜底失败也不能波及调用方事务里已改的其他字段（如 code/name）
        with db.begin_nested():
            db.add(Project(id=code, code=code, name=name, status="active"))
    except IntegrityError:
        # 另一请求刚建了同名/同编号项目：复查一次，名字已在即视为成功，否则原样抛
        if not db.query(Project.id).filter(Project.name == name, Project.status != PROJECT_DELETED).first():
            raise


@router.post("/project-info", summary="录入信息：登记一条项目信息（一项目一行，登录即可）")
async def create_project_info(
    project_code: str = Body(..., embed=True, description="项目编号（唯一，可改）"),
    project_name: str = Body(..., embed=True, description="项目名"),
    project_location: Optional[str] = Body(None, embed=True, description="项目地点"),
    customer_name: Optional[str] = Body(None, embed=True, description="客户名"),
    vehicle_model: Optional[str] = Body(None, embed=True, description="车型"),
    # 权限「登录即可」：扫码录入/确认链路普通用户也要用（2026-09-30 用户口径）
    current_user=Depends(get_current_active_user_from_token),
):
    db: Session = db_manager.get_db()
    try:
        code = _clean_info_value(project_code, "project_code")
        name = _clean_info_value(project_name, "project_name")
        if not code:
            raise HTTPException(status_code=400, detail="项目编号不能为空")
        if not name:
            raise HTTPException(status_code=400, detail="项目名称不能为空")
        # 项目名不在 project 表 → 用项目编号建一行（编号被占用则 400，见 _ensure_project_row）
        # 注：wechat_qrcodes 表内 project_code 不唯一——同一项目可录多张码（2026-09-30 口径）
        _ensure_project_row(db, name, code)

        q = WechatQrcode(
            # 码记录名跟随项目名：列表/预览不用另开字段就能看到是哪个项目
            name=name,
            type=QrcodeType.PERMANENT,
            project_code=code,
            project_name=name,
            project_location=_clean_info_value(project_location, "project_location"),
            customer_name=_clean_info_value(customer_name, "customer_name"),
            vehicle_model=_clean_info_value(vehicle_model, "vehicle_model"),
            created_by=current_user.get("username") if isinstance(current_user, dict) else str(current_user),
            # 录入信息保存后直接发布（2026-09-30 用户口径：扫码→录入→保存即 published）
            status=QrcodeStatus.PUBLISHED,
        )
        db.add(q)
        db.commit()
        db.refresh(q)
        return _to_dict(q)
    finally:
        db.close()


@router.put("/{qid}/project-info", summary="录入信息：更新一条项目信息（登录即可）")
async def update_project_info(
    qid: int,
    project_code: Optional[str] = Body(None, embed=True, description="项目编号（唯一，可改）；不传不改"),
    project_name: Optional[str] = Body(None, embed=True, description="项目名；不传不改"),
    project_location: Optional[str] = Body(None, embed=True, description="项目地点；传空串清空"),
    customer_name: Optional[str] = Body(None, embed=True, description="客户名；传空串清空"),
    vehicle_model: Optional[str] = Body(None, embed=True, description="车型；传空串清空"),
    # 权限「登录即可」：扫码录入/确认链路普通用户也要用（2026-09-30 用户口径）
    current_user=Depends(get_current_active_user_from_token),
):
    db: Session = db_manager.get_db()
    try:
        q = db.query(WechatQrcode).filter(WechatQrcode.id == qid).first()
        if not q:
            raise HTTPException(status_code=404, detail="二维码不存在")

        if project_code is not None:
            code = _clean_info_value(project_code, "project_code")
            if not code:
                raise HTTPException(status_code=400, detail="项目编号不能为空")
            # 注：wechat_qrcodes 表内 project_code 不唯一——同一项目可录多张码
            q.project_code = code

        if project_name is not None:
            name = _clean_info_value(project_name, "project_name")
            if not name:
                raise HTTPException(status_code=400, detail="项目名称不能为空")
            # 项目名不在 project 表 → 用（本次改后或原有的）项目编号建一行；
            # 编号被占用则 400，见 _ensure_project_row
            _ensure_project_row(db, name, q.project_code)
            q.project_name = name
            q.name = name  # 码记录名跟随项目名

        if project_location is not None:
            q.project_location = _clean_info_value(project_location, "project_location")
        if customer_name is not None:
            q.customer_name = _clean_info_value(customer_name, "customer_name")
        if vehicle_model is not None:
            q.vehicle_model = _clean_info_value(vehicle_model, "vehicle_model")

        # 录入信息保存后状态变更为 published（2026-09-30 用户口径：扫码→录入→保存即发布）
        if q.status == QrcodeStatus.ENTERING:
            q.status = QrcodeStatus.PUBLISHED

        db.commit()
        db.refresh(q)
        return _to_dict(q)
    finally:
        db.close()


# ── 状态流转 ──

_STATUS_TRANSITIONS = {
    QrcodeStatus.INIT: [QrcodeStatus.ENTERING],
    QrcodeStatus.ENTERING: [QrcodeStatus.PUBLISHED, QrcodeStatus.INIT],   # 可直接发布或回退
    QrcodeStatus.PUBLISHED: [QrcodeStatus.DEPRECATED],
    QrcodeStatus.DEPRECATED: [],
}


def _allowed_targets(q: WechatQrcode) -> list:
    """当前状态允许流转到的下一状态。"""
    return list(_STATUS_TRANSITIONS.get(q.status, []))


def _transition(db: Session, q: WechatQrcode, target: str, actor: str) -> WechatQrcode:
    allowed = _allowed_targets(q)
    if target not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"状态 {q.status} 无法转到 {target}，允许: {allowed}",
        )
    if target == QrcodeStatus.PUBLISHED and not q.ticket:
        # published 意味着对外可扫，不能没有 ticket（原 publish 接口里的自检，
        # 收进这里让 confirm「确认即发布」也走同一道闸，且状态机校验在前、报错更准）
        raise HTTPException(status_code=400, detail="未生成 ticket，无法发布")
    q.status = target
    if target == QrcodeStatus.PUBLISHED:
        q.published_by = actor
    if target == QrcodeStatus.DEPRECATED:
        q.deprecated_by = actor
    db.commit()
    db.refresh(q)
    return q


@router.post("/{qid}/confirm", summary="状态流转：entering → published（登录即可）")
async def confirm_qrcode(qid: int, current_user=Depends(get_current_active_user_from_token)):
    # 权限「登录即可」：扫码用户在录入信息详情页点「确认信息」直接发布（2026-09-30 用户口径）
    db: Session = db_manager.get_db()
    try:
        q = db.query(WechatQrcode).filter(WechatQrcode.id == qid).first()
        if not q:
            raise HTTPException(status_code=404, detail="二维码不存在")
        # 确认后直接发布
        target = QrcodeStatus.PUBLISHED
        actor = current_user.get("username") if isinstance(current_user, dict) else str(current_user)
        return _to_dict(_transition(db, q, target, actor))
    finally:
        db.close()


@router.post("/{qid}/publish", summary="状态流转 → published")
async def publish_qrcode(qid: int, current_user=require_permission("frontend:admin:other:show")):
    db: Session = db_manager.get_db()
    try:
        q = db.query(WechatQrcode).filter(WechatQrcode.id == qid).first()
        if not q:
            raise HTTPException(status_code=404, detail="二维码不存在")
        actor = current_user.get("username") if isinstance(current_user, dict) else str(current_user)
        return _to_dict(_transition(db, q, QrcodeStatus.PUBLISHED, actor))
    finally:
        db.close()


@router.post("/{qid}/deprecate", summary="状态流转 → deprecated")
async def deprecate_qrcode(qid: int, current_user=require_permission("frontend:admin:other:show")):
    db: Session = db_manager.get_db()
    try:
        q = db.query(WechatQrcode).filter(WechatQrcode.id == qid).first()
        if not q:
            raise HTTPException(status_code=404, detail="二维码不存在")
        actor = current_user.get("username") if isinstance(current_user, dict) else str(current_user)
        return _to_dict(_transition(db, q, QrcodeStatus.DEPRECATED, actor))
    finally:
        db.close()


# ── 删除 ──

@router.delete("/{qid}", summary="删除二维码记录")
async def delete_qrcode(qid: int, current_user=require_permission("frontend:admin:other:show")):
    db: Session = db_manager.get_db()
    try:
        q = db.query(WechatQrcode).filter(WechatQrcode.id == qid).first()
        if not q:
            raise HTTPException(status_code=404, detail="二维码不存在")
        if q.status not in (QrcodeStatus.INIT, QrcodeStatus.DEPRECATED):
            raise HTTPException(status_code=400, detail=f"状态 {q.status} 不可删除，请先弃用")
        db.delete(q)
        db.commit()
        return {"ok": True}
    finally:
        db.close()


# ── 状态统计 ──

@router.get("/stats/summary", summary="各状态数量统计")
async def qrcode_stats(current_user=require_permission("frontend:admin:other:show")):
    db: Session = db_manager.get_db()
    try:
        rows = db.query(
            WechatQrcode.status,
            WechatQrcode.type,
            db.query(WechatQrcode).filter(
                WechatQrcode.status == WechatQrcode.status,
                WechatQrcode.type == WechatQrcode.type,
            ).correlate(WechatQrcode).count(),
        ).distinct().all()

        summary = {"status": {}, "type": {}, "total": db.query(WechatQrcode).count()}
        for s in [QrcodeStatus.INIT, QrcodeStatus.ENTERING, QrcodeStatus.PUBLISHED, QrcodeStatus.DEPRECATED]:
            summary["status"][s] = db.query(WechatQrcode).filter(WechatQrcode.status == s).count()
        for t in [QrcodeType.TEMPORARY, QrcodeType.PERMANENT]:
            summary["type"][t] = db.query(WechatQrcode).filter(WechatQrcode.type == t).count()
        summary["permanent_quota_remaining"] = PERMANENT_QRCODE_MAX - summary["type"].get(QrcodeType.PERMANENT, 0)
        return summary
    finally:
        db.close()
