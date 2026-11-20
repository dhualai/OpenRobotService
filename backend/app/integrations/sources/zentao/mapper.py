"""禅道 task → ExternalTask 字段映射（INTEGRATION_DESIGN.md §6）。

禅道特有知识集中于此：状态 / 优先级 / 类型枚举映射、对象字段
（assignedTo / openedBy）解析。引擎与核心不感知禅道枚举。

映射要点：
- status: wait→new, doing→in_progress, pause→pending, done→resolved,
          cancel/closed→closed
- pri(1 最高 - 4 最低): 1→urgent, 2→high, 3→medium, 4→low
- type(devel/test/...): devel→feature, test→support, 其余→other
- assigned_account / created_account 保留禅道 account 原值，
  由 SyncEngine 查 task_user_mapping 解析为本平台 user_id。
- 工时与层级信息放入 extra，最终落到 Task.metadata_info。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from app.integrations.base import ExternalTask
from app.models.task import TaskPriority, TaskStatus, TaskType

# 禅道 status -> 本平台 TaskStatus
ZENTAO_STATUS_MAP: Dict[str, TaskStatus] = {
    "wait": TaskStatus.NEW,
    "doing": TaskStatus.IN_PROGRESS,
    "pause": TaskStatus.PENDING,
    "done": TaskStatus.RESOLVED,
    "cancel": TaskStatus.CANCELED,
    "closed": TaskStatus.CLOSED,
}

# 禅道 pri(1-4, 1 最高) -> 本平台 TaskPriority
ZENTAO_PRIORITY_MAP: Dict[int, TaskPriority] = {
    1: TaskPriority.URGENT,
    2: TaskPriority.HIGH,
    3: TaskPriority.MEDIUM,
    4: TaskPriority.LOW,
}

# 禅道 type -> 本平台 TaskType（语义不完全对应，研发任务为主）
ZENTAO_TYPE_MAP: Dict[str, TaskType] = {
    "devel": TaskType.FEATURE,
    "test": TaskType.SUPPORT,
    "design": TaskType.OTHER,
    "research": TaskType.OTHER,
    "study": TaskType.OTHER,
    "discuss": TaskType.OTHER,
    "misc": TaskType.OTHER,
}


def _flatten_user(field: Any) -> Dict[str, Any]:
    """assignedTo / openedBy 在返回里可能是对象 {account, realname}，也可能是字符串 / None。"""
    if isinstance(field, dict):
        return field
    if isinstance(field, str) and field:
        return {"account": field, "realname": field}
    return {}


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    # 禅道日期：openedDate 为 ISO 8601（"2026-07-14T06:13:37Z"），
    # deadline 可能仅日期（"2026-07-18"）。
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def map_priority(pri: Any) -> TaskPriority:
    try:
        return ZENTAO_PRIORITY_MAP.get(int(pri), TaskPriority.MEDIUM)
    except (TypeError, ValueError):
        return TaskPriority.MEDIUM


def map_status(status: Any) -> TaskStatus:
    if not status:
        return TaskStatus.NEW
    return ZENTAO_STATUS_MAP.get(str(status), TaskStatus.NEW)


def map_task_type(t: Any) -> TaskType:
    if not t:
        return TaskType.OTHER
    return ZENTAO_TYPE_MAP.get(str(t), TaskType.OTHER)


def build_external_url(base_url: str, task_id: Any) -> str:
    base = (base_url or "").strip().rstrip("/")
    return f"{base}/task-view-{task_id}.html" if base else ""


def build_story_url(base_url: str, story_id: Any) -> str:
    base = (base_url or "").strip().rstrip("/")
    return f"{base}/story-view-{story_id}.html" if base else ""


def zentao_task_to_external(t: Dict[str, Any], *, base_url: str = "") -> ExternalTask:
    """把一条禅道 task 翻译为 ExternalTask。"""
    assigned = _flatten_user(t.get("assignedTo"))
    opened = _flatten_user(t.get("openedBy"))

    extra: Dict[str, Any] = {}
    extra["zentao_entity_type"] = "task"
    for k in ("estimate", "consumed", "left"):
        if t.get(k) is not None:
            extra[k] = t.get(k)
    exec_id = t.get("executi1n") or t.get("execution") or t.get("parent")
    if exec_id:
        extra["execution_id"] = exec_id
    if t.get("project"):
        extra["zentao_project_id"] = t.get("project")
    if t.get("pri") is not None:
        extra["zentao_pri"] = t.get("pri")
    if t.get("type"):
        extra["zentao_type"] = t.get("type")
    if opened.get("realname"):
        extra["opened_realname"] = opened.get("realname")
    if assigned.get("realname"):
        extra["assigned_realname"] = assigned.get("realname")

    task_id = t.get("id")
    name = t.get("name") or ""
    desc = t.get("desc") or ""

    return ExternalTask(
        external_id=str(task_id),
        title=name,
        description=desc if desc else name,
        status=map_status(t.get("status")),
        priority=map_priority(t.get("pri")),
        task_type=map_task_type(t.get("type")),
        assigned_account=assigned.get("account"),
        created_account=opened.get("account"),
        created_at=_parse_datetime(t.get("openedDate")),
        deadline_at=_parse_datetime(t.get("deadline")),
        url=build_external_url(base_url, task_id),
        extra=extra,
    )


ZENTAO_STORY_STATUS_MAP: Dict[str, TaskStatus] = {
    "draft": TaskStatus.NEW,
    "active": TaskStatus.IN_PROGRESS,
    "reviewed": TaskStatus.IN_PROGRESS,
    "planned": TaskStatus.IN_PROGRESS,
    "developed": TaskStatus.IN_PROGRESS,
    "tested": TaskStatus.RESOLVED,
    "released": TaskStatus.RESOLVED,
    "closed": TaskStatus.CLOSED,
    "changed": TaskStatus.IN_PROGRESS,
}


def map_story_status(status: Any) -> TaskStatus:
    if not status:
        return TaskStatus.NEW
    return ZENTAO_STORY_STATUS_MAP.get(str(status), TaskStatus.NEW)


def zentao_story_to_external(s: Dict[str, Any], *, base_url: str = "") -> ExternalTask:
    """把一条禅道 story 翻译为 ExternalTask。"""
    assigned = _flatten_user(s.get("assignedTo"))
    opened = _flatten_user(s.get("openedBy"))

    extra: Dict[str, Any] = {}
    extra["zentao_entity_type"] = "story"
    if s.get("execution"):
        extra["execution_id"] = s.get("execution")
    if s.get("project"):
        extra["zentao_project_id"] = s.get("project")
    if s.get("product"):
        extra["zentao_product_id"] = s.get("product")
    if s.get("branch"):
        extra["zentao_branch_id"] = s.get("branch")
    if s.get("pri") is not None:
        extra["zentao_pri"] = s.get("pri")
    if s.get("category"):
        extra["zentao_category"] = s.get("category")
    if s.get("stage"):
        extra["zentao_stage"] = s.get("stage")
    if s.get("plan"):
        extra["zentao_plan"] = s.get("plan")
    if s.get("source"):
        extra["zentao_source"] = s.get("source")
    if opened.get("realname"):
        extra["opened_realname"] = opened.get("realname")
    if assigned.get("realname"):
        extra["assigned_realname"] = assigned.get("realname")

    story_id = s.get("id")
    title = s.get("title") or ""

    return ExternalTask(
        external_id=f"story-{story_id}",
        title=title,
        description=title,
        status=map_story_status(s.get("status")),
        priority=map_priority(s.get("pri")),
        task_type=TaskType.FEATURE,
        assigned_account=assigned.get("account"),
        created_account=opened.get("account"),
        created_at=_parse_datetime(s.get("openedDate")),
        deadline_at=_parse_datetime(s.get("deadline")),
        url=build_story_url(base_url, story_id),
        extra=extra,
    )
