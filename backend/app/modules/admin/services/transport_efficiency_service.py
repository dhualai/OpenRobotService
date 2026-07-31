"""搬运效率分析数据服务。

数据来源：更多功能-数据管理 页面导入（Excel 或 JSON），每个项目每天一条汇总记录，
另有一张按 AGV 型号对比的明细表（同一 project_code + report_date 下多行，每行一个型号）。
"""
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import io

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.modules.admin.models_das.models import (
    ProjectTransportEfficiency,
    ProjectTransportEfficiencyRobot,
)
from app.modules.admin.utils_das.config import DATABASE_URL

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 汇总表：中文指标名 -> 字段名
SUMMARY_FIELD_MAP = {
    "总任务数": "total_tasks",
    "搬运任务数量": "carry_task_count",
    "有效工作时长": "effective_work_hours",
    "机器人故障时长": "fault_hours",
    "空闲无任务时间": "idle_hours",
    "平均错误次数": "avg_error_count",
    "平均单次故障时间": "avg_fault_duration_minutes",
    "平均单次搬运任务时间": "avg_carry_duration_minutes",
    "平均切手动次数": "avg_manual_switch_count",
    "人工干预率": "manual_intervention_rate",
}

SUMMARY_INT_FIELDS = {"total_tasks", "carry_task_count"}

# 型号对比表：中文指标名 -> 字段名
ROBOT_FIELD_MAP = {
    "搬运任务总数(个)": "carry_task_total",
    "有效工作时长(h)": "effective_work_hours",
    "有效搬运效率(小时/个)": "effective_efficiency",
    "机器人故障时间(小时)": "fault_hours",
    "无工作时间(小时)": "idle_hours",
    "平均单次故障(分钟)": "avg_fault_duration_minutes",
    "平均单次搬运时间(分钟)": "avg_carry_duration_minutes",
}

ROBOT_INT_FIELDS = {"carry_task_total"}


