"""项目信息树节点 API（树查询 + 值写入 + 增补信息 + 详情模板 + 编辑历史 + 关注/项目动态）。

路由前缀 /info-nodes，挂载到 admin_router 后实际路径为
/api/admin/info-nodes/projects/{project_id}/...。

**鉴权口径（本次改造的核心变化之一）**
  结构类写接口 = 改「字段定义」：`PUT /nodes/{id}`、`PATCH /nodes/{id}/move`、
  `DELETE /nodes/{id}`、`POST /projects/{id}`（增补自定义字段）、`/template`
  → 一律 `Depends(get_current_admin_user)`。旧实现的注释是「写接口不强制鉴权
  （沿用网关管控）」，但网关只管「登没登录」，不区分管理员与普通用户；
  新需求要求「普通用户不能再直接修改项目信息树」，只靠前端藏按钮是拦不住的
  （接口是公开可直连的），所以闸门必须落在这里。

  值类写接口 = 填「项目数据」：`PUT /nodes/{id}/value`
  → 任何登录用户都能写，且只能写**已存在节点**的值，不能改结构、不能加节点。
  普通用户要记表外信息，走 `POST /projects/{id}/custom-nodes`——即「增补信息」，
  任何节点下都能加（层数 ≤ 4），全局定义不受影响。

  读接口（树 / 历史 / 关注 / 动态 / 模板读取）沿用网关管控，不额外鉴权：
  普通用户本来就要看项目信息。

性能约定：除 parse-file（需 await 大模型调用）外，本组路由均为同步 def——
Service 层是同步 SQLAlchemy，async def 里跑同步 DB 会阻塞事件循环、拖慢全部并发请求；
同步 def 由 FastAPI 自动放入线程池执行（默认 40 线程），互不阻塞。
"""
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field
from app.modules.admin.api.auth import get_request_actor_optional
from app.modules.admin.api.permissions import get_current_admin_user
from app.modules.admin.services.info_node_service import info_node_service
from app.modules.admin.services.info_node_change_service import info_node_change_service
from app.modules.admin.services.info_node_mark_service import info_node_mark_service
from app.modules.admin.services import info_node_import_service
from app.modules.admin.services.info_template_service import info_template_service
from app.models.delivery import PROJECT_INFO_VALUE_TYPES


# ── 请求模型 ───────────────────────────────────────────

class InfoNodeCreate(BaseModel):
    """增补自定义字段（管理员在本项目范围内新增，不动全局模板）。"""
    parent_id: Optional[str] = Field(None, description="父节点ID, NULL=最外层")
    title: Optional[str] = Field(None, description="节点名称")
    node_name: Optional[str] = Field(None, description="节点名称（与 title 等价）")
    content_type: Optional[str] = Field(None, description="内容类型: text/select/file/image")
    value_type: Optional[str] = Field(None, description="值类型（优先于 content_type）")
    node_key: Optional[str] = Field(None, description="节点标识；不传则自动生成")
    sort_order: Optional[int] = Field(None, description="同级排序；不传则排到末尾")


class InfoNodeUpdate(BaseModel):
    title: Optional[str] = None
    node_name: Optional[str] = None
    content_type: Optional[str] = None
    value_type: Optional[str] = None
    sort_order: Optional[int] = None
    required: Optional[bool] = None
    allow_custom: Optional[bool] = None
    options: Optional[List[str]] = Field(None, description="下拉选项（字段定义，仅管理员）")
    titleOptions: Optional[List[str]] = Field(None, description="标题备选项（字段定义，仅管理员）")


class InfoNodeMove(BaseModel):
    new_parent_id: Optional[str] = Field(None, description="目标父节点ID, NULL=移到最外层")
    new_sort_order: int = Field(0, description="目标排序位置")


class InfoNodeValueWrite(BaseModel):
    value: Any = Field(None, description="节点值；下拉/布尔传 {selected, options}，附件传 {name, resource_id, size}")


class InfoNodeImport(BaseModel):
    nodes: List[Dict[str, Any]] = Field(..., description="信息树(递归嵌套, 含children)")


class InfoTemplateSave(BaseModel):
    nodes: List[Dict[str, Any]] = Field(..., description="详情模板节点树(递归嵌套, 含children)")
    dry_run: bool = Field(False, description="为 True 时只预览影响，不保存")


# 内容类型 → 值类型（编辑页仍按 content_type 提交）
_CONTENT_TO_VALUE_TYPE = {
    "text": "text", "select": "select", "file": "attachment", "image": "attachment",
}


def _resolve_value_type(content_type: Optional[str], value_type: Optional[str]) -> str:
    """请求里的类型统一成内部 value_type：显式 value_type 优先，其次 content_type。"""
    if value_type and value_type in PROJECT_INFO_VALUE_TYPES:
        return value_type
    if content_type and content_type in _CONTENT_TO_VALUE_TYPE:
        return _CONTENT_TO_VALUE_TYPE[content_type]
    return "text"


