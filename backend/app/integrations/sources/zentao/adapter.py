"""禅道任务源适配器（INTEGRATION_DESIGN.md Phase 2）。

实现 TaskSourceAdapter：登录禅道 → 遍历 project → execution → task，
每条 task 经 mapper 翻译为 ExternalTask 交给 SyncEngine。
"""
from __future__ import annotations

import json
import logging
from typing import AsyncIterator, List, Optional

from app.core.config import settings
from app.integrations.base import ExternalTask, TaskSourceAdapter
from app.integrations.sources.zentao.client import ZentaoError, ZentaoRestClient
from app.integrations.sources.zentao.mapper import zentao_task_to_external

logger = logging.getLogger(__name__)


def parse_project_ids(raw: str) -> List[int]:
    """解析项目ID列表，兼容 JSON 数组 / 逗号 / 分号 / 空白分隔。

    承自 candao_dev/main.py 的 parse_project_ids，并增加逐项容错：
    任何无法转为 int 的片段都被跳过，避免配置误填时抛异常。
    """
    raw = (raw or "").strip()
    if not raw:
        return []

    def _safe_int(x) -> Optional[int]:
        try:
            return int(x)
        except (TypeError, ValueError):
            return None

    # 优先按 JSON 解析
    try:
        val = json.loads(raw)
        if isinstance(val, list):
            return [i for v in val if (i := _safe_int(v)) is not None]
        if isinstance(val, (int, float)) and val:
            i = _safe_int(val)
            return [i] if i is not None else []
    except (ValueError, TypeError):
        pass
    # 退化为分隔符解析
    parts = [p for p in raw.replace(";", ",").replace(" ", ",").split(",") if p.strip()]
    return [i for p in parts if (i := _safe_int(p)) is not None]


class ZentaoAdapter(TaskSourceAdapter):
    name = "zentao"
    display_name = "禅道"

    def is_enabled(self) -> bool:
        return bool(
            settings.ZENTAO_BASE_URL
            and settings.ZENTAO_ACCOUNT
            and settings.ZENTAO_PASSWORD
            and parse_project_ids(settings.ZENTAO_PROJECT_IDS)
        )

    async def fetch(self) -> AsyncIterator[ExternalTask]:
        project_ids = parse_project_ids(settings.ZENTAO_PROJECT_IDS)
        async with ZentaoRestClient(
            settings.ZENTAO_BASE_URL,
            settings.ZENTAO_ACCOUNT,
            settings.ZENTAO_PASSWORD,
            verify_ssl=settings.ZENTAO_VERIFY_SSL,
        ) as client:
            try:
                await client.login()
            except ZentaoError as exc:
                logger.error("禅道登录失败，终止本轮同步：%s", exc)
                return  # async generator 中 return 即结束迭代

            for pid in project_ids:
                try:
                    executions = await client.get_project_executions(pid)
                except ZentaoError as exc:
                    logger.warning("获取禅道项目 %s 执行列表失败，跳过：%s", pid, exc)
                    continue
                logger.info("禅道项目 %s：共 %d 个执行", pid, len(executions))
                for exe in executions:
                    eid = exe.get("id")
                    try:
                        tasks = await client.get_execution_tasks(eid)
                    except ZentaoError as exc:
                        logger.warning("获取禅道执行 %s 任务失败，跳过：%s", eid, exc)
                        continue
                    for t in tasks:
                        yield zentao_task_to_external(t, base_url=settings.ZENTAO_BASE_URL)
