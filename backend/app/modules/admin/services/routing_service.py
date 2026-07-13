from fastapi import HTTPException, Request
from typing import Optional, Dict, Any
from datetime import datetime
from app.modules.admin.schemas_das.request_models import DataAccessRequest
from app.modules.admin.services.data_service import DataService
from app.modules.admin.services.permission_service import PermissionService
from app.modules.admin.services.permission_checker import PermissionChecker
from app.modules.admin.utils_das.request_utils import generate_request_id, get_current_timestamp
from app.modules.admin.utils_das.config import DEBUG_MODE


class BaseDataAccessHandler:
    
    async def _get_token(self, credentials: Optional) -> str:
        if DEBUG_MODE:
            print("调试模式：跳过认证检查和token获取")
            return "debug_token"
        else:
            if not credentials:
                raise HTTPException(status_code=401, detail="未提供认证凭据")
            return credentials.credentials
    
    async def _generate_response_metadata(self) -> Dict[str, Any]:
        return {
            "request_id": await generate_request_id(),
            "timestamp": await get_current_timestamp()
        }
    
    async def _handle_exception(self, e: Exception, request_id: str, timestamp: str) -> Dict[str, Any]:
        if isinstance(e, HTTPException):
            raise
        return {
            "code": 500,
            "message": f"获取数据失败: {str(e)}",
            "data": None,
            "timestamp": timestamp,
            "request_id": request_id
        }
    
    async def _check_project_permissions(self, permissions_data: Dict[str, Any], project: str) -> bool:
        project_permissions = permissions_data.get("projectPermissions", {})
        print(f"权限：{project_permissions}")
        return project in project_permissions
    
    async def _get_authorized_indicators(self, permissions_data: Dict[str, Any], project: str, tag: str, indicators: list) -> list:
        authorized_indicators = PermissionChecker.has_permissions_for_indicators(
            permissions_data, project, tag, indicators
        )
        print(f"请求指标: {indicators}")
        print(f"授权指标: {authorized_indicators}")
        return authorized_indicators


class DataAccessHandler(BaseDataAccessHandler):
    
    async def handle(self, request_data: DataAccessRequest, request: Request, credentials: Optional) -> Dict[str, Any]:
        try:
            token = await self._get_token(credentials)
            metadata = await self._generate_response_metadata()
            request_id = metadata["request_id"]
            timestamp = metadata["timestamp"]
            
            if DEBUG_MODE:
                results = DataService.get_data_for_indicators(
                    request_data.project,
                    request_data.tag,
                    request_data.indicator
                )
                return {
                    "code": 0,
                    "message": "success (调试模式)",
                    "data": results if results else None,
                    "timestamp": timestamp,
                    "request_id": request_id
                }
            
            permissions_data = await PermissionService.get_user_permissions(request, token)
            
            if not await self._check_project_permissions(permissions_data, request_data.project):
                return {
                    "code": 1,
                    "message": "项目权限不足",
                    "data": None,
                    "timestamp": timestamp,
                    "request_id": request_id
                }
            
            authorized_indicators = await self._get_authorized_indicators(
                permissions_data,
                request_data.project,
                request_data.tag,
                request_data.indicator
            )
            
            results = None
            if authorized_indicators:
                results = DataService.get_history_data_for_indicators(
                    request_data.project,
                    request_data.tag,
                    authorized_indicators,
                    request_data.start_time,
                    request_data.end_time
                )
            
            return {
                "code": 0,
                "message": "success",
                "data": results if results else None,
                "timestamp": timestamp,
                "request_id": request_id
            }
            
        except Exception as e:
            metadata = await self._generate_response_metadata()
            return await self._handle_exception(e, metadata["request_id"], metadata["timestamp"])


