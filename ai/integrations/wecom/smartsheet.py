"""
企业微信 Smartsheet 客户端

提供 pull（查询记录）和 push（更新记录）两个核心能力。
内部自动处理拍扁/还原，对外暴露干净的扁平 JSON。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from ai.config import get_ai_config
from ai.integrations.wecom.token import AccessTokenManager
from ai.integrations.wecom.types import (
    FieldSchema,
    flatten_record,
    flatten_value,
    reconstruct_values,
)

logger = logging.getLogger("ai.wecom.smartsheet")

_API_BASE = "https://qyapi.weixin.qq.com"


class WecomSmartsheetClient:
    """企业微信 Smartsheet 客户端"""

    def __init__(
        self,
        corpid: str = "",
        corpsecret: str = "",
        docid: str = "",
        sheet_id: str = "",
    ):
        cfg = get_ai_config()
        self._docid = docid or cfg.wecom_docid
        self._sheet_id = sheet_id or cfg.wecom_sheet_id
        self._token_mgr = AccessTokenManager(corpid, corpsecret)
        self._schema = FieldSchema()
        self._schema_loaded = False

    # ── 公开方法 ────────────────────────────────────────────────

    async def pull_all(self) -> list[dict]:
        """
        拉取全部记录，自动翻页。
        返回拍扁后的 [{"record_id": ..., "values": {...}}, ...]
        """
        all_records: list[dict] = []
        offset = 0
        limit = 500

        while True:
            data = await self._get_records(offset=offset, limit=limit)
            records = data.get("records", [])
            all_records.extend(records)

            if not data.get("has_more"):
                break
            offset = data.get("next", offset + limit)

        # 首次 pull 时推断 schema
        if not self._schema_loaded and all_records:
            self._schema.infer_from_raw(all_records)
            self._schema_loaded = True

        # 拍扁
        flat = [flatten_record(r, self._schema) for r in all_records]
        logger.info(f"pull_all: {len(flat)} 条记录")
        return flat

    async def pull(self, limit: int = 100, offset: int = 0,
                   filter_spec: dict = None, field_titles: list[str] = None) -> dict:
        """
        分页拉取（不自动翻页）。
        返回 {"total": int, "has_more": bool, "next": int, "records": [...]}
        """
        raw = await self._get_records(
            offset=offset, limit=limit,
            filter_spec=filter_spec, field_titles=field_titles,
        )

        if not self._schema_loaded:
            self._schema.infer_from_raw(raw.get("records", []))
            self._schema_loaded = True

        raw["records"] = [flatten_record(r, self._schema) for r in raw.get("records", [])]
        return raw

    async def push_one(self, record_id: str, values: dict) -> bool:
        """
        更新单条记录。
        values: {"字段名": "新值", ...}  扁平格式
        """
        if not self._schema_loaded:
            logger.warning("schema 未加载，请先 pull 一次以推断字段类型")

        api_values = reconstruct_values(values, self._schema)
        return await self._update_records([{
            "record_id": record_id,
            "values": api_values,
        }])

    async def push(self, records: list[dict]) -> bool:
        """
        批量更新。
        records: [{"record_id": "xxx", "values": {...}}, ...]
        """
        api_records = []
        for r in records:
            api_values = reconstruct_values(r.get("values", {}), self._schema)
            api_records.append({"record_id": r["record_id"], "values": api_values})
        return await self._update_records(api_records)

    # ── 底层 API 调用 ────────────────────────────────────────────

    async def _get_records(
        self,
        offset: int = 0,
        limit: int = 100,
        filter_spec: dict = None,
        field_titles: list[str] = None,
        sort: list[dict] = None,
    ) -> dict:
        token = await self._token_mgr.get_token()
        url = f"{_API_BASE}/cgi-bin/wedoc/smartsheet/get_records"
        params = {"access_token": token}

        body: Dict[str, Any] = {
            "docid": self._docid,
            "sheet_id": self._sheet_id,
            "key_type": "CELL_VALUE_KEY_TYPE_FIELD_TITLE",
            "offset": offset,
            "limit": limit,
        }
        if field_titles:
            body["field_titles"] = field_titles
        if filter_spec:
            body["filter_spec"] = filter_spec
        if sort:
            body["sort"] = sort

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            resp = await client.post(url, params=params, json=body)
            data = resp.json()

        if data.get("errcode") != 0:
            raise RuntimeError(f"get_records 失败: {data}")

        return data

    async def _update_records(self, records: list[dict]) -> bool:
        if not records:
            return True

        token = await self._token_mgr.get_token()
        url = f"{_API_BASE}/cgi-bin/wedoc/smartsheet/update_records"
        params = {"access_token": token}

        body = {
            "docid": self._docid,
            "sheet_id": self._sheet_id,
            "key_type": "CELL_VALUE_KEY_TYPE_FIELD_TITLE",
            "records": records,
        }

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            resp = await client.post(url, params=params, json=body)
            data = resp.json()

        if data.get("errcode") != 0:
            raise RuntimeError(f"update_records 失败: {data}")

        logger.info(f"push: {len(records)} 条记录更新成功")
        return True
