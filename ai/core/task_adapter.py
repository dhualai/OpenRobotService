"""AI ticket ↔ backend Task 适配层

把 AI 诊断 Agent 生成的 ticket dict 与 backend 的 tasks 表（app.models.task.Task）
双向映射。AI 专属字段（diagnosis / fault_code / robot_type 等）平铺进 metadata_info；
AI 来源用 source="ai" + external_id=session_id 幂等标识（借 tasks 表的
(source, external_id) 唯一约束）。

背景：backend 已完成 ticket→task 统一（MIGRATION.md Wave 2.2），AI 侧原本直连
legacy tickets 表，本模块让 AI 改为读写 tasks 表，与 backend 工单体系对齐。

实现纪律：
1. SQLAlchemy JSON 列不感知就地修改 → metadata_info 一律整体替换 dict。
2. (source, external_id) 唯一约束 → 同 session 重复 submit 走 upsert。
"""
import hashlib

from app.core.db import SessionLocal
from app.models.task import Task, TaskStatus, TaskPriority, TaskType, TaskOperationLog, OperationType
from app.core.database import db_manager


def _dedup_attachments(items: list) -> list:
    """按 object_path + filename 去重，保留首次出现顺序。
    防止 agent_state.attachments 累加时引入重复条目透传到 tasks.attachments。"""
    seen = set()
    result = []
    for it in items:
        if not isinstance(it, dict):
            continue
        key = (it.get("object_path"), it.get("filename"))
        if key in seen:
            continue
        seen.add(key)
        result.append(it)
    return result


def _resolve_operator_name(operator: str) -> str:
    """通过 username 解析显示名（查用户表兜底）。"""
    try:
        user = db_manager.get_user(operator)
        if user and user.get("name"):
            return user["name"]
    except Exception:
        pass
    return operator


AI_SOURCE = "ai"

# 优先级：中文 / 英文 → 枚举
_PRIORITY_TO_ENUM = {
    "紧急": TaskPriority.URGENT, "urgent": TaskPriority.URGENT,
    "高": TaskPriority.HIGH, "high": TaskPriority.HIGH,
    "中": TaskPriority.MEDIUM, "medium": TaskPriority.MEDIUM,
    "低": TaskPriority.LOW, "low": TaskPriority.LOW,
}

# 枚举 → 中文（反向映射，保持前端零改动）
_ENUM_TO_PRIORITY_CN = {
    TaskPriority.URGENT: "紧急",
    TaskPriority.HIGH: "高",
    TaskPriority.MEDIUM: "中",
    TaskPriority.LOW: "低",
}

# 类型：字符串 → 枚举（AI 的 type 值与 TaskType value 恰好 1:1）
_TYPE_TO_ENUM = {
    "problem": TaskType.PROBLEM,
    "bug": TaskType.BUG,
    "feature": TaskType.FEATURE,
    "support": TaskType.SUPPORT,
    "other": TaskType.OTHER,
}

# ticket dict → metadata_info 平铺的类型专属字段映射
_TICKET_META_FIELD_MAP = {
    "location": "location", "robot_type": "robot_type",
    "fault_code": "fault_code", "special_notes": "special_notes",
    "occurrence_time": "occurrence_time", "frequency": "frequency",
    "steps_to_reproduce": "steps_to_reproduce",
    "expected_result": "expected_result", "actual_result": "actual_result",
    "severity": "severity", "version": "version",
    "scenario": "scenario", "expected_effect": "expected_effect",
    "support_type": "support_type", "preferred_response": "preferred_response",
}


def _external_id_for(session_id: str) -> str:
    """session_id → external_id（≤64 字符）。

    ≤64 直存（便于人工排查）；超长走 sha1 截断 + h_ 前缀避免与直存冲突。
    完整 session_id 永远在 metadata_info.session_id，读取端不从 external_id 反推。
    """
    if not session_id:
        return ""
    if len(session_id) <= 64:
        return session_id
    return "h_" + hashlib.sha1(session_id.encode("utf-8")).hexdigest()[:37]


def _priority_to_enum(value):
    return _PRIORITY_TO_ENUM.get((value or "中").strip(), TaskPriority.MEDIUM)


def _type_to_enum(value):
    return _TYPE_TO_ENUM.get((value or "other").strip(), TaskType.OTHER)