class DataInsertHandler(BaseDataAccessHandler):
    
    async def handle_real_time_data(self, request_data: Dict[str, Any], request: Request, credentials: Optional) -> Dict[str, Any]:
        try:
            token = await self._get_token(credentials)
            metadata = await self._generate_response_metadata()
            request_id = metadata["request_id"]
            timestamp = metadata["timestamp"]
            
            success, record_id = await self._insert_realtime_data(
                request_data.get("project"),
                request_data.get("indicator"),
                request_data.get("content"),
                request_data.get("collection_time")
            )
            
            if success:
                return {
                    "code": 0,
                    "message": "success",
                    "data": {"record_id": record_id},
                    "timestamp": timestamp,
                    "request_id": request_id
                }
            else:
                return {
                    "code": 500,
                    "message": "插入数据失败",
                    "data": None,
                    "timestamp": timestamp,
                    "request_id": request_id
                }
                
        except Exception as e:
            metadata = await self._generate_response_metadata()
            return await self._handle_exception(e, metadata["request_id"], metadata["timestamp"])
    
    async def handle_historical_data(self, request_data: Dict[str, Any], request: Request, credentials: Optional) -> Dict[str, Any]:
        try:
            token = await self._get_token(credentials)
            metadata = await self._generate_response_metadata()
            request_id = metadata["request_id"]
            timestamp = metadata["timestamp"]
            
            data = request_data.get("content", None)
            is_batch = isinstance(data, list)
            if not is_batch:
                return {
                    "code": 400,
                    "message": "历史数据插入请求必须包含数据列表",
                    "data": None,
                    "timestamp": timestamp,
                    "request_id": request_id
                }
            
            success = False
            result_id = None
            result_ids = []
            
            if is_batch:
                historical_data = []
                realtime_data = []
                project = request_data.get("project")
                indicator = request_data.get("indicator")
                collection_time = request_data.get("collection_time")
                
                for item in data:
                    item_project = item.get("project", project)
                    item_indicator = item.get("indicator", indicator)
                    item_data = item
                    item_collection_time = item.get("collection_time", collection_time)
                    item_start_time = item.get("start_time", request_data.get("start_time"))
                    item_end_time = item.get("end_time", request_data.get("end_time"))
                    
                    if not all([item_project, item_indicator, item_start_time, item_end_time]):
                        return {
                            "code": 400,
                            "message": "批量数据中缺少必要字段",
                            "data": None,
                            "timestamp": timestamp,
                            "request_id": request_id
                        }
                    
                    try:
                        if item_collection_time and item_end_time:
                            coll_time = datetime.fromisoformat(item_collection_time)
                            end_time = datetime.fromisoformat(item_end_time)
                            
                            if coll_time <= end_time:
                                historical_data.append({
                                    "project": item_project,
                                    "indicator": item_indicator,
                                    "data": item_data,
                                    "collection_time": item_collection_time,
                                    "start_time": item_start_time,
                                    "end_time": item_end_time
                                })
                                realtime_data.append({
                                    "project": item_project,
                                    "indicator": item_indicator,
                                    "data": item_data,
                                    "collection_time": item_collection_time
                                })
                            else:
                                realtime_data.append({
                                    "project": item_project,
                                    "indicator": item_indicator,
                                    "data": item_data,
                                    "collection_time": item_collection_time
                                })
                        else:
                            realtime_data.append({
                                "project": item_project,
                                "indicator": item_indicator,
                                "data": item_data,
                                "collection_time": item_collection_time
                            })
                    except (ValueError, TypeError) as e:
                        realtime_data.append({
                            "project": item_project,
                            "indicator": item_indicator,
                            "data": item_data,
                            "collection_time": item_collection_time
                        })
                
                success = True
                result_ids = []
                
                if historical_data:
                    hist_success, hist_ids = await self._insert_batch_historical_data(historical_data)
                    success = success and hist_success
                    if hist_ids:
                        result_ids.extend(hist_ids)
                
                if realtime_data:
                    for item in realtime_data:
                        rt_success, rt_id = await self._insert_realtime_data(
                            item["project"],
                            item["indicator"],
                            item["data"],
                            item["collection_time"]
                        )
                        success = success and rt_success
                        if rt_id:
                            result_ids.append(rt_id)
            
            if success:
                if is_batch:
                    return {
                        "code": 0,
                        "message": "批量插入成功",
                        "data": {"record_ids": result_ids, "total_count": len(result_ids)},
                        "timestamp": timestamp,
                        "request_id": request_id
                    }
                else:
                    return {
                        "code": 0,
                        "message": "success",
                        "data": {"record_id": result_id},
                        "timestamp": timestamp,
                        "request_id": request_id
                    }
            else:
                return {
                    "code": 500,
                    "message": "插入数据失败",
                    "data": None,
                    "timestamp": timestamp,
                    "request_id": request_id
                }
                
        except Exception as e:
            metadata = await self._generate_response_metadata()
            return await self._handle_exception(e, metadata["request_id"], metadata["timestamp"])
    
    async def _insert_realtime_data(self, project, indicator, data, collection_time):
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 调用DataService插入实时数据: 项目={project}, 指标={indicator}")
        return DataService.insert_real_data(project, indicator, data, collection_time)

    async def _insert_batch_historical_data(self, batch_data):
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 调用DataService批量插入历史数据: 共{len(batch_data)}条记录")
        return DataService.insert_batch_historical_data(batch_data)

