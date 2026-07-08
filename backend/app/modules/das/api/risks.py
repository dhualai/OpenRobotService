from fastapi import APIRouter, Depends, Query, HTTPException, Body
from typing import Optional, Dict, List
from app.modules.das.schemas.request_models import (
    RiskCreate, RiskUpdate, RiskResponse, RiskFilterOptions,
    RiskListResponse
)
from app.modules.das.services.risk_service import risk_service
from app.modules.das.utils.config import security, DEBUG_MODE

risk_router = APIRouter(prefix="/projects/risks", tags=["risks"])

@risk_router.get("/", summary="获取风险列表")
async def get_risks(
    searchTerm: Optional[str] = Query(None, description="搜索关键词（匹配风险描述）"),
    projectName: Optional[str] = Query(None, description="项目名称"),
    riskCategory: Optional[str] = Query(None, description="风险分类"),
    customCategory: Optional[str] = Query(None, description="自定义分类"),
    riskLevel: Optional[str] = Query(None, description="风险等级"),
    status: Optional[str] = Query(None, description="状态"),
    page: int = Query(1, ge=1, description="页码，默认1"),
    pageSize: int = Query(10, ge=1, le=100, description="每页条数，默认10"),
    sortBy: str = Query("discoveryTime", description="排序字段，默认discoveryTime"),
    sortOrder: str = Query("desc", description="排序方向，默认desc"),
    credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None)
) -> RiskListResponse:
    skip = (page - 1) * pageSize
    
    total = risk_service.get_total_count(
        search_term=searchTerm,
        project_name=projectName,
        risk_category=riskCategory,
        custom_category=customCategory,
        risk_level=riskLevel,
        status=status
    )
    
    if searchTerm:
        risks = risk_service.search_risks(searchTerm, skip, pageSize)
    else:
        sort_by = sortBy
        if sortBy == "discoveryTime":
            sort_by = "discovery_time"
        elif sortBy == "createdAt":
            sort_by = "created_at"
        
        risks = risk_service.filter_risks(
            project_name=projectName,
            risk_category=riskCategory,
            custom_category=customCategory,
            risk_level=riskLevel,
            status=status,
            skip=skip,
            limit=pageSize,
            sort_by=sort_by,
            sort_order=sortOrder
        )
    
    risk_responses = [RiskResponse(**risk) for risk in risks]
    
    return RiskListResponse(
        total=total,
        page=page,
        pageSize=pageSize,
        risks=risk_responses
    )

@risk_router.post("/", summary="新增风险")
async def create_risk(
    risk_data: RiskCreate,
    credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None)
) -> RiskResponse:
    if not risk_data.project_code:
        raise HTTPException(status_code=400, detail="项目代码不能为空")
    if not risk_data.project_name:
        raise HTTPException(status_code=400, detail="项目名称不能为空")
    if not risk_data.risk_category:
        raise HTTPException(status_code=400, detail="风险分类不能为空")
    if not risk_data.description:
        raise HTTPException(status_code=400, detail="风险描述不能为空")
    if not risk_data.risk_level:
        raise HTTPException(status_code=400, detail="风险等级不能为空")
    if not risk_data.responsible_person:
        raise HTTPException(status_code=400, detail="负责人不能为空")
    if not risk_data.responsible_person_id:
        raise HTTPException(status_code=400, detail="负责人ID不能为空")
    if not risk_data.status:
        raise HTTPException(status_code=400, detail="状态不能为空")
    if not risk_data.discovery_time:
        raise HTTPException(status_code=400, detail="发现时间不能为空")
    
    if risk_data.status == "关闭" and not risk_data.close_time:
        raise HTTPException(status_code=400, detail="关闭时间不能为空")
    
    risk = risk_service.create_risk(risk_data.model_dump())
    return RiskResponse(**risk)

@risk_router.get("/{risk_code}", summary="获取单个风险详情")
async def get_risk(
    risk_code: str,
    credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None)
) -> RiskResponse:
    risk = risk_service.get_risk(risk_code)
    if not risk:
        raise HTTPException(status_code=404, detail="风险不存在")
    return RiskResponse(**risk)

@risk_router.put("/{risk_code}", summary="更新风险信息")
async def update_risk(
    risk_code: str,
    update_data: RiskUpdate,
    credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None)
) -> RiskResponse:
    update_dict = {k: v for k, v in update_data.model_dump().items() if v is not None}
    
    if update_dict.get("status") == "关闭" and not update_dict.get("close_time"):
        raise HTTPException(status_code=400, detail="关闭时间不能为空")
    
    risk = risk_service.update_risk(risk_code, update_dict)
    if not risk:
        raise HTTPException(status_code=404, detail="风险不存在")
    return RiskResponse(**risk)

@risk_router.delete("/{risk_code}", summary="删除风险")
async def delete_risk(
    risk_code: str,
    credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None)
) -> Dict[str, bool]:
    success = risk_service.delete_risk(risk_code)
    if not success:
        raise HTTPException(status_code=404, detail="风险不存在")
    return {"success": True}

@risk_router.get("/filters", summary="获取过滤器选项数据")
async def get_filter_options(
    credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None)
) -> RiskFilterOptions:
    options = risk_service.get_filter_options()
    return RiskFilterOptions(**options)