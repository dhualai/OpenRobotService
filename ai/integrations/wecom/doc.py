# -*- coding: utf-8 -*-
"""企业微信文档管理：创建智能表格 / 查询子表。

为什么单独拆一个模块：
`smartsheet.py` 只负责「已存在的表」的读写；建表属于文档生命周期管理，
而且它是**唯一能拿到 API docid 的途径**——手工在企微里新建的智能表格，
其浏览器链接路径段是 `s3_xxx`（URL ID），不是 docid，拿去调接口会报
301085 invalid docid。所以「接一张新表」的正确起点是这里的 create_smart_sheet。

官方文档口径：docid 仅在创建时返回，需要开发者妥善保存。
本系统把它存进 backend 的 `wecom_sheet_source.docid`，由管理页面维护。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from ai.integrations.wecom.token import AccessTokenManager

logger = logging.getLogger("ai.wecom.doc")

_API_BASE = "https://qyapi.weixin.qq.com"

# wedoc/create_doc 的 doc_type：3 文档 / 4 表格 / 10 智能表格 / 11 智能文档
DOC_TYPE_DOC = 3
DOC_TYPE_SHEET = 4
DOC_TYPE_SMART_SHEET = 10
DOC_TYPE_SMART_DOC = 11

_DOC_TYPE_LABEL = {
    DOC_TYPE_DOC: "文档",
    DOC_TYPE_SHEET: "表格",
    DOC_TYPE_SMART_SHEET: "智能表格",
    DOC_TYPE_SMART_DOC: "智能文档",
}

_ERR_HINT = {
    301002: "无权限（自建应用需在「可调用应用」列表内）",
    301085: "docid 无效或文档未对应用可见",
    41001: "缺少 access_token",
    45009: "接口调用超过频率限制",
    # admin_users 里的值必须是通讯录 userid，不是姓名/登录账号/手机号
    60111: "admin_users 中有 userid 在本企业通讯录里不存在（填姓名/账号会这样），去掉该字段即可，创建者自动成为管理员",
    60020: "IP 不在应用的可信 IP 列表内",
    301024: "不是文档管理员或文档不存在",
}


class WecomDocError(RuntimeError):
    """企微文档接口调用失败。"""


class WecomDocClient:
    """企业微信文档（含智能表格）管理客户端。"""

    def __init__(self, corpid: str = "", corpsecret: str = ""):
        self._token_mgr = AccessTokenManager(corpid, corpsecret)

    # ── 建表 ────────────────────────────────────────────────

    async def create_smart_sheet(
        self,
        doc_name: str,
        spaceid: str = "",
        fatherid: str = "",
        admin_users: Optional[List[str]] = None,
    ) -> Dict[str, str]:
        """新建一张智能表格，返回 {"docid", "url", "doc_type"}。

        docid 只在创建时返回一次，调用方必须落库。
        spaceid 非必填；一旦传了 spaceid，fatherid 也要同时传（根目录时填 spaceid）。
        """
        return await self._create_doc(
            doc_name=doc_name,
            doc_type=DOC_TYPE_SMART_SHEET,
            spaceid=spaceid,
            fatherid=fatherid,
            admin_users=admin_users,
        )

    async def _create_doc(
        self,
        doc_name: str,
        doc_type: int,
        spaceid: str = "",
        fatherid: str = "",
        admin_users: Optional[List[str]] = None,
    ) -> Dict[str, str]:
        name = (doc_name or "").strip()
        if not name:
            raise ValueError("doc_name 不能为空")
        # 接口限制：文件名最多 255 字符，超出会被截断
        name = name[:255]

        token = await self._token_mgr.get_token()
        body: Dict[str, Any] = {"doc_type": int(doc_type), "doc_name": name}
        if spaceid:
            body["spaceid"] = spaceid
            body["fatherid"] = fatherid or spaceid
        elif fatherid:
            body["fatherid"] = fatherid
        users = [u.strip() for u in (admin_users or []) if u and u.strip()]
        if users:
            body["admin_users"] = users

        url = f"{_API_BASE}/cgi-bin/wedoc/create_doc"
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            resp = await client.post(url, params={"access_token": token}, json=body)
            data = resp.json()

        errcode = data.get("errcode", 0)
        if errcode:
            hint = _ERR_HINT.get(errcode, "")
            msg = f"创建{_DOC_TYPE_LABEL.get(doc_type, '文档')}失败: [{errcode}] {data.get('errmsg')}"
            if hint:
                msg += f"（{hint}）"
            raise WecomDocError(msg)

        docid = data.get("docid") or ""
        if not docid:
            raise WecomDocError("企微未返回 docid，创建结果无效")
        logger.info(f"create_doc: name={name}, doc_type={doc_type}, docid={docid[:12]}...")
        return {
            "docid": docid,
            "url": data.get("url") or "",
            "doc_type": int(doc_type),
            "doc_name": name,
        }

    # ── 查子表 ──────────────────────────────────────────────

    async def list_sheets(self, docid: str) -> List[Dict[str, str]]:
        """查询一个智能表格文档下的所有子表，返回 [{"sheet_id", "title"}]。

        建表后立刻调它就能拿到默认子表的 sheet_id，不用再去浏览器 URL 上抠 tab 参数。
        企微该接口的返回键名历史上有过变化，这里做兼容并保留 raw 便于排查。
        """
        docid = (docid or "").strip()
        if not docid:
            raise ValueError("docid 不能为空")

        token = await self._token_mgr.get_token()
        url = f"{_API_BASE}/cgi-bin/wedoc/smartsheet/get_sheet"
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            resp = await client.post(
                url, params={"access_token": token}, json={"docid": docid}
            )
            data = resp.json()

        errcode = data.get("errcode", 0)
        if errcode:
            hint = _ERR_HINT.get(errcode, "")
            msg = f"查询子表失败: [{errcode}] {data.get('errmsg')}"
            if hint:
                msg += f"（{hint}）"
            raise WecomDocError(msg)

        raw_items = (
            data.get("sheet_list")
            or data.get("sheets")
            or data.get("sheet_infos")
            or []
        )
        sheets: List[Dict[str, str]] = []
        for it in raw_items:
            if not isinstance(it, dict):
                continue
            sheets.append({
                "sheet_id": str(it.get("sheet_id") or it.get("id") or ""),
                "title": str(it.get("title") or it.get("name") or ""),
            })
        if not sheets and raw_items:
            logger.warning(f"list_sheets: 子表结构未识别，原样返回前 {len(raw_items)} 项")
        return [s for s in sheets if s["sheet_id"]]
