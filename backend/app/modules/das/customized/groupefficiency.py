import json
from datetime import datetime, timedelta
import os
from collections import defaultdict

def parse_time(time_str):
    try:
        if isinstance(time_str, str) and time_str.endswith('Z'):
            time_str = time_str[:-1] + '+00:00'
        return datetime.fromisoformat(time_str.replace('+08:00', '+00:00'))
    except Exception as e:
        print(f"解析时间失败: {time_str}, 错误: {e}")
        return None

def format_time(dt):
    return dt.strftime('%Y-%m-%dT%H:%M:%S+08:00')

def transform_data(data):
    project = data.get('project')
    indicator = data.get('indicator')
    content = data.get('content', [])
    collection_time = data.get('collection_time')
    if indicator == 'AGVEfficiency':
        indicator = 'GroupEfficiency'
    if indicator != 'GroupEfficiency':
        return data
    if not content:
        print("没有数据需要切分")
        return data
    
    content_item = content[0]
    data_list = content_item.get('data', [])
    overall_start_time = content_item.get('start_time')
    overall_end_time = content_item.get('end_time')
    
    print(f"项目: {project}")
    print(f"指标: {indicator}")
    print(f"总时间范围: {overall_start_time} 到 {overall_end_time}")
    print(f"数据项数量: {len(data_list)}")
    
    if not data_list:
        print("数据列表为空")
        return []
    
    timestamp_data = []
    for item in data_list:
        if isinstance(item, dict):
            for key in item.keys():
                if key.startswith('20'):
                    item_time = parse_time(key)
                    if item_time:
                        timestamp_data.append({
                            'timestamp': item_time,
                            'timestamp_str': key,
                            'data': item[key]
                        })
                    
    
    print(f"找到的时间戳数量: {len(timestamp_data)}")
    
    if not timestamp_data:
        print("没有找到时间戳")
        return []
    
    timestamp_data.sort(key=lambda x: x['timestamp'])
    
    trans_data = {
            "project": project,
            "indicator": indicator,
            "content": [
            ],
            "collection_time": collection_time
        }
    for i, item in enumerate(timestamp_data):
        day_start = item['timestamp'].replace(hour=0, minute=0, second=0)
        day_end = item['timestamp'].replace(hour=23, minute=59, second=59)
        content_item = {
            "data": [item['data']],
            "start_time": format_time(day_start),
            "end_time": format_time(day_end)
        }
        trans_data['content'].append(content_item)
        
        print(f"块 {i+1}: {format_time(day_start)} 到 {format_time(day_end)}, 时间戳: {item['timestamp_str']}")
    
    return trans_data

def save_chunks(chunks, output_prefix):
    output_file = f"{output_prefix}_chunk_1.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(chunks, f, ensure_ascii=False, indent=2)
    print(f"已保存: {output_file}")

if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    input_file = os.path.join(script_dir, "metricLibrary_20260325_130307.json")
    output_prefix = os.path.join(script_dir, "metricLibrary_20260325_130307")
    
    print("开始切分数据...")
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    chunks = split_data_by_timestamp(data)
    
    if chunks:
        print(f"\n切分完成，共 {len(chunks)} 个数据块")
        save_chunks(chunks, output_prefix)
    else:
        print("切分失败")