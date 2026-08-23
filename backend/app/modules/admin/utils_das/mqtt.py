import json
from datetime import datetime, UTC
import time
import paho.mqtt.client as mqtt
import os
import threading
from app.modules.admin.utils_das.config import MQTT_CONFIG, MQTT_PROTOCOL_CONFIG, HEADER_ID_FILE

status_store = {}
status_lock = threading.Lock()

def load_header_id():
    try:
        if os.path.exists(HEADER_ID_FILE):
            with open(HEADER_ID_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('header_id', MQTT_PROTOCOL_CONFIG['header_id'])
        return MQTT_PROTOCOL_CONFIG['header_id']
    except Exception as e:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 加载 headerId 失败：{e}")
        return MQTT_PROTOCOL_CONFIG['header_id']

def save_header_id(header_id):
    try:
        with open(HEADER_ID_FILE, 'w', encoding='utf-8') as f:
            json.dump({'header_id': header_id}, f, ensure_ascii=False)
    except Exception as e:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 保存 headerId 失败：{e}")

def create_protocol_header():
    header_id = load_header_id()
    
    header = {
        'headerId': header_id,
        'timestamp': datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
        'version': MQTT_PROTOCOL_CONFIG['version'],
    }
    
    new_header_id = header_id + 1
    if new_header_id >= 2**31:
        new_header_id = 1
    
    save_header_id(new_header_id)
    
    return header

def publish_to_mqtt(data, topic=None, wait_for_status=False, timeout=30):
    project_code = data.get('project_code', '')
    
    if topic is None:
        topic = f"data/1.0/EP/license/apply"
        
    if data is None:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 没有数据可发布到 MQTT")
        return False
    
    try:
        message_with_header = {
            **create_protocol_header(),
            **data,
        }
        
        payload = json.dumps(message_with_header, ensure_ascii=False)
        
        status_key = f"license_status:{project_code}"
        if wait_for_status and project_code:
            with status_lock:
                status_store[status_key] = {'status': 'pending', 'message': '等待授权', 'expire_at': time.time() + timeout}
        
        client = mqtt.Client(client_id=MQTT_CONFIG['client_id'])
        
        client.username_pw_set(MQTT_CONFIG['username'], MQTT_CONFIG['password'])
        
        def on_connect(client, userdata, flags, rc):
            if rc == 0:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] MQTT 连接成功")
                if wait_for_status and project_code:
                    status_topic = "data/1.0/EP/license/return"
                    client.subscribe(status_topic)
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 已订阅主题：{status_topic}")
            else:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] MQTT 连接失败，返回码：{rc}")
        
        def on_message(client, userdata, msg):
            try:
                status_data = json.loads(msg.payload.decode('utf-8'))
                status = status_data.get('status', '')
                message = status_data.get('message', '')
                response_project_code = status_data.get('project_code', '')
                
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 收到状态更新：{status} - {message}")
                
                if wait_for_status and project_code and response_project_code == project_code:
                    with status_lock:
                        status_store[status_key] = status_data
            except Exception as e:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 处理状态消息失败：{e}")
        
        def on_publish(client, userdata, mid):
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 消息发布成功，消息 ID: {mid}")
        
        client.on_connect = on_connect
        client.on_message = on_message
        client.on_publish = on_publish
        
        client.connect(
            MQTT_CONFIG['broker'], 
            MQTT_CONFIG['port'], 
            MQTT_CONFIG['keepalive']
        )
        
        client.loop_start()
        
        start_time = time.time()
        result = client.publish(topic, payload, qos=1, retain=False)
        
        result.wait_for_publish(timeout=5)
        
        if wait_for_status and project_code:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 等待授权状态变更，超时时间：{timeout}秒")
            wait_start = time.time()
            
            while time.time() - wait_start < timeout:
                with status_lock:
                    status_info = status_store.get(status_key)
                if status_info and status_info.get('status') in ['approved', 'rejected']:
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 授权状态已变更：{status_info}")
                    client.loop_stop()
                    client.disconnect()
                    with status_lock:
                        status_store.pop(status_key, None)
                    return status_info

                time.sleep(0.5)
            
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 等待授权状态变更超时")
            client.loop_stop()
            client.disconnect()
            return {'status': 'failed', 'error': '超时'}
        
        end_time = time.time()
        
        client.loop_stop()
        client.disconnect()
        
        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 数据已成功发布到 MQTT 主题：{topic}")
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] MQTT 发布耗时：{(end_time - start_time):.2f}秒")
            return True
        else:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] MQTT 发布失败，错误码：{result.rc}")
            return False
            
    except Exception as e:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] MQTT 发布异常：{e}")
        return False