# ── 路由 ───────────────────────────────────────────────

info_node_router = APIRouter(prefix="/info-nodes", tags=["admin-info-nodes"])


@info_node_router.get("/projects/{project_id}", summary="获取项目信息树")
def get_info_tree(project_id: str):
    """返回项目信息树的递归嵌套结构（每节点含 children 数组）。

    树 = 全部启用中的全局字段定义 ∪ 本项目自己增补的节点，
    每个节点的 value 是该**本项目**在 project_info_value 里的值（没填过则为 null）。
    接口字段名沿用旧契约（title / content_type / value），前端展示卡与编辑页无需改读逻辑。
    """
    return info_node_service.get_tree(project_id)


@info_node_router.post("/projects/{project_id}", summary="增补信息节点（仅管理员）", status_code=201)
def create_info_node(project_id: str, node: InfoNodeCreate,
                     current_user: Dict[str, Any] = Depends(get_current_admin_user),
                     actor: Dict[str, Optional[str]] = Depends(get_request_actor_optional)):
    """在某项目范围内增补一个自定义字段（不动全局模板，别的项目看不到）。

    普通用户请走 /custom-nodes——那条路径只改本项目、且限 4 层，
    本接口假定调用者有意在项目里直接加字段。
    """
    name = node.node_name or node.title
    if not name or not name.strip():
        raise HTTPException(status_code=400, detail="节点名称不能为空")
    try:
        return info_node_service.add_custom_node(
            project_id,
            parent_id=node.parent_id,
            node_name=name,
            value_type=_resolve_value_type(node.content_type, node.value_type),
            sort_order=node.sort_order,
            node_key=node.node_key,
            operator=actor.get("username") or current_user.get("username"),
            operator_name=actor.get("name") or current_user.get("name"),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@info_node_router.post("/projects/{project_id}/custom-nodes",
                       summary="增补信息（登录用户，任意节点下都可加，限 4 层）", status_code=201)
def add_custom_node(project_id: str, node: InfoNodeCreate,
                    actor: Dict[str, Optional[str]] = Depends(get_request_actor_optional)):
    """普通用户「增补信息」的唯一入口：加一条本项目自己的字段。

    与管理员接口的区别：只能加在本项目下，层级不得超过 4 层。
    任何节点下都能增补（allow_custom 闸门已于 2026-09-18 取消）。
    """
    name = node.node_name or node.title
    if not name or not name.strip():
        raise HTTPException(status_code=400, detail="节点名称不能为空")
    if not node.parent_id:
        raise HTTPException(status_code=400, detail="增补信息必须指定要挂在哪个节点下")
    # 增补入口不认自定义 node_key（外部标识只对管理员开放），强制由服务端生成
    try:
        return info_node_service.add_custom_node(
            project_id,
            parent_id=node.parent_id,
            node_name=name,
            value_type=_resolve_value_type(node.content_type, node.value_type),
            sort_order=node.sort_order,
            node_key=None,
            operator=actor.get("username"), operator_name=actor.get("name"),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@info_node_router.put("/nodes/{node_id}/value", summary="写入节点值（登录用户）")
def set_info_node_value(node_id: str, payload: InfoNodeValueWrite,
                        project_id: str = Query(..., description="项目ID（值的归属项目）"),
                        actor: Dict[str, Optional[str]] = Depends(get_request_actor_optional)):
    """填某个项目某个节点的内容（值 + 历史同一事务）。

    这是普通用户**唯一**能改数据的地方：只能写已存在节点的值，
    改不了节点名称/类型/位置，也加不了节点。
    节点属于本项目（含全局字段）才允许写；别的项目的增补节点一律 403。
    """
    try:
        return info_node_service.set_value(
            project_id, node_id, payload.value,
            operator=actor.get("username"), operator_name=actor.get("name"),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@info_node_router.put("/nodes/{node_id}", summary="更新节点定义（仅管理员）")
def update_info_node(node_id: str, update: InfoNodeUpdate,
                     current_user: Dict[str, Any] = Depends(get_current_admin_user),
                     actor: Dict[str, Optional[str]] = Depends(get_request_actor_optional)):
    """改节点定义（名称 / 类型 / 排序 / 是否允许增补）。

    只对**本项目增补的节点**有效；全局字段的定义请在「详情模板」里改
    （那是全体项目共享的一份，改这里会 403）。
    """
    update_data = {k: v for k, v in update.model_dump().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="无更新字段")
    if 'content_type' in update_data:
        # 编辑页仍按 content_type 提交，转成 value_type 再落库
        update_data['value_type'] = _resolve_value_type(
            update_data.pop('content_type'), update_data.get('value_type'),
        )
    try:
        return info_node_service.update_node(
            node_id, update_data,
            operator=actor.get("username") or current_user.get("username"),
            operator_name=actor.get("name") or current_user.get("name"),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@info_node_router.patch("/nodes/{node_id}/move", summary="移动节点（仅管理员）")
def move_info_node(node_id: str, move: InfoNodeMove,
                   current_user: Dict[str, Any] = Depends(get_current_admin_user),
                   actor: Dict[str, Optional[str]] = Depends(get_request_actor_optional)):
    """把本项目增补的节点移到新父节点下（拖拽排序）。全局字段的位置由模板决定。"""
    try:
        return info_node_service.move_node(
            node_id, move.new_parent_id, move.new_sort_order,
            operator=actor.get("username") or current_user.get("username"),
            operator_name=actor.get("name") or current_user.get("name"),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@info_node_router.delete("/nodes/{node_id}", summary="删除节点(含子树，仅管理员)")
def delete_info_node(node_id: str,
                     current_user: Dict[str, Any] = Depends(get_current_admin_user),
                     actor: Dict[str, Optional[str]] = Depends(get_request_actor_optional)):
    """删除本项目增补的节点及其子树（连带其值与关注）。

    全局字段不可在此删除——它属于模板，停用请走 /template 保存时移除。
    删除记录只有一条，挂在被删节点的上级节点上（见 info_node_change_service）。
    """
    try:
        deleted = info_node_service.delete_node(
            node_id,
            operator=actor.get("username") or current_user.get("username"),
            operator_name=actor.get("name") or current_user.get("name"),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    if not deleted:
        raise HTTPException(status_code=404, detail="节点不存在")
    return {"detail": "已删除节点及其子树"}


@info_node_router.post("/projects/{project_id}/import", summary="批量导入信息树（仅管理员）")
def import_info_tree(project_id: str, data: InfoNodeImport,
                     current_user: Dict[str, Any] = Depends(get_current_admin_user),
                     actor: Dict[str, Optional[str]] = Depends(get_request_actor_optional)):
    """把一棵信息树导入为**本项目的增补节点**（只增不改不删），返回新增数量。

    旧实现是「先清空旧节点再导入」；新结构下那等于抹掉项目已填的全部信息，
    所以改为纯增补：同 node_key 的节点已存在就跳过，不动全局定义、不动已有值。
    """
    try:
        count = info_node_service.import_tree(
            project_id, data.nodes,
            operator=actor.get("username") or current_user.get("username"),
            operator_name=actor.get("name") or current_user.get("name"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"imported": count}


# ── 编辑历史（节点操作记录） ──────────────────────────

@info_node_router.get("/projects/{project_id}/changes", summary="获取节点操作记录")
def get_info_node_changes(
    project_id: str,
    node_id: Optional[str] = Query(None, description="节点ID；给了则只返回该节点的历史（含其直接子节点的删除记录）"),
    limit: int = Query(100, ge=1, le=500, description="最多返回条数（最新在前）"),
):
    """节点编辑历史。传 node_id 返回该节点的记录（自身操作 + 其子节点的删除记录）；
    不传则返回项目全部记录（含整树级导入）。"""
    if node_id:
        changes = info_node_change_service.list_for_node(project_id, node_id, limit)
    else:
        changes = info_node_change_service.list_project_changes(project_id, limit)
    return {"changes": changes}


@info_node_router.get("/projects/{project_id}/changes/summary",
                      summary="各节点最新记录 id（小红点）")
def get_info_node_change_summary(project_id: str):
    """返回 {节点id: 最新记录 id}，用于前端判断哪些节点的「历史」有新变动（小红点）：
    与本机已读水位（也是记录 id）不一致即未读。

    记录 id 时间有序，只做相等比较；不用时间戳是因为它只到秒，同秒内的新记录会漏。
    与 changes 接口同口径：子节点的删除记录计入其上级节点。
    """
    return {"latest": info_node_change_service.latest_by_node(project_id)}


# ── 关注（标注）与项目动态（按当前登录人隔离） ────────────

def _require_operator(actor: Dict[str, Optional[str]]) -> str:
    """关注列表是「每人一份」，识别不到操作人就无法读写个人列表 → 401。

    正常前端请求带 Bearer token（JWT sub 即登录名）；401 会触发前端的
    刷新重试链路，token 过期场景可自愈。
    """
    username = actor.get("username")
    if not username:
        raise HTTPException(status_code=401, detail="无法识别当前用户，请重新登录后再关注")
    return username


@info_node_router.get("/projects/{project_id}/marks", summary="获取当前用户关注的节点ID列表")
def get_info_node_marks(project_id: str,
                        actor: Dict[str, Optional[str]] = Depends(get_request_actor_optional)):
    """返回当前登录人在该项目关注的节点 id（「项目信息管理」卡据它点亮星标）。

    关注按人隔离：自己关注的自己才能看到，各人的星标互不影响。
    """
    operator = _require_operator(actor)
    return {"node_ids": info_node_mark_service.list_for_project(project_id, operator)}


@info_node_router.post("/nodes/{node_id}/mark",
                       summary="切换当前用户的节点关注状态")
def toggle_info_node_mark(node_id: str,
                          project_id: Optional[str] = Query(
                              None, description="项目ID（关注发生的项目）；缺省时按该节点已有标注切换"),
                          actor: Dict[str, Optional[str]] = Depends(get_request_actor_optional)):
    """点星标：未关注→关注，已关注→取消。返回 {"marked": 切换后是否被关注}。

    只动当前登录人自己的关注，别人对同一节点的关注不受影响。
    project_id 由调用方传入：全局字段各项目共用同一 node_id，
    关注必须记在「哪个项目里关注了」，不能从节点上推（全局节点的 project_id 是空的）。

    project_id 缺省时：增补节点用它自己的 project_id；全局节点则按当前用户在该节点上
    已有的标注整体切换（有就删、没有就报 404，因为没有项目就无从记录新关注）。
    展示卡（ProjectInfoCard）必须传 project_id——新结构下节点几乎全是全局的，
    不传就只能取消关注、无法新增。
    """
    operator = _require_operator(actor)
    try:
        marked = info_node_mark_service.toggle(
            node_id, operator,
            project_id=project_id, operator_name=actor.get("name"),
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="节点不存在")
    except PermissionError:
        raise HTTPException(status_code=403, detail="该节点属于其它项目")
    return {"marked": marked}


@info_node_router.get("/projects/{project_id}/activity",
                      summary="项目动态（当前用户被关注节点的最新变动）")
def get_project_activity(project_id: str,
                         actor: Dict[str, Optional[str]] = Depends(get_request_actor_optional)):
    """当前登录人关注的每个节点只返回其**最新一条**变动（整体最新在前）。

    与前端「项目动态」卡一致：只给变动内容（detail，服务端拼好的人话描述），
    时间/人员字段存在但前端不展示（用户要求动态里不出现修改时间与人员）。
    没记过任何操作的被关注节点不出现（无变动可展示）。
    """
    operator = _require_operator(actor)
    return {"activity": info_node_mark_service.marked_activity(project_id, operator)}


@info_node_router.post("/projects/{project_id}/parse-file",
                       summary="AI 识别导入文件（预览，不落库）")
async def parse_import_file(project_id: str, file: UploadFile = File(...)):
    """上传 Word/Markdown/Excel/文本，由大模型（摇人同款，默认 DeepSeek flash）识别其中
    的项目信息，与现有节点匹配后按「将填写 / 将覆盖 / 未匹配到节点」三类返回预览。

    本接口只读不写：用户在前端勾选确认后，由前端逐节点调用值写入接口落库。
    未识别到信息时三个数组均为空。错误约定：400=文件/状态问题，503=AI 未配置或调用失败。
    """
    data = await file.read()
    try:
        return await info_node_import_service.analyze_import_file(project_id, file.filename or "", data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


# ── 详情模板（管理员维护全局字段定义） ────────────────────

@info_node_router.get("/template", summary="获取项目详情模板（仅管理员）")
def get_info_template(current_user: Dict[str, Any] = Depends(get_current_admin_user)):
    """返回全局字段定义（project_info_node 中 project_id 为 NULL 的那部分）与项目数量。

    返回字段：nodes（节点树，含稳定 id）/ name / updated_at / updated_by /
    project_count / source（恒为 'db'）。
    """
    return info_template_service.get_template()


@info_node_router.post("/template", summary="保存项目详情模板（仅管理员）")
def save_info_template(
    payload: InfoTemplateSave,
    current_user: Dict[str, Any] = Depends(get_current_admin_user),
):
    """保存全局字段定义。**保存即对全体项目生效**（项目不再持有节点副本，无需同步）。

    dry_run=true：只预览影响（会停用哪些字段、影响多少项目），不写库；
    否则：校验 → 更新/新增/停用全局节点行。

    模板里移除的字段是**停用**（status='disabled'）而非删除：项目已填的值
    原样留在 project_info_value，字段重新加回来即恢复。
    校验失败返回 400（层级过深 / 标题为空 / 同层重名等）。
    节点可以既带值又有子节点（如下拉的车型 + 该车型的数量）；根节点的值类型一律归为 text。
    """
    try:
        if payload.dry_run:
            return info_template_service.preview_sync(payload.nodes)
        return info_template_service.save_and_sync(
            payload.nodes,
            current_user.get("username") or "",
            current_user.get("name") or "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
