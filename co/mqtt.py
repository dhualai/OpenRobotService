#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
获取数据分析接口数据的工具脚本 - 支持MQTT推送
"""

import json
from datetime import datetime, UTC
import time
import paho.mqtt.client as mqtt

import os

# MQTT配置
MQTT_CONFIG = {
    'broker': '125.122.97.107',      # MQTT服务器地址
    'port': 8084,              # MQTT端口
    'username': 'test',       # 用户名
    'password': 'qazokm1029.',    # 密码
    'client_id': f'data_analysis_client_{int(time.time())}',  # 客户端ID
    'keepalive': 60            # 保活时间
}

# MQTT协议头配置
MQTT_PROTOCOL_CONFIG = {
    'version': 'V2.0.0',      # 版本号
    'header_id': 1            # 初始headerId
}

# 为了保持headerId的持久性，使用简单的文件存储
HEADER_ID_FILE = 'mqtt_header_id.json'

def load_header_id():
    """
    从文件加载headerId
    """
    try:
        if os.path.exists(HEADER_ID_FILE):
            with open(HEADER_ID_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('header_id', MQTT_PROTOCOL_CONFIG['header_id'])
        return MQTT_PROTOCOL_CONFIG['header_id']
    except Exception as e:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 加载headerId失败: {e}")
        return MQTT_PROTOCOL_CONFIG['header_id']

def save_header_id(header_id):
    """
    保存headerId到文件
    """
    try:
        with open(HEADER_ID_FILE, 'w', encoding='utf-8') as f:
            json.dump({'header_id': header_id}, f, ensure_ascii=False)
    except Exception as e:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 保存headerId失败: {e}")

def create_protocol_header(message_type='realtime_data'):
    """
    创建协议头信息
    """
    # 获取当前headerId
    header_id = load_header_id()
    
    # 创建协议头
    header = {
        'headerId': header_id,
        'timestamp': datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
        'version': MQTT_PROTOCOL_CONFIG['version'],
        'type':message_type

    }
    
    # 更新headerId（循环使用，确保在32位整数范围内）
    new_header_id = header_id + 1
    if new_header_id >= 2**31:
        new_header_id = 1
    
    # 保存新的headerId
    save_header_id(new_header_id)
    
    return header

def publish_to_mqtt(data, project_id=None, topic=None,message_type='realtime_data'):
    """
    将数据发布到MQTT主题
    :param data: 要发布的数据
    :param project_id: 项目ID，如'test8082'或'test8083'
    :param topic: 完整的MQTT主题（如果提供则忽略project_id）
    :return: 发布是否成功
    """
    # 验证MQTT配置
    if 'port' not in MQTT_CONFIG:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] MQTT配置错误：未设置端口")
        return False
    
    port = MQTT_CONFIG['port']
    if not isinstance(port, int) or port <= 0 or port > 65535:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] MQTT配置错误：端口设置无效: {port}")
        return False
    
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 使用MQTT端口: {port}")
    
    # 获取项目ID
    project = project_id
    
    # 如果没有提供topic，则根据project_id生成
    if topic is None:
        topic = f"data/1.0/EP/{project}/dataIndicators"
        
    if data is None:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 没有数据可发布到MQTT")
        return False
    
    try:
        # 创建带有协议头和项目ID的消息
        message_with_header = {
            **create_protocol_header(message_type),
            **data,  # 合并原始数据
            'projectId': project  # 添加项目ID字段
        }
        
        # 将数据转换为JSON字符串
        payload = json.dumps(message_with_header, ensure_ascii=False)
        
        # 创建MQTT客户端
        client = mqtt.Client(client_id=MQTT_CONFIG['client_id'])
        
        # 设置用户名和密码
        client.username_pw_set(MQTT_CONFIG['username'], MQTT_CONFIG['password'])
        
        # 连接回调函数
        def on_connect(client, userdata, flags, rc):
            if rc == 0:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] MQTT连接成功")
            else:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] MQTT连接失败，返回码: {rc}")
        
        # 发布回调函数
        def on_publish(client, userdata, mid):
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 消息发布成功，消息ID: {mid}")
        
        # 设置回调函数
        client.on_connect = on_connect
        client.on_publish = on_publish
        
        # 连接MQTT服务器
        client.connect(
            MQTT_CONFIG['broker'], 
            port,  # 使用验证后的端口
            MQTT_CONFIG['keepalive']
        )
        
        # 启动客户端循环（非阻塞模式）
        client.loop_start()
        
        # 发布消息
        start_time = time.time()
        result = client.publish(topic, payload, qos=1, retain=False)
        
        # 等待发布完成
        result.wait_for_publish(timeout=5)
        end_time = time.time()
        
        # 停止客户端循环
        client.loop_stop()
        client.disconnect()
        
        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 数据已成功发布到MQTT主题: {topic}")
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] MQTT发布耗时: {(end_time - start_time):.2f}秒")
            return True
        else:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] MQTT发布失败，错误码: {result.rc}")
            return False
            
    except Exception as e:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] MQTT发布异常: {e}")
        return False

