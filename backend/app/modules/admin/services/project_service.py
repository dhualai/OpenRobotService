from typing import List, Optional, Dict
import json
import requests
from sqlalchemy import create_engine, text, inspect, bindparam
from sqlalchemy.orm import sessionmaker
from app.models.delivery import UNDERTAKE_YES, PROJECT_DELETED
from app.modules.admin.schemas_das.request_models import ProjectBase, ProjectCreate, ProjectUpdate
from app.modules.admin.models_das.models import Project
from app.modules.admin.utils_das.config import DATABASE_URL, AUTH_SERVICE_BASE_URL

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

_PROJECT_COLUMNS = {c.key for c in inspect(Project).mapper.column_attrs}

def _filter_project_fields(data: Dict) -> Dict:
    return {k: v for k, v in data.items() if k in _PROJECT_COLUMNS}

def _to_float_or_none(value) -> Optional[float]:
    """将 JSON 提取出的值转 float；None/空串/非法值返回 None。"""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class ProjectService:
    
    def __init__(self):
        self.engine = engine
    
    def _init_sample_data(self):
        sample_projects = [
            {
                "project_code": "PROJ-001",
                "name": "项目A",
                "description": "在系统A中实现新的任务调度功能",
                "contact_person": "张三",
                "contact_person_id": 1,
                "status": "进行中",
                "expected_trend": "正常推进",
                "issues": 2,
                "risks": 1,
                "personnel_plan": "3人",
                "risk_list": "技术风险：对现有系统的影响",
                "deployment_date": "2025-01-25",
                "deployment_version": "v1.0.0",
                "recent_delivery_date": "2025-01-15",
                "recent_delivery_content": "完成任务调度核心功能",
                "final_delivery_date": "2025-01-31",
                "project_summary": "项目进展顺利，按计划推进",
                "task_execution_status": "已完成80%",
                "field_links": {"dashboard": "http://example.com/proj-001/dashboard", "docs": "http://example.com/proj-001/docs"}
            },
            {
                "project_code": "PROJ-002",
                "name": "项目B",
                "description": "系统B的性能优化，提高响应速度和稳定性",
                "contact_person": "李四",
                "contact_person_id": 2,
                "status": "进行中",
                "expected_trend": "正常推进",
                "issues": 0,
                "risks": 0,
                "personnel_plan": "3人",
                "risk_list": "无",
                "deployment_date": "2025-01-28",
                "deployment_version": "v2.1.0",
                "recent_delivery_date": "2025-01-20",
                "recent_delivery_content": "完成数据库优化",
                "final_delivery_date": "2025-01-31",
                "project_summary": "项目进展顺利，无风险",
                "task_execution_status": "已完成90%",
                "field_links": {"monitor": "http://example.com/proj-002/monitor"}
            },
            {
                "project_code": "PROJ-003",
                "name": "项目C",
                "description": "系统C的安全加固和漏洞修复",
                "contact_person": "王五",
                "contact_person_id": 3,
                "status": "进行中",
                "expected_trend": "延迟",
                "issues": 1,
                "risks": 0,
                "personnel_plan": "1人",
                "risk_list": "无",
                "deployment_date": "2025-01-20",
                "deployment_version": "v3.0.1",
                "recent_delivery_date": "2025-01-18",
                "recent_delivery_content": "修复高危漏洞",
                "final_delivery_date": "2025-01-25",
                "project_summary": "项目略有延迟，正在追赶进度",
                "task_execution_status": "已完成70%",
                "field_links": {"security": "http://example.com/proj-003/security"}
            },
            {
                "project_code": "PROJ-004",
                "name": "项目D",
                "description": "系统D的新功能开发和集成测试",
                "contact_person": "赵六",
                "contact_person_id": 4,
                "status": "进行中",
                "expected_trend": "正常推进",
                "issues": 3,
                "risks": 1,
                "personnel_plan": "3人",
                "risk_list": "人员风险",
                "deployment_date": "2025-01-28",
                "deployment_version": "v1.5.0",
                "recent_delivery_date": "2025-01-20",
                "recent_delivery_content": "完成新功能开发",
                "final_delivery_date": "2025-01-30",
                "project_summary": "项目进展顺利，解决了3个关键问题",
                "task_execution_status": "已完成85%",
                "field_links": {"features": "http://example.com/proj-004/features", "test": "http://example.com/proj-004/test"}
            },
            {
                "project_code": "PROJ-005",
                "name": "项目E",
                "description": "系统E的升级和数据迁移工作",
                "contact_person": "钱七",
                "contact_person_id": 5,
                "status": "待开始",
                "expected_trend": "计划启动",
                "issues": 0,
                "risks": 0,
                "personnel_plan": "1人",
                "risk_list": "无",
                "deployment_date": "2025-02-10",
                "deployment_version": "v4.0.0",
                "recent_delivery_date": "2025-02-05",
                "recent_delivery_content": "完成需求分析",
                "final_delivery_date": "2025-02-15",
                "project_summary": "项目即将启动，正在做准备工作",
                "task_execution_status": "已完成10%",
                "field_links": {"planning": "http://example.com/proj-005/planning"}
            }
        ]
        
        db = SessionLocal()
        for project_data in sample_projects:
            project_data = project_data.copy()
            if project_data.get("field_links"):
                project_data["field_links"] = json.dumps(project_data["field_links"])
            
            if "project_code" in project_data:
                project_data["code"] = project_data.pop("project_code")
                project_data["id"] = project_data["code"]
            
            existing_project = db.query(Project).filter(Project.code == project_data["code"]).first()
            if not existing_project:
                project_data = _filter_project_fields(project_data)
                db_project = Project(**project_data)
                db.add(db_project)
        db.commit()
        db.close()
    
    def _convert_to_dict(self, project: Project) -> Dict:
        project_dict = {
            "id": project.id,
            "system_id": project.system_id,
            "project_code": project.code,
            "name": project.name,
            "description": project.description,
            "contact_person": project.contact_person,
            "contact_person_id": project.contact_person_id,
            "project_contact": project.project_contact,
            "status": project.status,
            "expected_trend": project.expected_trend,
            "issues": project.issues,
            "risks": project.risks,
            "personnel_plan": project.personnel_plan,
            "risk_list": project.risk_list,
            "deployment_date": project.deployment_date,
            "deployment_version": project.deployment_version,
            "recent_delivery_date": project.recent_delivery_date,
            "recent_delivery_content": project.recent_delivery_content,
            "final_delivery_date": project.final_delivery_date,
            "project_summary": project.project_summary,
            "task_execution_status": project.task_execution_status,
            "field_links": json.loads(project.field_links) if project.field_links else None,
            "category_basis": project.category_basis,
            "project_type": project.project_type,
            "stage_notes": json.loads(project.stage_notes) if project.stage_notes else None,
            "risk_carrying_type": project.risk_carrying_type,
            "special_attention": project.special_attention,
            "risk_task_description": project.risk_task_description,
            "management_strategy": project.management_strategy,
            "project_documents": json.loads(project.project_documents) if project.project_documents else None,
            "sales": project.sales,
            "pre_sales": project.pre_sales,
            "project_manager": project.project_manager,
            "field_engineer": project.field_engineer,
            "internal_code": project.internal_code,
            "project_region": project.project_region,
            "total_vehicle_count": project.total_vehicle_count,
            "controller_vendor": project.controller_vendor,
            "system_integration": json.loads(project.system_integration) if project.system_integration else None,
            "server_deployment_status": project.server_deployment_status,
            "settlement_period": project.settlement_period,
            "undertake_status": project.undertake_status,
        }
        return project_dict
    
    def get_projects(self, skip: int = 0, limit: int = 999999999, include_pending: bool = False) -> List[Dict]:
        """项目列表。默认只返回已承接项目（undertake_status='是'）。

        include_pending=True 时把「待定」项目一并返回，目前仅仪表盘月柱图
        （dashboard.py get_project_monthly_summary）使用，用于统计浅色段数量；
        其余列表/统计都不应放开，否则项目总数、紧急度看板等口径会跟着变。
        """
        db = SessionLocal()
        try:
            query = db.query(Project).filter(
                Project.id != None,
                Project.id != "",
                Project.status != PROJECT_DELETED,
            )
            if not include_pending:
                query = query.filter(Project.undertake_status == UNDERTAKE_YES)
            projects = query.offset(skip).limit(limit).all()
            return [self._convert_to_dict(project) for project in projects]
        finally:
            db.close()

    def get_projects_by_ids(self, project_ids: List[str], include_pending: bool = False) -> List[Dict]:
        """按项目 ID 列表批量查询项目，用于仪表盘按当前用户关联项目过滤统计。

        同 get_projects：默认只返回已承接项目。
        """
        if not project_ids:
            return []
        db = SessionLocal()
        try:
            query = db.query(Project).filter(
            Project.id != None,
            Project.id != "",
            Project.id.in_(project_ids),
            Project.status != PROJECT_DELETED,
        )
            if not include_pending:
                query = query.filter(Project.undertake_status == UNDERTAKE_YES)
            projects = query.all()
            return [self._convert_to_dict(project) for project in projects]
        finally:
            db.close()

    def get_project(self, project_id: int) -> Optional[Dict]:
        db = SessionLocal()
        try:
            project = db.query(Project).filter(
                Project.id == project_id,
                Project.status != PROJECT_DELETED,
            ).first()
            return self._convert_to_dict(project) if project else None
        finally:
            db.close()
    
    def get_project_code_by_system_id(self, system_id: str) -> Optional[str]:
        db = SessionLocal()
        try:
            project = db.query(Project).filter(Project.system_id == system_id).first()
            return project.code if project else None
        finally:
            db.close()

    def check_project_duplicate(self, project_code: str, name: str, exclude_id: Optional[str] = None) -> Optional[str]:
        """校验项目编号/项目名称是否已存在（两者均为唯一 key）。

        返回冲突字段的中文描述；无冲突返回 None。
        exclude_id 用于更新场景：排除当前项目自身，避免自比较命中。
        """
        db = SessionLocal()
        try:
            query = db.query(Project)
            code_query = query.filter(Project.code == project_code)
            if exclude_id:
                code_query = code_query.filter(Project.id != exclude_id)
            if code_query.first():
                return f"项目编号「{project_code}」已存在"

            name_query = query.filter(Project.name == name)
            if exclude_id:
                name_query = name_query.filter(Project.id != exclude_id)
            if name_query.first():
                return f"项目名称「{name}」已存在"
            return None
        finally:
            db.close()

    def create_project(self, project_data: Dict) -> Dict:
        db = SessionLocal()
        try:
            project_data = project_data.copy()
            
            if "project_code" in project_data:
                project_data["code"] = project_data.pop("project_code")
                project_data["id"] = project_data["code"]
            
            if project_data.get("field_links"):
                project_data["field_links"] = json.dumps(project_data["field_links"])

            if project_data.get("stage_notes"):
                project_data["stage_notes"] = json.dumps(project_data["stage_notes"])

            if project_data.get("project_documents"):
                project_data["project_documents"] = json.dumps(project_data["project_documents"])

            if project_data.get("system_integration"):
                project_data["system_integration"] = json.dumps(project_data["system_integration"])

            project_data = _filter_project_fields(project_data)

            db_project = Project(**project_data)
            db.add(db_project)
            db.commit()
            db.refresh(db_project)
            return self._convert_to_dict(db_project)
        finally:
            db.close()
    
    def update_project(self, project_id: int, update_data: Dict) -> Optional[Dict]:
        db = SessionLocal()
        try:
            project = db.query(Project).filter(
                Project.id == project_id,
                Project.status != PROJECT_DELETED,
            ).first()
            if not project:
                return None

            if "project_code" in update_data:
                update_data["code"] = update_data.pop("project_code")

            if "field_links" in update_data:
                if update_data["field_links"]:
                    update_data["field_links"] = json.dumps(update_data["field_links"])
                else:
                    update_data["field_links"] = None

            if "stage_notes" in update_data:
                if update_data["stage_notes"]:
                    update_data["stage_notes"] = json.dumps(update_data["stage_notes"])
                else:
                    update_data["stage_notes"] = None

            if "project_documents" in update_data:
                if update_data["project_documents"]:
                    update_data["project_documents"] = json.dumps(update_data["project_documents"])
                else:
                    update_data["project_documents"] = None

            if "system_integration" in update_data:
                if update_data["system_integration"]:
                    update_data["system_integration"] = json.dumps(update_data["system_integration"])
                else:
                    update_data["system_integration"] = None

            update_data = _filter_project_fields(update_data)

            for field, value in update_data.items():
                setattr(project, field, value)
            
            db.commit()
            db.refresh(project)
            return self._convert_to_dict(project)
        finally:
            db.close()
    
    def delete_project(self, project_id: int) -> bool:
        db = SessionLocal()
        try:
            project = db.query(Project).filter(Project.id == project_id).first()
            if not project or project.status == PROJECT_DELETED:
                return False

            # 先清理 user_project_roles 中引用本项目的关联记录，否则外键约束
            # user_project_roles_ibfk_2（project_id → project.id）会阻止删除
            from app.models.identity import user_project_roles
            db.execute(user_project_roles.delete().where(
                user_project_roles.c.project_id == str(project.id)))

            # 软删除：保留 project 记录，仅标记为已删除。
            # 后续创建新项目时 check_project_duplicate 仍会命中本记录（按编号/名称），
            # 从而阻止编号/名称被复用，达到去重目的。
            project.status = PROJECT_DELETED
            db.commit()
            return True
        finally:
            db.close()
    
    def search_projects(self, keyword: str) -> List[Dict]:
        db = SessionLocal()
        try:
            projects = db.query(Project).filter(
                Project.undertake_status == UNDERTAKE_YES,
                Project.status != PROJECT_DELETED,
                (Project.name.ilike(f"%{keyword}%") |
                 Project.description.ilike(f"%{keyword}%") |
                 Project.code.ilike(f"%{keyword}%") |
                 Project.contact_person.ilike(f"%{keyword}%"))
            ).all()
            return [self._convert_to_dict(project) for project in projects]
        finally:
            db.close()
    
    def filter_projects(self, status: Optional[str] = None, 
                       execution_status: Optional[str] = None,
                       contact_person_id: Optional[str] = None) -> List[Dict]:
        db = SessionLocal()
        try:
            query = db.query(Project).filter(
            Project.undertake_status == UNDERTAKE_YES,
            Project.status != PROJECT_DELETED,
        )

            if status:
                query = query.filter(Project.status == status)
            
            if execution_status:
                query = query.filter(Project.task_execution_status == execution_status)
            
            if contact_person_id:
                query = query.filter(Project.contact_person_id == contact_person_id)
            
            projects = query.all()
            return [self._convert_to_dict(project) for project in projects]
        finally:
            db.close()
    
    def get_task_execution_metrics_7d_batch(self, project_codes: List[str]) -> Dict[str, Dict]:
        """批量获取多项目任务执行指标（一次批量查询，取近 7 天内最新一天的数据）。

        替代循环内逐项目调用 get_task_execution_status_7d / get_task_execution_stats_7d
        （两者 SQL 几乎相同，逐项目时为 2N 条 JSON 聚合查询，是 /projects?include_analysis
        列表接口的主要耗时来源）。数据源为 collection_data 表（indicator='GroupEfficiency'），
        某一天的数据整体存在 `data` JSON 字段（data[0] 为该日指标），按项目取
        近 7 天内最新一天（MAX start_time_int）：
        - 任务总数/已完成任务：dataIndicators.taskNumber.totalTasks / finishedTasks；
        - 任务完成率：dataIndicators.taskNumber.completionRate（如 "90%"，解析为小数）；
        - 切手动次数：averageManualCount.averageManualCount（如 6.5）。
        返回：
        {
          code: {
            "status": "搬运任务：X，移动任务：Y，任务总数：Z，完成总数：W" | "无数据",
            "stats": {
                "total_tasks": int, "finished_tasks": int,
                "completion_rate": float|None, "manual_switch_count": float|None,
            },
          }
        }
        未出现在返回 dict 中的项目码表示无数据（status="无数据"、stats 全 0）。
        """
        if not project_codes:
            return {}
        db = SessionLocal()
        try:
            sql = text("""
            SELECT
                cd.project,
                JSON_EXTRACT(
                    JSON_EXTRACT(cd.`data`, '$.data[0].dataIndicators.taskNumber'),
                    '$.carry'
                ) AS total_carry,
                JSON_EXTRACT(
                    JSON_EXTRACT(cd.`data`, '$.data[0].dataIndicators.taskNumber'),
                    '$.navigate'
                ) AS total_navigate,
                JSON_EXTRACT(
                    JSON_EXTRACT(cd.`data`, '$.data[0].dataIndicators.taskNumber'),
                    '$.totalTasks'
                ) AS total_totalTasks,
                JSON_EXTRACT(
                    JSON_EXTRACT(cd.`data`, '$.data[0].dataIndicators.taskNumber'),
                    '$.finishedTasks'
                ) AS total_finishedTasks,
                JSON_UNQUOTE(
                    JSON_EXTRACT(
                        JSON_EXTRACT(cd.`data`, '$.data[0].dataIndicators.taskNumber'),
                        '$.completionRate'
                    )
                ) AS latest_completion_rate,
                JSON_EXTRACT(
                    JSON_EXTRACT(cd.`data`, '$.data[0].averageManualCount'),
                    '$.averageManualCount'
                ) AS latest_manual_count
            FROM collection_data cd
            JOIN (
                SELECT project, MAX(start_time_int) AS max_start
                FROM collection_data
                WHERE indicator = 'GroupEfficiency'
                AND start_time_int >= UNIX_TIMESTAMP(NOW() - INTERVAL 7 DAY)
                AND project IN :codes
                GROUP BY project
            ) t ON t.project = cd.project AND cd.start_time_int = t.max_start
            WHERE cd.indicator = 'GroupEfficiency'
            """).bindparams(bindparam("codes", expanding=True))
            rows = db.execute(sql, {"codes": list(project_codes)}).fetchall()

            metrics: Dict[str, Dict] = {}
            for row in rows:
                total_carry = int(_to_float_or_none(row.total_carry) or 0)
                total_navigate = int(_to_float_or_none(row.total_navigate) or 0)
                total_tasks = int(_to_float_or_none(row.total_totalTasks) or 0)
                finished_tasks = int(_to_float_or_none(row.total_finishedTasks) or 0)
                metrics[row.project] = {
                    "status": (
                        f"搬运任务：{total_carry}，移动任务：{total_navigate}，"
                        f"任务总数：{total_tasks}，完成总数：{finished_tasks}"
                    ),
                    "stats": {
                        "total_tasks": total_tasks,
                        "finished_tasks": finished_tasks,
                        "completion_rate": self._parse_completion_rate(
                            row.latest_completion_rate, total_tasks, finished_tasks
                        ),
                        "manual_switch_count": _to_float_or_none(row.latest_manual_count),
                    },
                }
            return metrics
        finally:
            db.close()

    @staticmethod
    def _parse_completion_rate(raw, total_tasks: int, finished_tasks: int) -> Optional[float]:
        """解析 collection_data 中的完成率字段为小数。

        字段为百分比字符串（如 "90%"、"90.5%"）或小数（如 0.9），
        缺失/非法时回退为 finished_tasks / total_tasks。
        """
        if raw is not None:
            try:
                text = str(raw).strip()
                if text.endswith("%"):
                    return round(float(text[:-1]) / 100, 4)
                return round(float(text), 4)
            except (TypeError, ValueError):
                pass
        return round(finished_tasks / total_tasks, 4) if total_tasks else None

    def get_task_execution_status_7d(self, project_code: str) -> str:
        db = SessionLocal()
        try:
            sql = """
            SELECT 
                SUM(
                    JSON_EXTRACT(
                        JSON_EXTRACT(`data`, '$.data[0].dataIndicators.taskNumber'), 
                        '$.carry'
                    )
                ) AS total_carry,
                SUM(
                    JSON_EXTRACT(
                        JSON_EXTRACT(`data`, '$.data[0].dataIndicators.taskNumber'), 
                        '$.navigate'
                    )
                ) AS total_navigate,
                SUM(
                    JSON_EXTRACT(
                        JSON_EXTRACT(`data`, '$.data[0].dataIndicators.taskNumber'), 
                        '$.totalTasks'
                    )
                ) AS total_totalTasks,
                SUM(
                    JSON_EXTRACT(
                        JSON_EXTRACT(`data`, '$.data[0].dataIndicators.taskNumber'), 
                        '$.finishedTasks'
                    )
                ) AS total_finishedTasks
            FROM collection_data
            WHERE project = :project
            AND indicator = 'GroupEfficiency'
            AND start_time_int >= UNIX_TIMESTAMP(NOW() - INTERVAL 7 DAY)
            """
            result = db.execute(text(sql), {"project": project_code}).fetchone()
            
            if result:
                total_carry = int(result.total_carry or 0)
                total_navigate = int(result.total_navigate or 0)
                total_totalTasks = int(result.total_totalTasks or 0)
                total_finishedTasks = int(result.total_finishedTasks or 0)
                
                return f"搬运任务：{total_carry}，移动任务：{total_navigate}，任务总数：{total_totalTasks}，完成总数：{total_finishedTasks}"
            else:
                return "无数据"
        finally:
            db.close()

    def get_task_execution_stats_7d(self, project_code: str) -> Dict:
        db = SessionLocal()
        try:
            sql = """
            SELECT
                SUM(
                    JSON_EXTRACT(
                        JSON_EXTRACT(`data`, '$.data[0].dataIndicators.taskNumber'),
                        '$.totalTasks'
                    )
                ) AS total_totalTasks,
                SUM(
                    JSON_EXTRACT(
                        JSON_EXTRACT(`data`, '$.data[0].dataIndicators.taskNumber'),
                        '$.finishedTasks'
                    )
                ) AS total_finishedTasks
            FROM collection_data
            WHERE project = :project
            AND indicator = 'GroupEfficiency'
            AND start_time_int >= UNIX_TIMESTAMP(NOW() - INTERVAL 7 DAY)
            """
            result = db.execute(text(sql), {"project": project_code}).fetchone()

            total_tasks = int(result.total_totalTasks or 0) if result else 0
            finished_tasks = int(result.total_finishedTasks or 0) if result else 0
            completion_rate = round(finished_tasks / total_tasks, 4) if total_tasks else None

            return {
                "total_tasks": total_tasks,
                "finished_tasks": finished_tasks,
                "completion_rate": completion_rate,
            }
        finally:
            db.close()

    def create_license(self, license_data: Dict) -> Dict:
        from app.modules.admin.models_das.models import ProjectLicense
        from datetime import datetime
        
        db = SessionLocal()
        try:
            license_data['created_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            db_license = ProjectLicense(**license_data)
            db.add(db_license)
            db.commit()
            db.refresh(db_license)
            
            license_dict = {
                "id": db_license.id,
                "project_code": db_license.project_code,
                "machine_code": db_license.machine_code,
                "apply_time": db_license.apply_time,
                "expire_time": db_license.expire_time,
                "license_code": db_license.license_code,
                "applicant": db_license.applicant,
                "applicant_id": db_license.applicant_id,
                "max_vehicles": db_license.max_vehicles,
                "created_at": db_license.created_at
            }
            return license_dict
        finally:
            db.close()

    def delete_license(self, license_id: int) -> bool:
        """按 ID 删除项目授权（撤销）。不存在返回 False。"""
        from app.modules.admin.models_das.models import ProjectLicense

        db = SessionLocal()
        try:
            license = db.query(ProjectLicense).filter(ProjectLicense.id == license_id).first()
            if not license:
                return False
            db.delete(license)
            db.commit()
            return True
        finally:
            db.close()

    def _get_user_name_by_username(self, username: str) -> str:
        try:
            url = f"{AUTH_SERVICE_BASE_URL}/users/{username}/detail"
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                user_data = response.json()
                return user_data.get("name", username)
        except Exception:
            pass
        return username
    
    def get_licenses_by_project_code(self, project_code: str, type: str = 'last') -> List[Dict]:
        from app.modules.admin.models_das.models import ProjectLicense, Project

        db = SessionLocal()
        try:
            # 兼容历史数据：project_license.project_code 早期可能存的是项目名称而非项目代码，
            # 因此按项目代码查出项目名称后，同时匹配 code 与 name。
            match_values = [project_code]
            project = db.query(Project).filter(Project.code == project_code).first()
            if project and project.name and project.name != project_code:
                match_values.append(project.name)

            query = db.query(ProjectLicense).filter(ProjectLicense.project_code.in_(match_values))

            if type == 'last':
                license = query.order_by(ProjectLicense.created_at.desc()).first()
                if license:
                    license_dict = {
                        "id": license.id,
                        "project_code": license.project_code,
                        "machine_code": license.machine_code,
                        "apply_time": license.apply_time,
                        "expire_time": license.expire_time,
                        "license_code": license.license_code,
                        "applicant": license.applicant,
                        "applicant_id": license.applicant_id,
                        "max_vehicles": license.max_vehicles,
                        "created_at": license.created_at
                    }
                    return [license_dict]
                else:
                    return []
            else:
                licenses = query.order_by(ProjectLicense.created_at.desc()).all()
                license_list = []
                for license in licenses:
                    license_dict = {
                        "id": license.id,
                        "project_code": license.project_code,
                        "machine_code": license.machine_code,
                        "apply_time": license.apply_time,
                        "expire_time": license.expire_time,
                        "license_code": license.license_code,
                        "applicant": license.applicant,
                        "applicant_id": license.applicant_id,
                        "max_vehicles": license.max_vehicles,
                        "created_at": license.created_at
                    }
                    license_list.append(license_dict)
                return license_list
        finally:
            db.close()

project_service = ProjectService()