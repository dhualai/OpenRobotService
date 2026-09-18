"""项目信息树 Service（节点定义的读写 + 项目值的读写 + 树查询）。

**与旧实现的根本差别**：节点（字段定义）与值（项目数据）分开存。
  - `project_info_node.project_id IS NULL` → 全局模板节点，所有项目共用同一行；
    改一次名称/类型，全体项目立刻生效，不再逐项目同步副本。
  - `project_info_node.project_id = A` → 项目 A 的「增补」节点，仅 A 可见。
  - 项目的实际内容一律在 `project_info_value`，靠 (project_id, node_id) 关联。
所以「同一个全局字段，A 项目的值」和「B 项目的值」天然互相隔离（SKILL 第 4 节）。

**写权限分成两类**：
  结构改动（新增/改名/移动/删除/改值类型）→ 只有管理员能改全局节点
    （路由层 get_current_admin_user；本层再校验「非本项目增补节点不得由普通写路径改动」）；
  值写入 → 任何登录用户都能写**已存在**节点的值，但不能改结构、不能新增节点。
普通用户要多记东西，走 add_custom_node（任何节点下都能增补，只受层数限制，最多 4 层）——
「增补信息」而非「改树」。

所有写操作在同一事务里追加历史（info_node_change_service.add_history）：
值变动与历史必须同事务（SKILL 第 8.1 节），失败一起回滚。
"""
from datetime import datetime
from types import SimpleNamespace
from typing import Dict, List, Optional, Tuple
import uuid

from sqlalchemy import text

# 共享数据库引擎（pool_pre_ping / pool_recycle，空闲连接失效自愈），见 app/core/db.py；
# 保留 engine / SessionLocal 模块级名字，供 info_template_service、info_node_import_service 等既有引用沿用。
from app.core.db import SessionLocal, engine
from app.modules.admin.models_das.models import (
    ProjectInfoNode, ProjectInfoValue, Project,
)
from app.modules.admin.services import info_node_change_service as history_log
from app.modules.admin.services import info_node_mark_service as node_marks
from app.models.delivery import (
    PROJECT_DELETED,
    PROJECT_INFO_NODE_ACTIVE,
    PROJECT_INFO_NODE_DISABLED,
    PROJECT_INFO_NODE_TYPES,
    PROJECT_INFO_VALUE_TYPES,
)


# 信息树最大层级（与 default.yaml 的说明、前端 PROJECT_INFO_MAX_DEPTH 一致）
MAX_INFO_DEPTH = 4

# 项目增补节点的 node_key 前缀：全局 key 表里不会出现这个前缀，
# 因此增补节点永远不会撞上全局定义。
CUSTOM_KEY_PREFIX = 'custom.'

# 值类型 → 前端 content_type。
# 前端只有四种内容形式；新模型的值类型更细，在接口层收敛回旧口径，
# 「项目信息管理」卡与编辑页因此不必改渲染逻辑。
# number/boolean/date/person/json 都按文字渲染（值本身仍是原生 JSON，
# 需要精细控件时再给前端加分支，不必动表结构）。
_LEGACY_CONTENT_TYPE = {
    'text': 'text',
    'number': 'text',
    'boolean': 'select',
    'date': 'text',
    'select': 'select',
    'multi_select': 'text',
    'person': 'text',
    'attachment': 'file',
    'json': 'text',
}

# 有后缀名的图片按 image 渲染，其余附件按 file（前端两种内容形式分两套图标/缩略图）
_IMAGE_SUFFIXES = ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.svg', '.tif', '.tiff')


