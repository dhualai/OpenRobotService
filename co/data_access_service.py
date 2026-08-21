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
import os
import asyncio
import traceback
from data_service import data_service

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
                print(f"[{ts}] [TOPIC] project={project}, indicator={indicator}")

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


def main():
    """主函数"""
    print("数据接入服务 - MQTT订阅 → DAS接口持久化")
    print("=" * 50)
    print(f"订阅主题: {SUBSCRIBE_TOPIC}")
    print(f"转发目标: data_service.insert_project_data → /api/data/insert/")
    print("=" * 50)

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
