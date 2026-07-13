import aiohttp
import logging
from typing import Dict, Optional, List
from app.core.config import settings

logger = logging.getLogger(__name__)


class ProjectTicketService:

    async def get_user_projects(self, contact_person_id: str, token: Optional[str] = None, keyword: Optional[str] = None, status: Optional[str] = None) -> Optional[List[Dict]]:
        try:
            params = {
                "contact_person_id": contact_person_id
            }
            if keyword:
                params["keyword"] = keyword
            if status:
                params["status"] = status

            headers = {}
            if token:
                headers["Authorization"] = f"Bearer {token}"

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{settings.DATA_SERVICE_URL}/api/data/projects/",
                    params=params,
                    headers=headers,
                    timeout=5
                ) as response:
                    if response.status == 200:
                        response_data = await response.json()
                        return response_data if isinstance(response_data, list) else []
                    else:
                        logger.error(f"获取项目列表失败: HTTP {response.status}")
                        return None
        except Exception as e:
            logger.error(f"获取项目列表时发生异常: {e}")
            return None

    async def get_user_tickets(self, assigned_to: str, token: Optional[str] = None, keyword: Optional[str] = None, status: Optional[str] = None) -> Optional[Dict]:
        try:
            params = {
                "page": 1,
                "size": 100,
                "assigned_to": assigned_to
            }
            if keyword:
                params["keyword"] = keyword
            if status:
                params["status"] = status

            headers = {}
            if token:
                headers["Authorization"] = f"Bearer {token}"

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{settings.FAQ_SERVER_URL}/api/FQA/tickets/",
                    params=params,
                    headers=headers,
                    timeout=5
                ) as response:
                    if response.status == 200:
                        response_data = await response.json()
                        return response_data
                    else:
                        logger.error(f"获取工单列表失败: HTTP {response.status}")
                        return None
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