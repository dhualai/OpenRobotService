"""数据包导入服务 —— 解析并切分 DAS 数据包文件。

数据包来源：DAS 导出的 .bz2（bzip2 压缩）或 .json 文件，结构为
`{project, indicator, content: [{data, start_time, end_time}], collection_time}`。

参考实现：项目数据/DAS/customized/groupefficiency.py（transform_data）与
项目数据/DAS/api/data.py（upload_file 的数据包解析链）。
"""
import base64
import binascii
import bz2
import gzip
import json
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional

# 东八区时区常量
_BEIJING_TZ = timezone(timedelta(hours=8))

# 允许的上传文件扩展名
ALLOWED_EXTENSIONS = {'.json', '.bz2'}

# 文件大小上限（20MB）
MAX_FILE_SIZE = 20 * 1024 * 1024

# 二维码内容长度上限（字符数）：21 天分片约 3 × 2900 字符，放大余量防超大 payload 拖垮解码
MAX_QR_PAYLOAD_CHARS = 200 * 1024


def parse_packet_file(file_bytes: bytes, filename: str) -> dict:
    """按扩展名解析上传的数据包文件，返回原始 JSON dict。

    - .bz2：先 bz2 解压再按 UTF-8 解析 JSON
    - .json：直接按 UTF-8 解析 JSON
    """
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"不支持的文件格式: {ext or '(无扩展名)'}，仅支持 .json 和 .bz2")

    try:
        if ext == '.bz2':
            content = bz2.decompress(file_bytes).decode('utf-8')
        else:
            content = file_bytes.decode('utf-8')
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError(f"文件解压/解码失败: {e}")

    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"文件内容不是合法 JSON: {e}")


def _parse_time(time_str) -> Optional[datetime]:
    """解析时间字符串为带时区的 datetime。

    - Z 后缀归一化为 +00:00（UTC）
    - 保留原始时区信息，不进行时区换算
    - 无时区信息时按东八区处理
    """
    try:
        if isinstance(time_str, str) and time_str.endswith('Z'):
            time_str = time_str[:-1] + '+00:00'
        dt = datetime.fromisoformat(time_str)
        # 若时间字符串无时区信息，按东八区处理
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_BEIJING_TZ)
        return dt
    except (ValueError, TypeError):
        return None


