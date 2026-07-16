"""禅道 REST API (v1) 异步客户端（INTEGRATION_DESIGN.md Phase 2）。

由 candao_dev/zentao_client.py 异步化而来（requests -> httpx.AsyncClient），
保留 candao_dev 踩坑后的全部容错：
- 明文 / MD5 自适应登录（不同禅道部署对密码形式要求不一）；
- 鉴权头名为 ``Token``（非 ``Authorization: Bearer``）；
- 分页以 ``total`` + 「空页」终止，**不**用「本页条数 < 页大小」判断
  （部分禅道接口会忽略传入 limit，那样会提前终止、漏取数据）。

调用流程：
  1. ``POST /tokens`` —— 账号密码换 token（长期有效），后续请求头携带 ``Token``。
  2. ``GET /projects/{id}/executions`` —— 项目下的执行列表。
  3. ``GET /executions/{id}/tasks`` —— 执行下的任务列表。
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import httpx

logger = logging.getLogger(__name__)


class ZentaoError(Exception):
    """禅道 API 调用异常基类。"""


class ZentaoAuthError(ZentaoError):
    """登录 / 鉴权失败。"""


class ZentaoRestClient:
    """禅道 REST API (v1) 异步客户端。

    典型用法::

        async with ZentaoRestClient(base, account, password) as client:
            await client.login()
            for exe in await client.get_project_executions(12):
                tasks = await client.get_execution_tasks(exe["id"])
    """

    API_PREFIX = "api.php/v1/"

    def __init__(
        self,
        base_url: str,
        account: str,
        password: str,
        *,
        verify_ssl: bool = True,
        timeout: float = 30.0,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        # 规范化 base_url，保证以单个 "/" 结尾，便于后续 urljoin
        self.base_url = base_url.strip().rstrip("/") + "/"
        self.account = account
        self.password = password
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self._token: Optional[str] = None
        self._client = client or httpx.AsyncClient(timeout=timeout, verify=verify_ssl)
        self._owns_client = client is None  # 自建 client 需在 close() 时关闭

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    async def __aenter__(self) -> "ZentaoRestClient":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_client and not self._client.is_closed:
            await self._client.aclose()

    # ------------------------------------------------------------------ #
    # HTTP 封装
    # ------------------------------------------------------------------ #
    def _full_url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        return urljoin(self.base_url, self.API_PREFIX + path.lstrip("/"))

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        auth: bool = True,
    ) -> Any:
        headers = {"Content-Type": "application/json"}
        if auth:
            if not self._token:
                raise ZentaoAuthError("尚未登录，请先调用 login()。")
            # 禅道 REST 鉴权头名为 Token（非 Authorization: Bearer）
            headers["Token"] = self._token

        url = self._full_url(path)
        logger.debug("%s %s params=%s", method, url, params)
        try:
            resp = await self._client.request(
                method, url, params=params, json=json_body, headers=headers
            )
        except httpx.RequestError as exc:
            raise ZentaoError(f"请求失败 ({method} {url})：{exc}") from exc

        return self._parse(resp, method)

    @staticmethod
    def _parse(resp: httpx.Response, method: str = "") -> Any:
        # 错误信息带上实际请求 URL，便于排查 base_url 前缀等问题
        tag = f"[{method} {resp.url}]" if method else f"[{resp.url}]"
        if resp.status_code == 401:
            raise ZentaoAuthError(f"鉴权失败 (401) {tag}：{resp.text}")
        if resp.status_code >= 400:
            raise ZentaoError(f"HTTP {resp.status_code} {tag}：{resp.text}")
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            # 非 JSON 响应原样返回文本，便于排查
            return resp.text

    # ------------------------------------------------------------------ #
    # 认证
    # ------------------------------------------------------------------ #
    async def login(self) -> str:
        """账号密码换 token；明文失败自动回退 MD5。"""
        attempts = [
            ("plain", self.password),
            ("md5", hashlib.md5(self.password.encode("utf-8")).hexdigest()),
        ]
        last_error: Optional[ZentaoError] = None
        for kind, pwd in attempts:
            try:
                data = await self._request(
                    "POST",
                    "tokens",
                    json_body={"account": self.account, "password": pwd},
                    auth=False,
                )
            except ZentaoAuthError as exc:
                # 401 才尝试下一种密码形式
                last_error = exc
                logger.debug("登录方式 %s 失败：%s", kind, exc)
                continue

            token = self._extract_token(data)
            if token:
                self._token = token
                logger.info("禅道登录成功 (account=%s, pwd=%s)", self.account, kind)
                return token

            # 非 401 的异常结构，直接抛出
            raise ZentaoAuthError(f"登录响应未包含 token：{data}")

        raise last_error or ZentaoAuthError("登录失败，请检查账号密码。")

    @staticmethod
    def _extract_token(data: Any) -> Optional[str]:
        if isinstance(data, dict):
            # 兼容 {"token": "..."} 与 {"data": {"token": "..."}}
            if data.get("token"):
                return data["token"]
            nested = data.get("data")
            if isinstance(nested, dict) and nested.get("token"):
                return nested["token"]
        return None

    # ------------------------------------------------------------------ #
    # 通用分页
    # ------------------------------------------------------------------ #
    async def _get_paged(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        list_key: str,
        page_size: int = 100,
        max_pages: int = 1000,
    ) -> List[Dict[str, Any]]:
        """自动翻页拉取列表。

        终止条件以 ``total`` 与「空页」为准——不使用「本页条数 < 页大小」判断，
        因为部分禅道接口（如 executions 默认 limit=20）会忽略传入的 limit，
        那样会导致提前终止、漏取数据。
        """
        params = dict(params or {})
        items: List[Dict[str, Any]] = []
        total: Optional[int] = None
        page = 1
        while True:
            params["page"] = page
            params["limit"] = page_size
            data = await self._request("GET", path, params=params)

            chunk: List[Dict[str, Any]] = []
            if isinstance(data, dict):
                chunk = data.get(list_key) or []
                if "total" in data:
                    total = data["total"]
            elif isinstance(data, list):
                chunk = data

            if not chunk:
                break  # 空页 → 结束
            items.extend(chunk)

            if total is not None and len(items) >= total:
                break
            page += 1
            if page > max_pages:
                logger.warning("已达 max_pages=%d 上限，停止分页：%s", max_pages, path)
                break

        return items

    # ------------------------------------------------------------------ #
    # 业务接口
    # ------------------------------------------------------------------ #
    async def get_project_executions(self, project_id: int, *, page_size: int = 100) -> List[Dict[str, Any]]:
        """``GET /projects/{id}/executions``：获取项目下的执行列表。"""
        return await self._get_paged(
            f"projects/{project_id}/executions",
            list_key="executions",
            page_size=page_size,
        )

    async def get_execution_tasks(self, execution_id: int, *, page_size: int = 100) -> List[Dict[str, Any]]:
        """``GET /executions/{id}/tasks``：获取执行下的任务列表。"""
        return await self._get_paged(
            f"executions/{execution_id}/tasks",
            list_key="tasks",
            page_size=page_size,
        )
