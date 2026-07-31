"""admin 搬运效率分析 API。

数据来源：更多功能-数据管理 页面导入的 Excel（或程序化 JSON）。
每个项目每天一条汇总记录，另有一张按 AGV 型号对比的明细表。
"""
from fastapi import APIRouter, Depends, Query, HTTPException, Body, UploadFile, File, Form
from typing import Optional, Dict, List
from pydantic import BaseModel

from app.modules.admin.services.transport_efficiency_service import transport_efficiency_service
from app.modules.admin.utils_das.config import security, DEBUG_MODE

transport_efficiency_router = APIRouter(prefix="/transport-efficiency", tags=["admin-transport-efficiency"])


class TransportEfficiencyImportBody(BaseModel):
    project_code: str
    report_date: str
    summary: Dict = {}
    robots: List[Dict] = []


@transport_efficiency_router.post("/import/file", summary="导入搬运效率数据(Excel)")
async def import_transport_efficiency_file(
    project_code: str = Form(...),
    report_date: str = Form(...),
    file: UploadFile = File(...),
    credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None)
) -> Dict:
    file_bytes = await file.read()
    try:
        summary, robot_rows = transport_efficiency_service.parse_excel(file_bytes)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Excel解析失败: {str(e)}")

    if not summary and not robot_rows:
        raise HTTPException(status_code=400, detail="未解析到有效数据，请检查表格格式（需包含'汇总'或'机型明细'工作表）")

    summary_result = transport_efficiency_service.upsert_daily_summary(project_code, report_date, summary) if summary else None
    robot_result = transport_efficiency_service.upsert_robot_rows(project_code, report_date, robot_rows) if robot_rows else []

    return {"summary": summary_result, "robots": robot_result}


@transport_efficiency_router.post("/import/json", summary="导入搬运效率数据(JSON)")
async def import_transport_efficiency_json(
    body: TransportEfficiencyImportBody,
    credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None)
) -> Dict:
    summary_result = transport_efficiency_service.upsert_daily_summary(body.project_code, body.report_date, body.summary) if body.summary else None
    robot_result = transport_efficiency_service.upsert_robot_rows(body.project_code, body.report_date, body.robots) if body.robots else []

    return {"summary": summary_result, "robots": robot_result}


@transport_efficiency_router.get("/{project_code}", summary="获取项目某日搬运效率数据")
async def get_transport_efficiency(
    project_code: str,
    date: str = Query(..., description="数据日期，格式YYYY-MM-DD"),
    credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None)
) -> Dict:
    summary = transport_efficiency_service.get_daily_efficiency(project_code, date)
    robots = transport_efficiency_service.get_robot_efficiency(project_code, date)
    return {"summary": summary, "robots": robots}