class DataHandler(BaseDataAccessHandler):

    async def _insert_data(self, batch_data):
        try:
            metadata = await self._generate_response_metadata()
            request_id = metadata["request_id"]
            timestamp = metadata["timestamp"]
            data_project = batch_data.get("project", None)
            data_indicator = batch_data.get("indicator", None)
            data_content = batch_data.get("content", None)
            data_collection_time = batch_data.get("collection_time", None)

            if not all([data_project, data_indicator, data_content, data_collection_time]):
                return {
                    "code": 400,
                    "message": "批量数据中缺少必要字段",
                    "data": None,
                    "timestamp": timestamp,
                    "request_id": request_id
                }
            if not data_content:
                return {
                    "code": 400,
                    "message": "批量插入数据不能为空",
                    "data": None,
                    "timestamp": timestamp,
                    "request_id": request_id
                }
            if not isinstance(data_content, list):
                data_content = [data_content]
            
            collection_data = []
            for item in data_content:
                item_start_time = item.get("start_time", None)
                item_end_time = item.get("end_time", None)
                
                if not all([item_start_time, item_end_time]):
                    return {
                        "code": 400,
                        "message": "批量数据中缺少必要字段(start_time或end_time)",  
                        "data": None,
                        "timestamp": timestamp,
                        "request_id": request_id
                    }
                collection_data.append({
                    "project": data_project,
                    "indicator": data_indicator,
                    "data": item,
                    "collection_time": data_collection_time,
                    "start_time": item_start_time,
                    "end_time": item_end_time
                })

            
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 调用DataService批量插入历史数据: 共{len(batch_data)}条记录")
            success, result_ids = DataService.insert_batch_collection_data(collection_data)
            
            if success:
                return {
                    "code": 0,
                    "message": "批量插入成功",
                    "data": {"record_ids": result_ids, "total_count": len(result_ids) if result_ids else 0},
                    "timestamp": timestamp,
                    "request_id": request_id
                }
            else:
                return {
                    "code": 500,
                    "message": "插入数据失败",
                    "data": None,
                    "timestamp": timestamp,
                    "request_id": request_id
                }
                
        except Exception as e:
            metadata = await self._generate_response_metadata()
            return await self._handle_exception(e, metadata["request_id"], metadata["timestamp"])
    
    def _convert_time_format(self, request_data: DataAccessRequest):
        if not request_data.autoflag:
            return request_data
        
        latest_collection_time = DataService.get_collection_time(
            request_data.project,
            request_data.tag
        )
        if latest_collection_time:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 最新采集时间: {latest_collection_time}")
        else:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 未找到最新采集时间")
            return request_data
        
        try:
            timezone_part = ""
            if latest_collection_time.endswith('Z'):
                timezone_part = 'Z'
            elif '+' in latest_collection_time:
                timezone_part = latest_collection_time[latest_collection_time.rindex('+'):]
            elif '-' in latest_collection_time and latest_collection_time.rindex('-') > 10:
                timezone_part = latest_collection_time[latest_collection_time.rindex('-'):]
            
            if not timezone_part:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 无法从最新采集时间提取时区信息")
                return request_data
            
            if request_data.start_time:
                start_time = request_data.start_time
                if start_time.endswith('Z'):
                    start_time = start_time[:-1]
                elif '+' in start_time:
                    start_time = start_time[:start_time.rindex('+')]
                elif '-' in start_time and start_time.rindex('-') > 10:
                    start_time = start_time[:start_time.rindex('-')]
                request_data.start_time = f"{start_time}{timezone_part}"
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] start_time时区转换: {request_data.start_time}")
            
            if request_data.end_time:
                end_time = request_data.end_time
                if end_time.endswith('Z'):
                    end_time = end_time[:-1]
                elif '+' in end_time:
                    end_time = end_time[:end_time.rindex('+')]
                elif '-' in end_time and end_time.rindex('-') > 10:
                    end_time = end_time[:end_time.rindex('-')]
                request_data.end_time = f"{end_time}{timezone_part}"
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] end_time时区转换: {request_data.end_time}")
        except ValueError as e:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 时间格式转换失败: {e}")
        except Exception as e:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 时区处理失败: {e}")
        
        return request_data
    
    async def _get_data(self, request_data: DataAccessRequest):
        metadata = await self._generate_response_metadata()
        request_id = metadata["request_id"]
        timestamp = metadata["timestamp"]
        request_data = self._convert_time_format(request_data)

        results = DataService.get_collection_data_for_indicators(
            request_data.project,
            request_data.tag,
            request_data.indicator,
            request_data.start_time,
            request_data.end_time
        )
        return {
                "code": 0,
                "message": "success (调试模式)",
                "data": [results] if results else [],
                "timestamp": timestamp,
                "request_id": request_id
            }


