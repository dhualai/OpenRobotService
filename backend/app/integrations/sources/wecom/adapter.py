"""企业微信项目数据源适配器。

调用 AI 服务的 /api/ai/wecom/projects 接口获取项目数据，
解析并存储到数据库的 project 表中。
"""
import httpx
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from app.core.config import settings
from app.modules.admin.services.project_service import project_service

logger = logging.getLogger(__name__)

WECOM_PROJECT_API_URL = "https://usp.ep-zl.com/p/api/ai/wecom/projects"

STATUS_MAP = {
    "售前方案": "售前方案",
    "投标阶段": "投标阶段",
    "签单洽谈": "签单洽谈",
    "已签合同": "已签合同",
    "出厂测试": "出厂测试",
    "即将进场": "即将进场",
    "延期进场": "延期进场",
    "正在实施": "正在实施",
    "实施暂停": "实施暂停",
    "试运行中": "试运行中",
    "验收运营": "验收运营",
    "项目暂停": "项目暂停",
    "项目终止": "项目终止",
    "项目变更": "项目变更",
    "项目结束": "项目结束",
}

CATEGORY_MAP = {
    "受关注项目": "重要紧急",
    "普通项目": "重要不紧急",
    "一般项目": "紧急不重要",
    "其他": "不重要不紧急",
}


def map_wecom_record_to_project(record: Dict[str, Any]) -> Dict[str, Any]:
    """将企业微信项目记录映射为数据库 Project 模型字段。"""
    values = record.get("values", {})
    
    project_code = str(values.get("项目编号", record.get("record_id", "")))
    lifecycle = values.get("项目生命周期", "")
    
    return {
        "project_code": project_code,
        "name": values.get("项目名称", ""),
        "description": values.get("承接描述", ""),
        "contact_person": values.get("调度对接人", ""),
        "contact_person_id": "",
        "status": STATUS_MAP.get(lifecycle, lifecycle) or "待开始",
        "expected_trend": "",
        "issues": 0,
        "risks": 0,
        "personnel_plan": "",
        "risk_list": "",
        "deployment_date": values.get("预计AGV下线时间", ""),
        "deployment_version": "",
        "recent_delivery_date": values.get("更新时间", ""),
        "recent_delivery_content": values.get("车型&车数", ""),
        "final_delivery_date": "",
        "project_summary": f"{values.get('方案项目命名', '')} - {values.get('车型&车数', '')}",
        "task_execution_status": "",
        "field_links": None,
        "category_basis": CATEGORY_MAP.get(values.get("项目类型", ""), "重要紧急"),
        "system_id": record.get("record_id", ""),
    }


class WecomProjectAdapter:
    """企业微信项目数据源适配器。"""
    
    name = "wecom"
    display_name = "企业微信项目"
    
    def is_enabled(self) -> bool:
        return True
    
    async def fetch_projects(self) -> List[Dict[str, Any]]:
        """从企业微信API拉取项目数据。"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(WECOM_PROJECT_API_URL)
                resp.raise_for_status()
                data = resp.json()
                
                if data.get("code") != 0:
                    logger.error(f"企业微信项目API返回错误: {data}")
                    return []
                
                records = data.get("data", {}).get("records", [])
                logger.info(f"成功从企业微信获取 {len(records)} 个项目记录")
                return records
        except httpx.TimeoutException:
            logger.error("企业微信项目API超时")
            return []
        except httpx.HTTPStatusError as e:
            logger.error(f"企业微信项目API返回HTTP错误: {e}")
            return []
        except Exception as e:
            logger.error(f"企业微信项目API请求失败: {e}")
            return []
    
    async def sync_projects(self) -> Dict[str, Any]:
        """同步项目数据到数据库。"""
        records = await self.fetch_projects()
        
        created = 0
        updated = 0
        skipped = 0
        errors = []
        filtered = 0
        
        for record in records:
            try:
                values = record.get("values", {})
                if values.get("是否承接") != "是":
                    filtered += 1
                    continue
                
                project_data = map_wecom_record_to_project(record)
                project_code = project_data["project_code"]
                
                existing_project = project_service.get_project(project_code)
                
                if existing_project:
                    update_data = {k: v for k, v in project_data.items() if v}
                    if update_data:
                        result = project_service.update_project(project_code, update_data)
                        if result:
                            updated += 1
                        else:
                            errors.append(f"更新项目失败: {project_code}")
                    else:
                        skipped += 1
                else:
                    result = project_service.create_project(project_data)
                    if result:
                        created += 1
                    else:
                        errors.append(f"创建项目失败: {project_code}")
            except Exception as e:
                errors.append(f"处理项目记录失败: {record.get('record_id', 'unknown')}, error={str(e)}")
        
        return {
            "fetched": len(records),
            "filtered": filtered,
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "errors": errors,
        }


wecom_project_adapter = WecomProjectAdapter()