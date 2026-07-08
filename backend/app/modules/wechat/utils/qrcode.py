import os
import base64
import brotli
import json
import logging

logger = logging.getLogger(__name__)


def compress_data(data):
    compressed = brotli.compress(data.encode('utf-8'), quality=5)
    encoded = base64.b64encode(compressed).decode('utf-8')
    return f"COMPRESSED:{encoded}"


def decompress_data(compressed_data):
    if compressed_data.startswith("COMPRESSED:"):
        encoded = compressed_data[len("COMPRESSED:"):]
        compressed = base64.b64decode(encoded)
        return brotli.decompress(compressed).decode('utf-8')
    return compressed_data


def process_qrcode_content(qrcode_content):
    if qrcode_content is None:
        return False, None, None, None
    
    logger.info(f"QR码内容分析: 内容长度: {len(qrcode_content)} 字符, 内容预览: {qrcode_content[:100]}{'...' if len(qrcode_content) > 100 else ''}")
    
    if len(qrcode_content) == 0:
        logger.warning("警告: QR码内容为空字符串, 无法获取任何可处理的数据")
        return False, None, None, None
    
    if qrcode_content.startswith("COMPRESSED:"):
        logger.info("发现压缩数据，开始解压...")
        
        try:
            decompressed_content = decompress_data(qrcode_content)
            logger.info(f"解压成功！压缩前: {len(qrcode_content)} 字符, 解压后: {len(decompressed_content)} 字符, 压缩率: {len(qrcode_content) / len(decompressed_content) * 100:.1f}%")
            
            try:
                json_data = json.loads(decompressed_content)
                logger.info(f"内容分析: 有效的JSON格式, 数据类型: {type(json_data).__name__}")
                
                if isinstance(json_data, dict):
                    logger.info(f"JSON对象包含 {len(json_data)} 个键值对, 键列表: {list(json_data.keys())}")
                elif isinstance(json_data, list):
                    logger.info(f"JSON数组包含 {len(json_data)} 个元素")
                
                return True, json_data, 'json', qrcode_content
                
            except json.JSONDecodeError as e:
                logger.warning(f"解压后的内容不是有效的JSON格式: {e}")
                return True, decompressed_content, 'text', qrcode_content
            
        except Exception as e:
            logger.error(f"解压数据时出错: {e}", exc_info=True)
            return True, qrcode_content, 'compressed', qrcode_content
    else:
        logger.info("QR码内容未被压缩")
        return True, qrcode_content, 'original', qrcode_content


def save_processed_data(data, data_type, output_json_path):
    try:
        if data_type == 'json':
            with open(output_json_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"数据已成功保存, 输出文件: {output_json_path}, 文件大小: {os.path.getsize(output_json_path) / 1024:.2f} KB")
            return True
        elif data_type in ['text', 'original']:
            text_output_path = output_json_path.replace('.json', '.txt')
            with open(text_output_path, 'w', encoding='utf-8') as f:
                f.write(data)
            logger.info(f"文本数据已成功保存, 输出文件: {text_output_path}, 文件大小: {os.path.getsize(text_output_path) / 1024:.2f} KB")
            return True
        elif data_type == 'compressed':
            raw_output_path = output_json_path.replace('.json', '_compressed.txt')
            with open(raw_output_path, 'w', encoding='utf-8') as f:
                f.write(data)
            logger.info(f"压缩数据已成功保存, 输出文件: {raw_output_path}, 文件大小: {os.path.getsize(raw_output_path) / 1024:.2f} KB")
            return True
        else:
            logger.error(f"未知的数据类型: {data_type}")
            return False
    except Exception as e:
        logger.error(f"保存文件时出错: {e}, 尝试保存到备用位置...", exc_info=True)
        try:
            backup_path = output_json_path + '.bak'
            if isinstance(data, (dict, list)):
                with open(backup_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False)
            else:
                with open(backup_path, 'w', encoding='utf-8') as f:
                    f.write(data)
            logger.info(f"已保存到备用位置: {backup_path}")
            return True
        except Exception as backup_error:
            logger.error(f"保存到备用位置时也出错: {backup_error}", exc_info=True)
            return False