from typing import List, Optional, Dict
import json
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.modules.admin.models_das.models import ProjectDailyReport
from app.modules.admin.utils_das.config import DATABASE_URL

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class DailyReportService:
    
    def _convert_to_dict(self, report: ProjectDailyReport) -> Dict:
        report_dict = {
            "id": report.id,
            "project_code": report.project_code,
            "report_date": report.report_date,
            "report_content": json.loads(report.report_content) if report.report_content else {},
            "reporter": report.reporter,
            "reporter_id": report.reporter_id,
            "created_at": report.created_at,
            "updated_at": report.updated_at
        }
        return report_dict
    
    def get_reports(self, skip: int = 0, limit: int = 100) -> List[Dict]:
        db = SessionLocal()
        try:
            reports = db.query(ProjectDailyReport).order_by(ProjectDailyReport.report_date.desc()).offset(skip).limit(limit).all()
            return [self._convert_to_dict(report) for report in reports]
        finally:
            db.close()
    
    def get_report(self, report_id: int) -> Optional[Dict]:
        db = SessionLocal()
        try:
            report = db.query(ProjectDailyReport).filter(ProjectDailyReport.id == report_id).first()
            return self._convert_to_dict(report) if report else None
        finally:
            db.close()
    
    def get_reports_by_project(self, project_code: str, skip: int = 0, limit: int = 100) -> List[Dict]:
        db = SessionLocal()
        try:
            reports = db.query(ProjectDailyReport).filter(
                ProjectDailyReport.project_code == project_code
            ).order_by(ProjectDailyReport.report_date.desc()).offset(skip).limit(limit).all()
            return [self._convert_to_dict(report) for report in reports]
        finally:
            db.close()
    
    def get_report_by_date(self, project_code: str, report_date: str) -> Optional[Dict]:
        db = SessionLocal()
        try:
            report = db.query(ProjectDailyReport).filter(
                ProjectDailyReport.project_code == project_code,
                ProjectDailyReport.report_date == report_date
            ).first()
            return self._convert_to_dict(report) if report else None
        finally:
            db.close()
    
    def create_report(self, report_data: Dict) -> Dict:
        db = SessionLocal()
        try:
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            if report_data.get("report_content"):
                report_data["report_content"] = json.dumps(report_data["report_content"])
            
            report_data["created_at"] = current_time
            
            db_report = ProjectDailyReport(**report_data)
            db.add(db_report)
            db.commit()
            db.refresh(db_report)
            return self._convert_to_dict(db_report)
        finally:
            db.close()
    
    def update_report(self, report_id: int, update_data: Dict) -> Optional[Dict]:
        db = SessionLocal()
        try:
            report = db.query(ProjectDailyReport).filter(ProjectDailyReport.id == report_id).first()
            if not report:
                return None
            
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            if "report_content" in update_data:
                if update_data["report_content"]:
                    update_data["report_content"] = json.dumps(update_data["report_content"])
                else:
                    update_data["report_content"] = "{}"
            
            update_data["updated_at"] = current_time
            
            for field, value in update_data.items():
                setattr(report, field, value)
            
            db.commit()
            db.refresh(report)
            return self._convert_to_dict(report)
        finally:
            db.close()
    
    def delete_report(self, report_id: int) -> bool:
        db = SessionLocal()
        try:
            report = db.query(ProjectDailyReport).filter(ProjectDailyReport.id == report_id).first()
            if not report:
                return False
            
            db.delete(report)
            db.commit()
            return True
        finally:
            db.close()
    
    def search_reports(self, keyword: str) -> List[Dict]:
        db = SessionLocal()
        try:
            reports = db.query(ProjectDailyReport).filter(
                (ProjectDailyReport.reporter.ilike(f"%{keyword}%") |
                 ProjectDailyReport.project_code.ilike(f"%{keyword}%"))
            ).all()
            return [self._convert_to_dict(report) for report in reports]
        finally:
            db.close()


daily_report_service = DailyReportService()