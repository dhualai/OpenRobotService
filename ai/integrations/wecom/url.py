# -*- coding: utf-8 -*-
"""企业微信智能表格 URL 解析。

重要结论（实测 + 官方文档）：
    浏览器分享链接路径段里的 ID（s3_ / w3_ / p3_ 前缀）**不是** API 的 docid。
    把 s3_xxx 当 docid 调 get_records 会返回 301085 invalid docid。
    API 可用的 docid 形如 dc-xxxx（长串），只有通过 wedoc/create_doc 创建、
    或在应用可管理的入口创建的智能表格才有；手工新建 / 复制出来的文档拿不到。

因此本模块只从 URL 提取 **sheet_id**（tab 参数，这个是准的），
docid 必须由使用者另外提供（后台页面手填，或由 create_doc 返回后回填）。

支持形态：
    https://doc.weixin.qq.com/smartsheet/<url_id>?scode=...&tab=<sheet_id>&viewId=...
    https://doc.weixin.qq.com/smartsheet/<url_id>#tab=<sheet_id>
    https://doc.weixin.qq.com/...?docid=<dc-xxx>&tab=<sheet_id>
"""
from __future__ import annotations

import re
from typing import Dict
from urllib.parse import parse_qs, urlsplit

# 路径中的文档标识段（注意：这只是 URL ID，不是 API docid）
_DOC_PATH_RE = re.compile(r"/(?:smartsheet|sheet|doc)/([^/?#]+)", re.I)

# 子表 ID 在 query / fragment 里可能出现的 key，按优先级排列
_SHEET_KEYS = ("tab", "sheet_id", "sheetId", "sheet")

# 浏览器 URL ID 的常见前缀 —— 出现这些前缀说明拿到的是链接 ID 而非 docid
_URL_ID_PREFIXES = ("s3_", "w3_", "p3_", "d3_")

_EMPTY = {"url_id": "", "docid": "", "sheet_id": "", "scode": "", "view_id": ""}


def _first(q: Dict[str, list], *keys: str) -> str:
    for k in keys:
        vals = q.get(k)
        if vals:
            v = str(vals[0]).strip()
            if v:
                return v
    return ""


def _parse_qs(s: str) -> Dict[str, list]:
    """解析 query 或 fragment；非 key=value 形态时返回空，避免脏 key。"""
    if not s:
        return {}
    try:
        return parse_qs(s.lstrip("#"), keep_blank_values=False)
    except Exception:
        return {}


def is_api_docid(value: str) -> bool:
    """判断是否为 API 可用的 docid（dc- 前缀的长串）。

    只做形态判断，不能替代实际调用验证——形态对也可能因文档未授权而 301085。
    """
    v = (value or "").strip()
    return v.startswith("dc-") and len(v) > 20


def is_url_id(value: str) -> bool:
    """判断是否为浏览器链接路径段的 ID（s3_/w3_ 等前缀）。"""
    return (value or "").strip().lower().startswith(_URL_ID_PREFIXES)


def parse_smartsheet_url(url: str) -> Dict[str, str]:
    """解析表格 URL，返回 {"url_id", "docid", "sheet_id", "scode", "view_id"}。

    - url_id：路径段 ID（s3_ 等），仅用于展示/提示，不能当 docid 用
    - docid ：仅当链接里显式带 dc- 开头的 docid 参数时才非空
    - sheet_id：子表 ID，取自 tab 参数，可直接用于 API
    """
    url = (url or "").strip()
    if not url:
        return dict(_EMPTY)

    parts = urlsplit(url)
    query = _parse_qs(parts.query)
    frag = _parse_qs(parts.fragment)

    # 路径段：URL ID
    url_id = ""
    m = _DOC_PATH_RE.search(parts.path or "")
    if m:
        url_id = m.group(1).strip("/")

    # docid：只有显式 dc- 参数才算数
    docid = _first(query, "docid", "docId")
    if not is_api_docid(docid):
        docid = ""

    # sheet_id：query 优先，其次 fragment
    sheet_id = _first(query, *_SHEET_KEYS) or _first(frag, *_SHEET_KEYS)

    return {
        "url_id": url_id,
        "docid": docid,
        "sheet_id": sheet_id,
        "scode": _first(query, "scode"),
        "view_id": _first(query, "viewId", "view_id"),
    }


def parse_sheet_id(url: str) -> str:
    """便捷函数：只取 sheet_id。"""
    return parse_smartsheet_url(url)["sheet_id"]
