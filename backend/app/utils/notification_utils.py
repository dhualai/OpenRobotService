from datetime import datetime, timedelta
import yaml
import os
import random
import string
import json
import uuid
import time
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, List, Dict, Any
from app.core.config import settings
from app.core.user_identity import to_usernames

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="notification")

class NotificationUtils:
    _mqtt_client = None
    _mqtt_connected = False
    
    @classmethod
    def _get_mqtt_broker(cls):
        return settings.MQTT_BROKER
    
    @classmethod
    def _get_mqtt_port(cls):
        return settings.MQTT_PORT
    
    @classmethod
    def _get_mqtt_user(cls):
        return settings.MQTT_USER
    
    @classmethod
    def _get_mqtt_password(cls):
        return settings.MQTT_PASSWORD
    
    @classmethod
    def _get_mqtt_topic_request(cls):
        return settings.MQTT_TOPIC_WORKORDER_REQUEST
    
    @classmethod
    def _get_mqtt_topic_response(cls):
        return settings.MQTT_TOPIC_WORKORDER_RESPONSE
    
    STATUS_CHANGE = 3
    NEW_TICKET = 5
    CUIBAN_TICKET = 1
    DAYILY_TICKET = 2
    YUQU_TICKET = 4
    YUQU_TICKET_STATUS_CHANGE = 6
    REASSIGN_TICKET = 7
    MENTION_TICKET = 8
    OVERDUE_WARNING_TICKET = 9
    TICKET_HOST = "https://usp.ep-zl.com/p/app/tasks"

    @classmethod
    def _initialize_mqtt(cls):
        if cls._mqtt_client is not None:
            return
        
        try:
            import paho.mqtt.client as mqtt
            
            def on_connect(client, userdata, flags, rc, properties=None):
                if rc == 0:
                    client.subscribe(cls._get_mqtt_topic_response())
                    cls._mqtt_connected = True
                    logger.info("MQTT连接成功，已订阅标题简化响应主题")
                else:
                    cls._mqtt_connected = False
                    logger.error(f"MQTT连接失败，错误码: {rc}")
            
            cls._mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            cls._mqtt_client.username_pw_set(cls._get_mqtt_user(), cls._get_mqtt_password())
            cls._mqtt_client.on_connect = on_connect
            
            cls._mqtt_client.connect(cls._get_mqtt_broker(), cls._get_mqtt_port(), 60)
            cls._mqtt_client.loop_start()
            time.sleep(1)
        except Exception as e:
            logger.error(f"初始化MQTT客户端失败: {str(e)}")
            cls._mqtt_client = None
            cls._mqtt_connected = False

    @classmethod
    async def simplify_title(cls, title: str, max_length: int = 20) -> str:
        def _truncate_title(t: str) -> str:
            max_chars = 20
            if not t:
                return t
            return t[:max_chars] if len(t) > max_chars else t
        
        if not title:
            return title
        
        if len(title) <= max_length:
            return title
        
        try:
            cls._initialize_mqtt()
            
            if not cls._mqtt_client or not cls._mqtt_connected:
                logger.warning("MQTT客户端未初始化或未连接，使用保底截断")
                return _truncate_title(title)
            
            request_id = str(uuid.uuid4())
            
            response_data = None
            response_received = False
            
            def on_message(client, userdata, msg, properties=None):
                nonlocal response_data, response_received
                try:
                    message_content = msg.payload.decode()
                    response_msg = json.loads(message_content)
                    
                    if response_msg.get("request_id") == request_id:
                        response_data = response_msg
                        response_received = True
                except Exception as e:
                    logger.error(f"处理MQTT响应异常: {str(e)}")
                    response_received = True
            
            cls._mqtt_client.on_message = on_message
            
            mqtt_message = {
                "request_id": request_id,
                "title": title,
                "max_lens": max_length
            }
            cls._mqtt_client.publish(cls._get_mqtt_topic_request(), json.dumps(mqtt_message, ensure_ascii=False))
            
            start_time = time.time()
            while not response_received and time.time() - start_time < 20:
                time.sleep(0.1)
            
            if response_data and "simplified" in response_data:
                simplified = response_data["simplified"]
                if not simplified:
                    logger.warning("MQTT返回空字符串，使用原始标题")
                    return _truncate_title(title)
                if len(simplified) > 20:
                    simplified = _truncate_title(simplified)
                return simplified
            else:
                logger.warning("未收到MQTT标题简化响应，使用保底截断")
                return _truncate_title(title)
                
        except Exception as e:
            logger.error(f"MQTT标题简化请求异常: {str(e)}")
            return _truncate_title(title)

    @staticmethod
    async def send_notification(payload, token):
        try:
            from app.wechat.api.message import send_notification_core
            return await send_notification_core(payload, token)
        except Exception as e:
            logger.error(f"发送通知失败：{str(e)}")
            return {
                "code": 500,
                "message": f"请求失败: {str(e)}",
                "data": {
                    "status": "failed",
                    "error": str(e)
                }
            }

    @staticmethod
    def instantiate_template(template_id: int, *params, **args) -> Dict[str, Any]:
        template_file = os.path.join(os.path.dirname(__file__), 'template.yaml')
        with open(template_file, 'r', encoding='utf-8') as f:
            templates = yaml.safe_load(f)['template']
        
        template = templates.get(template_id)
        if not template:
            raise ValueError(f"Template with id {template_id} not found")
        
        data = {}
        template_keys = list(template['data'].keys())
        
        for i, param in enumerate(params):
            if i < len(template_keys):
                key = template_keys[i]
                data[key] = {
                    "value": param
                }
        
        for key, _ in template['data'].items():
            if key in args:
                data[key] = {
                    "value": args[key]
                }
        
        def generate_message_id():
            return ''.join(random.choices(string.ascii_letters + string.digits, k=16))
        
        payload = {
            "msg_type": "template",
            "message_id": generate_message_id(),
            "template": {
                "id": template['id'],
                "data": data,
                "url": args.get('url', "https://usp.ep-zl.com/p/app/tasks")
            },
            "at": {
                "user_names": list(set(to_usernames(args.get('user_names', [])))),
                "is_all": args.get('is_all', False)
            }
        }
        
        return payload

    @staticmethod
    async def send_ticket_update_notification(
        ticket_id: int,
        title: str,
        project_name: str,
        update_content: str,
        operator: str,
        user_names: List[str] = None,
        token: Optional[str] = None,
    ) -> Dict[str, Any]:
        def _send():
            try:
                # 枚举值 → 中文映射（兼容枚举全名和 .value 两种格式）
                enum_value_mapping = {
                    'TicketType.PROBLEM': '问题',
                    'TicketType.FEATURE': '功能请求',
                    'TicketType.BUG': 'Bug 报告',
                    'TicketType.SUPPORT': '技术支持',
                    'TicketType.OTHER': '其他',
                    'problem': '问题',
                    'feature': '功能请求',
                    'bug': 'Bug 报告',
                    'support': '技术支持',
                    'other': '其他',
                    'TicketStatus.NEW': '新建',
                    'TicketStatus.IN_PROGRESS': '处理中',
                    'TicketStatus.PENDING': '待处理',
                    'TicketStatus.RESOLVED': '已解决',
                    'TicketStatus.CANCELED': '已取消',
                    'TicketStatus.CLOSED': '已关闭',
                    'new': '新建',
                    'in_progress': '处理中',
                    'pending': '待处理',
                    'resolved': '已解决',
                    'canceled': '已取消',
                    'closed': '已关闭',
                    'TicketPriority.LOW': '低',
                    'TicketPriority.MEDIUM': '中',
                    'TicketPriority.HIGH': '高',
                    'TicketPriority.URGENT': '紧急',
                    'low': '低',
                    'medium': '中',
                    'high': '高',
                    'urgent': '紧急',
                }

                # 可读的字段中文名
                field_display_names = {
                    'status': '工单状态',
                    'assigned_to': '处理人',
                    'customer': '客户',
                    'priority': '优先级',
                    'type': '工单类型',
                    'title': '工单标题',
                    'description': '工单描述',
                    'project_name': '所属项目',
                    'deadline_at': '截止时间',
                    'mentioned': '被提及',
                }

                # 1. 解析 update_content → {field: raw_value}
                field_values: Dict[str, str] = {}
                for line in update_content.split('\n'):
                    if ':' in line:
                        k, v = line.split(':', 1)
                        field_values[k.strip()] = v.strip()

                updated_fields = list(field_values.keys())

                # 2. 根据 status 字段决定「工单状态」展示值
                if 'status' in field_values:
                    raw_status = field_values['status']
                    link_title = enum_value_mapping.get(raw_status, raw_status)
                else:
                    # 非状态类更新：用核心字段概览
                    if 'priority' in field_values:
                        link_title = '优先级调整'
                    elif 'assigned_to' in field_values:
                        link_title = '重新派单'
                    elif 'customer' in field_values:
                        link_title = '客户变更'
                    elif len(updated_fields) == 0:
                        link_title = '-'
                    else:
                        link_title = '信息更新'

                # 3. 生成「变更原因」——基于具体变更字段动态描述
                status_only = (updated_fields == ['status'])
                other_fields = [f for f in updated_fields if f != 'status']

                if status_only and 'status' in field_values:
                    status_label = enum_value_mapping.get(field_values['status'], field_values['status'])
                    reason = f"状态变更为「{status_label}」"
                elif 'status' in field_values and other_fields:
                    status_label = enum_value_mapping.get(field_values['status'], field_values['status'])
                    other_labels = [field_display_names.get(f, f) for f in other_fields]
                    reason = f"状态变更为「{status_label}」，同步更新了{len(other_fields)}项：{'、'.join(other_labels)}"
                elif other_fields:
                    labels = [field_display_names.get(f, f) for f in updated_fields]
                    reason = f"{'、'.join(labels)}等{len(updated_fields)}项信息已更新"
                else:
                    reason = "工单信息已更新"

                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    processed_project_name = loop.run_until_complete(
                        NotificationUtils.simplify_title(project_name)
                    )
                finally:
                    loop.close()

                payload = NotificationUtils.instantiate_template(NotificationUtils.STATUS_CHANGE,
                                                                 title, processed_project_name or "无", link_title, reason, operator,
                                                                 user_names=user_names, url=NotificationUtils.TICKET_HOST + f"/{ticket_id}")
                loop2 = asyncio.new_event_loop()
                asyncio.set_event_loop(loop2)
                try:
                    loop2.run_until_complete(NotificationUtils.send_notification(payload, token))
                finally:
                    loop2.close()
            except Exception as e:
                logger.error(f"发送通知失败：{str(e)}")
        
        asyncio.get_event_loop().run_in_executor(_executor, _send)
        return {"code": 200, "message": "通知已发送"}

    @staticmethod
    async def send_ticket_create_notification(
        ticket_id: int,
        title: str,
        project_name: str,
        operator: str,
        deadline_at: Optional[datetime] = None,
        user_names: List[str] = None,
        token: Optional[str] = None,
    ) -> Dict[str, Any]:
        def _send():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    processed_title = loop.run_until_complete(
                        NotificationUtils.simplify_title(title)
                    )
                    processed_project_name = loop.run_until_complete(
                        NotificationUtils.simplify_title(project_name)
                    )
                finally:
                    loop.close()

                deadline_str = deadline_at.strftime('%Y-%m-%d %H:%M:%S') if deadline_at else (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
                payload = NotificationUtils.instantiate_template(NotificationUtils.NEW_TICKET,
                                                                 ticket_id, processed_project_name, processed_title, operator, deadline_str,
                                                                 user_names=user_names, url=NotificationUtils.TICKET_HOST + f"/{ticket_id}")
                loop2 = asyncio.new_event_loop()
                asyncio.set_event_loop(loop2)
                try:
                    loop2.run_until_complete(NotificationUtils.send_notification(payload, token))
                finally:
                    loop2.close()
            except Exception as e:
                logger.error(f"发送通知失败：{str(e)}")
        
        asyncio.get_event_loop().run_in_executor(_executor, _send)
        return {"code": 200, "message": "通知已发送"}

    @staticmethod
    async def send_ticket_reassign_notification(
        ticket_id: int,
        title: str,
        project_name: str,
        operator: str,
        new_assignee: str,
        deadline_at: Optional[datetime] = None,
        user_names: List[str] = None,
        token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """工单转派提醒（模板 7）。

        通知对象：工单创建人 + 新被指派人。
        模板字段：[工单名称, 项目名称, 转派人, 新负责人, 要求完成时间]
        """
        def _send():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    processed_title = loop.run_until_complete(
                        NotificationUtils.simplify_title(title)
                    )
                    processed_project_name = loop.run_until_complete(
                        NotificationUtils.simplify_title(project_name)
                    )
                finally:
                    loop.close()

                deadline_str = deadline_at.strftime('%Y-%m-%d %H:%M:%S') if deadline_at else (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
                payload = NotificationUtils.instantiate_template(NotificationUtils.REASSIGN_TICKET,
                                                                 processed_title, processed_project_name, operator, new_assignee, deadline_str,
                                                                 user_names=user_names, url=NotificationUtils.TICKET_HOST + f"/{ticket_id}")
                loop2 = asyncio.new_event_loop()
                asyncio.set_event_loop(loop2)
                try:
                    loop2.run_until_complete(NotificationUtils.send_notification(payload, token))
                finally:
                    loop2.close()
            except Exception as e:
                logger.error(f"发送转派通知失败：{str(e)}")

        asyncio.get_event_loop().run_in_executor(_executor, _send)
        return {"code": 200, "message": "通知已发送"}

    @staticmethod
    async def send_ticket_cuiban_notification(
        ticket_id: int = 0,
        notify_type: int = 0,
        project_name: str = "",
        assigned_name: str = "",
        deadline_at: datetime = None,
        create_at: datetime = None,
        user_names: List[str] = None,
        token: Optional[str] = None,
        ticket_name: Optional[str] = None,
        extr: dict = None,
        yuqi_day: str = ""
    ) -> Dict[str, Any]:
        def _send():
            try:
                payload = {}
                if notify_type == 2:
                    payload = NotificationUtils.instantiate_template(NotificationUtils.DAYILY_TICKET,
                                                                     extr.get('pending_count', 0), extr.get('near_overdue_count', 0),
                                                                     extr.get('overdue_count', 0), user_names=user_names)
                elif notify_type == 6:
                    # 工单逾期提醒, [工单名称, 项目名称, 逾期天数, 派单时间, 逾期时间]
                    deadline_str = deadline_at.strftime('%Y-%m-%d %H:%M:%S') if deadline_at else ''
                    create_str = create_at.strftime('%Y-%m-%d %H:%M:%S') if create_at else ''

                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        processed_ticket_name = loop.run_until_complete(
                            NotificationUtils.simplify_title(ticket_name)
                        )
                        processed_project_name = loop.run_until_complete(
                            NotificationUtils.simplify_title(project_name)
                        )
                    finally:
                        loop.close()

                    payload = NotificationUtils.instantiate_template(NotificationUtils.YUQU_TICKET_STATUS_CHANGE,
                                                                     processed_ticket_name, processed_project_name, yuqi_day, create_str, deadline_str,
                                                                     user_names=user_names, url=NotificationUtils.TICKET_HOST + f"/{ticket_id}")
                elif notify_type == 9:
                    # 工单超时预警提醒, [工单名称, 项目名称, 处理人, 提交时间, 截止时间]
                    deadline_str = deadline_at.strftime('%Y-%m-%d %H:%M:%S') if deadline_at else ''
                    create_str = create_at.strftime('%Y-%m-%d %H:%M:%S') if create_at else ''

                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        processed_ticket_name = loop.run_until_complete(
                            NotificationUtils.simplify_title(ticket_name)
                        )
                        processed_project_name = loop.run_until_complete(
                            NotificationUtils.simplify_title(project_name)
                        )
                    finally:
                        loop.close()

                    payload = NotificationUtils.instantiate_template(NotificationUtils.OVERDUE_WARNING_TICKET,
                                                                     processed_ticket_name, processed_project_name, assigned_name, create_str, deadline_str,
                                                                     user_names=user_names, url=NotificationUtils.TICKET_HOST + f"/{ticket_id}")
                else:
                    reason = "工单长时间未更新"
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        processed_project_name = loop.run_until_complete(
                            NotificationUtils.simplify_title(project_name)
                        )
                        processed_ticket_name = loop.run_until_complete(
                            NotificationUtils.simplify_title(ticket_name)
                        )
                    finally:
                        loop.close()
                    
                    deadline_str = deadline_at.strftime('%Y-%m-%d %H:%M:%S') if deadline_at else ''
                    create_str = create_at.strftime('%Y-%m-%d %H:%M:%S') if create_at else ''

                    if notify_type == 1:
                        payload = NotificationUtils.instantiate_template(NotificationUtils.CUIBAN_TICKET,
                                                                         ticket_id, processed_project_name, assigned_name, reason, deadline_str,
                                                                         user_names=user_names, url=NotificationUtils.TICKET_HOST + f"/{ticket_id}")
                    elif notify_type == 3:
                        payload = NotificationUtils.instantiate_template(NotificationUtils.YUQU_TICKET, ticket_id,
                                                                         processed_project_name, processed_ticket_name, create_str, deadline_str,
                                                                         user_names=user_names, url=NotificationUtils.TICKET_HOST + f"/{ticket_id}")
                
                loop2 = asyncio.new_event_loop()
                asyncio.set_event_loop(loop2)
                try:
                    loop2.run_until_complete(NotificationUtils.send_notification(payload, token))
                finally:
                    loop2.close()
            except Exception as e:
                logger.error(f"发送通知失败：{str(e)}")
        
        asyncio.get_event_loop().run_in_executor(_executor, _send)
        return {"code": 200, "message": "通知已发送"}