"""项目信息树节点「关注」标注 Service（项目动态的个人订阅源）。

用户在项目详情页「项目信息管理」展示卡上点子节点右侧的星标即关注该节点；
被关注节点的**最新一条**变动（project_info_value_history 里该节点最新记录）
展示在同页「项目动态」卡里——只展示变动内容（detail），不带时间与人员
（对照原型 ProjectActivityCard 的「关注节点变动」分组，用户明确要求）。

**按人隔离**（用户口径：「自己关注的自己才能看到，每个人可能关注的节点不一样」）：
关注列表以 (node_id, operator) 为主键，星标状态、项目动态都按当前登录人过滤；
operator 取 JWT sub（网关管控路由，识别不到用户身份的请求由 API 层拒绝）。

**节点身份的变化**（新结构）：节点定义改为「全局一份 + 项目增补」，
全局节点的 project_id 是 NULL。所以标注里的 project_id **必须由调用方从
请求路径传入**，不能再从 node.project_id 推——否则全局字段的星标会记成
project_id=NULL，项目动态就查不到它了。

清理：项目增补节点（含子树）被删除时，**所有人**对该节点的标注随节点一起删除
（remove_marks 在业务事务里调用），避免留下点不开的孤儿关注。

查询接口见 api/info_nodes.py：
  GET  /info-nodes/projects/{id}/marks     当前用户被关注的节点 id 列表（前端星标状态）
  POST /info-nodes/nodes/{node_id}/mark    切换当前用户的关注状态
  GET  /info-nodes/projects/{id}/activity  项目动态（当前用户被关注节点的最新变动）
"""
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import func

from app.core.db import SessionLocal  # 共享引擎（pool_pre_ping/pool_recycle），见 app/core/db.py
from app.modules.admin.models_das.models import (
    ProjectInfoNode, ProjectInfoNodeMark, ProjectInfoValueHistory,
)
from app.models.delivery import PROJECT_INFO_NODE_ACTIVE

# 项目动态一次最多返回多少条（每个被关注节点至多一条，正常远小于此）
ACTIVITY_LIMIT = 50


