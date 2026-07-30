from typing import List, Optional, Dict
import json
import requests
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from app.modules.admin.schemas_das.request_models import ProjectBase, ProjectCreate, ProjectUpdate
from app.modules.admin.models_das.models import Project
from app.modules.admin.utils_das.config import DATABASE_URL, AUTH_SERVICE_BASE_URL

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

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
        }
        return project_dict
    
    def get_projects(self, skip: int = 0, limit: int = 999999999) -> List[Dict]:
        db = SessionLocal()
        try:
            projects = db.query(Project).filter(Project.id != None, Project.id != "").offset(skip).limit(limit).all()
            return [self._convert_to_dict(project) for project in projects]
        finally:
            db.close()
    
    def get_project(self, project_id: int) -> Optional[Dict]:
        db = SessionLocal()
        try:
            project = db.query(Project).filter(Project.id == project_id).first()
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
            project = db.query(Project).filter(Project.id == project_id).first()
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
            if not project:
                return False
            
            db.delete(project)
            db.commit()
            return True
        finally:
            db.close()
    
    def search_projects(self, keyword: str) -> List[Dict]:
        db = SessionLocal()
        try:
            projects = db.query(Project).filter(
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
            query = db.query(Project)
            
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
                "created_at": db_license.created_at
            }
            return license_dict
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
        from app.modules.admin.models_das.models import ProjectLicense
        
        db = SessionLocal()
        try:
            query = db.query(ProjectLicense).filter(ProjectLicense.project_code == project_code)
            
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
                        "created_at": license.created_at
                    }
                    license_list.append(license_dict)
                return license_list
        finally:
            db.close()

project_service = ProjectService()