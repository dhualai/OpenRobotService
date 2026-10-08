"""ProjectInfoCapability — 按组读取项目当前信息。

只读 project_info_node + project_info_value 的当前行。
不读变更历史，不读关注标注。

做成能力而不是子 Agent：现场档案是一张当前快照，选哪一组、哪些值
不能进提示词，都在程序里做完再交给调度。子 Agent 会把整棵树交回模型，
口令和地址更容易漏进去。

调度没写明组别时，只返回已填组的目录。写明之后只返回那几组里
可以给模型看的字段。
"""

from __future__ import annotations

from typing import Iterable

from ai.core.logging import get_logger
from ai.agents.AiTaskPlatform.capabilities.core.base import BaseCapability, CapabilityResult

logger = get_logger("TASK_AGENT")

_MAX_CHARS = 2400
_MAX_VALUE = 180

# (组 key, 给模型看的组名, 调度目标/用户原话里的触发词)
_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("base", "项目基础", ("客户", "地点", "区域", "进厂", "项目类型", "省份")),
    ("hardware", "车型与载具", ("车型", "车辆", "载具", "几台", "多少台")),
    ("vehicle_software", "车端软件", ("车端", "控制器", "固件")),
    ("dispatch_software", "调度软件", ("调度版本", "调度软件", "usp版本", "usp 版本")),
    ("network", "网络", ("外网", "公网", "网络", "远程")),
    ("server_deploy", "服务器部署", ("服务器", "云主机", "云服务器")),
    ("environment", "现场环境", ("地图", "库位", "电梯", "自动门", "外设", "输送", "通道", "光电")),
    ("business_system", "业务系统", ("wms", "mes", "pda", "das", "对接", "孪生", "上层系统")),
    ("business_flow", "业务流程", ("搬运", "节拍", "分拣", "装卸", "物料")),
    ("personnel", "项目人员", ("实施", "项目经理", "负责人", "人员", "联系人")),
    ("project_feature", "风险与注意", ("风险", "注意事项", "注意点")),
    ("project_customization", "项目定制", ("定制", "大屏")),
)

_GROUP_LABEL = {key: label for key, label, _ in _GROUPS}

# 整棵子树只允许说「已填写」，值本身不进文本。
_PRESENCE_PREFIXES = (
    "network.remote",
    "network.public_ip",
)

_SECRET_KEY_PARTS = ("password", "passwd", "secret", "token", "credential")
_SECRET_NAME_WORDS = ("密码", "口令", "远程码", "验证码", "密钥", "激活码", "手机", "电话", "微信")


def _norm(text: str) -> str:
    return (text or "").strip().lower().replace(" ", "")


# 用户明确要整份现场档案时展开全部可给的组，不再停在目录。
_OVERVIEW_PHRASES = ("项目信息", "现场信息", "项目情况", "项目档案")


def select_groups(query: str) -> list[str]:
    """从调度目标或用户原话里认出要哪几组。

    点了具体组就只返回那些组。说「项目信息 / 现场信息」则返回全部可给的组。
    都没点到就返回空，调用方只给目录。
    """
    blob = _norm(query)
    if not blob:
        return []
    picked = []
    for key, label, words in _GROUPS:
        if _norm(label) in blob or _norm(key) in blob:
            picked.append(key)
            continue
        if any(_norm(w) in blob for w in words):
            picked.append(key)
    if picked:
        return picked
    if any(_norm(phrase) in blob for phrase in _OVERVIEW_PHRASES):
        return [key for key, _, _ in _GROUPS]
    return []


def _key_secret(key: str) -> bool:
    low = (key or "").lower()
    return any(part in low for part in _SECRET_KEY_PARTS)


def is_secret_node(node: dict) -> bool:
    """口令、远程码、联系方式：整段丢掉，连「已填写」都不说具体值。"""
    key = str(node.get("node_key") or "")
    name = str(node.get("node_name") or "")
    if _key_secret(key):
        return True
    if "license" in key.lower() and not key.lower().endswith("expire_at"):
        return True
    return any(word in name for word in _SECRET_NAME_WORDS)


def is_presence_only(node: dict) -> bool:
    """地址和远程入口：只说明配过，不把 IP、端口、远程码写出来。"""
    key = str(node.get("node_key") or "")
    return any(key == prefix or key.startswith(prefix + ".") for prefix in _PRESENCE_PREFIXES)


