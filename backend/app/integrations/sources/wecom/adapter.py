"""企业微信项目数据源适配器。

调用 AI 服务的 /api/ai/wecom/projects 接口获取项目数据，
解析并存储到数据库的 project 表中。
"""
import httpx
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from app.core.config import settings
from app.core.database import db_manager, UserDB
from app.models.delivery import UNDERTAKE_PENDING, UNDERTAKE_YES
from app.modules.admin.services.project_service import project_service
from app.services.identity_service import IdentityService

logger = logging.getLogger(__name__)


def _resolve_contact_person_id(name: str) -> str:
    """根据姓名（user.name）反查用户表，返回 user.id。

    找不到或查询异常时返回空字符串，避免影响项目同步主流程。
    注意：wecom 的“调度对接人”字段可能带前后空格或不可见字符，这里统一 strip 后再精确匹配。
    """
    if not name:
        return ""
    cleaned = name.strip()
    if not cleaned:
        return ""
    db = db_manager.get_db()
    try:
        user = db.query(UserDB).filter(UserDB.name == cleaned).first()
        if user:
            logger.info(f"contact_person 命中用户: raw={name!r}, cleaned={cleaned!r}, user_id={user.id}")
            return user.id
        logger.info(f"contact_person 未命中用户: raw={name!r}, cleaned={cleaned!r}（users 表无完全相等的 name）")
        return ""
    except Exception as e:
        logger.warning(f"根据姓名查询用户ID失败: name={name!r}, error={e}")
        return ""
    finally:
        db.close()


def _ensure_contact_person_role(project_id: str, user_id: str) -> bool:
    """给 contact_person 在该项目上赋予"调度研发"角色（幂等）。

    委托 IdentityService.ensure_user_project_role_by_name，使用确定性 upr id，
    与项目创建接口的授权机制一致，多次同步不会产生重复授权。
    """
    if not user_id or not project_id:
        return False
    ok = IdentityService.ensure_user_project_role_by_name(project_id, user_id, "调度研发")
    if ok:
        logger.info(f"自动授权: project={project_id}, user={user_id}, role=调度研发")
    return ok


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
    
    contact_person = values.get("调度对接人", "")

    return {
        "project_code": project_code,
        "name": values.get("项目名称", ""),
        "description": values.get("承接描述", ""),
        "contact_person": contact_person,
        "contact_person_id": _resolve_contact_person_id(contact_person),
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
        "project_type": values.get("项目类型") or None,
        "system_id": record.get("record_id", ""),
        "settlement_period": values.get("业绩核算期") or None,
        "sales": values.get("销售") or None,
        "pre_sales": values.get("售前方案") or None,
        "project_manager": values.get("项目经理") or None,
        "field_engineer": values.get("实施工程师") or None,
        "undertake_status": values.get("是否承接") or UNDERTAKE_YES,
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
        authorized = 0
        errors = []
        filtered = 0
        pending = 0

        for record in records:
            try:
                values = record.get("values", {})
                # 「是」入库为正式项目；「待定」也入库但仅供仪表盘月柱图浅色段统计，
                # 其余项目列表/统计一律不含（见 project_service 的 include_pending 开关）；
                # 「否」及空值仍旧整条丢弃。
                undertake_status = values.get("是否承接")
                if undertake_status not in (UNDERTAKE_YES, UNDERTAKE_PENDING):
                    filtered += 1
                    continue
                is_pending = undertake_status == UNDERTAKE_PENDING
                if is_pending:
                    pending += 1

                project_data = map_wecom_record_to_project(record)
                project_code = project_data["project_code"]
                # 待定项目不做自动授权：授权即意味着对接人能在各处看到该项目，
                # 与「待定只进月柱图」的口径冲突；待其转为「是」后下次同步补授权。
                contact_person_id = "" if is_pending else project_data.get("contact_person_id", "")

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
                    # 回填：早期创建的项目当时 contact_person_id 为空未授权，这里幂等补上
                    if contact_person_id and _ensure_contact_person_role(project_code, contact_person_id):
                        authorized += 1
                else:
                    result = project_service.create_project(project_data)
                    if result:
                        created += 1
                        # 新项目导入后自动授权：给调度对接人赋予"调度研发"角色
                        if contact_person_id and _ensure_contact_person_role(project_code, contact_person_id):
                            authorized += 1
                    else:
                        errors.append(f"创建项目失败: {project_code}")
            except Exception as e:
                errors.append(f"处理项目记录失败: {record.get('record_id', 'unknown')}, error={str(e)}")

        return {
            "fetched": len(records),
            "filtered": filtered,
            "pending": pending,
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "authorized": authorized,
            "errors": errors,
        }


wecom_project_adapter = WecomProjectAdapter()