
import aiohttp
import traceback
import json
from typing import Dict, Optional, List, Tuple


#DATA_SERVICE_URL ="http://localhost:8801"
DATA_SERVICE_URL ="http://127.0.0.1:8002"
class DataService:
    """数据服务类，负责获取项目数据"""
    
    async def get_project_data(self, project_id: str, tag: Optional[str], indicator: List[str], headers: Dict) -> Optional[Dict]:
        """获取项目数据
        
        Args:
            project_id: 项目ID
            tag: 标签
            indicator: 指标列表
            headers: 请求头，包含认证信息
        
        Returns:
            项目数据或None
        """
        try:
            # 将参数放入请求体中，使用POST请求
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{DATA_SERVICE_URL}/api/data/access",
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
                        print(f"获取项目数据失败: HTTP {response.status}")
        except Exception as e:
            print(f"获取项目数据时发生异常: {e}")
        
        return None
    
    def format_project_reply(self, project_id: str, access_data: Dict) -> str:
        """格式化项目数据回复内容
        
        Args:
            project_id: 项目ID
            access_data: 项目数据
        
        Returns:
            格式化后的回复内容
        """
        reply_content = f"{project_id}\n\n"
        reply_content += f"有权限的内容：{access_data['authorized_indicators']}\n"
        
        if '*' in access_data['authorized_indicators']:
            # 对于全部权限，返回所有值
            import json
            reply_content += json.dumps(access_data['value'], ensure_ascii=False)
        else:
            # 对于部分权限，只返回有权限的指标值
            for value in access_data['authorized_indicators']:
                if value in access_data['value']:
                    reply_content += f"\n{value}: {access_data['value'][value]}\n"
        
        return reply_content
    
    # def build_project_articles(self, project_permissions: Dict[str, Dict]) -> List[Dict[str, str]]:
    #     """构建项目图文消息列表
        
    #     Args:
    #         project_permissions: 项目权限字典
        
    #     Returns:
    #         图文消息列表
    #     """
    #     articles = []
        
    #     if project_permissions:
    #         # 添加总览文章作为第一条
    #         articles.append({
    #             'title': '项目数据概览',
    #             'description': f'您有 {len(project_permissions)} 个可访问的项目',
    #             'picurl': 'https://via.placeholder.com/300x200?text=Project+Overview',
    #             'url': 'http://hao.cavacn.com/wechat/user/projects'
    #         })
            
    #         # 为每个项目创建一个图文消息条目
    #         for project_id, perms in project_permissions.items():
    #             # 构建指标权限描述
    #             indicators_text = ""
    #             if 'indicators' in perms:
    #                 indicator_list = []
    #                 for perm in perms['indicators']:
    #                     parts = perm.split(':')
    #                     if len(parts) >= 4 and parts[0] == 'indicators':
    #                         indicator_list.append(f"{parts[1]}:{parts[2]}")
    #                 if indicator_list:
    #                     indicators_text = "\n可访问指标: " + ", ".join(indicator_list[:3])
    #                     if len(indicator_list) > 3:
    #                         indicators_text += f" 等{len(indicator_list)}项"
    #                 else:
    #                     indicators_text = "\n暂无具体可访问的指标权限"
    #             else:
    #                 indicators_text = "\n暂无具体可访问的指标权限"
                
    #             # 构建项目链接
    #             project_url = f"http://120.26.23.199:8003/projects/{project_id}/{project_id}-AGVTaskCount.html"
                
    #             # 添加项目图文消息
    #             articles.append({
    #                 'title': f'项目 {project_id}',
    #                 'description': f'点击查看项目详情{indicators_text}',
    #                 'picurl': f'https://via.placeholder.com/300x200?text=Project+{project_id}',
    #                 'url': project_url
    #             })
        
    #     return articles
    
    # def build_project_list_reply(self, project_permissions: Dict[str, Dict]) -> str:
    #     """构建项目列表回复内容
        
    #     Args:
    #         project_permissions: 项目权限字典
        
    #     Returns:
    #         格式化后的回复内容
    #     """
    #     reply_content = '可访问项目：'
        
    #     for project_id, perms in project_permissions.items():
    #         # 构建指标权限描述
    #         reply_content += f'\n\n{project_id}\n'
    #         indicators_text = ""
    #         if 'indicators' in perms:
    #             indicator_list = []
    #             for perm in perms['indicators']:
    #                 parts = perm.split(':')
    #                 if len(parts) >= 4 and parts[0] == 'indicators':
    #                     indicator_list.append(f"{parts[1]}:{parts[2]}")
    #             if indicator_list:
    #                 indicators_text = "可查看指标:\n " + ", \n".join(indicator_list)
    #             else:
    #                 indicators_text = "\n暂无具体可访问的指标权限"
    #         else:
    #             indicators_text = "\n暂无具体可访问的指标权限"
    #         reply_content += indicators_text
        
    #     if project_permissions:
    #         reply_content += '\n请回复项目名称查看详情'
        
    #     return reply_content

    async def insert_project_data(self, data: Dict, headers: Dict={"Content-Type": "application/json"}) -> Tuple[Optional[int], Optional[Dict]]:
        """插入项目数据
        
        Args:
            project_id: 项目ID
            data: 项目数据
        
        Returns:
            (状态码, 响应数据) 或 (None, None) 如果发生异常
        """
        try:
            # 使用 DATA_SERVICE_URL 作为基础URL
            url = f"{DATA_SERVICE_URL}/api/data/insert/"
            print(f"[HTTP] POST {url}")
            print(f"[HTTP] 请求payload: {json.dumps(data, ensure_ascii=False)[:500]}")

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=data,
                    headers=headers
                ) as response:
                    # 获取响应状态
                    status_code = response.status
                    print(f"[HTTP] 响应状态码: {status_code}")

                    # 获取响应内容
                    try:
                        response_data = await response.json()
                        print(f"[HTTP] 响应内容: {json.dumps(response_data, indent=2, ensure_ascii=False)}")
                    except json.JSONDecodeError:
                        response_text = await response.text()
                        print(f"[HTTP] 响应文本(非JSON): {response_text}")
                        response_data = None

                    # 修复运算符优先级bug：原写法 response_data if status_code == 200 else response_data.get('error', None)
                    # 当 status_code != 200 且 response_data 为 None 时会抛 AttributeError，被外层 except 吞掉，静默返回 (None, None)
                    if status_code == 200:
                        return status_code, response_data
                    else:
                        err_msg = response_data.get('error', None) if isinstance(response_data, dict) else (str(response_data) if response_data is not None else None)
                        print(f"[HTTP] 插入失败 status={status_code}, error={err_msg}")
                        return status_code, err_msg

        except Exception as e:
            print(f"[HTTP] 发送数据失败: {e}")
            traceback.print_exc()
            return None, None

# 创建全局数据服务实例
data_service = DataService()