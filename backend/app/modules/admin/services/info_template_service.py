"""项目信息树「详情模板」服务 —— 管理员维护全局字段定义（project_info_node 里 project_id 为 NULL 的那些行）。

**与旧实现的根本差别**：模板不再是存在 project_info_template 里的一份 JSON，
也不再需要「保存后同步到所有项目」。模板**就是**全局节点行本身，项目不再持有副本，
所以管理员改一次，全体项目立刻生效——`compute_sync_plan` 那套
「按标题路径回填锚点 → 逐项目算出增/改/删」的差异引擎连同 template_node_id 锚点
一起废弃：它存在的理由是「模板与项目各存一份、需要对齐」，新结构下不存在两份。

顺带消失的问题：
  - 旧同步会把模板的标题/类型/顺序强制覆盖到所有项目（`_carry_value` 那套「尽量不丢值」
    的兼容逻辑就是在给这个覆盖擦屁股）；现在节点只有一份，改它就是改它，没有「覆盖谁」。
  - 旧同步用 template_node_id 判断「节点是不是模板带来的」；现在 project_id 是否为空
    天然区分全局定义与项目增补，不会再误删用户自建节点。

模板节点 id 的来源：新增节点时按「标题路径」派生稳定 UUID
（`_template_node_id`，与 alembic 播种同一个 uuid5 命名空间），
所以同一份模板反复保存、或在另一套环境里重建，得到的 id 是一致的；
已有节点则沿用库里的 id（改名不会换 id，历史和关注因此不会断）。

**绝不触碰 project_info_value**：模板管的是字段定义，项目已填的数据不属于模板的职责范围。
停用（而非删除）一个字段时，它的值原样留在库里，重新启用即可恢复。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.models.delivery import (
    PROJECT_INFO_NODE_ACTIVE,
    PROJECT_INFO_NODE_DISABLED,
    PROJECT_INFO_VALUE_TYPES,
)
from app.modules.admin.models_das.models import Project, ProjectInfoNode
from app.modules.admin.services.info_node_service import SessionLocal

MAX_TEMPLATE_DEPTH = 4  # 与前端 PROJECT_INFO_MAX_DEPTH / 导入服务 MAX_NODE_DEPTH 一致
MAX_TITLE_LEN = 255

# 前端内容形式 → 值类型（接口层沿用旧口径，内部存细类型）
_CONTENT_TYPE_TO_VALUE_TYPE = {
    "text": "text",
    "select": "select",
    "file": "attachment",
    "image": "attachment",
}
# 值类型 → 前端内容形式（只用于把库里已有的细类型回吐给编辑页）
_VALUE_TYPE_TO_CONTENT_TYPE = {
    "text": "text",
    "number": "text",
    "boolean": "select",
    "date": "text",
    "select": "select",
    "multi_select": "text",
    "person": "text",
    "attachment": "file",
    "json": "text",
}


def _now_str() -> str:
    """与 delivery.py 一致的时间戳字符串。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _template_node_id(path: Tuple[str, ...]) -> str:
    """按标题路径派生稳定的模板节点 id。

    与 alembic 播种（7c1e9a4b2d38）用同一个命名空间与同一套路径拼接，
    因此「迁移播种的节点」与「管理员新建的节点」在 id 规则上是一致的。
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'ors://project-info-node/' + '/'.join(path)))


def _to_content_type(value_type: Optional[str]) -> str:
    return _VALUE_TYPE_TO_CONTENT_TYPE.get(value_type or "text", "text")


def _to_value_type(content_type: Optional[str]) -> str:
    if content_type in PROJECT_INFO_VALUE_TYPES:
        # 已经是内部细类型（编辑页回传库里读到的 value_type 时会走到这）
        return content_type
    return _CONTENT_TYPE_TO_VALUE_TYPE.get(content_type or "text", "text")


def _options_to_config(options: Any) -> Optional[Dict[str, Any]]:
    """选项清单 → config JSON：统一存 [{"value","label"}]。"""
    if not options:
        return None
    items = []
    for opt in options:
        if isinstance(opt, dict):
            value = opt.get("value", opt.get("label"))
            if value is None:
                continue
            items.append({"value": str(value), "label": str(opt.get("label", value))})
        else:
            text = str(opt).strip()
            if text:
                items.append({"value": text, "label": text})
    return {"options": items} if items else None


def normalize_template_nodes(nodes: Any, depth: int = 1) -> List[Dict]:
    """校验并规范化管理员提交的模板树（保存前调用）。

    规则：标题非空、整树最深 MAX_TEMPLATE_DEPTH 层、（另由 _assert_keys_unique 保证）
    同级节点标识不重复。返回规范化后的新树。不合法直接抛 ValueError（接口层转 400）。

    **值类型与子节点互不排斥**：一个节点可以既有自己的值（如下拉选中的车型）又有子节点
    （如该车型的数量）。原先「select/attachment 必须是末级」的限制已取消，只有根节点例外
    ——根节点是一级标签、只作分组用，值类型锁死 text。
    """
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("模板不能为空，至少保留一个节点")
    if depth > MAX_TEMPLATE_DEPTH:
        raise ValueError(f"模板最多 {MAX_TEMPLATE_DEPTH} 层，节点层级过深")

    normalized: List[Dict] = []
    for index, raw in enumerate(nodes):
        if not isinstance(raw, dict):
            raise ValueError("模板节点格式不正确")
        # title 优先：get_template 会把 node_name 一并下发，前端编辑页只改 title，
        # 若这里先读 node_name，读回来再存回去就会把旧名字写回，改名永远不生效。
        title = str(raw.get("title") or raw.get("node_name") or "").strip()
        if not title:
            raise ValueError(f"第 {depth} 层第 {index + 1} 个节点标题不能为空")

        content_type = raw.get("content_type")
        value_type = _to_value_type(content_type or raw.get("value_type"))
        children = raw.get("children") or []
        if depth == 1:
            # 根节点=一级标签，只作分组：它自己不带值，类型锁死 text（传了别的也归一）
            value_type = "text"

        node: Dict[str, Any] = {
            "id": raw.get("id") or None,
            "title": title[:MAX_TITLE_LEN],
            "value_type": value_type,
            "content_type": _to_content_type(value_type),
            "sort_order": (index + 1) * 10,
            "required": bool(raw.get("required")),
            # 所有节点都允许各项目在其下增补信息（2026-09-18 起取消「详情模板」页的开关），
            # 提交里带什么都归一到 true —— 闸门没了，增补的唯一边界是层数（4 层）
            "allow_custom": True,
            "children": normalize_template_nodes(children, depth + 1) if children else [],
        }
        if value_type == "select":
            node["options"] = _flatten_options(raw.get("options"))
        elif raw.get("options"):
            # 非下拉节点也可能带 options（历史数据/预留用法），保留透传避免丢数据
            node["options"] = _flatten_options(raw.get("options"))
        normalized.append(node)
    return normalized


def _flatten_options(options: Any) -> List[str]:
    result: List[str] = []
    for opt in options or []:
        if isinstance(opt, dict):
            picked = opt.get("value", opt.get("label"))
            if picked is not None:
                result.append(str(picked))
        else:
            text = str(opt).strip()
            if text:
                result.append(text)
    return result


def template_to_legacy_tree(nodes: List[Dict]) -> List[Dict]:
    """规范化模板树 → 编辑页用的读取结构（补上 id options 等展示字段）。"""
    out: List[Dict] = []
    for node in nodes:
        item = dict(node)
        item["options"] = node.get("options") or []
        item["children"] = template_to_legacy_tree(node.get("children") or [])
        out.append(item)
    return out


class InfoTemplateService:
    """全局字段定义（project_info_node 中 project_id IS NULL 的部分）的读取 / 保存。"""

    # ── 读取 ─────────────────────────────────────────

    def _load_global_nodes(self, db, include_disabled: bool = False) -> List[ProjectInfoNode]:
        query = db.query(ProjectInfoNode).filter(ProjectInfoNode.project_id.is_(None))
        if not include_disabled:
            query = query.filter(ProjectInfoNode.status == PROJECT_INFO_NODE_ACTIVE)
        return query.order_by(ProjectInfoNode.sort_order).all()

    def get_template(self) -> Dict[str, Any]:
        """模板完整信息（树 + 受影响项目数），供编辑页与保存前预览使用。

        `source` 恒为 'db'：模板的权威来源是数据库里的全局节点行
        （首次由 alembic 迁移从 default.yaml 播种），不再有 YAML 兜底分支。
        """
        db = SessionLocal()
        try:
            rows = self._load_global_nodes(db)
            tree = self._rows_to_tree(rows)
            project_count = db.query(Project).filter(Project.status != "deleted").count()
            updated_at = None
            updated_by = None
            for row in rows:
                if row.updated_at and (updated_at is None or row.updated_at > updated_at):
                    updated_at = row.updated_at
                    updated_by = row.updated_by
            return {
                "id": "default",
                "name": "项目详情模板",
                "nodes": template_to_legacy_tree(tree),
                "updated_at": updated_at,
                "updated_by": updated_by,
                "project_count": project_count,
                "source": "db",
            }
        finally:
            db.close()

    def get_template_nodes(self) -> List[Dict]:
        """模板节点树（无则空列表）。"""
        return self.get_template()["nodes"]

    def _rows_to_tree(self, rows: List[ProjectInfoNode]) -> List[Dict]:
        children_map: Dict[Optional[str], List[ProjectInfoNode]] = {}
        for row in rows:
            children_map.setdefault(row.parent_id, []).append(row)

        def build(parent_id: Optional[str]) -> List[Dict]:
            result = []
            for row in sorted(children_map.get(parent_id, []), key=lambda r: r.sort_order):
                options: List[str] = []
                config = row.config or {}
                if isinstance(config, dict):
                    for opt in config.get("options") or []:
                        if isinstance(opt, dict):
                            picked = opt.get("value", opt.get("label"))
                            if picked is not None:
                                options.append(str(picked))
                        elif opt is not None:
                            options.append(str(opt))
                result.append({
                    "id": row.id,
                    "title": row.node_name,
                    "node_name": row.node_name,
                    "node_key": row.node_key,
                    "value_type": row.value_type,
                    "content_type": _to_content_type(row.value_type),
                    "sort_order": row.sort_order,
                    "required": bool(row.required),
                    "allow_custom": bool(row.allow_custom),
                    "options": options,
                    "children": build(row.id),
                })
            return result

        return build(None)

    # ── 保存 ─────────────────────────────────────────

    def save_template(self, nodes: Any, username: str = "",
                      operator_name: str = "") -> Dict[str, Any]:
        """保存模板 = 把全局节点行改成提交的这棵树（不再有「同步到项目」这一步）。

        三件事在同一事务里完成：
          1. 提交树里已有的节点 → 改名 / 改类型 / 改排序
             （allow_custom 一律写 true：所有节点都可被各项目增补，2026-09-18 起无开关）；
          2. 提交树里没有的全局节点 → status='disabled'（软停用，不动 project_info_value，
             字段重新加回来时项目已填的值还在）；
          3. 提交树里的新节点 → 按标题路径派生稳定 id 插入。
        停用的节点若在本次又被加回来，会被重新置为 active（走第 1 步的恢复逻辑）。
        """
        normalized = normalize_template_nodes(nodes)
        now = _now_str()

        db = SessionLocal()
        try:
            existing = {row.id: row for row in self._load_global_nodes(db, include_disabled=True)}
            actions = self._collect_actions(normalized, existing, (), None)
            self._assert_keys_unique(normalized)

            kept_ids = set()
            for item in actions:
                kept_ids.add(item["id"])

            for item in actions:
                row = existing.get(item["id"])
                if row is None:
                    db.add(ProjectInfoNode(
                        id=item["id"],
                        project_id=None,
                        parent_id=item["parent_id"],
                        node_key=item["node_key"],
                        node_name=item["node_name"],
                        node_type=item["node_type"],
                        value_type=item["value_type"],
                        sort_order=item["sort_order"],
                        required=item["required"],
                        allow_custom=item["allow_custom"],
                        config=item["config"],
                        status=PROJECT_INFO_NODE_ACTIVE,
                        created_by=username or "admin",
                        created_at=now,
                        updated_by=username or "admin",
                        updated_at=now,
                    ))
                    continue
                row.parent_id = item["parent_id"]
                row.node_name = item["node_name"]
                row.node_type = item["node_type"]
                row.value_type = item["value_type"]
                row.sort_order = item["sort_order"]
                row.required = item["required"]
                row.allow_custom = item["allow_custom"]
                row.config = item["config"]
                row.status = PROJECT_INFO_NODE_ACTIVE
                row.updated_by = username or "admin"
                row.updated_at = now

            # 模板里已移除的全局节点：停用而非删除（SKILL 第 5.8 节）。
            # 值留在 project_info_value，字段重新加回来即刻恢复。
            disabled = 0
            for node_id, row in existing.items():
                if node_id in kept_ids or row.status == PROJECT_INFO_NODE_DISABLED:
                    continue
                row.status = PROJECT_INFO_NODE_DISABLED
                row.updated_by = username or "admin"
                row.updated_at = now
                disabled += 1

            db.commit()
            return {
                "id": "default",
                "name": "项目详情模板",
                "updated_at": now,
                "updated_by": username or "admin",
                "disabled": disabled,
            }
        finally:
            db.close()

    def _collect_actions(self, nodes: List[Dict], existing: Dict[str, ProjectInfoNode],
                         parent_path: Tuple[str, ...], parent_id: Optional[str]) -> List[Dict]:
        """规范化模板树 → 待落库的节点清单（父节点在子节点之前）。

        已存在的节点沿用库里的 id（改名不换身份，历史与关注不断线）；
        新节点按标题路径派生稳定 id。
        """
        actions: List[Dict] = []
        for node in nodes:
            path = parent_path + (node["title"],)
            node_id = node.get("id")
            if not node_id or node_id not in existing:
                node_id = _template_node_id(path)
            if node_id in existing:
                node_key = existing[node_id].node_key
            else:
                node_key = self._key_for(path, node_id)

            children = node.get("children") or []
            # node_type 与迁移播种同一套规则：最顶层是 root（哪怕它有子节点），
            # 非顶层且有子节点才是 group。两处不一致会让同一棵树在
            # 「迁移播种的节点」和「管理员保存过的节点」之间出现类型漂移。
            # 注意 node_type 只看**结构**：既带值又带子节点的节点（下拉+数量）仍是 group，
            # 值的那一面由 value_type 表达，两者互不影响。
            if parent_id is None:
                node_type = "root"
            elif children:
                node_type = "group"
            else:
                node_type = "field"
            actions.append({
                "id": node_id,
                "parent_id": parent_id,
                "node_key": node_key,
                "node_name": node["title"],
                "node_type": node_type,
                "value_type": node["value_type"],
                "sort_order": node["sort_order"],
                "required": node["required"],
                "allow_custom": node["allow_custom"],
                "config": _options_to_config(node.get("options")),
            })
            if children:
                actions.extend(self._collect_actions(children, existing, path, node_id))
        return actions

    def _key_for(self, path: Tuple[str, ...], node_id: str) -> str:
        """新全局节点的 node_key。

        取路径的英文/数字段拼成点分标识；一个都没有（纯中文标题）时退回 id 派生，
        保证仍然稳定、唯一、可读性尚可。**已存在的节点绝不重算 key**——
        key 是外部（导入、脚本、其它系统）对齐节点的锚，改名换 key 会让锚失效。
        """
        parts: List[str] = []
        for segment in path:
            ascii_part = "".join(
                ch if (ch.isascii() and (ch.isalnum() or ch in "._-")) else "_"
                for ch in segment
            ).strip("_").lower()
            if ascii_part and ascii_part.strip("_") and len(ascii_part) <= 40:
                parts.append(ascii_part)
        if len(parts) == len(path) and parts:
            return ".".join(parts)[:191]
        # 标题含中文等非 ASCII 字符：用路径派生的短 id 作 key，稳定且不与他节点冲突
        return "tpl." + node_id.replace("-", "")[:24]

    def _assert_keys_unique(self, nodes: List[Dict]) -> None:
        """同一父节点下的标题不允许重复（避免编辑页里两个同名节点分不清）。

        查重以「同一个父节点的直接子节点」为单位：不同分支下同名是合法的
        （硬件/厂家 与 网络/厂家 互不干扰），所以 seen 每层各建一份，
        不能跨分支共用——否则改一个字段名就可能误报「同名」挡住保存。
        """
        seen = set()
        for item in nodes:
            title = item["title"]
            if title in seen:
                raise ValueError(f"同一层存在同名节点「{title}」，请改名后再保存")
            seen.add(title)
            self._assert_keys_unique(item.get("children") or [])

    # ── 兼容旧接口：新结构下「同步」是空操作 ──────────────

    def preview_sync(self, nodes: Any) -> Dict[str, Any]:
        """保存前的预览。

        新结构下保存即是全局生效（项目不再持有副本），所以预览要说明的是
        「会停用哪些字段」而不是「会去改几个项目」。
        """
        normalized = normalize_template_nodes(nodes)
        kept = {item["id"] for item in self._collect_actions(normalized, {}, (), None)}
        db = SessionLocal()
        try:
            existing = {row.id: row for row in self._load_global_nodes(db, include_disabled=True)}
            project_count = db.query(Project).filter(Project.status != "deleted").count()
        finally:
            db.close()

        will_disable = [
            row.node_name for node_id, row in existing.items()
            if node_id not in kept and row.status == PROJECT_INFO_NODE_ACTIVE
        ]
        return {
            "dry_run": True,
            "projects": project_count,       # 影响面覆盖全部项目（字段是全局的）
            "changed_projects": 0,           # 不再有“逐项目改动”这回事
            "added": len([i for i in kept if i not in existing]),
            "updated": len([i for i in kept if i in existing]),
            "deleted": len(will_disable),    # 实为「停用」，沿用旧字段名保持接口兼容
            "details": [],
            "disabled_titles": will_disable[:20],
        }

    def save_and_sync(self, nodes: Any, username: str = "",
                      operator_name: str = "") -> Dict[str, Any]:
        """保存模板（旧名字沿用）。新结构下没有独立的同步步骤，保存即生效。"""
        info = self.save_template(nodes, username=username, operator_name=operator_name)
        return {
            "dry_run": False,
            **info,
            "projects": 0,
            "changed_projects": 0,
            "added": 0,
            "updated": 0,
            "deleted": info.get("disabled", 0),
            "failed_projects": [],
            "details": [],
        }


info_template_service = InfoTemplateService()
