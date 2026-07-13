import aiohttp
import traceback
import json
import logging
from typing import Dict, Optional, List, Tuple
from app.core.config import settings

logger = logging.getLogger(__name__)


class DataService:

    async def get_project_data(self, project_id: str, tag: Optional[str], indicator: List[str], headers: Dict) -> Optional[Dict]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{settings.DATA_SERVICE_URL}/api/data/access",
                    json={"project": project_id, "tag": tag, "indicator": indicator},
                    headers=headers,
                    timeout=3
                ) as response:
                    if response.status == 200:
                        response_data = await response.json()
                        access_data = response_data.get('data', [])
                        if access_data:
                            return access_data[0]
                    else:
                        logger.error(f"获取项目数据失败: HTTP {response.status}")
        except Exception as e:
            logger.error(f"获取项目数据时发生异常: {e}")

        return None

    def format_project_reply(self, project_id: str, access_data: Dict) -> str:
        reply_content = f"{project_id}\n\n"
        reply_content += f"有权限的内容：{access_data['authorized_indicators']}\n"

        if '*' in access_data['authorized_indicators']:
            import json
            reply_content += json.dumps(access_data['value'], ensure_ascii=False)
        else:
            for value in access_data['authorized_indicators']:
                if value in access_data['value']:
                    reply_content += f"\n{value}: {access_data['value'][value]}\n"

        return reply_content

    async def insert_project_data(self, data: Dict, headers: Dict = {"Content-Type": "application/json"}) -> Tuple[Optional[int], Optional[Dict]]:
        try:
            url = f"{settings.DATA_DEBUG_SERVICE_URL}/api/data/insert/"

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=data,
                    headers=headers
                ) as response:
                    status_code = response.status
                    logger.info(f"API响应状态码: {status_code}")

                    try:
                        response_data = await response.json()
                        logger.debug(f"API响应内容: {json.dumps(response_data, indent=2, ensure_ascii=False)}")
                    except json.JSONDecodeError:
                        response_text = await response.text()
                        logger.debug(f"API响应文本: {response_text}")
                        response_data = None

                    return status_code, response_data if status_code == 200 else response_data.get('error', None)

        except Exception as e:
            logger.error(f"发送数据失败: {e}", exc_info=True)
            return None, None


data_service = DataService()