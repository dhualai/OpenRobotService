"""项目信息值变动的历史记录 Service（编辑历史 / 审计）。

新结构下只有**一张**历史表 project_info_value_history，它同时承担两类记录：
  - 值变动：operation_type = create / update / delete，old_value / new_value 存原生 JSON；
  - 结构变动：operation_type = node_create / node_move / node_rename，记节点本身的变化。

每行都带 project_id **和** node_id（SKILL 第 8 节）：同一个全局节点的值在项目 A / B / C
各有各的历史，只存 node_id 会把三个项目的历史串成一条线。
node_key / node_name / node_type 是写入时的快照——节点被改名或停用后，历史列表
仍能显示当时这个节点叫什么。

展示归属（与前端「编辑历史」弹层一致，规则沿用旧实现）：
  - 节点 X 的历史 = node_id=X 的记录 + 「parent_id=X 且 operation_type=delete」的记录；
  - 删除记录挂在被删节点的**上级节点**上——节点删掉后自身记录查不到，
    用户要求「删除节点在其上级节点显示删除记录」；
  - 整树级操作 node_id 为 NULL，一条记录说明整树发生了什么。
  - 一级标签（根节点）的历史是**整棵子树**的：list_for_subtree 按子树里的全部节点 id 取，
    再加上「parent_id 落在子树里且 operation_type=delete」的记录——
    被删掉的子孙节点自己已不在节点表里，只看 node_id 会漏掉它们的删除记录。

**写入与业务同事务**（SKILL 第 8.1 节）：调用方在同一个 Session 里 add 记录再统一 commit，
任何一步失败一起回滚，不会出现「值改了但没记」或「记了但值没改」。

⚠ 敏感值：old_value / new_value 目前明文存储节点值（含 SSH / ToDesk / 服务器口令）。
  这是产品经理明确拍板的决定，接入字段级加密时以本表为改造点——见 SKILL 第 14 节。
"""
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import and_, func, or_

from app.core.db import SessionLocal  # 共享引擎（pool_pre_ping/pool_recycle），见 app/core/db.py
from app.modules.admin.models_das.models import ProjectInfoNode, ProjectInfoValueHistory
from app.models.delivery import PROJECT_INFO_NODE_ACTIVE

# 值变动
ACTION_CREATE = "create"
ACTION_UPDATE = "update"
ACTION_DELETE = "delete"
# 结构变动（管理员改模板 / 项目增补节点）
ACTION_NODE_CREATE = "node_create"
ACTION_NODE_MOVE = "node_move"
ACTION_NODE_RENAME = "node_rename"

# 值类型的中文名（与前端 VALUE_TYPE_NAMES 一致）
VALUE_TYPE_LABELS = {
    "text": "文字输入",
    "number": "数字",
    "boolean": "是/否",
    "date": "日期",
    "select": "下拉选择",
    "multi_select": "多选",
    "person": "人员",
    "attachment": "附件",
    "json": "结构化数据",
}

# detail 里单个值的展示上限，超出截断加省略号（完整值仍能在历史里对比）
MAX_VALUE_DISPLAY = 40


