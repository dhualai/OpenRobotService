import logging
from typing import Dict, Optional, List

from app.modules.admin.services.project_service import project_service
from app.modules.tasks.services.ticket_service import TicketService
from app.modules.tasks.schemas.ticket import TicketQueryParams
from app.core.db import AsyncSessionLocal

logger = logging.getLogger(__name__)


class ProjectTicketService:

    async def get_user_projects(self, contact_person_id: str, token: Optional[str] = None, keyword: Optional[str] = None, status: Optional[str] = None) -> Optional[List[Dict]]:
        try:
            projects = project_service.filter_projects(
                status=status,
                contact_person_id=contact_person_id
            )
            
            if keyword:
                projects = [
                    p for p in projects
                    if keyword.lower() in (p.get('name', '') + p.get('code', '')).lower()
                ]
            
            return projects if projects else []
        except Exception as e:
            logger.error(f"获取项目列表时发生异常: {e}")
            return None

    async def get_user_tickets(self, assigned_to: str, token: Optional[str] = None, keyword: Optional[str] = None, status: Optional[str] = None) -> Optional[Dict]:
        try:
            async with AsyncSessionLocal() as db:
                query_params = TicketQueryParams(
                    page=1,
                    size=100,
                    assigned_to=assigned_to,
                    status=status
                )
                result = await TicketService.get_tickets(db, query_params, token)
                
                if keyword:
                    if 'items' in result:
                        result['items'] = [
                            t for t in result['items']
                            if keyword.lower() in (t.get('title', '') + t.get('description', '')).lower()
                        ]
                        result['total'] = len(result['items'])
                
                return result
        except Exception as e:
            logger.error(f"获取工单列表时发生异常: {e}")
            return None

    def format_user_info_reply(self, user_name: str, projects: List[Dict], tickets_data: Dict) -> str:
        reply_content = f"{user_name}：\n"

        project_count = len(projects) if projects else 0
        reply_content += f"对接项目数量：{project_count}\n"

        if projects:
            for project in projects:
                project_name = project.get('name', '未知项目')
                reply_content += f"{project_name}\n"

        if tickets_data and 'items' in tickets_data:
            unfinished_tickets = [
                ticket for ticket in tickets_data['items']
                if ticket.get('status') not in ['closed']
            ]
            unfinished_count = len(unfinished_tickets)

            if unfinished_count > 0:
                reply_content += f"分配给我未关闭的工单：{unfinished_count}\n"
            else:
                reply_content += "分配给我未关闭的工单：0\n"
        else:
            reply_content += "分配给我未关闭的工单：0\n"

        return reply_content


project_ticket_service = ProjectTicketService()