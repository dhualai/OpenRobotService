#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据接入服务 - MQTT订阅并转发至DAS数据插入接口
订阅主题: data/1.0/EP/+/+
接收到消息后调用 data_service.insert_project_data → HTTP POST /api/data/insert/ 持久化
"""

import paho.mqtt.client as mqtt
import json
import time
from datetime import datetime
import threading
import sys
import asyncio
import traceback
from data_service import data_service
from db import SessionLocal, Project

# MQTT服务器配置
MQTT_CONFIG = {
    'broker': '125.122.97.107',      # MQTT服务器地址
    'port': 8084,              # MQTT端口
    'username': 'test',       # 用户名
    'password': 'qazokm1029.',    # 密码
    'keepalive': 60                 # 保活时间
}

# 要订阅的主题(通配所有项目所有指标)
SUBSCRIBE_TOPIC = 'data/1.0/EP/+/+'


# 项目标识映射表: MQTT topic中的project段(=project.system_id) → 系统内project编号(project.id/code)
# 未在映射表中的project将被丢弃，不入库。
# 映射不再硬编码，改为从数据库 project 表动态加载（模型定义见同目录 db.py 的 Project）：
#   key   = project.system_id（MQTT/外部系统标识，如 AJNQ）
#   value = project.id（系统内项目编号，如 24；与 code 一致）
# 仅要求 system_id 非空；内存缓存 + 定时刷新，DB 不可用时沿用上次缓存。
PROJECT_MAPPING_REFRESH_SECONDS = 300
_project_mapping = {}
_project_mapping_loaded_at = 0.0


def load_project_mapping(force: bool = False) -> dict:
    """从 project 表加载 {system_id: project_id} 映射。

    - 仅要求 system_id 非空（不过滤 status）
    - 缓存 TTL 为 PROJECT_MAPPING_REFRESH_SECONDS；force=True 强制刷新（如 MQTT 重连后）
    - 查询失败时保留旧缓存并返回，保证 DB 短暂不可用不影响消息转发
    """
    global _project_mapping, _project_mapping_loaded_at
    if not force and _project_mapping and (time.time() - _project_mapping_loaded_at < PROJECT_MAPPING_REFRESH_SECONDS):
        return _project_mapping
    try:
        db = SessionLocal()
        try:
            rows = db.query(
                Project.system_id, Project.id
            ).filter(Project.system_id.isnot(None)).all()
        finally:
            db.close()

        mapping = {}
        for system_id, project_id in rows:
            if not system_id or not project_id:
                continue
            key = str(system_id).strip()
            if not key:
                continue
            value = str(project_id).strip()
            if key in mapping and mapping[key] != value:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [WARN] system_id={key} "
                      f"对应多个项目编号({mapping[key]} / {value})，以后者为准")
            mapping[key] = value

        _project_mapping = mapping
        _project_mapping_loaded_at = time.time()
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 项目映射加载成功: "
              f"共{len(mapping)}条")
    except Exception as e:
        # DB 不可用时沿用旧缓存；重置计时避免每条消息都打 DB
        _project_mapping_loaded_at = time.time()
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [WARN] 加载项目映射失败，"
              f"沿用上次缓存({len(_project_mapping)}条): {e}")
    return _project_mapping


class MQTTSubscriber:
    """MQTT订阅者类 - 订阅消息并转发至DAS接口"""

    def __init__(self):
        """初始化MQTT订阅者"""
        self.client = mqtt.Client(client_id=f'data_access_service_{int(time.time())}')
        self.client.username_pw_set(MQTT_CONFIG['username'], MQTT_CONFIG['password'])
        self.setup_callbacks()
        self.running = False
        self.message_count = 0
        self.success_count = 0
        self.fail_count = 0
        self.last_reconnect_time = 0

    def setup_callbacks(self):
        """设置MQTT回调函数"""
        def on_connect(client, userdata, flags, rc):
            if rc == 0:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] MQTT连接成功")
                client.subscribe(SUBSCRIBE_TOPIC, qos=1)
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 已订阅主题: {SUBSCRIBE_TOPIC}")
                self.last_reconnect_time = time.time()
                # 连接/重连后强制刷新项目映射
                load_project_mapping(force=True)
            else:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] MQTT连接失败，返回码: {rc}")

        def on_message(client, userdata, msg):
            ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            try:
                # [诊断日志] 消息入口
                payload = msg.payload.decode('utf-8')
                self.message_count += 1
                print(f"[{ts}] [MSG-IN] topic={msg.topic}, qos={msg.qos}, payload_len={len(payload)}")
                print(f"[{ts}] [MSG-IN] payload={payload[:500]}")

                # 解析主题，提取项目和指标
                # 格式: data/1.0/EP/{project}/{indicator}
                topic_parts = msg.topic.split('/')
                print(f"[{ts}] [TOPIC-PARSE] parts={topic_parts}, len={len(topic_parts)}")
                if len(topic_parts) < 5:
                    print(f"[{ts}] [WARN] 无效的主题格式(段数不足): {msg.topic}")
                    return
                project = topic_parts[3]
                indicator = topic_parts[4]
                print(f"[{ts}] [TOPIC] raw_project={project}, indicator={indicator}")

                # 项目标识映射: MQTT topic中的project段(=project.system_id) → 系统内project编号
                # 映射从DB project表加载(带缓存)，未在映射中的project将被丢弃，不入库
                project_mapping = load_project_mapping()
                if project not in project_mapping:
                    print(f"[{ts}] [WARN] 未找到项目映射,丢弃消息: raw_project={project}, topic={msg.topic}")
                    return
                project = project_mapping[project]
                print(f"[{ts}] [MAPPED] mapped_project={project}")

                # 解析消息内容
                data_dict = json.loads(payload)
                print(f"[{ts}] [JSON] 解析成功, 顶层keys={list(data_dict.keys())}")

                # 报文类型(仅日志用;DAS接口不区分realtime/history，统一处理)
                # 兼容消息里 message_type 或 type 字段
                message_type = data_dict.get('message_type', data_dict.get('type', 'realtime_data'))
                print(f"[{ts}] [TYPE] message_type={message_type} (默认realtime_data)")

                # 采集时间:取timestamp字段，缺省用当前时间
                collection_time = data_dict.get('timestamp', datetime.now().astimezone().isoformat(timespec='milliseconds'))
                print(f"[{ts}] [TIME] collection_time={collection_time}")

                # 提取 content 字段(DAS接口要求)
                # 兼容:若消息用 'data' 而非 'content'，做一次适配
                content_list = data_dict.get('content', [])
                if not content_list and 'data' in data_dict:
                    # 兼容部分发布者使用 data 字段
                    raw_data = data_dict.get('data')
                    if isinstance(raw_data, list):
                        content_list = raw_data
                    elif raw_data is not None:
                        content_list = [raw_data]
                    print(f"[{ts}] [FALLBACK] content为空,改用data字段,类型={type(raw_data).__name__}")
                # 归一化为 list
                if not isinstance(content_list, list):
                    content_list = [content_list] if content_list else []
                print(f"[{ts}] [CONTENT] 长度={len(content_list)}")

                if len(content_list) == 0:
                    print(f"[{ts}] [WARN] content为空,不会发送数据到DAS接口")
                    return

                # 构造DAS接口请求体
                # DAS /api/data/insert/ → _insert_data 要求:
                #   project/indicator/content(list)/collection_time
                #   每个content元素需含 start_time + end_time
                project_data = {
                    'project': project,
                    'content': content_list,
                    'indicator': indicator,
                    'message_type': message_type,
                    'collection_time': collection_time
                }
                print(f"[{ts}] [SEND] project={project}, indicator={indicator}, content_items={len(content_list)}")

                # 调用DAS接口持久化(一次提交整个content数组)
                try:
                    status_code, response_data = asyncio.run(data_service.insert_project_data(project_data))
                    print(f"[{ts}] [RESP] status_code={status_code}, response={response_data}")
                    if status_code == 200:
                        self.success_count += 1
                        print(f"[{ts}] [OK] 插入成功: project={project}, indicator={indicator}")
                    else:
                        self.fail_count += 1
                        print(f"[{ts}] [ERROR] 插入失败 status={status_code}, response={response_data}")
                except Exception as e:
                    self.fail_count += 1
                    print(f"[{ts}] [EXCEPTION] 调用DAS接口失败: {e}")
                    traceback.print_exc()

            except json.JSONDecodeError:
                print(f"[{ts}] [ERROR] 消息不是有效JSON: {msg.payload[:100]}...")
            except Exception as e:
                print(f"[{ts}] [ERROR] 处理消息时发生错误: {e}")
                traceback.print_exc()

        def on_disconnect(client, userdata, rc):
            if rc != 0:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] MQTT意外断开连接，返回码: {rc}")
                self._reconnect()
            else:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] MQTT正常断开连接")

        def on_log(client, userdata, level, buf):
            pass

        self.client.on_connect = on_connect
        self.client.on_message = on_message
        self.client.on_disconnect = on_disconnect
        self.client.on_log = on_log

    def _reconnect(self):
        """尝试重新连接MQTT服务器"""
        current_time = time.time()
        if current_time - self.last_reconnect_time < 5:
            return
        try:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 尝试重新连接MQTT服务器...")
            self.client.reconnect()
        except Exception as e:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 重新连接失败: {e}")
            threading.Timer(5, self._reconnect).start()

    def connect(self):
        """连接到MQTT服务器"""
        try:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 正在连接到MQTT服务器...")
            print(f"  服务器: {MQTT_CONFIG['broker']}:{MQTT_CONFIG['port']}")
            print(f"  用户名: {MQTT_CONFIG['username']}")
            self.client.connect(
                MQTT_CONFIG['broker'],
                MQTT_CONFIG['port'],
                keepalive=MQTT_CONFIG['keepalive']
            )
            return True
        except Exception as e:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 连接失败: {e}")
            return False

    def start(self):
        """启动MQTT订阅服务"""
        if not self.connect():
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 启动失败，将在5秒后重试...")
            threading.Timer(5, self.start).start()
            return

        self.running = True
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 数据接入服务已启动")
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 转发目标: DAS /api/data/insert/")
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 正在等待消息...")
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 按 Ctrl+C 停止服务")

        try:
            self.client.loop_forever()
        except KeyboardInterrupt:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 用户中断操作")
        except Exception as e:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 运行时出错: {e}")
        finally:
            self.stop()

    def stop(self):
        """停止MQTT订阅服务"""
        self.running = False
        try:
            self.client.disconnect()
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] MQTT客户端已断开连接")
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 服务停止: "
                  f"共处理 {self.message_count} 条消息, 成功 {self.success_count}, 失败 {self.fail_count}")
        except Exception as e:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 断开连接时出错: {e}")


def test_mapping_mode():
    """测试运行模式：仅加载并输出项目映射字典后退出，不连接MQTT。"""
    print("数据接入服务 - 测试模式（仅输出项目映射）")
    print("=" * 50)
    mapping = load_project_mapping(force=True)
    print("-" * 50)
    print(f"项目映射字典（project.system_id → project.id，共 {len(mapping)} 条）:")
    print(mapping)
    print("-" * 50)
    if not mapping:
        print("[WARN] 映射为空：请检查数据库连接（DATABASE_URL）或 project 表是否有 system_id 非空的记录")


def main():
    """主函数"""
    # 测试模式: --test-mapping，仅输出项目映射字典后退出
    if "--test-mapping" in sys.argv:
        test_mapping_mode()
        return

    print("数据接入服务 - MQTT订阅 → DAS接口持久化")
    print("=" * 50)
    print(f"订阅主题: {SUBSCRIBE_TOPIC}")
    print(f"转发目标: data_service.insert_project_data → /api/data/insert/")
    print("=" * 50)

    # 启动时预加载项目映射（project.system_id → project.id）
    load_project_mapping(force=True)

    try:
        subscriber = MQTTSubscriber()
        subscriber.start()
    except Exception as e:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 服务初始化失败: {e}")
        traceback.print_exc()
    finally:
        print("数据接入服务已停止")


if __name__ == "__main__":
    main()
