from datetime import datetime, timedelta, timezone
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

TZ_SHANGHAI = timezone(timedelta(hours=8))


def _format_shanghai(dt: Optional[datetime]) -> str:
    """格式化为东八区时间字符串。

    DB 中 deadline_at / created_at 为 naive UTC（见 backend convert_to_shanghai_time），
    naive 视为 UTC 后转 +8；aware datetime 直接转 +8。None 返回空串。
    """
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ_SHANGHAI).strftime("%Y-%m-%d %H:%M:%S")


def _format_local(dt: Optional[datetime], tz_name: Optional[str] = None) -> str:
    """按**接收人时区**格式化时间；未指定或非法时区回退东八区。

    多时区场景下模板里的「截止 17:00」必须是收件人自己的本地时间，
    否则海外用户会误读。使用 zoneinfo 而非固定 +8，夏令时自动处理。
    """
    if dt is None:
        return ""
    tz = TZ_SHANGHAI
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = TZ_SHANGHAI
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")

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
    # 模板 2 = 工单处理结果通知 [工单编号, 工单名称, 受理时间, 处理人, 处理结果]
    # 注意：语义是「处理结果」，不是每日统计；当前无调用方，接入时需按上面 5 个字段传参
    TICKET_RESULT = 2
    YUQU_TICKET_STATUS_CHANGE = 6
    REASSIGN_TICKET = 7
    MENTION_TICKET = 8
    OVERDUE_WARNING_TICKET = 9
    ROBOT_ALARM = 10
    STEP_NOTIFICATION = 11  # 步骤协商通知模板
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
    def _resolve_mobiles(user_names: List[str]) -> List[str]:
        """根据 username/id 列表查 users 表取手机号。

        用于企业微信群机器人 mentioned_mobile_list @ 指定成员。
        查不到手机号的用户会被跳过。
        """
        mobiles: List[str] = []
        seen = set()
        try:
            from app.core.database import db_manager
        except Exception as e:
            logger.error(f"企业微信通知：导入 db_manager 失败: {str(e)}")
            return mobiles

        for uname in user_names or []:
            try:
                user = db_manager.get_user(uname) or db_manager.get_user_by_id(uname)
            except Exception as e:
                logger.error(f"企业微信通知：查询用户 {uname} 失败: {str(e)}")
                user = None
            phone = (user or {}).get("phone")
            if phone and phone not in seen:
                seen.add(phone)
                mobiles.append(phone)
        return mobiles

    @staticmethod
    def send_wechat_work_notification(
        content: str,
        user_names: List[str] = None,
        is_all: bool = False,
    ) -> Dict[str, Any]:
        """通过企业微信群机器人 webhook 发送文本消息。

        参考 wechat_api.md：向 webhook 发起 HTTP POST，msgtype=text，
        通过 mentioned_mobile_list 指定被 @ 的成员（手机号列表）。
        - is_all=True 时 mentioned_mobile_list=["@all"]，@ 群内所有人；
        - 否则按 user_names 查 users.phone 填充 mentioned_mobile_list。

        webhook 未配置（WECHAT_WORK_WEBHOOK_URL 为空）时跳过推送。
        """
        webhook_url = getattr(settings, "WECHAT_WORK_WEBHOOK_URL", "")
        if not webhook_url:
            logger.warning("企业微信群机器人 webhook 未配置，跳过推送")
            return {
                "code": 500,
                "message": "企业微信 webhook 未配置",
                "data": {"status": "failed"},
            }

        if is_all:
            mentioned_mobile_list = ["@all"]
            resolved_mobiles: List[str] = []
        else:
            resolved_mobiles = NotificationUtils._resolve_mobiles(to_usernames(user_names))
            mentioned_mobile_list = resolved_mobiles

        # content 最长不超过 2048 个字节（utf8）
        payload = {
            "msgtype": "text",
            "text": {
                "content": content,
                "mentioned_mobile_list": mentioned_mobile_list,
            },
        }

        try:
            import requests
            resp = requests.post(webhook_url, json=payload, timeout=5)
            result = resp.json()
        except Exception as e:
            logger.error(f"企业微信通知请求异常: {str(e)}")
            return {
                "code": 500,
                "message": f"请求失败: {str(e)}",
                "data": {"status": "failed", "error": str(e)},
            }

        if result.get("errcode", 0) == 0:
            logger.info(f"企业微信通知已发送，@ 手机号: {resolved_mobiles}")
            return {
                "code": 200,
                "message": "企业微信通知已发送",
                "data": {
                    "status": "success",
                    "mentioned_mobile_list": mentioned_mobile_list,
                },
            }

        errcode = result.get("errcode")
        errmsg = result.get("errmsg")
        logger.error(f"企业微信通知发送失败: errcode={errcode}, errmsg={errmsg}")
        return {
            "code": 500,
            "message": f"发送失败: {errmsg}",
            "data": {"status": "failed", "errcode": errcode, "errmsg": errmsg},
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
        reason_suffix: str = '',
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
                    'TicketStatus.NEW': '待处理',
                    'TicketStatus.IN_PROGRESS': '处理中',
                    'TicketStatus.PENDING_REQUESTED': '暂停请求中',
                    'TicketStatus.PENDING': '已挂起',
                    'TicketStatus.UNSOLVABLE_REQUESTED': '无法解决确认中',
                    'TicketStatus.UNSOLVABLE': '无法解决',
                    'TicketStatus.RESOLVED': '已解决',
                    'TicketStatus.CANCELED': '已取消',
                    'TicketStatus.CLOSED': '已关闭',
                    'TicketStatus.ARCHIVING': '知识沉淀中',
                    'TicketStatus.ARCHIVED': '已沉淀',
                    'new': '待处理',
                    'in_progress': '处理中',
                    'pending_requested': '暂停请求中',
                    'pending': '已挂起',
                    'unsolvable_requested': '无法解决确认中',
                    'unsolvable': '无法解决',
                    'resolved': '已解决',
                    'canceled': '已取消',
                    'closed': '已关闭',
                    'archiving': '知识沉淀中',
                    'archived': '已沉淀',
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

                if reason_suffix:
                    reason = f"{reason}{reason_suffix}"

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

                deadline_str = _format_shanghai(deadline_at) or _format_shanghai(datetime.now(timezone.utc) + timedelta(days=7))
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
    async def send_proxy_relation_notification(
        ticket_id: int,
        title: str,
        project_name: str,
        agent_name: str,
        principal_name: str,
        action: str,
        reason: str = "",
        user_names: List[str] = None,
        token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """代他人提单关系通知（一期**复用现有模板 3 / 5**，零公众号审核）。

        action 语义（与设计文档 §4.2 通知矩阵对齐）：
          - ``created``   代提成功 → 通知**被代理人**，模板 5，发起人写「张三（代你提交）」
          - ``acked``     被代理人确认跟进 → 通知**代理人**，模板 3
          - ``declined``  被代理人拒绝 → 通知**代理人**，模板 3（带原因）

        模板字段长度硬约束：thing 类 ≤ 20 字，统一经 simplify_title 截断。
        """
        action = (action or "").strip().lower()

        def _send():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    processed_title = loop.run_until_complete(
                        NotificationUtils.simplify_title(title)
                    )
                    processed_project = loop.run_until_complete(
                        NotificationUtils.simplify_title(project_name or "")
                    )
                    # 代理人姓名 + 后缀需整体 ≤20 字
                    operator_display = loop.run_until_complete(
                        NotificationUtils.simplify_title(
                            f"{agent_name}（代你提交）" if action == "created" else agent_name
                        )
                    )
                finally:
                    loop.close()

                url = NotificationUtils.TICKET_HOST + f"/{ticket_id}"

                if action == "created":
                    # 模板 5：新建工单 [工单编号, 项目名称, 工单标题, 发起人, 工单截止时间]
                    deadline_str = _format_shanghai(
                        datetime.now(timezone.utc) + timedelta(days=7)
                    )
                    payload = NotificationUtils.instantiate_template(
                        NotificationUtils.NEW_TICKET,
                        ticket_id,
                        processed_project or "无",
                        processed_title,
                        operator_display,
                        deadline_str,
                        user_names=user_names,
                        url=url,
                    )
                else:
                    # 模板 3：工单状态变更 [工单名称, 项目名称, 工单状态, 变更原因, 处理人]
                    if action == "acked":
                        link_title = "已确认跟进"
                        reason = f"{principal_name} 已确认跟进本工单"
                    else:
                        link_title = "与本单无关"
                        reason = f"{principal_name} 表示与本单无关：{reason or '未填原因'}"
                    reason = reason if len(reason) <= 20 else reason[:20]
                    payload = NotificationUtils.instantiate_template(
                        NotificationUtils.STATUS_CHANGE,
                        processed_title,
                        processed_project or "无",
                        link_title,
                        reason,
                        agent_name,
                        user_names=user_names,
                        url=url,
                    )

                loop2 = asyncio.new_event_loop()
                asyncio.set_event_loop(loop2)
                try:
                    loop2.run_until_complete(NotificationUtils.send_notification(payload, token))
                finally:
                    loop2.close()
            except Exception as e:
                logger.error(f"发送代提关系通知失败 ticket_id={ticket_id} action={action}: {str(e)}")

        asyncio.get_event_loop().run_in_executor(_executor, _send)
        return {"code": 200, "message": "通知已发送"}

    @staticmethod
    async def send_robot_alarm_notification(
        robot_type: str,
        robot_id: str,
        content: str,
        level: str,
        start_time: datetime,
        user_names: List[str] = None,
        token: Optional[str] = None,
        project_code: str = "",
    ) -> Dict[str, Any]:
        """设备报警提醒（模板 10）。

        供内部其他后端服务直接调用：调用方传入报警字段，本方法按
        template.yaml 模板 10 组装后向 user_names 列表发送微信模板消息。
        模板字段顺序：[报警机型, 设备编号, 报警原因, 告警级别, 告警时间]
        project_code 不入模板，仅写入日志用于追溯项目来源。
        """
        def _send():
            try:
                start_time_str = _format_shanghai(start_time)
                payload = NotificationUtils.instantiate_template(
                    NotificationUtils.ROBOT_ALARM,
                    robot_type, robot_id, content, level, start_time_str,
                    user_names=user_names,
                )
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(NotificationUtils.send_notification(payload, token))
                finally:
                    loop.close()
            except Exception as e:
                logger.error(f"发送设备报警通知失败：{str(e)}")

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

                deadline_str = _format_shanghai(deadline_at) or _format_shanghai(datetime.now(timezone.utc) + timedelta(days=7))
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
        yuqi_day: str = "",
        wait: bool = False,
        tz_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """发送工单催办/逾期/预警通知。

        wait=False（默认，兼容既有调用）：提交线程池后立即返回，不阻塞业务请求。
        wait=True：等待微信返回，返回体即 ``send_notification_core`` 的原始结果
        （``data.recipients[].status`` 为 delivered/failed），供通知 Outbox 回写真实送达状态。
        """
        def _send() -> Dict[str, Any]:
            try:
                payload = {}
                if notify_type == 6:
                    # 工单逾期提醒, [工单名称, 项目名称, 逾期天数, 派单时间, 逾期时间]
                    deadline_str = _format_local(deadline_at, tz_name)
                    create_str = _format_local(create_at, tz_name)

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
                    deadline_str = _format_local(deadline_at, tz_name)
                    create_str = _format_local(create_at, tz_name)

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
                    # 兜底分支只处理人工催办（notify_type=1）。notify_type=3 用的模板 4 已从
                    # template.yaml 移除，其余类型没有对应模板，一律跳过，避免把空 payload 发给微信
                    if notify_type != 1:
                        logger.warning(
                            f"跳过无对应模板的通知 notify_type={notify_type} ticket={ticket_id}"
                        )
                        return {"code": 400, "message": f"不支持的通知类型: {notify_type}",
                                "data": {"recipients": []}}

                    reason = "工单长时间未更新"
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        processed_ticket_name = loop.run_until_complete(
                            NotificationUtils.simplify_title(ticket_name)
                        )
                    finally:
                        loop.close()
                    
                    deadline_str = _format_local(deadline_at, tz_name)
                    create_str = _format_local(create_at, tz_name)

                    # 模板 1 字段顺序固定（见 template.yaml 注释）：
                    # [工单名称, 提报时间, 催办原因, 催办方, 截止时间]
                    # const13 是枚举字段，只能取「工单长时间未更新」/「紧急工单」，
                    # 此前把处理人姓名填进去会被微信判为非法数据，整条消息发送失败
                    payload = NotificationUtils.instantiate_template(NotificationUtils.CUIBAN_TICKET,
                                                                     processed_ticket_name, create_str, reason, assigned_name, deadline_str,
                                                                     user_names=user_names, url=NotificationUtils.TICKET_HOST + f"/{ticket_id}")
                
                loop2 = asyncio.new_event_loop()
                asyncio.set_event_loop(loop2)
                try:
                    return loop2.run_until_complete(
                        NotificationUtils.send_notification(payload, token)
                    ) or {}
                finally:
                    loop2.close()
            except Exception as e:
                logger.error(f"发送通知失败：{str(e)}")
                return {"code": 500, "message": str(e), "data": {"recipients": []}}

        if wait:
            # 同步等待微信返回：Outbox 需要真实送达结果来决定 sent / 重试 / failed
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(_executor, _send)

        asyncio.get_event_loop().run_in_executor(_executor, _send)
        return {"code": 200, "message": "通知已提交发送"}

    @staticmethod
    async def send_step_notification(
        ticket_id: int,
        title: str,
        status_label: str,
        action_detail: str,
        user_names: List[str] = None,
        token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """步骤协商通知（模板 11）。

        模板字段顺序：[工单标题(thing33), 工单状态(phrase15), 处理措施(thing13)]

        用于 negotiate-step / respond / complete-step / set-step-time / reopen-step
        等阶段性处理接口，通知协商对方侧"轮到你操作了"。
        """
        def _send():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    processed_title = loop.run_until_complete(
                        NotificationUtils.simplify_title(title)
                    )
                    processed_status = loop.run_until_complete(
                        NotificationUtils.simplify_title(status_label)
                    )
                    processed_action = loop.run_until_complete(
                        NotificationUtils.simplify_title(action_detail)
                    )
                finally:
                    loop.close()

                payload = NotificationUtils.instantiate_template(
                    NotificationUtils.STEP_NOTIFICATION,
                    processed_title,
                    processed_status,
                    processed_action,
                    user_names=user_names,
                    url=NotificationUtils.TICKET_HOST + f"/{ticket_id}",
                )
                loop2 = asyncio.new_event_loop()
                asyncio.set_event_loop(loop2)
                try:
                    loop2.run_until_complete(NotificationUtils.send_notification(payload, token))
                finally:
                    loop2.close()
            except Exception as e:
                logger.error(f"发送步骤协商通知失败 ticket_id={ticket_id}: {str(e)}")

        asyncio.get_event_loop().run_in_executor(_executor, _send)
        return {"code": 200, "message": "步骤协商通知已发送"}
