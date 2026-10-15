"""wxpay-service（Go）内部 HTTP 客户端。

只做三件事：发起转账、查单、撤销。签名/验签/加解密全部在 Go 侧完成，
本模块**不持有任何支付密钥**。
"""

from typing import Any, Dict, Optional

import httpx

from app.core.config import settings
from app.services.logging import get_logger

logger = get_logger(__name__)


class WxpayServiceError(Exception):
    """Go 服务返回的错误。

    system_error=True 时，调用方**必须先用原 out_bill_no 查单**，
    确认已到终态后才可换单重试（方案 11.1）。
    """

    def __init__(
        self,
        message: str,
        code: str = "",
        status_code: int = 0,
        system_error: bool = False,
        request_id: str = "",
    ):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.system_error = system_error
        self.request_id = request_id


class WxpayServiceClient:
    """调用 wxpay-service 的内部接口。"""

    def __init__(self):
        self.base_url = (settings.WXPAY_SERVICE_URL or "http://127.0.0.1:9100").rstrip("/")
        self.timeout = float(getattr(settings, "WXPAY_SERVICE_TIMEOUT", 10.0) or 10.0)

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        token = getattr(settings, "WXPAY_INTERNAL_TOKEN", "")
        if token:
            headers["X-Internal-Token"] = token
        return headers

    async def _request(
        self, method: str, path: str, json_body: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.request(method, url, json=json_body, headers=self._headers())

        try:
            payload = resp.json()
        except Exception:  # noqa: BLE001
            payload = {}

        if resp.status_code >= 400 or payload.get("code") not in ("OK", None):
            data = payload.get("data") or {}
            raise WxpayServiceError(
                message=payload.get("message") or f"wxpay-service error {resp.status_code}",
                code=str(payload.get("code") or ""),
                status_code=resp.status_code,
                system_error=bool(data.get("system_error")),
                request_id=str(data.get("request_id") or ""),
            )
        return payload.get("data") or {}

    # ---------------- 商家转账 ----------------

    async def create_transfer(
        self,
        out_bill_no: str,
        openid: str,
        amount: int,
        remark: str,
        scene_name: str,
        reward_desc: str,
        user_name: str = "",
        perception: str = "",
        scene_id: str = "",
    ) -> Dict[str, Any]:
        """发起转账，返回 {out_bill_no, transfer_bill_no, state, package_info, create_time}。"""
        body = {
            "out_bill_no": out_bill_no,
            "openid": openid,
            "transfer_amount": int(amount),
            "transfer_remark": remark,
            "scene_name": scene_name,
            "reward_desc": reward_desc,
        }
        if user_name:
            body["user_name"] = user_name
        if perception:
            body["user_recv_perception"] = perception
        if scene_id:
            body["transfer_scene_id"] = scene_id
        return await self._request("POST", "/internal/transfer/create", body)

    async def query_transfer(self, out_bill_no: str) -> Dict[str, Any]:
        """按商户单号查单（提现决策 + 兜底查单均依赖）。"""
        return await self._request("GET", f"/internal/transfer/query/{out_bill_no}")

    async def query_transfer_by_bill_no(self, transfer_bill_no: str) -> Dict[str, Any]:
        """按微信转账单号查单。

        排障用：微信账单/客服工单里只有 transfer_bill_no，无法反推商户单号。
        """
        return await self._request(
            "GET", f"/internal/transfer/query-by-bill/{transfer_bill_no}"
        )

    async def cancel_transfer(self, out_bill_no: str) -> Dict[str, Any]:
        """撤销转账（仅运营后台；返回成功仅代表受理，需再查单确认）。"""
        return await self._request("POST", "/internal/transfer/cancel", {"out_bill_no": out_bill_no})


wxpay_client = WxpayServiceClient()
