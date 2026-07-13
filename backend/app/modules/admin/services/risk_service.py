from typing import List, Optional, Dict
from sqlalchemy import create_engine, or_, and_
from sqlalchemy.orm import sessionmaker
from app.modules.admin.models_das.models import Risk
from app.modules.admin.utils_das.config import DATABASE_URL
from datetime import datetime, timedelta

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class RiskService:
    
    def __init__(self):
        self.engine = engine
    
    def _convert_to_dict(self, risk: Risk) -> Dict:
        risk_dict = {
            "id": risk.id,
            "risk_code": risk.risk_code,
            "project_code": risk.project_code,
            "project_name": risk.project_name,
            "risk_category": risk.risk_category,
            "custom_category": risk.custom_category,
            "description": risk.description,
            "risk_level": risk.risk_level,
            "response_measure": risk.response_measure,
            "progress": risk.progress,
            "responsible_person": risk.responsible_person,
            "responsible_person_id": risk.responsible_person_id,
            "status": risk.status,
            "discovery_time": risk.discovery_time,
            "close_time": risk.close_time,
            "created_at": risk.created_at,
            "updated_at": risk.updated_at
        }
        return risk_dict
    
    def get_risks(self, skip: int = 0, limit: int = 10) -> List[Dict]:
        db = SessionLocal()
        try:
            risks = db.query(Risk).offset(skip).limit(limit).all()
            return [self._convert_to_dict(risk) for risk in risks]
        finally:
            db.close()
    
    def get_risk(self, risk_code: str) -> Optional[Dict]:
        db = SessionLocal()
        try:
            risk = db.query(Risk).filter(Risk.risk_code == risk_code).first()
            return self._convert_to_dict(risk) if risk else None
        finally:
            db.close()
    
    def create_risk(self, risk_data: Dict) -> Dict:
        db = SessionLocal()
        try:
            now = datetime.now().strftime('%Y-%m-%dT%H:%M:%S.%fZ')
            risk_data["created_at"] = now
            risk_data["updated_at"] = now
            
            if not risk_data.get("risk_code"):
                project_code = risk_data.get("project_code")
                if project_code:
                    count = db.query(Risk).filter(Risk.project_code == project_code).count()
                    risk_data["risk_code"] = f"{project_code}-{count + 1}"
            
            db_risk = Risk(**risk_data)
            db.add(db_risk)
            db.commit()
            db.refresh(db_risk)
            return self._convert_to_dict(db_risk)
        finally:
            db.close()
    
    def update_risk(self, risk_code: str, update_data: Dict) -> Optional[Dict]:
        db = SessionLocal()
        try:
            risk = db.query(Risk).filter(Risk.risk_code == risk_code).first()
            if not risk:
                return None
            
            update_data["updated_at"] = datetime.now().strftime('%Y-%m-%dT%H:%M:%S.%fZ')
            
            for field, value in update_data.items():
                setattr(risk, field, value)
            
            db.commit()
            db.refresh(risk)
            return self._convert_to_dict(risk)
        finally:
            db.close()
    
    def delete_risk(self, risk_code: str) -> bool:
        db = SessionLocal()
        try:
            risk = db.query(Risk).filter(Risk.risk_code == risk_code).first()
            if not risk:
                return False
            
            db.delete(risk)
            db.commit()
            return True
        finally:
            db.close()
    
    def search_risks(self, search_term: str, skip: int = 0, limit: int = 10) -> List[Dict]:
        db = SessionLocal()
        try:
            risks = db.query(Risk).filter(
                Risk.description.ilike(f"%{search_term}%")
            ).offset(skip).limit(limit).all()
            return [self._convert_to_dict(risk) for risk in risks]
        finally:
            db.close()
    
    def filter_risks(self, project_name: Optional[str] = None, 
                     risk_category: Optional[str] = None,
                     custom_category: Optional[str] = None,
                     risk_level: Optional[str] = None,
                     status: Optional[str] = None,
                     skip: int = 0, limit: int = 10,
                     sort_by: str = "discovery_time",
                     sort_order: str = "desc") -> List[Dict]:
        db = SessionLocal()
        try:
            query = db.query(Risk)
            
            if project_name:
                query = query.filter(Risk.project_name == project_name)
            if risk_category:
                query = query.filter(Risk.risk_category == risk_category)
            if custom_category:
                query = query.filter(Risk.custom_category == custom_category)
            if risk_level:
                query = query.filter(Risk.risk_level == risk_level)
            if status:
                query = query.filter(Risk.status == status)
            
            if sort_by == "discovery_time":
                if sort_order == "desc":
                    query = query.order_by(Risk.discovery_time.desc())
                else:
                    query = query.order_by(Risk.discovery_time.asc())
            elif sort_by == "created_at":
                if sort_order == "desc":
                    query = query.order_by(Risk.created_at.desc())
                else:
                    query = query.order_by(Risk.created_at.asc())
            
            risks = query.offset(skip).limit(limit).all()
            return [self._convert_to_dict(risk) for risk in risks]
        finally:
            db.close()
    
    def get_filter_options(self) -> Dict:
        db = SessionLocal()
        try:
            project_names = db.query(Risk.project_name).distinct().all()
            project_names = [name[0] for name in project_names]
            
            risk_categories = db.query(Risk.risk_category).distinct().all()
            risk_categories = [category[0] for category in risk_categories]
            
            custom_categories = db.query(Risk.custom_category).distinct().all()
            custom_categories = [category[0] for category in custom_categories if category[0]]
            
            risk_levels = db.query(Risk.risk_level).distinct().all()
            risk_levels = [level[0] for level in risk_levels]
            
            statuses = db.query(Risk.status).distinct().all()
            statuses = [status[0] for status in statuses]
            
            return {
                "project_names": project_names,
                "risk_categories": risk_categories,
                "custom_categories": custom_categories,
                "risk_levels": risk_levels,
                "statuses": statuses
            }
        finally:
            db.close()
    
    def get_total_count(self, search_term: Optional[str] = None,
                        project_name: Optional[str] = None,
                        risk_category: Optional[str] = None,
                        custom_category: Optional[str] = None,
                        risk_level: Optional[str] = None,
                        status: Optional[str] = None) -> int:
        db = SessionLocal()
        try:
            query = db.query(Risk)
            
            if search_term:
                query = query.filter(Risk.description.ilike(f"%{search_term}%"))
            if project_name:
                query = query.filter(Risk.project_name == project_name)
            if risk_category:
                query = query.filter(Risk.risk_category == risk_category)
            if custom_category:
                query = query.filter(Risk.custom_category == custom_category)
            if risk_level:
                query = query.filter(Risk.risk_level == risk_level)
            if status:
                query = query.filter(Risk.status == status)
            
            return query.count()
        finally:
            db.close()
      
    def get_detailed_open_risks_by_project_codes(self, project_codes: List[str]) -> Dict[str, List[Dict]]:
        db = SessionLocal()
        try:
            now = datetime.now()
            week_start = now - timedelta(days=now.weekday())
            week_start_str = week_start.strftime('%Y-%m-%d')
            
            risks = db.query(Risk).filter(
                Risk.project_code.in_(project_codes),
                or_(
                    Risk.status != "已关闭",
                    and_(
                        Risk.status == "已关闭",
                        Risk.close_time >= week_start_str
                    )
                )
            ).all()
            
            risk_dict = {}
            for risk in risks:
                risk_data = self._convert_to_dict(risk)
                project_code = risk_data["project_code"]
                if project_code not in risk_dict:
                    risk_dict[project_code] = []
                risk_dict[project_code].append(risk_data)
            
            return risk_dict
        finally:
            db.close()


risk_service = RiskService()