def _now_str() -> str:
    """与 delivery.py / info_node_service 一致，用字符串存时间戳。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ── 清理（与业务同事务，由调用方 commit；不等于「取消关注」，按节点全量删） ──


def remove_marks(db, node_ids: Optional[Iterable[str]]) -> None:
    """删除给定节点的关注标注（节点被删时调用；不 commit）。"""
    ids = [node_id for node_id in (node_ids or []) if node_id]
    if not ids:
        return
    db.query(ProjectInfoNodeMark).filter(
        ProjectInfoNodeMark.node_id.in_(ids)
    ).delete(synchronize_session=False)


def clear_project_marks(db, project_id: str) -> None:
    """清空某项目的全部关注标注（不 commit）。

    新结构下没有「整树替换」这种操作了（全局定义不逐项目同步），
    留此函数供项目删除时清理本方标注。
    """
    db.query(ProjectInfoNodeMark).filter(
        ProjectInfoNodeMark.project_id == project_id
    ).delete(synchronize_session=False)


def root_title_of(node_id: Optional[str], nodes: Dict[str, Dict[str, Any]]) -> str:
    """沿 parent_id 向上走到的根节点标题（纯函数，便于单测）。

    nodes: {节点id: {"title": ..., "parent_id": ...}}（当前树快照）。
    节点不存在 / 链断了 / 成环（超过 32 层）返回空串，由调用方回退到节点自身标题。
    """
    current = node_id
    title = ""
    for _ in range(32):
        if not current:
            return title  # 走到最顶层
        node = nodes.get(current)
        if node is None:
            return ""  # 节点不存在或 parent 指向了已删除的节点
        title = node.get("title") or title
        current = node.get("parent_id")
    return ""  # 超过 32 层：数据成环，放弃


class InfoNodeMarkService:
    """关注标注的读写（按 operator 隔离）；activity 由标注 ⋈ 变动历史聚合而成。"""

    def list_for_project(self, project_id: str, operator: str) -> List[str]:
        """某项目里 operator 关注的节点 id 列表（前端据此点亮星标）。"""
        db = SessionLocal()
        try:
            rows = db.query(ProjectInfoNodeMark.node_id).filter(
                ProjectInfoNodeMark.project_id == project_id,
                ProjectInfoNodeMark.operator == operator,
            ).all()
            return [row[0] for row in rows]
        finally:
            db.close()

    def toggle(self, node_id: str, operator: str, project_id: Optional[str] = None,
               operator_name: Optional[str] = None) -> bool:
        """切换 operator 对该节点在某项目里的关注状态，返回切换后是否被关注。

        node_id 是节点身份（全局节点各项目共用一行），project_id 是**关注发生的项目**：
        同一个全局字段，A 项目里关注了不代表 B 项目里也关注。

        project_id 缺省（None）时按「该项目里该节点的标注」整体处理：先删掉匹配到的
        全部标注（等价于取消关注），一条都没删到时才新增。这条回退路径**只对增补节点
        有效**（节点自带 project_id）；新结构下节点几乎全是全局的，全局节点推不出项目，
        调用方不传 project_id 就只能取消关注、新增会抛 LookupError。

        节点不存在抛 LookupError（调用方转 404）——前端树可能已过期；
        节点属于别的项目抛 PermissionError（调用方转 403）。
        """
        db = SessionLocal()
        try:
            node = db.query(ProjectInfoNode).filter(
                ProjectInfoNode.id == node_id
            ).first()
            if not node:
                raise LookupError("节点不存在")
            if project_id and node.project_id is not None and node.project_id != project_id:
                raise PermissionError("该节点属于其它项目，不能在本项目关注")
            if project_id is None and node.project_id is not None:
                # 调用方没说是哪个项目：增补节点自带项目，直接用它
                project_id = node.project_id

            query = db.query(ProjectInfoNodeMark).filter(
                ProjectInfoNodeMark.node_id == node_id,
                ProjectInfoNodeMark.operator == operator,
            )
            if project_id:
                query = query.filter(ProjectInfoNodeMark.project_id == project_id)
            rows = query.all()
            if rows:
                for row in rows:
                    db.delete(row)
                db.commit()
                return False

            if not project_id:
                # 全局节点的标注必须记在某个项目下，没有项目就无从归属
                raise LookupError("节点不存在或未指定项目")

            db.add(ProjectInfoNodeMark(
                node_id=node_id,
                operator=operator,
                project_id=project_id,  # 取自请求，不取自 node.project_id（全局节点为空）
                operator_name=operator_name,
                created_at=_now_str(),
            ))
            db.commit()
            return True
        finally:
            db.close()

    def marked_activity(self, project_id: str, operator: str,
                        limit: int = ACTIVITY_LIMIT) -> List[Dict[str, Any]]:
        """项目动态：operator 关注的每个节点只取最新一条变动，整体最新在前。

        标题用**当前**树里的节点/根节点标题（记录里的 node_name 只是当时的快照）；
        节点没记过任何操作则不出现在动态里（无变动可展示）。
        """
        db = SessionLocal()
        try:
            node_ids = [row[0] for row in db.query(ProjectInfoNodeMark.node_id).filter(
                ProjectInfoNodeMark.project_id == project_id,
                ProjectInfoNodeMark.operator == operator,
            ).all()]
            if not node_ids:
                return []

            # 每个被关注节点只取最新一条：先 GROUP BY node_id + max(id) 拿到各节点
            # 最新记录 id（id 时间有序，见 info_node_change_service._new_id），再按
            # 主键回查这 ≤N 条。避免把全部历史行（含 detail 大文本）拉回内存再丢弃——
            # 读取量因此与「关注了几个节点」成正比，而与「编辑过多少次」无关。
            latest_id_rows = db.query(
                func.max(ProjectInfoValueHistory.id),
            ).filter(
                ProjectInfoValueHistory.project_id == project_id,
                ProjectInfoValueHistory.node_id.in_(node_ids),
            ).group_by(ProjectInfoValueHistory.node_id).all()
            latest_ids = [rid for (rid,) in latest_id_rows if rid]
            if not latest_ids:
                return []

            latest = db.query(ProjectInfoValueHistory).filter(
                ProjectInfoValueHistory.id.in_(latest_ids),
            ).order_by(
                ProjectInfoValueHistory.changed_at.desc(),
                ProjectInfoValueHistory.id.desc(),
            ).limit(limit).all()

            # 标题查当前树：全局节点 + 本项目增补节点（只取组树需要的两列）
            nodes = {
                item.id: {"title": item.node_name, "parent_id": item.parent_id}
                for item in db.query(
                    ProjectInfoNode.id, ProjectInfoNode.node_name,
                    ProjectInfoNode.parent_id,
                ).filter(
                    ProjectInfoNode.status == PROJECT_INFO_NODE_ACTIVE,
                    ProjectInfoNode.project_id.is_(None)
                    | (ProjectInfoNode.project_id == project_id),
                ).all()
            }

            activity = []
            for row in latest:
                node = nodes.get(row.node_id) or {}
                root_title = root_title_of(row.node_id, nodes)
                node_title = node.get("title") or row.node_name or ""
                activity.append({
                    "node_id": row.node_id,
                    # 只展示变动内容（不带时间与人员）；标题仅用于说明「这是哪个节点」
                    "node_title": node_title,
                    "root_title": root_title or node_title,
                    "action": row.operation_type,
                    "detail": row.detail or "",
                    "created_at": row.changed_at,
                })
            return activity
        finally:
            db.close()


info_node_mark_service = InfoNodeMarkService()