def _now_str() -> str:
    """与 delivery.py 一致，用字符串存时间戳。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _new_id() -> str:
    return str(uuid.uuid4())


def _to_legacy_content_type(node: ProjectInfoNode, value) -> str:
    """值类型 + 实际值 → 前端 content_type（附件按文件名后缀细分图片）。"""
    base = _LEGACY_CONTENT_TYPE.get(node.value_type or 'text', 'text')
    if node.value_type == 'attachment':
        name = ''
        if isinstance(value, dict):
            name = str(value.get('name') or '')
        if name.lower().endswith(_IMAGE_SUFFIXES):
            return 'image'
    return base


def _options_of(node: ProjectInfoNode) -> List[str]:
    """节点的下拉选项清单：config.options 支持 [{"value","label"}] 与 ["a","b"] 两种写法。"""
    return _options_of_key(node, 'options')


def _options_of_key(node: ProjectInfoNode, key: str) -> List[str]:
    """按 config 里的键取选项清单（options = 下拉内容，titleOptions = 标题备选）。"""
    config = node.config or {}
    raw = config.get(key) if isinstance(config, dict) else None
    options: List[str] = []
    for item in raw or []:
        if isinstance(item, dict):
            picked = item.get('value')
            if picked is None:
                picked = item.get('label')
            if picked is not None:
                options.append(str(picked))
        elif item is not None:
            options.append(str(item))
    return options


def _encode_value(node: ProjectInfoNode, value):
    """节点值 → 接口结构（值本身原样透传，只给下拉补上选项清单）。

    前端的下拉控件需要 {selected, options} 两件套才能渲染：selected 来自
    project_info_value，options 来自节点 config——在建表时选项属于「字段定义」，
    不该随每个项目的值复制一份，所以在读取时现拼。
    """
    if node.value_type == 'select':
        options = _options_of(node)
        selected = value if isinstance(value, str) else ('' if value is None else str(value))
        return {'selected': selected, 'options': options}
    if node.value_type == 'boolean':
        # 前端没有独立的布尔控件，借下拉渲染「是/否」；值仍是原生 bool
        return {'selected': _bool_label(value), 'options': ['是', '否']}
    return value


def _options_in_value(value) -> List[str]:
    """从接口口径的值里剥出下拉选项（导入源把 options 塞在 value 里带过来）。

    选项属于节点定义，不该跟每个项目的值存一份；导入时落进节点 config。
    """
    if not isinstance(value, dict):
        return []
    raw = value.get('options')
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if isinstance(item, (str, int, float))]


def _bool_label(value) -> str:
    if value is True:
        return '是'
    if value is False:
        return '否'
    return ''


def _decode_value(node: ProjectInfoNode, value):
    """接口收到的新值 → 落库的原生 JSON。

    前端沿用旧契约提交：text 直接是字符串；select/boolean 提交 {selected, options}；
    attachment 提交对象或对象数组。这里剥掉 options（选项属于节点定义，不跟值存），
    并把 boolean 的「是/否」还原成真正的布尔。
    """
    if value is None:
        return None
    if node.value_type == 'boolean':
        if isinstance(value, dict):
            return _parse_bool(value.get('selected'))
        return _parse_bool(value)
    if node.value_type == 'select':
        if isinstance(value, dict):
            return value.get('selected') or None
        return value
    if node.value_type == 'attachment' and isinstance(value, dict):
        return value
    if isinstance(value, str):
        # 前端把结构化值序列化成 JSON 字符串再发过来（encodeInfoValue）；
        # text 类型原样存字符串，其余类型尝试还原成结构。
        if node.value_type == 'text':
            return value
        restored = _try_json(value)
        return restored if restored is not None else value
    return value


def _parse_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value in ('是', 'true', 'True', '1'):
            return True
        if value in ('否', 'false', 'False', '0', ''):
            return False if value != '' else None
    return None


def _try_json(raw):
    import json
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _node_to_dict(node: ProjectInfoNode, value, children: List[Dict]) -> Dict:
    """节点行 + 本项目值 → 前端一直以来的节点结构。

    对外字段名沿用旧契约（title / content_type / value），这样「项目信息管理」卡
    与编辑页的读渲染完全不必改；新字段（node_key / node_type / value_type /
    required / allow_custom / is_custom）作为附加信息给出，供编辑页区分
    「全局字段」与「本项目增补字段」并决定是否允许增删改。
    （allow_custom 已不再是增补闸门，保留下发只为兼容既有前端契约。）
    """
    return {
        "id": node.id,
        "project_id": node.project_id,
        "parent_id": node.parent_id,
        "title": node.node_name,
        "content_type": _to_legacy_content_type(node, value),
        "value": _encode_value(node, value),
        "sort_order": node.sort_order,
        "created_at": node.created_at,
        "updated_at": node.updated_at,
        # ── 新结构附加字段 ──
        "node_key": node.node_key,
        "node_type": node.node_type,
        "value_type": node.value_type,
        "required": bool(node.required),
        "allow_custom": bool(node.allow_custom),
        "options": _options_of(node),
        # 标题备选项（非末级节点标题可下拉选择）同样属于字段定义
        "titleOptions": _options_of_key(node, 'titleOptions'),
        # project_id 非空 = 本项目自己增补的节点，前端据此决定可改可删
        "is_custom": bool(node.project_id),
        "children": children,
    }


class InfoNodeService:
    """项目信息树：全局定义 ∪ 本项目增补节点，再挂上本项目的值。"""

    def __init__(self):
        self.engine = engine

    # ── 查询 ──────────────────────────────────────────

    def _load_scope_nodes(self, db, project_id: str) -> List[ProjectInfoNode]:
        """取「全部启用中的全局节点」+「本项目的增补节点」（SKILL 第 12 节）。

        全局节点的停用走 status，项目增补节点目前只有「存在/删除」两态。
        """
        return db.query(ProjectInfoNode).filter(
            ProjectInfoNode.status == PROJECT_INFO_NODE_ACTIVE,
            (ProjectInfoNode.project_id.is_(None))
            | (ProjectInfoNode.project_id == project_id),
        ).order_by(ProjectInfoNode.sort_order).all()

    def _values_of(self, db, project_id: str, node_ids: List[str]) -> Dict[str, object]:
        """{node_id: value_json}——本项目对这些节点的值。没有值的节点不出现在结果里。"""
        if not node_ids:
            return {}
        rows = db.query(ProjectInfoValue.node_id, ProjectInfoValue.value_json).filter(
            ProjectInfoValue.project_id == project_id,
            ProjectInfoValue.node_id.in_(node_ids),
        ).all()
        return {node_id: value for node_id, value in rows}

    def get_tree(self, project_id: str) -> List[Dict]:
        """项目信息树（递归嵌套，每节点含 children）。

        一次查全量节点行 + 一次查本项目全部值，Python 组装：
        单项目百级节点下比递归 CTE 简单，且不依赖数据库版本。
        """
        db = SessionLocal()
        try:
            nodes = self._load_scope_nodes(db, project_id)
            if not nodes:
                return []
            values = self._values_of(db, project_id, [n.id for n in nodes])

            children_map: Dict[Optional[str], List[ProjectInfoNode]] = {}
            for n in nodes:
                children_map.setdefault(n.parent_id, []).append(n)

            def build(parent_id: Optional[str]) -> List[Dict]:
                result = []
                # 全局节点与增补节点可能在同一父下，统一按 sort_order 升序
                for n in sorted(children_map.get(parent_id, []), key=lambda x: x.sort_order):
                    result.append(_node_to_dict(n, values.get(n.id), build(n.id)))
                return result

            return build(None)
        finally:
            db.close()

    def get_node(self, node_id: str) -> Optional[Dict]:
        """查单个节点定义（不含 children、不含值）。"""
        db = SessionLocal()
        try:
            node = db.query(ProjectInfoNode).filter(
                ProjectInfoNode.id == node_id
            ).first()
            return _node_to_dict(node, None, []) if node else None
        finally:
            db.close()

    def _require_node(self, db, node_id: str) -> ProjectInfoNode:
        node = db.query(ProjectInfoNode).filter(ProjectInfoNode.id == node_id).first()
        if not node:
            raise LookupError("节点不存在")
        return node

    def _assert_key_unique(self, db, project_id: Optional[str], node_key: str,
                           exclude_id: Optional[str] = None) -> None:
        """校验 node_key 在作用域内唯一（SKILL 第 6 节）。

        MySQL 里 UNIQUE(project_id, node_key) 管不到 project_id IS NULL 那一半
        （NULL 不参与唯一比较），全局作用域的唯一性只能落在这一层。
        """
        query = db.query(ProjectInfoNode).filter(ProjectInfoNode.node_key == node_key)
        if project_id is None:
            query = query.filter(ProjectInfoNode.project_id.is_(None))
        else:
            query = query.filter(ProjectInfoNode.project_id == project_id)
        if exclude_id:
            query = query.filter(ProjectInfoNode.id != exclude_id)
        if query.first():
            scope = "全局" if project_id is None else f"项目 {project_id} 内"
            raise ValueError(f"{scope}已存在同标识节点：{node_key}")

    def _depth_of(self, db, parent_id: Optional[str]) -> int:
        """某父节点所处层级（根为 1）；沿 parent_id 上溯，带 32 层防环。"""
        depth = 0
        current = parent_id
        for _ in range(32):
            if not current:
                return depth
            parent = db.query(ProjectInfoNode).filter(
                ProjectInfoNode.id == current
            ).first()
            if not parent:
                return depth
            depth += 1
            current = parent.parent_id
        raise ValueError("节点层级异常（可能成环），请检查父节点链")

    def _ancestors(self, db, node_id: Optional[str]) -> List[ProjectInfoNode]:
        """某节点自身及其全部祖先（近到远），带 32 层防环。"""
        chain: List[ProjectInfoNode] = []
        current = node_id
        for _ in range(32):
            if not current:
                return chain
            node = db.query(ProjectInfoNode).filter(
                ProjectInfoNode.id == current
            ).first()
            if not node:
                return chain
            chain.append(node)
            current = node.parent_id
        raise ValueError("节点层级异常（可能成环），请检查父节点链")

    # ── 值写入（任何登录用户都可以） ──────────────────────

    def set_value(self, project_id: str, node_id: str, value,
                  operator: Optional[str] = None,
                  operator_name: Optional[str] = None,
                  change_reason: Optional[str] = None) -> Dict:
        """写某项目某节点的值（值 + 历史同一事务）。

        允许的节点：全局节点（项目只写自己的值，不动定义）或本项目自己的增补节点。
        **别的项目的增补节点一律拒绝**——否则 A 项目能把值挂到 B 项目的私有字段上，
        在 SKILL 第 18 节的「历史隔离」验收里就是失败项。
        """
        db = SessionLocal()
        try:
            node = self._require_node(db, node_id)
            self._assert_writable_scope(db, project_id, node)

            row = db.query(ProjectInfoValue).filter(
                ProjectInfoValue.project_id == project_id,
                ProjectInfoValue.node_id == node_id,
            ).first()
            old_value = row.value_json if row else None
            decoded = _decode_value(node, value)

            now = _now_str()
            action = history_log.ACTION_CREATE

            # 值与现值相同不记流水（前端乐观更新失败重试会重复提交同样的值，避免刷屏）
            if row is None:
                db.add(ProjectInfoValue(
                    id=_new_id(), project_id=project_id, node_id=node_id,
                    value_json=decoded, created_at=now, updated_at=now,
                    updated_by=operator,
                ))
            else:
                if _is_same_value(old_value, decoded):
                    return _node_to_dict(node, old_value, [])
                action = (history_log.ACTION_DELETE if decoded in (None, '', [])
                          else history_log.ACTION_UPDATE)
                row.value_json = decoded
                row.updated_at = now
                row.updated_by = operator

            detail = history_log.build_value_detail(
                node.value_type, old_value, decoded, action,
            )
            if detail:
                history_log.add_history(
                    db, project_id=project_id,
                    operation_type=action,
                    detail=detail,
                    node_id=node.id, parent_id=node.parent_id,
                    node_key=node.node_key, node_name=node.node_name,
                    node_type=node.node_type,
                    old_value=old_value, new_value=decoded,
                    changed_by=operator, changed_by_name=operator_name,
                    change_reason=change_reason, changed_at=now,
                )

            db.commit()
            return _node_to_dict(node, decoded, [])
        finally:
            db.close()

    def _assert_writable_scope(self, db, project_id: str, node: ProjectInfoNode) -> None:
        """值只能写到「全局节点」或「本项目自己的增补节点」上。"""
        if node.project_id is None:
            return
        if node.project_id == project_id:
            return
        raise PermissionError("该节点属于其它项目，不能写入本项目")

    # ── 项目增补节点（普通用户的「增补信息」路径） ──────────

    def add_custom_node(self, project_id: str, parent_id: Optional[str],
                        node_name: str, value_type: str = 'text',
                        sort_order: Optional[int] = None,
                        node_key: Optional[str] = None,
                        operator: Optional[str] = None,
                        operator_name: Optional[str] = None) -> Dict:
        """在项目里增补一个自定义节点（可选带初始值）。

        与「改树」的区别：这里**只动本项目**（project_id=A），全局定义不受影响，
        别的项目也看不到。

        任何节点下都能增补（2026-09-18 用户要求「所有节点都默认可以增加」，
        allow_custom 不再是闸门），层层套下去直到 MAX_INFO_DEPTH 为止——
        第 4 层是唯一的边界。
        """
        name = (node_name or '').strip()
        if not name:
            raise ValueError("节点名称不能为空")
        if value_type not in PROJECT_INFO_VALUE_TYPES:
            raise ValueError(f"不支持的节点类型：{value_type}")

        db = SessionLocal()
        try:
            parent = None
            if parent_id:
                parent = self._require_node(db, parent_id)
                self._assert_writable_scope(db, project_id, parent)
                if self._depth_of(db, parent_id) + 1 > MAX_INFO_DEPTH:
                    raise ValueError(f"信息层级最多 {MAX_INFO_DEPTH} 层，该位置不能再往下加")

            key = (node_key or '').strip() or f'{CUSTOM_KEY_PREFIX}{uuid.uuid4().hex[:12]}'
            if not key.startswith(CUSTOM_KEY_PREFIX):
                key = f'{CUSTOM_KEY_PREFIX}{key}'
            self._assert_key_unique(db, project_id, key)

            if sort_order is None:
                sort_order = self._next_sort_order(db, project_id, parent_id)

            now = _now_str()
            node = ProjectInfoNode(
                id=_new_id(),
                project_id=project_id,
                parent_id=parent_id,
                node_key=key,
                node_name=name,
                node_type='field',
                value_type=value_type,
                sort_order=sort_order,
                required=False,
                # 一律 true：所有节点都可被增补（2026-09-18 起 allow_custom 不再是闸门，
                # 从此只是「此节点下允许增补」这个事实的记录）
                allow_custom=True,
                config=None,
                status=PROJECT_INFO_NODE_ACTIVE,
                created_by=operator,
                created_at=now,
                updated_by=operator,
                updated_at=now,
            )
            db.add(node)

            history_log.add_history(
                db, project_id=project_id,
                operation_type=history_log.ACTION_NODE_CREATE,
                detail=history_log.build_node_create_detail(
                    name, parent.node_name if parent else None,
                ),
                node_id=node.id, parent_id=parent_id,
                node_key=key, node_name=name, node_type='field',
                changed_by=operator, changed_by_name=operator_name, changed_at=now,
            )
            db.commit()
            db.refresh(node)
            return _node_to_dict(node, None, [])
        finally:
            db.close()

    def _next_sort_order(self, db, project_id: str, parent_id: Optional[str]) -> int:
        """同级末尾的排序值：取当前最大 + 10（与播种的 10/20/30 间隔一致）。"""
        from sqlalchemy import func
        query = db.query(func.max(ProjectInfoNode.sort_order)).filter(
            ProjectInfoNode.project_id.is_(None)
            | (ProjectInfoNode.project_id == project_id),
        )
        if parent_id is None:
            query = query.filter(ProjectInfoNode.parent_id.is_(None))
        else:
            query = query.filter(ProjectInfoNode.parent_id == parent_id)
        current = query.scalar()
        return (current or 0) + 10

    # ── 结构改动（管理员；全局节点由 info_template_service 负责） ──

    def update_node(self, node_id: str, update_data: Dict,
                    operator: Optional[str] = None,
                    operator_name: Optional[str] = None) -> Optional[Dict]:
        """改节点定义（名称 / 值类型 / 排序 / 是否允许增补）。

        只管**本项目增补的节点**；全局节点走 info_template_service（管理员改模板），
        这样「一次改动全体项目生效」与「只改我这一份」在代码层面就分得清清楚楚，
        不会出现普通用户顺手把全局字段改掉的情况。
        """
        db = SessionLocal()
        try:
            node = self._require_node(db, node_id)
            if node.project_id is None:
                raise PermissionError("全局字段定义请在「详情模板」里修改")
            if not update_data:
                return _node_to_dict(node, None, [])

            old_name = node.node_name
            old_type = node.value_type
            old_sort = node.sort_order

            if 'node_name' in update_data or 'title' in update_data:
                new_name = (update_data.get('node_name') or update_data.get('title') or '').strip()
                if not new_name:
                    raise ValueError("节点名称不能为空")
                node.node_name = new_name
            if 'value_type' in update_data or 'content_type' in update_data:
                new_type = update_data.get('value_type') or update_data.get('content_type')
                if new_type not in PROJECT_INFO_VALUE_TYPES:
                    raise ValueError(f"不支持的节点类型：{new_type}")
                if node.parent_id is None and new_type != 'text':
                    # 一级标签只作分组、自己不填值（前端也不给它渲染值编辑器），
                    # 与 info_template_service.normalize_template_nodes 的根节点规则一致
                    raise ValueError("一级标签只作分组，不能设置内容类型")
                node.value_type = new_type
            if 'sort_order' in update_data and update_data['sort_order'] is not None:
                node.sort_order = int(update_data['sort_order'])
            if 'required' in update_data and update_data['required'] is not None:
                node.required = bool(update_data['required'])
            if 'allow_custom' in update_data and update_data['allow_custom'] is not None:
                node.allow_custom = bool(update_data['allow_custom'])
            if 'node_type' in update_data and update_data['node_type'] in PROJECT_INFO_NODE_TYPES:
                node.node_type = update_data['node_type']
            # 下拉选项 / 标题备选项属于字段定义，落在 config 上（读取时由 _encode_value 拼回值里）
            if 'options' in update_data or 'titleOptions' in update_data:
                config = dict(node.config) if isinstance(node.config, dict) else {}
                if 'options' in update_data:
                    config['options'] = [str(item) for item in (update_data.get('options') or [])]
                if 'titleOptions' in update_data:
                    config['titleOptions'] = [str(item) for item in (update_data.get('titleOptions') or [])]
                node.config = config

            node.updated_at = _now_str()
            node.updated_by = operator

            detail = history_log.build_rename_detail(
                old_name, node.node_name,
                changed_type=(node.value_type != old_type),
                old_type=old_type, new_type=node.value_type,
            )
            if node.sort_order != old_sort:
                order_part = f"调整了同级顺序（{old_sort} → {node.sort_order}）"
                detail = f"{detail}；{order_part}" if detail else order_part
            if detail:
                history_log.add_history(
                    db, project_id=node.project_id,
                    operation_type=history_log.ACTION_NODE_RENAME,
                    detail=detail,
                    node_id=node.id, parent_id=node.parent_id,
                    node_key=node.node_key, node_name=node.node_name,
                    node_type=node.node_type,
                    changed_by=operator, changed_by_name=operator_name,
                )

            db.commit()
            db.refresh(node)
            return _node_to_dict(node, None, [])
        finally:
            db.close()

    def move_node(self, node_id: str, new_parent_id: Optional[str],
                  new_sort_order: int = 0,
                  operator: Optional[str] = None,
                  operator_name: Optional[str] = None) -> Optional[Dict]:
        """移动本项目增补节点（换父 + 排序）。全局节点不可移动。"""
        db = SessionLocal()
        try:
            node = self._require_node(db, node_id)
            if node.project_id is None:
                raise PermissionError("全局字段的位置由模板决定，不能在本页移动")

            if new_parent_id:
                new_parent = self._require_node(db, new_parent_id)
                self._assert_writable_scope(db, node.project_id, new_parent)
                # 不能把节点拖到自己的子树里（会把子树摘出整棵树）
                if new_parent_id in [n.id for n in self._ancestors(db, node_id)]:
                    raise ValueError("不能把节点移动到它自己或其子节点下")
                if self._depth_of(db, new_parent_id) + 1 > MAX_INFO_DEPTH:
                    raise ValueError(f"信息层级最多 {MAX_INFO_DEPTH} 层，该位置不能再往下加")

            old_parent_id = node.parent_id
            old_sort_order = node.sort_order
            node.parent_id = new_parent_id
            node.sort_order = new_sort_order
            node.updated_at = _now_str()
            node.updated_by = operator

            if new_parent_id != old_parent_id or new_sort_order != old_sort_order:
                def _name(parent_id: Optional[str]) -> Optional[str]:
                    if not parent_id:
                        return None
                    parent = db.query(ProjectInfoNode).filter(
                        ProjectInfoNode.id == parent_id
                    ).first()
                    return parent.node_name if parent else None

                detail = history_log.build_node_move_detail(
                    node.node_name, _name(old_parent_id), _name(new_parent_id),
                    same_parent=(new_parent_id == old_parent_id),
                )
                history_log.add_history(
                    db, project_id=node.project_id,
                    operation_type=history_log.ACTION_NODE_MOVE,
                    detail=detail,
                    node_id=node.id, parent_id=node.parent_id,
                    node_key=node.node_key, node_name=node.node_name,
                    node_type=node.node_type,
                    changed_by=operator, changed_by_name=operator_name,
                )

            db.commit()
            db.refresh(node)
            return _node_to_dict(node, None, [])
        finally:
            db.close()

    def delete_node(self, node_id: str, operator: Optional[str] = None,
                    operator_name: Optional[str] = None) -> bool:
        """删除本项目增补节点及其子树（含这些节点的值）。

        全局节点不可在此删除——它属于模板，停用请走 info_template_service。
        历史记录一条挂在被删节点的上级上，之后仍能在上级节点的「编辑历史」里看到。
        """
        db = SessionLocal()
        try:
            node = self._require_node(db, node_id)
            if node.project_id is None:
                raise PermissionError("全局字段定义请在「详情模板」里停用，不能在此删除")

            project_id = node.project_id
            ids = self._descendant_ids(db, project_id, node_id)
            if not ids:
                return False

            history_log.add_history(
                db, project_id=project_id,
                operation_type=history_log.ACTION_DELETE,
                detail=history_log.build_node_delete_detail(node.node_name, len(ids) - 1),
                node_id=node.id, parent_id=node.parent_id,
                node_key=node.node_key, node_name=node.node_name,
                node_type=node.node_type,
                changed_by=operator, changed_by_name=operator_name,
            )
            # 被删节点的「关注」随节点一起清掉（树里已无此节点，星标点不开）
            node_marks.remove_marks(db, ids)
            # 值随节点一起删（节点定义没了，值再留着也无处展示）
            db.query(ProjectInfoValue).filter(
                ProjectInfoValue.project_id == project_id,
                ProjectInfoValue.node_id.in_(ids),
            ).delete(synchronize_session=False)
            db.query(ProjectInfoNode).filter(
                ProjectInfoNode.id.in_(ids)
            ).delete(synchronize_session=False)
            db.commit()
            return True
        finally:
            db.close()

    def _descendant_ids(self, db, project_id: str, node_id: str) -> List[str]:
        """某增补节点及其全部后代 ID（含自身）。

        只在本项目的增补节点范围内递归——全局节点不参与，避免一次误删牵连模板。
        """
        sql = text("""
            WITH RECURSIVE subtree AS (
                SELECT id, parent_id, project_id FROM project_info_node WHERE id = :nid
                UNION ALL
                SELECT n.id, n.parent_id, n.project_id
                FROM project_info_node n
                INNER JOIN subtree s ON n.parent_id = s.id
                WHERE n.project_id = :pid
            )
            SELECT id FROM subtree
        """)
        rows = db.execute(sql, {"nid": node_id, "pid": project_id}).fetchall()
        return [r[0] for r in rows]

    # ── 批量导入 ────────────────────────────────────────

    def import_tree(self, project_id: str, nodes: List[Dict],
                    operator: Optional[str] = None,
                    operator_name: Optional[str] = None,
                    source: str = "import") -> int:
        """导入信息树为**增补节点**（只增不改不删，返回新增数量）。

        旧实现是「先清空该项目全部节点再写入」——在新结构下那等于把项目已填的
        项目信息全部抹掉。现在改成：导入的每个节点都建成该项目自己的增补节点，
        已存在的节点（按 node_key 对齐）原样保留，不动全局定义、不动已有值。
        """
        if not nodes:
            return 0

        db = SessionLocal()
        try:
            existing_keys = {
                key for (key,) in db.query(ProjectInfoNode.node_key).filter(
                    ProjectInfoNode.project_id == project_id,
                ).all()
            }
            now = _now_str()
            added = 0

            def walk(items: List[Dict], parent_id: Optional[str], depth: int):
                nonlocal added
                if depth > MAX_INFO_DEPTH:
                    raise ValueError(f"信息层级最多 {MAX_INFO_DEPTH} 层，导入的树过深")
                order = 0
                for item in items:
                    order += 1
                    name = (item.get("title") or item.get("node_name") or "未命名节点").strip()
                    raw_key = (item.get("node_key") or "").strip()
                    key = raw_key or f'{CUSTOM_KEY_PREFIX}{uuid.uuid4().hex[:12]}'
                    if not key.startswith(CUSTOM_KEY_PREFIX):
                        key = f'{CUSTOM_KEY_PREFIX}{key}'
                    # 已存在的同 key 节点不再重复导入（导入是增补，不是替换）
                    if key in existing_keys:
                        continue
                    # 导入源可能是旧契约的 content_type（text/select/file/image），
                    # 也可能是新契约的 value_type，两者都要认
                    value_type = (item.get("value_type") or "").strip()
                    if value_type not in PROJECT_INFO_VALUE_TYPES:
                        content_type = (item.get("content_type") or "").strip()
                        value_type = _LEGACY_CONTENT_TYPE.get(content_type, "text")

                    # 下拉节点的选项属于字段定义（节点 config），不跟值走；
                    # 导入源把 options 放在 value 里带来，这里剥出来落到节点上。
                    raw_value = item.get("value")
                    options = _options_in_value(raw_value)
                    config = {"options": options} if options else None
                    if options and isinstance(raw_value, dict):
                        raw_value = {"selected": raw_value.get("selected") or ""}

                    node_id = _new_id()
                    db.add(ProjectInfoNode(
                        id=node_id,
                        project_id=project_id,
                        parent_id=parent_id,
                        node_key=key,
                        node_name=name,
                        node_type="field",
                        value_type=value_type,
                        sort_order=item.get("sort_order", order * 10),
                        required=False,
                        # 与 add_custom_node 同一口径：新增的节点自己开放增补，
                        # 层数由上面的 depth 校验（MAX_INFO_DEPTH）兜底
                        allow_custom=True,
                        config=config,
                        status=PROJECT_INFO_NODE_ACTIVE,
                        created_by=operator,
                        created_at=now,
                        updated_by=operator,
                        updated_at=now,
                    ))
                    existing_keys.add(key)
                    added += 1
                    if raw_value not in (None, ''):
                        # 导入自带的初始值：写成该项目的值（与节点同一事务）。
                        # 走 _decode_value 归一：导入源给的是接口口径的
                        # {selected, options} / 对象，落库要还原成原生 JSON。
                        db.add(ProjectInfoValue(
                            id=_new_id(), project_id=project_id, node_id=node_id,
                            value_json=_decode_value(
                                SimpleNamespace(value_type=value_type), raw_value,
                            ),
                            created_at=now, updated_at=now, updated_by=operator,
                        ))
                    if item.get("children"):
                        walk(item["children"], node_id, depth + 1)

            walk(nodes, None, 1)
            if added:
                history_log.add_history(
                    db, project_id=project_id,
                    operation_type=history_log.ACTION_NODE_CREATE,
                    detail=history_log.build_import_detail(source, added),
                    changed_by=operator, changed_by_name=operator_name, changed_at=now,
                )
            db.commit()
            return added
        finally:
            db.close()

    # ── 项目信息文本：给 AI 摘要 / 文件识别用 ───────────────

    def flatten_tree(self, tree: List[Dict], parent_path: str = "") -> List[Dict]:
        """递归树 → 扁平列表，每项带 path（"父 / 子"），供文本化与匹配。"""
        flat: List[Dict] = []
        for node in tree or []:
            path = f"{parent_path} / {node.get('title')}" if parent_path else str(node.get("title") or "")
            flat.append({**node, "path": path})
            flat.extend(self.flatten_tree(node.get("children") or [], path))
        return flat


info_node_service = InfoNodeService()


def _is_same_value(old, new) -> bool:
    """值与现值是否等价（None / 空串 / 空数组视为同一回事）。"""
    def _norm(value):
        if value is None or value == '' or value == []:
            return None
        return value
    return _norm(old) == _norm(new)
