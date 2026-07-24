"""
企业微信 Smartsheet 字段类型定义 + 拍扁/还原逻辑

拍扁规则：
  TEXT             [{"type":"text","text":"xxx"}]  →  "xxx"
  NUMBER           123                              →  123
  SINGLE_SELECT    [{"id":"xx","text":"yy"}]        →  "yy"  (只取 text)
  USER             [{"user_id":"123"}]              →  {"user_id":"123","name":"张三"}
  DATE_TIME        "1784115898909" (ms时间戳)        →  "2026-07-15" (ISO 日期)
  CHECKBOX         true                             →  true
  IMAGE            [{"image_url":"..."}]            →  [{...}] 保留原结构
  ATTACHMENT       [{...}]                          →  [{...}] 保留原结构
"""
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ── 用户映射表 ──────────────────────────────────────────────────

_USER_MAP: Dict[str, dict] = {}
_USER_MAP_LOADED = False


def _load_user_map() -> Dict[str, dict]:
    global _USER_MAP, _USER_MAP_LOADED
    if _USER_MAP_LOADED:
        return _USER_MAP
    try:
        f = Path(__file__).resolve().parent / "user_map.json"
        if f.exists():
            _USER_MAP = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        pass
    _USER_MAP_LOADED = True
    return _USER_MAP


def resolve_user(user_id: str) -> dict:
    """根据工号查找用户信息（user_map.json），自动加载"""
    u = _load_user_map().get(str(user_id), {})
    return {"user_id": str(user_id),
            "name": u.get("name", ""),
            "email": u.get("email", "")}


def resolve_user_id(name_or_id: str) -> str:
    """根据人名或工号反查 user_id。如果本身就是纯数字工号则直接返回"""
    u = _load_user_map()
    # 直接是工号
    if name_or_id in u:
        return name_or_id
    # 按名字反查
    for uid, info in u.items():
        if info.get("name") == name_or_id:
            return uid
    return name_or_id

# ── 企业微信字段类型枚举 ────────────────────────────────────────

# 可拍扁为简单值的类型
_SIMPLE_TEXT_TYPES = {"FIELD_TYPE_TEXT", "FIELD_TYPE_URL", "FIELD_TYPE_PHONE_NUMBER",
                      "FIELD_TYPE_EMAIL", "FIELD_TYPE_BARCODE", "FIELD_TYPE_AUTONUMBER"}
_SIMPLE_NUMBER_TYPES = {"FIELD_TYPE_NUMBER", "FIELD_TYPE_CURRENCY", "FIELD_TYPE_PERCENTAGE",
                        "FIELD_TYPE_PROGRESS"}
_OPTION_TYPES = {"FIELD_TYPE_SINGLE_SELECT", "FIELD_TYPE_SELECT"}
_USER_TYPES = {"FIELD_TYPE_USER"}
_CHECKBOX_TYPES = {"FIELD_TYPE_CHECKBOX"}
_DATETIME_TYPES = {"FIELD_TYPE_DATE_TIME"}
_ARRAY_RETAIN_TYPES = {"FIELD_TYPE_IMAGE", "FIELD_TYPE_ATTACHMENT", "FIELD_TYPE_LOCATION",
                       "FIELD_TYPE_REFERENCE"}


# ── 字段类型缓存（从原始返回值推断）────────────────────────────


class FieldSchema:
    """缓存子表各字段的类型信息，用于拍扁和还原"""

    def __init__(self):
        # {字段名: 字段类型字符串, 如 "FIELD_TYPE_TEXT"}
        self._types: Dict[str, str] = {}
        # {字段名: [{"id": "xx", "text": "yy"}, ...]}  选项类字段的候选值
        self._options: Dict[str, List[dict]] = {}

    def infer_from_raw(self, records: list[dict]):
        """从原始返回的 records 中推断字段类型"""
        for r in records:
            values = r.get("values", {})
            for field_name, raw_val in values.items():
                if field_name in self._types:
                    continue  # 已推断过
                ftype = _infer_field_type(raw_val)
                if ftype:
                    self._types[field_name] = ftype
                # 收集选项值
                if ftype in _OPTION_TYPES and isinstance(raw_val, list) and raw_val:
                    existing = {o.get("id") for o in self._options.setdefault(field_name, [])}
                    for o in raw_val:
                        if o.get("id") and o["id"] not in existing:
                            self._options[field_name].append({"id": o["id"], "text": o.get("text", "")})
                            existing.add(o["id"])

    def get_type(self, field_name: str) -> str:
        return self._types.get(field_name, "FIELD_TYPE_TEXT")