class TransportEfficiencyService:

    def upsert_daily_summary(self, project_code: str, report_date: str, fields: Dict) -> Dict:
        db = SessionLocal()
        try:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            record = db.query(ProjectTransportEfficiency).filter(
                ProjectTransportEfficiency.project_code == project_code,
                ProjectTransportEfficiency.report_date == report_date,
            ).first()

            allowed_fields = set(SUMMARY_FIELD_MAP.values())
            clean_fields = {k: v for k, v in fields.items() if k in allowed_fields}

            if record:
                for key, value in clean_fields.items():
                    setattr(record, key, value)
                record.updated_at = now_str
            else:
                record = ProjectTransportEfficiency(
                    project_code=project_code,
                    report_date=report_date,
                    created_at=now_str,
                    updated_at=now_str,
                    **clean_fields,
                )
                db.add(record)

            db.commit()
            db.refresh(record)
            return self._summary_to_dict(record)
        finally:
            db.close()

    def upsert_robot_rows(self, project_code: str, report_date: str, rows: List[Dict]) -> List[Dict]:
        db = SessionLocal()
        try:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            db.query(ProjectTransportEfficiencyRobot).filter(
                ProjectTransportEfficiencyRobot.project_code == project_code,
                ProjectTransportEfficiencyRobot.report_date == report_date,
            ).delete()

            allowed_fields = set(ROBOT_FIELD_MAP.values())
            created = []
            for row in rows:
                robot_model = row.get("robot_model")
                if not robot_model:
                    continue
                clean_row = {k: v for k, v in row.items() if k in allowed_fields}
                record = ProjectTransportEfficiencyRobot(
                    project_code=project_code,
                    report_date=report_date,
                    robot_model=robot_model,
                    created_at=now_str,
                    **clean_row,
                )
                db.add(record)
                created.append(record)

            db.commit()
            for record in created:
                db.refresh(record)
            return [self._robot_to_dict(record) for record in created]
        finally:
            db.close()

    def get_daily_efficiency(self, project_code: str, report_date: str) -> Optional[Dict]:
        db = SessionLocal()
        try:
            record = db.query(ProjectTransportEfficiency).filter(
                ProjectTransportEfficiency.project_code == project_code,
                ProjectTransportEfficiency.report_date == report_date,
            ).first()
            return self._summary_to_dict(record) if record else None
        finally:
            db.close()

    def get_robot_efficiency(self, project_code: str, report_date: str) -> List[Dict]:
        db = SessionLocal()
        try:
            records = db.query(ProjectTransportEfficiencyRobot).filter(
                ProjectTransportEfficiencyRobot.project_code == project_code,
                ProjectTransportEfficiencyRobot.report_date == report_date,
            ).all()
            return [self._robot_to_dict(record) for record in records]
        finally:
            db.close()

    def get_latest_manual_switch_count(self, project_code: str) -> Optional[float]:
        db = SessionLocal()
        try:
            record = db.query(ProjectTransportEfficiency).filter(
                ProjectTransportEfficiency.project_code == project_code,
            ).order_by(ProjectTransportEfficiency.report_date.desc()).first()
            return record.avg_manual_switch_count if record else None
        finally:
            db.close()

    def parse_excel(self, file_bytes: bytes) -> Tuple[Dict, List[Dict]]:
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(file_bytes), data_only=True)

        summary: Dict = {}
        if "汇总" in wb.sheetnames:
            ws = wb["汇总"]
            for row in ws.iter_rows(min_row=1, values_only=True):
                if not row or len(row) < 2:
                    continue
                label, value = row[0], row[1]
                if label is None:
                    continue
                field = SUMMARY_FIELD_MAP.get(str(label).strip())
                if not field or value is None:
                    continue
                summary[field] = self._coerce_number(field, value, SUMMARY_INT_FIELDS)

        robot_rows: List[Dict] = []
        if "机型明细" in wb.sheetnames:
            ws = wb["机型明细"]
            rows = list(ws.iter_rows(values_only=True))
            if rows:
                header = rows[0]
                robot_models = [str(cell).strip() if cell is not None else None for cell in header[1:]]
                robot_data: Dict[str, Dict] = {model: {"robot_model": model} for model in robot_models if model}

                for row in rows[1:]:
                    if not row:
                        continue
                    label = row[0]
                    if label is None:
                        continue
                    field = ROBOT_FIELD_MAP.get(str(label).strip())
                    if not field:
                        continue
                    for idx, model in enumerate(robot_models):
                        if not model:
                            continue
                        cell_value = row[idx + 1] if idx + 1 < len(row) else None
                        if cell_value is None:
                            continue
                        robot_data[model][field] = self._coerce_number(field, cell_value, ROBOT_INT_FIELDS)

                robot_rows = list(robot_data.values())

        return summary, robot_rows

    def _coerce_number(self, field: str, value, int_fields: set):
        try:
            num = float(value)
        except (TypeError, ValueError):
            return None
        if field in int_fields:
            return int(num)
        return num

    def _summary_to_dict(self, record: ProjectTransportEfficiency) -> Dict:
        return {
            "id": record.id,
            "project_code": record.project_code,
            "report_date": record.report_date,
            "total_tasks": record.total_tasks,
            "carry_task_count": record.carry_task_count,
            "effective_work_hours": record.effective_work_hours,
            "fault_hours": record.fault_hours,
            "idle_hours": record.idle_hours,
            "avg_error_count": record.avg_error_count,
            "avg_fault_duration_minutes": record.avg_fault_duration_minutes,
            "avg_carry_duration_minutes": record.avg_carry_duration_minutes,
            "avg_manual_switch_count": record.avg_manual_switch_count,
            "manual_intervention_rate": record.manual_intervention_rate,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }

    def _robot_to_dict(self, record: ProjectTransportEfficiencyRobot) -> Dict:
        return {
            "id": record.id,
            "project_code": record.project_code,
            "report_date": record.report_date,
            "robot_model": record.robot_model,
            "carry_task_total": record.carry_task_total,
            "effective_work_hours": record.effective_work_hours,
            "effective_efficiency": record.effective_efficiency,
            "fault_hours": record.fault_hours,
            "idle_hours": record.idle_hours,
            "avg_fault_duration_minutes": record.avg_fault_duration_minutes,
            "avg_carry_duration_minutes": record.avg_carry_duration_minutes,
            "created_at": record.created_at,
        }


transport_efficiency_service = TransportEfficiencyService()
