import json
import logging
from typing import Dict, Optional, List, Tuple

from app.modules.admin.services.data_service import DataService as AdminDataService

logger = logging.getLogger(__name__)


class DataService:

    async def get_project_data(self, project_id: str, tag: Optional[str], indicator: List[str], headers: Dict) -> Optional[Dict]:
        try:
            result_item = AdminDataService.get_collection_data_for_indicators(
                project=project_id,
                tag=tag,
                indicators=indicator,
                start_time='',
                end_time=''
            )
            
            if result_item and result_item.get('project'):
                return {
                    'data': [result_item],
                    'authorized_indicators': result_item.get('authorized_indicators', []),
                    'value': result_item.get('content', [])[0] if result_item.get('content') else {}
                }
        except Exception as e:
            logger.error(f"获取项目数据时发生异常: {e}")

        return None

    def format_project_reply(self, project_id: str, access_data: Dict) -> str:
        reply_content = f"{project_id}\n\n"
        reply_content += f"有权限的内容：{access_data['authorized_indicators']}\n"

        if '*' in access_data['authorized_indicators']:
            reply_content += json.dumps(access_data['value'], ensure_ascii=False)
        else:
            for value in access_data['authorized_indicators']:
                if value in access_data['value']:
                    reply_content += f"\n{value}: {access_data['value'][value]}\n"

        return reply_content

    async def insert_project_data(self, data: Dict, headers: Dict = {"Content-Type": "application/json"}) -> Tuple[Optional[int], Optional[Dict]]:
        try:
            batch_data = data if isinstance(data, list) else [data]
            success, result_ids = AdminDataService.insert_batch_collection_data(batch_data)
            
            if success:
                return 200, {"ids": result_ids, "message": "插入成功"}
            else:
                return None, {"error": "插入失败"}

        except Exception as e:
            logger.error(f"发送数据失败: {e}", exc_info=True)
            return None, None


data_service = DataService()