def _infer_field_type(raw_val: Any) -> Optional[str]:
    """根据原始值推断字段类型"""
    if isinstance(raw_val, bool):
        return "FIELD_TYPE_CHECKBOX"
    if isinstance(raw_val, (int, float)):
        return "FIELD_TYPE_NUMBER"
    if isinstance(raw_val, str):
        # 时间戳字符串（毫秒）→ DATE_TIME
        if raw_val.isdigit() and len(raw_val) >= 13:
            return "FIELD_TYPE_DATE_TIME"
        return "FIELD_TYPE_TEXT"
    if isinstance(raw_val, list) and raw_val:
        first = raw_val[0]
        if not isinstance(first, dict):
            return None
        if "type" in first:
            t = first["type"]
            if t == "text":
                return "FIELD_TYPE_TEXT"
            if t == "url":
                return "FIELD_TYPE_URL"
            if t == "image":
                return "FIELD_TYPE_IMAGE"
        if "user_id" in first:
            return "FIELD_TYPE_USER"
        if "image_url" in first:
            return "FIELD_TYPE_IMAGE"
        if "file_url" in first:
            return "FIELD_TYPE_ATTACHMENT"
        if "id" in first and "text" in first:
            return "FIELD_TYPE_SINGLE_SELECT"
        if "latitude" in first or "longitude" in first:
            return "FIELD_TYPE_LOCATION"
    return None


# ── 拍扁 ────────────────────────────────────────────────────────


def _ms_timestamp_to_iso(ts: Any) -> str:
    """毫秒时间戳 → ISO 日期字符串"""
    try:
        sec = float(ts) / 1000.0
        return datetime.fromtimestamp(sec, tz=timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, OSError):
        return str(ts)


def flatten_value(raw_val: Any, field_type: str = "") -> Any:
    """把企业微信返回的嵌套值拍扁为简单值"""
    if raw_val is None:
        return None

    # 日期 → 转换时间戳
    if field_type in _DATETIME_TYPES:
        return _ms_timestamp_to_iso(raw_val)

    # 布尔 / 数字 / 字符串 → 直接返回
    if isinstance(raw_val, bool) or isinstance(raw_val, (int, float)):
        return raw_val

    if isinstance(raw_val, str):
        return raw_val

    if isinstance(raw_val, list):
        if not raw_val:
            return "" if field_type in _SIMPLE_TEXT_TYPES else None

        first = raw_val[0]
        if not isinstance(first, dict):
            return raw_val[0] if len(raw_val) == 1 else raw_val

        # 文本 → 取 text
        if "type" in first and "text" in first:
            return first["text"]

        # 选项 → 只取 text（不返回 id）
        if "id" in first and "text" in first:
            return first["text"]

        # 成员 → 只返回人名
        if "user_id" in first:
            return resolve_user(first["user_id"]).get("name", first["user_id"])

        # 图片 / 附件 / 位置 → 保留原结构
        return raw_val

    return raw_val


def flatten_record(record: dict, schema: FieldSchema = None) -> dict:
    """拍扁一条记录"""
    values = record.get("values", {})
    flat = {}
    for field_name, raw_val in values.items():
        ftype = schema.get_type(field_name) if schema else ""
        flat[field_name] = flatten_value(raw_val, ftype)
    return {
        "record_id": record.get("record_id", ""),
        "values": flat,
        "create_time": _ms_timestamp_to_iso(record.get("create_time", "")),
        "update_time": _ms_timestamp_to_iso(record.get("update_time", "")),
        "creator_name": record.get("creator_name", ""),
        "updater_name": record.get("updater_name", ""),
    }


# ── 还原 ────────────────────────────────────────────────────────


def reconstruct_value(flat_val: Any, field_type: str,
                      field_options: List[dict] = None) -> Any:
    """把扁平值还原为企业微信 API 格式"""
    if flat_val is None:
        return None

    # 文本类 → [{"type": "text", "text": "xxx"}]
    if field_type in _SIMPLE_TEXT_TYPES:
        return [{"type": "text", "text": str(flat_val)}]

    # 数字类 → 保持数字
    if field_type in _SIMPLE_NUMBER_TYPES:
        return float(flat_val) if isinstance(flat_val, str) else flat_val

    # 复选框 → 保持布尔
    if field_type in _CHECKBOX_TYPES:
        return bool(flat_val)

    # 日期 → 保持字符串
    if field_type in _DATETIME_TYPES:
        return str(flat_val)

    # 选项 → [{"id": "xx", "text": "yy"}]
    if field_type in _OPTION_TYPES:
        if isinstance(flat_val, dict):
            return [{"id": flat_val.get("id", ""), "text": flat_val.get("text", "")}]
        if isinstance(flat_val, str):
            # 只有文本，尝试从 options 找回 id
            option_id = ""
            if field_options:
                for o in field_options:
                    if o.get("text") == flat_val:
                        option_id = o.get("id", "")
                        break
            return [{"id": option_id, "text": flat_val}]
        return [{"id": "", "text": str(flat_val)}]

    # 成员 → [{"user_id": "xxx"}]
    if field_type in _USER_TYPES:
        if isinstance(flat_val, dict):
            uid = flat_val.get("user_id", "")
        else:
            uid = resolve_user_id(str(flat_val))
        return [{"user_id": uid}]

    # 图片 / 附件 / 位置 → 保留原结构，不做转换
    if field_type in _ARRAY_RETAIN_TYPES:
        return flat_val

    # 未知类型 → 按文本处理
    return [{"type": "text", "text": str(flat_val)}]


def reconstruct_values(flat_values: dict, schema: FieldSchema) -> dict:
    """把一组扁平 values 还原为企业微信 API 格式"""
    result = {}
    for field_name, flat_val in flat_values.items():
        ftype = schema.get_type(field_name)
        options = schema._options.get(field_name)
        result[field_name] = reconstruct_value(flat_val, ftype, options)
    return result