def _plain(value) -> str:
    if value is None or value == "" or value == [] or value == {}:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        return value.strip()[:_MAX_VALUE]
    if isinstance(value, list):
        parts = [_plain(item) for item in value[:8]]
        return "、".join(part for part in parts if part)[:_MAX_VALUE]
    if isinstance(value, dict):
        selected = value.get("selected")
        if selected is not None and str(selected).strip():
            return str(selected).strip()[:_MAX_VALUE]
        label = value.get("label") or value.get("name") or value.get("filename")
        if label and not any(k in value for k in ("password", "secret", "code")):
            return str(label).strip()[:_MAX_VALUE]
        bits = []
        for key, item in list(value.items())[:8]:
            if _key_secret(str(key)) or str(key).lower() in ("url", "path", "object_path"):
                continue
            shown = _plain(item)
            if shown:
                bits.append(f"{key}={shown}")
        return "；".join(bits)[:_MAX_VALUE]
    return ""


def _group_of(node: dict, by_id: dict) -> str:
    key = str(node.get("node_key") or "")
    if not key.startswith("custom."):
        return key.split(".", 1)[0]
    parent_id = node.get("parent_id")
    seen = set()
    while parent_id and parent_id not in seen:
        seen.add(parent_id)
        parent = by_id.get(parent_id)
        if not parent:
            break
        parent_key = str(parent.get("node_key") or "")
        if parent_key and not parent_key.startswith("custom."):
            return parent_key.split(".", 1)[0]
        parent_id = parent.get("parent_id")
    return "custom"


def _field_label(node: dict, by_id: dict) -> str:
    """字段名带上父节点，避免两个「数量」分不清是哪一款车。"""
    parts = []
    current = node
    guard = 0
    while current and guard < 4:
        name = str(current.get("node_name") or "").strip()
        if name:
            parts.append(name)
        parent = by_id.get(current.get("parent_id"))
        if not parent or str(parent.get("node_key") or "").count(".") == 0:
            break
        current = parent
        guard += 1
    parts.reverse()
    return " / ".join(parts)


def _filled_rows(nodes: Iterable[dict], values: Iterable[dict]) -> list[dict]:
    """启用节点 × 当前值。空值、停用节点不出现。"""
    by_id = {n["id"]: n for n in nodes if n.get("status", "active") == "active"}
    out = []
    for value in values:
        node = by_id.get(value.get("node_id"))
        if not node or node.get("node_type") == "group" or node.get("node_type") == "root":
            continue
        shown = _plain(value.get("value_json"))
        if not shown:
            continue
        out.append({
            "node": node,
            "group": _group_of(node, by_id),
            "label": _field_label(node, by_id),
            "shown": shown,
            "secret": is_secret_node(node),
            "presence": is_presence_only(node),
        })
    out.sort(key=lambda row: (row["group"], int(row["node"].get("sort_order") or 0), str(row["node"].get("node_key"))))
    return out


def render_project_info(nodes: Iterable[dict], values: Iterable[dict], query: str = "") -> str:
    """拼给模型的文本。敏感值不会出现在返回值里。"""
    rows = _filled_rows(nodes, values)
    groups = select_groups(query)
    catalog = _catalog(rows)
    if not groups:
        if not catalog:
            return "本项目还没有可引用的现场信息。"
        return (
            "未指明要哪一组，下面只列出已填写的组。需要详情时在目标里写明组名。\n"
            + catalog
        )
    lines = [_catalog(rows), ""]
    for key in groups:
        label = _GROUP_LABEL.get(key, key)
        body = _render_group(key, label, rows)
        if body:
            lines.append(body)
    text = "\n".join(line for line in lines if line is not None).strip()
    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS] + "\n（其余字段本次未展开）"
    return text or "这几组里没有可引用的现场信息。"


def catalog_line(nodes: Iterable[dict], values: Iterable[dict]) -> str:
    """讨论入口用的一行目录：只有组名和条数，没有字段值。"""
    return _catalog(_filled_rows(nodes, values))


def _catalog(rows: list[dict]) -> str:
    counts: dict[str, int] = {}
    presence_remote = False
    presence_ip = False
    for row in rows:
        key = str(row["node"].get("node_key") or "")
        if key.startswith("network.remote"):
            presence_remote = True
            continue
        if key.startswith("network.public_ip"):
            presence_ip = True
            continue
        if row["secret"]:
            continue
        if row["group"] not in _GROUP_LABEL:
            continue
        counts[row["group"]] = counts.get(row["group"], 0) + 1
    parts = [f"{_GROUP_LABEL[key]}({counts[key]})" for key in _GROUP_LABEL if key in counts]
    if presence_ip:
        parts.append("公网地址已填写")
    if presence_remote:
        parts.append("远程方式已配置")
    if not parts:
        return ""
    return "已填现场信息：" + "、".join(parts) + "。地址和口令不在此提供。"


