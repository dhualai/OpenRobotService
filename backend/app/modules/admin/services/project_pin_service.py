"""项目「置顶」标注 Service（项目进度管理页的个人置顶列表）。

长按项目卡片弹出操作卡，「置顶」/「取消置顶」即切换当前登录人对该项目的置顶；
项目进度管理页拉取当前用户的置顶 id 列表，把置顶的项目排到最前
（同一人置顶多个时按置顶时间新的在前）。

**按人隔离**（与 project_info_node_mark 的节点关注同口径）：置顶列表以
(project_id, operator) 为主键，operator 取 JWT sub；识别不到用户身份的请求
由 API 层 401 拒绝——置顶是「每人一份」的东西，没有登录人就无处安放。

项目删除（软删）时清理该项目的全部置顶（remove_pins_for_project 在业务事务里调用），
避免留下点不开的孤儿置顶。

查询接口见 api/projects.py：
  GET    /projects/pins             当前用户置顶的项目 id 列表（置顶时间新的在前）
  POST   /projects/{id}/pin         置顶
  DELETE /projects/{id}/pin         取消置顶
"""
from datetime import datetime
from typing import List, Optional

from app.core.db import SessionLocal  # 共享引擎（pool_pre_ping/pool_recycle），见 app/core/db.py
from app.models.delivery import ProjectPin


def _now_str() -> str:
    """与 delivery.py / info_node_service 一致，用字符串存时间戳。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ── 清理（与业务同事务，由调用方 commit） ──


def remove_pins_for_project(db, project_id: str) -> None:
    """删除某项目的全部置顶标注（项目被删除时调用；不 commit）。"""
    if not project_id:
        return
    db.query(ProjectPin).filter(
        ProjectPin.project_id == project_id
    ).delete(synchronize_session=False)


class ProjectPinService:
    """置顶标注的读写（按 operator 隔离）。"""

    def list_for_operator(self, operator: str) -> List[str]:
        """operator 置顶的项目 id 列表，最近置顶的在前（前端据此排序）。"""
        db = SessionLocal()
        try:
            rows = db.query(ProjectPin.project_id).filter(
                ProjectPin.operator == operator,
            ).order_by(ProjectPin.created_at.desc()).all()
            return [row[0] for row in rows]
        finally:
            db.close()

    def is_pinned(self, project_id: str, operator: str) -> bool:
        db = SessionLocal()
        try:
            return db.query(ProjectPin.project_id).filter(
                ProjectPin.project_id == project_id,
                ProjectPin.operator == operator,
            ).first() is not None
        finally:
            db.close()

    def pin(self, project_id: str, operator: str,
            operator_name: Optional[str] = None) -> bool:
        """置顶（已置顶则原样返回 True，不刷新置顶时间）。"""
        db = SessionLocal()
        try:
            exists = db.query(ProjectPin.project_id).filter(
                ProjectPin.project_id == project_id,
                ProjectPin.operator == operator,
            ).first()
            if exists:
                return True
            db.add(ProjectPin(
                project_id=project_id,
                operator=operator,
                operator_name=operator_name,
                created_at=_now_str(),
            ))
            db.commit()
            return True
        finally:
            db.close()

    def unpin(self, project_id: str, operator: str) -> bool:
        """取消置顶（未置顶时为幂等空操作），返回是否仍然置顶（恒为 False）。"""
        db = SessionLocal()
        try:
            db.query(ProjectPin).filter(
                ProjectPin.project_id == project_id,
                ProjectPin.operator == operator,
            ).delete(synchronize_session=False)
            db.commit()
            return False
        finally:
            db.close()


project_pin_service = ProjectPinService()