def ticket_dict_to_task_fields(ticket: dict, created_by: str = "") -> dict:
    """ticket dict → 可 Task(**fields) 的字段字典（不含 id / 时间戳，交给 server_default）。"""
    meta = {
        "session_id": ticket.get("session_id", ""),
        "ticket_ai_id": ticket.get("ticket_id", ""),
        "contact": ticket.get("contact", ""),
        "diagnosis": ticket.get("diagnosis") or {},
        "created_at_unix": ticket.get("created_at"),
    }
    # 类型专属字段平铺（仅写入非空值）
    for tk, mk in _TICKET_META_FIELD_MAP.items():
        if ticket.get(tk):
            meta[mk] = ticket[tk]
    # feature 类型的 source 存为 feature_source，避开 Task.source 列名
    if ticket.get("type") == "feature" and ticket.get("source"):
        meta["feature_source"] = ticket["source"]

    # 截止时间：弹窗编辑值（ISO 字符串）→ Task.deadline_at（DateTime 列）。
    # 前端 toISOString() 带时区后缀（如 2026-08-13T10:00:00.000Z），
    # MySQL DateTime 需无时区 naive datetime，统一转上海时间再剥时区。
    deadline = None
    _dl_raw = ticket.get("deadline_at")
    if _dl_raw:
        try:
            from datetime import datetime as _dt, timezone as _tz, timedelta as _td
            _dl = _dt.fromisoformat(str(_dl_raw).replace("Z", "+00:00"))
            if _dl.tzinfo is not None:
                _dl = _dl.astimezone(_tz(_td(hours=8))).replace(tzinfo=None)
            deadline = _dl
        except Exception:
            deadline = None

    ext_id = _external_id_for(ticket.get("session_id", ""))
    # 同一会话多次转单时，ticket_seq 确保 external_id 唯一
    if ticket.get("ticket_seq"):
        ext_id = f"{ext_id}#{ticket['ticket_seq']}"
    return {
        "title": ticket.get("title", "") or "",
        "description": ticket.get("description", "") or "",
        "task_type": _type_to_enum(ticket.get("type")),
        "priority": _priority_to_enum(ticket.get("priority")),
        "status": TaskStatus.NEW,
        "created_by": created_by or "",
        "source": AI_SOURCE,
        "project_name": ticket.get("project", "") or "",
    "project_id": ticket.get("project_id", "") or "",
        "external_id": ext_id,
        "external_url": None,
        "attachments": _dedup_attachments(ticket.get("attachments") or []),
        "tags": ["ai_generated"],
        "metadata_info": meta,
        "deadline_at": deadline,
    }