def _format_time(dt: datetime) -> str:
    """格式化 datetime 为 DAS 数据包的时间字符串（东八区）。

    将 datetime 转换为东八区后格式化，避免时区混用导致的日期偏移。
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_BEIJING_TZ)
    dt = dt.astimezone(_BEIJING_TZ)
    return dt.strftime('%Y-%m-%dT%H:%M:%S+08:00')


def transform_data(data: dict) -> dict:
    """按时间戳切分数据（每个时间戳一个数据块）。

    针对 GroupEfficiency 指标：content[0].data 是 `{时间戳: 数据}` 的列表，
    把每条时间戳数据切分为独立 content 块，并标注该时间戳所在日期的
    00:00:00 ~ 23:59:59 作为 start_time/end_time。
    其他指标原样返回，交由 DataHandler._insert_data 处理。
    """
    project = data.get('project')
    indicator = data.get('indicator')
    content = data.get('content', [])
    collection_time = data.get('collection_time')

    if indicator == 'AGVEfficiency':
        indicator = 'GroupEfficiency'
    if indicator != 'GroupEfficiency':
        return data
    if not content:
        return data

    content_item = content[0]
    data_list = content_item.get('data', [])

    if not data_list:
        return []

    # 提取所有时间戳及其对应的数据
    timestamp_data = []
    for item in data_list:
        if isinstance(item, dict):
            for key in item.keys():
                if key.startswith('20'):
                    item_time = _parse_time(key)
                    if item_time:
                        timestamp_data.append({
                            'timestamp': item_time,
                            'timestamp_str': key,
                            'data': item[key],
                        })

    if not timestamp_data:
        return []

    # 按时间戳排序
    timestamp_data.sort(key=lambda x: x['timestamp'])

    trans_data = {
        "project": project,
        "indicator": indicator,
        "content": [],
        "collection_time": collection_time,
    }

    for item in timestamp_data:
        # 先转换为东八区，再取当天 00:00:00 ~ 23:59:59，避免时区混用导致日期偏移
        ts = item['timestamp']
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=_BEIJING_TZ)
        ts = ts.astimezone(_BEIJING_TZ)
        day_start = ts.replace(hour=0, minute=0, second=0)
        day_end = ts.replace(hour=23, minute=59, second=59)
        trans_data['content'].append({
            "data": [item['data']],
            "start_time": _format_time(day_start),
            "end_time": _format_time(day_end),
        })

    return trans_data


def validate_and_prepare_import_data(data: dict, project: str = None) -> dict:
    """校验并构造插入数据：补全 project/collection_time，校验必要字段，返回切分结果。"""
    if not project:
        project = data.get("project")
    else:
        data["project"] = project
    indicator = data.get("indicator")
    data_content = data.get("content")

    if not project:
        raise ValueError("缺少必填参数: project")
    if not indicator:
        raise ValueError("缺少必填参数: indicator")
    if not data_content:
        raise ValueError("缺少必填参数: content")
    if not isinstance(data_content, list):
        raise ValueError("数据必须是列表类型")
    if len(data_content) == 0:
        raise ValueError("数据内容不能为空")

    # 默认使用当前时间作为采集时间
    data.setdefault("collection_time", datetime.now().isoformat())

    return transform_data(data)


def decode_qr_payload(payload: str = None, fragments: List[str] = None) -> dict:
    """解码数据平台导出的数据二维码，返回明文 JSON dict。

    格式（见 qr_samples 样例包）：
    - 完整数据 = QRV1| + Base64(gzip(UTF-8 JSON))
    - 分片每片 = QRV2|{片号}/{总片数}| + 片数据（第 1 片数据以 QRV1| 开头）

    分片需收集齐 n 片（片号 1..n 连续无重复），按片号排序去头拼接后恢复完整 QRV1 payload；
    payload（单码/已拼接）与 fragments（分片列表）二选一，同时传时 payload 优先。
    """
    texts: List[str] = []
    if payload:
        texts = [str(payload)]
    elif fragments:
        texts = [str(t) for t in fragments if t and str(t).strip()]
    texts = [t.strip() for t in texts if t and t.strip()]
    if not texts:
        raise ValueError("缺少二维码内容")

    full = _assemble_qr_payload(texts)
    if not full.startswith("QRV1|"):
        raise ValueError("不是数据平台导出的数据二维码（缺少 QRV1 标识）")
    try:
        compressed = base64.b64decode(full[len("QRV1|"):])
        raw = gzip.decompress(compressed).decode("utf-8")
        return json.loads(raw)
    except (binascii.Error, OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"二维码数据解压失败: {e}")


def _assemble_qr_payload(texts: List[str]) -> str:
    """把扫码得到的原始字符串列表（单码或分片）恢复成完整 QRV1 payload。

    - 任一文本以 QRV1| 开头视为完整单码，直接返回（分片场景不会与完整码混用）
    - 全部为 QRV2| 分片时：校验片号 1..n 连续无重复、总数一致，按片号排序去头拼接
    """
    for t in texts:
        if len(t) > MAX_QR_PAYLOAD_CHARS:
            raise ValueError("二维码内容过长")

    full = next((t for t in texts if t.startswith("QRV1|")), None)
    if full:
        return full

    parts = {}
    total = None
    for t in texts:
        if not t.startswith("QRV2|"):
            raise ValueError(f"无法识别的二维码内容（以「{t[:8]}…」开头）")
        _, head, data_part = t.split("|", 2)
        try:
            idx_s, total_s = head.split("/", 1)
            idx, frag_total = int(idx_s), int(total_s)
        except ValueError:
            raise ValueError(f"二维码分片头格式错误: QRV2|{head}")
        if idx < 1 or frag_total < 1 or idx > frag_total:
            raise ValueError(f"二维码分片编号非法: {idx}/{frag_total}")
        if total is None:
            total = frag_total
        elif frag_total != total:
            raise ValueError("二维码分片总数不一致，请重新扫描")
        if idx in parts:
            raise ValueError(f"第 {idx} 片重复，请扫描其他分片")
        parts[idx] = data_part

    if total is None:
        raise ValueError("未识别到分片信息")
    missing = [i for i in range(1, total + 1) if i not in parts]
    if missing:
        raise ValueError(f"二维码分片不完整（缺少第 {', '.join(map(str, missing))} 片），请继续扫描")
    return "".join(parts[i] for i in range(1, total + 1))


def summarize_content(content: list) -> List[dict]:
    """提取切分后的数据块摘要，供前端展示导入成功的条目。

    每个数据块返回时间范围（start_time/end_time），并尽量从 GroupEfficiency
    数据中提取组名列表（effectWorkTime 的键）。
    """
    chunks: List[dict] = []
    for item in content or []:
        start = item.get("start_time", "")
        end = item.get("end_time", "")
        groups: List[str] = []
        data = item.get("data")
        if isinstance(data, list):
            for d in data:
                if isinstance(d, dict):
                    eff = d.get("effectWorkTime") or {}
                    if isinstance(eff, dict) and eff:
                        groups = list(eff.keys())
                        break
        chunks.append({
            "start_time": start,
            "end_time": end,
            "groups": groups,
        })
    return chunks
