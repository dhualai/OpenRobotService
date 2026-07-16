"""外部任务源通用路由（INTEGRATION_DESIGN.md §8）。

挂 /api/tasks/sources，独立 ``X-API-Key`` 鉴权（与用户 JWT 分离，供 Airflow / 手动触发）。
按 source 名分发到对应 adapter，不随源增删而改动。
"""
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_async_db
from app.integrations.engine import SyncEngine
from app.integrations.registry import registry

router = APIRouter(prefix="/tasks/sources", tags=["integrations"])


def verify_sync_api_key(x_api_key: str = Header(default="", alias="X-API-Key")) -> str:
    """校验同步接口的 X-API-Key。未配置 key 时一律拒绝（安全默认）。"""
    expected = settings.HELPDESK_SYNC_API_KEY
    if not expected or x_api_key != expected:
        raise HTTPException(status_code=401, detail="invalid or missing api key")
    return x_api_key


@router.get("")
async def list_sources(_: str = Depends(verify_sync_api_key)):
    """列出已注册的任务源及启用状态。"""
    return [
        {"name": a.name, "display_name": a.display_name, "enabled": a.is_enabled()}
        for a in registry.all()
    ]


@router.post("/{source}/sync")
async def sync_source(
    source: str,
    db: AsyncSession = Depends(get_async_db),
    _: str = Depends(verify_sync_api_key),
):
    """触发指定任务源的一次同步（Airflow / 手动共用此入口）。"""
    if not registry.has(source):
        raise HTTPException(status_code=404, detail=f"未注册的任务源：{source}")
    engine = SyncEngine(db)
    result = await engine.sync_source(source)
    return {"code": 200, "message": "ok", "data": result.to_dict()}