def _now_str() -> str:
    """与 delivery.py / info_node_service 一致，用字符串存时间戳。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _new_id() -> str:
    """时间有序的 UUID（v7）：changed_at 只精确到秒，同一秒内的先后靠 id 兜底排序，
    「各节点最新记录 id」也直接取 max(id) 即可，不必按时间比。"""
    uuid7 = getattr(uuid, "uuid7", None)
    if uuid7 is not None:
        return str(uuid7())
    ms = int(time.time() * 1000)
    rand = uuid.uuid4().int & ((1 << 74) - 1)
    return str(uuid.UUID(int=(ms << 80) | (0x7 << 76) | rand))


def _short(text: str, limit: int = MAX_VALUE_DISPLAY) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _type_label(value_type: Optional[str]) -> str:
    return VALUE_TYPE_LABELS.get(value_type or "text", value_type or "文字输入")


def describe_value(value_type: Optional[str], value: Any) -> str:
    """节点值（原生 JSON）→ 人话文本。

    text/number/date 原样；select 取值本身；multi_select 用「、」连接；
    attachment 尽量取文件名；person 取姓名；对象类按紧凑 JSON 兜底，不抛异常。
    """
    if value is None or value == "":
        return ""
    if value_type == "multi_select":
        if isinstance(value, list):
            return "、".join(str(item) for item in value if item not in (None, ""))
        return str(value)
    if value_type == "select":
        # 落库的原生 JSON：下拉只存选中的那一项（选项属于节点定义）。
        # 这里读出 selected，带出整坨 {selected, options} 会让历史里全是 JSON 噪音。
        if isinstance(value, dict):
            picked = value.get("selected")
            return "" if picked in (None, "") else str(picked)
        return str(value)
    if value_type == "attachment":
        if isinstance(value, dict):
            for key in ("name", "file_name"):
                picked = value.get(key)
                if isinstance(picked, str) and picked.strip():
                    return picked.strip()
            # 附件可能存成列表（多附件节点）
        if isinstance(value, list):
            names = [
                str(item.get("name"))
                for item in value
                if isinstance(item, dict) and item.get("name")
            ]
            return "、".join(names)
        return ""
    if value_type == "person":
        if isinstance(value, dict):
            picked = value.get("name")
            return picked.strip() if isinstance(picked, str) else ""
        if isinstance(value, list):
            return "、".join(
                str(item.get("name"))
                for item in value
                if isinstance(item, dict) and item.get("name")
            )
        return str(value)
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (dict, list)):
        import json
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, str):
        return " ".join(value.split())
    return str(value)


# ── detail 文案（纯函数，便于单测） ─────────────────────


def build_value_detail(
    value_type: Optional[str],
    old_value: Any,
    new_value: Any,
    operation_type: str,
) -> str:
    """拼值变动的「改了什么」。值相同返回空串（调用方据此跳过写入）。"""
    old_disp = _short(describe_value(value_type, old_value)) or "空"
    new_disp = _short(describe_value(value_type, new_value)) or "空"
    if operation_type == ACTION_CREATE:
        return f"填写内容「{new_disp}」"
    if operation_type == ACTION_DELETE:
        return f"清空了内容（原为「{old_disp}」）"
    return f"把内容从「{old_disp}」改为「{new_disp}」"


def build_rename_detail(old_name: str, new_name: str, changed_type: bool,
                        old_type: Optional[str], new_type: Optional[str]) -> str:
    """管理员改节点定义时的人话描述（改名 / 改值类型可同时发生）。"""
    parts: List[str] = []
    if new_name != old_name:
        parts.append(f"把名称从「{old_name}」改为「{new_name}」")
    if changed_type and new_type != old_type:
        parts.append(f"把值类型从「{_type_label(old_type)}」改为「{_type_label(new_type)}」")
    return "；".join(parts)


def build_node_create_detail(node_name: str, parent_name: Optional[str]) -> str:
    if parent_name:
        return f"在「{parent_name}」下增补节点「{node_name}」"
    return f"增补节点「{node_name}」"


def build_node_delete_detail(node_name: str, child_count: int) -> str:
    if child_count > 0:
        return f"删除节点「{node_name}」及其 {child_count} 个子节点"
    return f"删除节点「{node_name}」"


def build_node_move_detail(
    node_name: str,
    old_parent_name: Optional[str],
    new_parent_name: Optional[str],
    same_parent: bool = False,
) -> str:
    if same_parent:
        return f"调整了「{node_name}」在同级中的顺序"
    if not new_parent_name:
        return f"把「{node_name}」从「{old_parent_name or '最外层'}」移到最外层"
    if not old_parent_name:
        return f"把「{node_name}」从最外层移到「{new_parent_name}」下"
    return f"把「{node_name}」从「{old_parent_name}」移到「{new_parent_name}」下"


def build_reset_detail(removed_nodes: int, cleared: int) -> str:
    """一键清空（恢复为模板结构）的整树级记录：删了多少增补节点、清了多少项内容。

    只描述这两个数——细节在各条节点/值的记录里（调用方只在至少动了一样时才写这条）。
    """
    parts: List[str] = []
    if removed_nodes:
        parts.append(f"删除 {removed_nodes} 个增补节点")
    if cleared:
        parts.append(f"清空 {cleared} 项已填内容")
    return "一键清空（恢复为模板结构）：" + "，".join(parts)


def build_import_detail(source: str, added: int) -> str:
    if source == "template":
        return f"按预设模板同步信息树：新增 {added} 个节点"
    return f"导入信息树：新增 {added} 个节点"


def build_sync_detail(added: int, updated: int, disabled: int) -> str:
    return f"全局模板变更：新增 {added} 个、更新 {updated} 个、停用 {disabled} 个节点"


# ── 写入（与业务同事务，由调用方 commit） ────────────────


def add_history(
    db,
    *,
    project_id: str,
    operation_type: str,
    detail: str,
    node_id: Optional[str] = None,
    parent_id: Optional[str] = None,
    node_key: str = "",
    node_name: str = "",
    node_type: Optional[str] = None,
    old_value: Any = None,
    new_value: Any = None,
    changed_by: Optional[str] = None,
    changed_by_name: Optional[str] = None,
    change_reason: Optional[str] = None,
    changed_at: Optional[str] = None,
) -> None:
    """把一条历史记录加入调用方的 Session（不 commit，由业务事务统一提交）。

    old_value / new_value 存原生 JSON，与 project_info_value.value_json 同尺度，
    便于做前后对比。⚠ 明文存储，见模块头。
    """
    db.add(ProjectInfoValueHistory(
        id=_new_id(),
        project_id=project_id,
        node_id=node_id,
        parent_id=parent_id,
        node_key=(node_key or "")[:191],
        node_name=(node_name or "")[:255],
        node_type=node_type,
        old_value=old_value,
        new_value=new_value,
        operation_type=operation_type,
        changed_by=changed_by,
        changed_by_name=changed_by_name,
        change_reason=change_reason,
        detail=detail,
        changed_at=changed_at or _now_str(),
    ))


# ── 查询 ───────────────────────────────────────────────


def _to_dict(row: ProjectInfoValueHistory) -> Dict[str, Any]:
    """历史行 → 接口结构。

    字段名沿用前端既有契约（node_title / action / operator / created_at），
    这样「项目信息管理」卡的编辑历史弹层不必改：新表里的
    node_name → node_title、operation_type → action、changed_by → operator、
    changed_at → created_at。old_value / new_value 是新增字段，供前端做前后对比。
    """
    return {
        "id": row.id,
        "project_id": row.project_id,
        "node_id": row.node_id,
        "parent_id": row.parent_id,
        "node_key": row.node_key,
        "node_title": row.node_name,
        "node_type": row.node_type,
        "action": row.operation_type,
        "operator": row.changed_by,
        "operator_name": row.changed_by_name,
        "old_value": row.old_value,
        "new_value": row.new_value,
        "change_reason": row.change_reason,
        "detail": row.detail,
        "created_at": row.changed_at,
    }


def subtree_node_ids(rows: Iterable[Any], root_id: str) -> List[str]:
    """子树节点 id（含 root_id 自己），按树的先序（父在前、同级保持入参顺序）。

    rows 只需要 id / parent_id 两个属性（ORM 行、SimpleNamespace、dict 都行）。
    同级顺序取决于调用方的排序——list_for_subtree 传的是按 sort_order 排好的行，
    与 get_tree 组装出的顺序一致，前端据此把记录分组才不会串位置。

    root_id 即便当前不在 rows 里（节点已被删）也照样返回，
    这样「查看一个已删节点的历史」与 list_for_node 的表现一致。
    """
    children: Dict[Optional[str], List[str]] = {}
    for row in rows:
        children.setdefault(
            row["parent_id"] if isinstance(row, dict) else row.parent_id, []
        ).append(row["id"] if isinstance(row, dict) else row.id)
    ordered: List[str] = []
    seen = set()
    stack = [root_id]
    while stack:
        node_id = stack.pop()
        if node_id in seen:  # 脏数据成环时兜底，不至于死循环
            continue
        seen.add(node_id)
        ordered.append(node_id)
        stack.extend(reversed(children.get(node_id, [])))
    return ordered


class InfoNodeChangeService:
    """历史记录的读写（写入走 add_history，与业务同事务）。"""

    def list_for_node(self, project_id: str, node_id: str, limit: int = 100) -> List[Dict]:
        """某节点的编辑历史：自身记录 + 直接子节点的删除记录（最新在前）。"""
        db = SessionLocal()
        try:
            rows = db.query(ProjectInfoValueHistory).filter(
                ProjectInfoValueHistory.project_id == project_id,
                or_(
                    ProjectInfoValueHistory.node_id == node_id,
                    and_(
                        ProjectInfoValueHistory.parent_id == node_id,
                        ProjectInfoValueHistory.operation_type == ACTION_DELETE,
                    ),
                ),
            ).order_by(
                ProjectInfoValueHistory.changed_at.desc(),
                ProjectInfoValueHistory.id.desc(),
            ).limit(limit).all()
            return [_to_dict(row) for row in rows]
        finally:
            db.close()

    def list_for_subtree(self, project_id: str, node_id: str, limit: int = 200) -> List[Dict]:
        """某节点**及其全部子孙**的历史（最新在前）：一级标签的「修改记录」用。

        取两条并集（与 list_for_node 同口径，只是把「直接子节点」放大成「子树里的所有节点」）：
          node_id 落在子树里的记录，加上 parent_id 落在子树里且 operation_type=delete 的记录。
        后一条是必需的：删除是**整棵子树一条记录**挂在被删节点的上级上，被删的子孙
        已不在节点表里，光看 node_id 会把它们的删除记录整片漏掉。

        节点范围按读树的同一把尺子取（启用中的全局节点 ∪ 本项目增补节点），
        否则前端看不到的节点会在这里冒出记录。节点已被删（不在节点表里）时，
        子树只有它自己，退化成「看这个已删节点的记录」——与 list_for_node 一致。
        """
        db = SessionLocal()
        try:
            rows = db.query(ProjectInfoNode.id, ProjectInfoNode.parent_id).filter(
                ProjectInfoNode.status == PROJECT_INFO_NODE_ACTIVE,
                (ProjectInfoNode.project_id.is_(None))
                | (ProjectInfoNode.project_id == project_id),
            ).order_by(ProjectInfoNode.sort_order).all()
            ids = subtree_node_ids(rows, node_id)
            records = db.query(ProjectInfoValueHistory).filter(
                ProjectInfoValueHistory.project_id == project_id,
                or_(
                    ProjectInfoValueHistory.node_id.in_(ids),
                    and_(
                        ProjectInfoValueHistory.parent_id.in_(ids),
                        ProjectInfoValueHistory.operation_type == ACTION_DELETE,
                    ),
                ),
            ).order_by(
                ProjectInfoValueHistory.changed_at.desc(),
                ProjectInfoValueHistory.id.desc(),
            ).limit(limit).all()
            return [_to_dict(row) for row in records]
        finally:
            db.close()

    def list_project_changes(self, project_id: str, limit: int = 100) -> List[Dict]:
        """项目全部历史（最新在前），含整树级记录（node_id 为 NULL）。"""
        db = SessionLocal()
        try:
            rows = db.query(ProjectInfoValueHistory).filter(
                ProjectInfoValueHistory.project_id == project_id,
            ).order_by(
                ProjectInfoValueHistory.changed_at.desc(),
                ProjectInfoValueHistory.id.desc(),
            ).limit(limit).all()
            return [_to_dict(row) for row in rows]
        finally:
            db.close()

    def latest_by_node(self, project_id: str) -> Dict[str, str]:
        """各节点最新记录的 id：{节点id: 记录id}，供前端算「小红点」。

        归属规则同 list_for_node：删除记录计入其上级节点。
        记录 id 是时间有序的 UUIDv7（见 _new_id），max(id) 即最新一条——不用 changed_at
        是因为它只到秒，同秒内的新记录按时间比会漏（点开过、同一秒又有新改动就不出红点）。
        前端拿这个 id 与本机已读水位做相等比较：一致=看过，不一致=有新变动。
        """
        db = SessionLocal()
        try:
            latest: Dict[str, str] = {}
            own = db.query(
                ProjectInfoValueHistory.node_id,
                func.max(ProjectInfoValueHistory.id),
            ).filter(
                ProjectInfoValueHistory.project_id == project_id,
                ProjectInfoValueHistory.node_id.isnot(None),
            ).group_by(ProjectInfoValueHistory.node_id).all()
            for node_id, record_id in own:
                if node_id and record_id:
                    latest[node_id] = record_id

            deleted = db.query(
                ProjectInfoValueHistory.parent_id,
                func.max(ProjectInfoValueHistory.id),
            ).filter(
                ProjectInfoValueHistory.project_id == project_id,
                ProjectInfoValueHistory.operation_type == ACTION_DELETE,
                ProjectInfoValueHistory.parent_id.isnot(None),
            ).group_by(ProjectInfoValueHistory.parent_id).all()
            for parent_id, record_id in deleted:
                if parent_id and record_id and record_id > latest.get(parent_id, ""):
                    latest[parent_id] = record_id
            return latest
        finally:
            db.close()


info_node_change_service = InfoNodeChangeService()