def _render_group(key: str, label: str, rows: list[dict]) -> str:
    lines = [f"【{label}】"]
    remote_noted = False
    ip_noted = False
    kept = 0
    for row in rows:
        if row["group"] != key:
            continue
        node_key = str(row["node"].get("node_key") or "")
        if node_key.startswith("network.remote"):
            if not remote_noted:
                lines.append("- 远程方式已配置，地址和口令不在这里提供")
                remote_noted = True
            continue
        if row["secret"]:
            continue
        if node_key.startswith("network.public_ip"):
            if not ip_noted:
                lines.append("- 公网地址已填写，具体地址不在这里提供")
                ip_noted = True
            continue
        label = row.get("label") or row["node"].get("node_name")
        if str(row["node"].get("value_type") or "") == "attachment":
            lines.append(f"- {label}: 已上传附件")
        else:
            lines.append(f"- {label}: {row['shown']}")
        kept += 1
    if kept == 0 and not remote_noted and not ip_noted:
        return ""
    return "\n".join(lines)


def load_snapshot(project_id: str) -> tuple[list[dict], list[dict]]:
    """读当前节点和当前值。不查询历史表。"""
    from sqlalchemy import or_

    from ai.core.database import ProjectInfoNode, ProjectInfoValue, SessionLocal

    db = SessionLocal()
    try:
        node_rows = (
            db.query(ProjectInfoNode)
            .filter(
                ProjectInfoNode.status == "active",
                or_(
                    ProjectInfoNode.project_id.is_(None),
                    ProjectInfoNode.project_id == project_id,
                ),
            )
            .all()
        )
        value_rows = (
            db.query(ProjectInfoValue)
            .filter(ProjectInfoValue.project_id == project_id)
            .all()
        )
        nodes = [
            {
                "id": row.id,
                "parent_id": row.parent_id,
                "project_id": row.project_id,
                "node_key": row.node_key,
                "node_name": row.node_name,
                "node_type": row.node_type,
                "value_type": row.value_type,
                "sort_order": row.sort_order or 0,
                "status": row.status,
            }
            for row in node_rows
        ]
        values = [
            {"node_id": row.node_id, "value_json": row.value_json}
            for row in value_rows
        ]
        return nodes, values
    finally:
        db.close()


def safe_catalog(project_id: str) -> str:
    """讨论入口的目录行。读库失败时返回空，不阻断讨论。"""
    if not (project_id or "").strip():
        return ""
    try:
        nodes, values = load_snapshot(project_id.strip())
    except Exception as exc:
        logger.warning(f"[project_info] 目录读取失败: {type(exc).__name__}")
        return ""
    return catalog_line(nodes, values)


class ProjectInfoCapability(BaseCapability):
    """按组读取工单所属项目的当前现场信息。

    适用于问题依赖本项目配置：车型与数量、车端或调度版本、现场外设、
    对接的业务系统、搬运场景、项目人员、风险和注意事项。
    输入: query（写明要哪一组）。输出: 该组已填字段。
    口令、远程码、公网地址和 IP 不会出现在输出里。
    工单没有 project_id 时不要派发。
    """

    name = "project_info"
    description = (
        "读取本工单所属项目的当前现场信息（车型、软件版本、外设、业务系统、"
        "搬运场景、人员、风险与注意事项）。只返回调度目标里点名的那几组。\n"
        "目标里要写明组，例如「车型与载具」「调度软件」「现场环境」「业务系统」。\n"
        "没有点名时只返回已填组目录，不会把全部字段展开。\n"
        "不要用它查别的项目，不要用它查口令、远程码或服务器地址。"
    )
    tags = ["project", "项目信息", "现场"]
    max_usage_per_session = 3

    async def run(self, **kwargs) -> CapabilityResult:
        current = kwargs.get("current_task") if isinstance(kwargs.get("current_task"), dict) else {}
        project_id = str(kwargs.get("project_id") or current.get("project_id") or "").strip()
        if not project_id:
            return CapabilityResult.failure("工单未关联项目，无法读取现场信息")
        query = " ".join(
            part for part in (
                str(kwargs.get("query") or ""),
                str(kwargs.get("user_query") or ""),
            ) if part
        )
        try:
            nodes, values = load_snapshot(project_id)
        except Exception as exc:
            logger.warning(f"[project_info] 读取失败 project={project_id}: {type(exc).__name__}")
            return CapabilityResult.failure(f"项目信息读取失败: {type(exc).__name__}")
        text = render_project_info(nodes, values, query)
        groups = select_groups(query)
        logger.info(f"[project_info] project={project_id} groups={groups or ['catalog']}")
        return CapabilityResult(
            text=text,
            meta={"groups": groups, "catalog_only": not groups},
            ok=True,
        )
