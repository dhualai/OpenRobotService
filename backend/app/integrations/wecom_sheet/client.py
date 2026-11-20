"""AI 服务「企业微信表格」接口的薄客户端。

后端不直接持有企微 corpid/secret，也不直连 qyapi.weixin.qq.com——
拉表这件事已经由 AI 服务（`ai/integrations/wecom/`）封装好（含 token 缓存、
拍扁、翻页），这里只做一次 HTTP 转发。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class WecomSheetClientError(RuntimeError):
    """拉表失败。message 可直接回给前端展示。"""


class WecomSheetClient:
    def __init__(self, base_url: str = ""):
        self._base = (base_url or settings.AI_SERVICE_URL or "").rstrip("/")

    @property
    def base_url(self) -> str:
        return self._base

    async def _request(self, method: str, path: str, params: Dict[str, Any],
                       body: Optional[Dict[str, Any]], timeout: float) -> Any:
        if not self._base:
            raise WecomSheetClientError("未配置 AI_SERVICE_URL，无法连接 AI 服务")
        url = f"{self._base}{path}"
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.request(method, url, params=params, json=body)
        except httpx.TimeoutException:
            raise WecomSheetClientError(f"AI 服务响应超时（{path}）")
        except httpx.RequestError as e:
            raise WecomSheetClientError(f"无法连接 AI 服务（{self._base}）: {e}")

        if resp.status_code != 200:
            raise WecomSheetClientError(f"AI 服务返回 HTTP {resp.status_code}: {resp.text[:300]}")

        try:
            payload = resp.json()
        except ValueError:
            raise WecomSheetClientError(f"AI 服务返回非 JSON: {resp.text[:300]}")

        if payload.get("code") not in (0, None):
            raise WecomSheetClientError(str(payload.get("message") or "AI 服务返回失败"))
        return payload.get("data") or {}

    async def _get(self, path: str, params: Dict[str, Any], timeout: float) -> Any:
        return await self._request("GET", path, params, None, timeout)

    async def _post(self, path: str, body: Dict[str, Any], timeout: float) -> Any:
        return await self._request("POST", path, {}, body, timeout)

    async def sample(self, docid: str, sheet_id: str, limit: int = 3) -> Dict[str, Any]:
        """试连：拉少量记录 + 真实列名。不落库。"""
        data = await self._get(
            "/api/ai/wecom/sheets/sample",
            {"docid": docid, "sheet_id": sheet_id, "limit": limit},
            timeout=30.0,
        )
        return {
            "total": int(data.get("total") or 0),
            "columns": list(data.get("columns") or []),
            "records": list(data.get("records") or []),
        }

    async def fetch_all(self, docid: str, sheet_id: str) -> List[Dict[str, Any]]:
        """全量拉取（AI 服务内部自动翻页 + 拍扁）。"""
        data = await self._get(
            "/api/ai/wecom/sheets/records",
            {"docid": docid, "sheet_id": sheet_id},
            timeout=120.0,
        )
        return list(data.get("records") or [])

    async def create_doc(
        self,
        doc_name: str,
        spaceid: str = "",
        fatherid: str = "",
        admin_users: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """新建一张企微智能表格，返回 {docid, url, doc_name, sheets}。

        这是**唯一能拿到 API docid** 的途径：手工在企微里建的表格，
        链接里的 s3_xxx 是 URL ID 不是 docid，调接口会 301085。
        docid 只在创建时返回一次，调用方必须落库。
        """
        data = await self._post(
            "/api/ai/wecom/docs/create",
            {
                "doc_name": doc_name,
                "spaceid": spaceid or "",
                "fatherid": fatherid or "",
                "admin_users": list(admin_users or []),
            },
            timeout=60.0,
        )
        return {
            "docid": str(data.get("docid") or ""),
            "url": str(data.get("url") or ""),
            "doc_name": str(data.get("doc_name") or doc_name),
            "sheets": [
                {"sheet_id": str(s.get("sheet_id") or ""), "title": str(s.get("title") or "")}
                for s in (data.get("sheets") or [])
                if isinstance(s, dict) and s.get("sheet_id")
            ],
        }

    async def list_sheets(self, docid: str) -> List[Dict[str, str]]:
        """查询某文档下的子表，用于页面选择 sheet_id。"""
        data = await self._get(
            "/api/ai/wecom/docs/sheets", {"docid": docid}, timeout=30.0,
        )
        return [
            {"sheet_id": str(s.get("sheet_id") or ""), "title": str(s.get("title") or "")}
            for s in (data.get("sheets") or [])
            if isinstance(s, dict) and s.get("sheet_id")
        ]