def task_to_dict(task: Task) -> dict:
    """Task → 兼容老 tickets 表字段名的字典（priority 反向映射回中文）。"""
    meta = task.metadata_info or {}
    # 发起人/处理人 + 显示名（与列表接口 /memory/tickets/all 口径一致）；
    # user_map 查询失败时降级为空，不影响主流程。
    created_by = task.created_by or ""
    assigned_to = task.assigned_to or ""
    created_by_name = created_by
    assigned_to_name = assigned_to
    try:
        from app.services.user_service import UserService
        user_map = UserService.get_user_map()
        created_by_name = user_map.get(created_by, created_by) if created_by else ""
        assigned_to_name = user_map.get(assigned_to, assigned_to) if assigned_to else ""
    except Exception:
        pass
    return {
        "id": task.id,
        # 数字 Task.id 即任务服务（/api/tasks）的工单号，前端催办/上报/评论/撤回都依赖它。
        # 此前缺失该字段，导致从 MySQL 降级的工单进入详情页时 ticket_id 为空、按钮报「工单号缺失」。
        "ticket_id": task.id,
        "session_id": meta.get("session_id", ""),
        "ticket_ai_id": meta.get("ticket_ai_id", ""),
        "title": task.title or "",
        "description": task.description or "",
        "type": task.task_type.value if task.task_type else "other",
        "priority": _ENUM_TO_PRIORITY_CN.get(task.priority, "中"),
        "status": task.status.value if task.status else "pending",
        "contact": meta.get("contact", ""),
        "location": meta.get("location", ""),
        "robot_type": meta.get("robot_type", ""),
        "fault_code": meta.get("fault_code", ""),
        "occurrence_time": meta.get("occurrence_time", ""),
        "frequency": meta.get("frequency", ""),
        "severity": meta.get("severity", ""),
        "scenario": meta.get("scenario", ""),
        "expected_effect": meta.get("expected_effect", ""),
        "support_type": meta.get("support_type", ""),
        "preferred_response": meta.get("preferred_response", ""),
        "special_notes": meta.get("special_notes", ""),
        "steps_to_reproduce": meta.get("steps_to_reproduce", ""),
        "expected_result": meta.get("expected_result", ""),
        "actual_result": meta.get("actual_result", ""),
        "version": meta.get("version", ""),
        "feature_source": meta.get("feature_source", ""),
        "project": task.project_name or "",
    "project_id": task.project_id or "",
        # 截止时间：DateTime → ISO 字符串（前端 formatDeadlineAbsolute 消费）
        "deadline_at": task.deadline_at.isoformat() if task.deadline_at else "",
        "attachments": task.attachments or [],
        "attachment_analysis": task.attachment_analysis or {},
        "diagnosis": meta.get("diagnosis") or {},
        "source": task.source or AI_SOURCE,
        "created_by": created_by,
        "created_by_name": created_by_name,
        "assigned_to": assigned_to,
        "assigned_to_name": assigned_to_name,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


def upsert_task(ticket: dict, created_by: str = "") -> Task:
    """按 (source='ai', external_id) 幂等写入 task。

    同一 session_id 重复 submit 只更新同一行（修复重复提单）；不存在则新建。
    metadata_info 整体替换以保证 SQLAlchemy JSON 列感知变更。
    """
    fields = ticket_dict_to_task_fields(ticket, created_by)
    db = SessionLocal()
    try:
        existing = db.query(Task).filter(
            Task.source == AI_SOURCE,
            Task.external_id == fields["external_id"],
        ).first()
        if existing:
            existing.title = fields["title"]
            existing.description = fields["description"]
            existing.task_type = fields["task_type"]
            existing.priority = fields["priority"]
            existing.attachments = fields["attachments"]
            existing.metadata_info = fields["metadata_info"]
            # 弹窗编辑的截止时间：None 表示用户没选（保持原值），非 None 才更新
            if fields.get("deadline_at") is not None:
                existing.deadline_at = fields["deadline_at"]
            # 如果传入的 created_by 非空且比已有值更准确（非 system/unknown），则更新
            if created_by and existing.created_by in ("system", "unknown", ""):
                existing.created_by = created_by
            db.commit()
            db.refresh(existing)
            db.expunge(existing)  # 脱离 session，避免返回后 DetachedInstanceError
            return existing
        rec = Task(**fields)
        db.add(rec)
        db.commit()
        db.refresh(rec)
        # 写入操作日志：创建工单 + 初始状态变更（source='ai' 的工单也补日志）
        _log_task_creation(db, rec, created_by)
        # _log_task_creation 内部的 commit 会过期 rec 的属性，需先 refresh 再 expunge，
        # 否则返回后调用方访问 record.id 会触发 DetachedInstanceError（首次提单报错、二次成功）
        db.refresh(rec)
        db.expunge(rec)  # 脱离 session，避免返回后 DetachedInstanceError
        return rec
    finally:
        db.close()


def _log_task_creation(db, task: Task, created_by: str) -> None:
    """AI 创建工单后补写操作日志（create + status_change 初始主节点）。

    失败不阻塞主流程（工单已入库），仅记日志。
    """
    try:
        operator = created_by or "system"
        resolved_name = _resolve_operator_name(operator)
        status_val = task.status.value if hasattr(task.status, 'value') else str(task.status)
        # create 操作日志
        db.add(TaskOperationLog(
            task_id=task.id,
            operation_type=OperationType.CREATE,
            operator=operator,
            operator_name=resolved_name,
            to_status=status_val,
            description=f"{resolved_name} 创建了工单（AI 诊断）",
        ))
        # status_change 主节点：状态从无 → new
        db.add(TaskOperationLog(
            task_id=task.id,
            operation_type=OperationType.STATUS_CHANGE,
            operator=operator,
            operator_name=resolved_name,
            to_status=status_val,
            detail={"from": None, "to": status_val},
            description=f"工单状态变更为「{status_val}」",
        ))
        db.commit()
    except Exception as e:
        # 日志失败不回滚工单（已 commit），仅记错误
        import logging
        logging.getLogger(__name__).warning(
            f"Failed to log task creation for task {task.id}: {e}"
        )
        try:
            db.rollback()
        except Exception:
            pass


def update_task_resolution(task_id, solution: dict, resolution: str = "resolved") -> bool:
    """任务 Agent submit：置状态 + 把方案写进 metadata_info.diagnosis。

    metadata_info 整体替换（JSON 列不感知就地修改）。
    """
    db = SessionLocal()
    try:
        task = db.query(Task).filter(Task.id == int(task_id)).first()
        if not task:
            return False
        try:
            task.status = TaskStatus(resolution)
        except ValueError:
            task.status = TaskStatus.RESOLVED
        meta = dict(task.metadata_info or {})
        diag = dict(meta.get("diagnosis") or {})
        diag["solution"] = solution
        diag["resolved_by_agent"] = True
        meta["diagnosis"] = diag
        task.metadata_info = meta
        db.commit()
        return True
    finally:
        db.close()


def load_task_context_dict(task_id) -> dict:
    """任务 Agent _load_task_context：读 task + 解构 diagnosis。

    额外合并最近评论（task_comments）里上传的附件（讨论区补发的截图/日志），
    使 AI 能解析到用户评论时上传的图片/日志，避免"没收到附件"的误判。
    """
    db = SessionLocal()
    try:
        task = db.query(Task).filter(Task.id == int(task_id)).first()
        if not task:
            return {}
        base = task_to_dict(task)
        diag = base.get("diagnosis") or {}
        base["problem_summary"] = diag.get("problem_summary", "")
        base["hypotheses"] = diag.get("hypotheses") or []
        base["ruled_out"] = diag.get("ruled_out") or []
        base["collected_info"] = diag.get("collected_info") or {}
        base["diagnosis_rounds"] = diag.get("rounds", 0)

        # 合并最近评论（task_comments）上传的附件，复用 _dedup_attachments 去重
        comment_atts = _collect_comment_attachments(db, int(task_id))
        if comment_atts:
            base["attachments"] = _dedup_attachments(
                (base.get("attachments") or []) + comment_atts
            )
        return base
    finally:
        db.close()


_COMMENT_ATTACHMENT_LIMIT = 50
_COMMENT_SCAN_ROWS = 30


def _collect_comment_attachments(db, task_id: int) -> list:
    """收集该工单最近评论里上传的附件，转成 AI 可解析的附件 dict 列表。

    task_comments.attachments 为 {bucket}/{object} 字符串（讨论区上传的截图/日志），
    这里转成 {filename, path=MinIO 预签名 URL, object_path} 字典，
    供 parse_attachments / analyze_images / extract_log_paths 统一读取。
    presign 失败时降级保留 bucket/object 原值（_read_bytes 的 MinIO 分支仍可直接读取）。
    """
    import logging
    _log = logging.getLogger(__name__)
    result: list = []
    try:
        from app.models.task import TaskComment
        rows = (
            db.query(TaskComment)
            .filter(TaskComment.task_id == task_id, TaskComment.attachments.isnot(None))
            .order_by(TaskComment.created_at.desc())
            .limit(_COMMENT_SCAN_ROWS)
            .all()
        )
        from ai.core.minio_client import minio_client
        for c in rows:
            for att in (c.attachments or []):
                if not isinstance(att, str) or not att.strip():
                    continue
                att = att.strip()
                obj = att.partition("/")[2] or att
                fname = obj.split("/")[-1] or att
                try:
                    url = minio_client.get_presigned_url(att, expires_minutes=10)
                except Exception as e:
                    _log.debug(f"[task_adapter] 评论附件 presign 失败 {att}: {e}")
                    url = att  # 降级：保留 bucket/object 原值，_read_bytes 的 MinIO 分支可读
                result.append({"filename": fname, "path": url, "object_path": att})
                if len(result) >= _COMMENT_ATTACHMENT_LIMIT:
                    break
            if len(result) >= _COMMENT_ATTACHMENT_LIMIT:
                break
    except Exception as e:
        _log.debug(f"[task_adapter] 收集评论附件失败: {e}")
    return result


def update_attachment_analysis(task_id, updates: dict) -> bool:
    """把本次分析过的附件结论写回 tasks.attachment_analysis 记忆。

    updates: {object_path: {kind, summary, analyzed_at?}} — 逐个合并（保留历史其它条目）。
    attachment_analysis 为 JSON 列，就地修改不触发 UPDATE（SQLAlchemy 不感知），
    故整体构建新 dict 后整体赋值。
    """
    from datetime import datetime

    db = SessionLocal()
    try:
        task = db.query(Task).filter(Task.id == int(task_id)).first()
        if not task:
            return False
        memo = dict(task.attachment_analysis or {})
        now = datetime.now().isoformat(timespec="seconds")
        for obj_path, rec in (updates or {}).items():
            if not obj_path:
                continue
            prev = dict(memo.get(obj_path) or {})
            prev.update({k: v for k, v in (rec or {}).items() if v is not None})
            prev["analyzed"] = True
            prev["analyzed_at"] = prev.get("analyzed_at") or now
            memo[obj_path] = prev
        task.attachment_analysis = memo
        db.commit()
        return True
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            f"[task_adapter] 更新附件记忆失败 task={task_id}: {e}"
        )
        try:
            db.rollback()
        except Exception:
            pass
        return False
    finally:
        db.close()