class HistoryDataAccessHandler(BaseDataAccessHandler):
    
    async def handle(self, request_data: DataAccessRequest, request: Request, credentials: Optional) -> Dict[str, Any]:
        try:
            token = await self._get_token(credentials)
            metadata = await self._generate_response_metadata()
            request_id = metadata["request_id"]
            timestamp = metadata["timestamp"]
            
            if DEBUG_MODE:
                results = DataService.get_history_data_for_indicators(
                    request_data.project,
                    request_data.tag,
                    request_data.indicator,
                    request_data.start_time,
                    request_data.end_time
                )
                return {
                    "code": 0,
                    "message": "success (调试模式)",
                    "data": results if results else None,
                    "timestamp": timestamp,
                    "request_id": request_id
                }
            
            permissions_data = await PermissionService.get_user_permissions(request, token)
            
            if not await self._check_project_permissions(permissions_data, request_data.project):
                return {
                    "code": 1,
                    "message": "项目权限不足",
                    "data": None,
                    "timestamp": timestamp,
                    "request_id": request_id
                }
            
            authorized_indicators = await self._get_authorized_indicators(
                permissions_data,
                request_data.project,
                request_data.tag,
                request_data.indicator
            )
            
            results = None
            if authorized_indicators:
                results = DataService.get_history_data_for_indicators(
                        request_data.project,
                        request_data.tag,
                        authorized_indicators
                    )
            
            return {
                "code": 0,
                "message": "success",
                "data": results if results else None,
                "timestamp": timestamp,
                "request_id": request_id
            }
            
        except Exception as e:
            metadata = await self._generate_response_metadata()
            return await self._handle_exception(e, metadata["request_id"], metadata["timestamp"])