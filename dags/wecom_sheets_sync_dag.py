"""企业微信表格数据源周期同步（通用 DAG）。

替代「一张表一个 DAG」的做法（对比 dags/wecom_projects_sync_dag.py）：
本 DAG 不认识任何具体表格，只调后端 `/api/wecom-sheets/sync-all`，
由后端遍历 wecom_sheet_source 表里所有 enabled 的数据源。
**新增一张表只需要在后台页面加一条配置，本文件与后端代码都不用改。**

与旧 DAG 的另一处差别：鉴权改用 X-API-Key（复用外部任务源那套
HELPDESK_SYNC_API_KEY），不再用 admin 账号登录换 JWT——
定时任务不该持有管理员口令，也不必在代码里留密码回退值。
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import requests
from airflow.decorators import dag, task
from airflow.exceptions import AirflowFailException

# ── 配置 ──────────────────────────────────────────────
API_BASE_URL = os.getenv("ORS_API_BASE_URL", "http://127.0.0.1:8400")
SYNC_API_KEY = os.getenv("HELPDESK_SYNC_API_KEY", "")

# 同步频率：与数据源默认 sync_interval_min=30 对齐
SYNC_INTERVAL_MINUTES = int(os.getenv("WECOM_SHEETS_SYNC_MINUTES", "30"))

WECOM_SHEETS_SYNC_PATH = "/api/wecom-sheets/sync-all"

TZ_SHANGHAI = timezone(timedelta(hours=8))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dag(
    dag_id="wecom_sheets_sync",
    description="企业微信表格数据源周期同步（通用，按库内配置遍历）",
    schedule=f"*/{SYNC_INTERVAL_MINUTES} * * * *",
    start_date=datetime(2026, 1, 1, tzinfo=TZ_SHANGHAI),
    catchup=False,
    tags=["wecom", "sheets", "sync"],
)
def wecom_sheets_sync_dag():

    @task
    def trigger_sync() -> list:
        """触发后端同步全部已启用数据源，返回逐源统计。

        后端单源失败不中断整批，返回 207 + 每源的 ok/error，
        这里只把失败项抬出来，便于在 Airflow UI 直接定位。
        """
        if not SYNC_API_KEY:
            raise AirflowFailException(
                "未配置 HELPDESK_SYNC_API_KEY（Airflow 环境变量），拒绝空 key 调用"
            )

        resp = requests.post(
            f"{API_BASE_URL}{WECOM_SHEETS_SYNC_PATH}",
            headers={"X-API-Key": SYNC_API_KEY},
            timeout=600,  # 多张表串行同步，给足时间
        )
        if resp.status_code != 200:
            logger.error(f"wecom 表格同步接口返回非 200: {resp.status_code} {resp.text[:500]}")
            raise AirflowFailException(f"同步触发失败: HTTP {resp.status_code} {resp.text[:300]}")

        body = resp.json()
        results = body.get("data") or []
        for r in results:
            logger.info(
                "wecom 表格 %s: ok=%s created=%s updated=%s unchanged=%s fetched=%s",
                r.get("key"), r.get("ok"), r.get("created"), r.get("updated"),
                r.get("unchanged"), r.get("fetched"),
            )

        failed = [r for r in results if not r.get("ok")]
        if failed:
            # 单个源失败已由后端落到 last_error，页面可见；这里汇总报错让 DAG 变红
            detail = "; ".join(f"{r.get('key')}: {r.get('error', '')}" for r in failed)
            raise AirflowFailException(f"{len(failed)} 个数据源同步失败 → {detail[:500]}")

        return results

    trigger_sync()


dag_instance = wecom_sheets_sync_dag()
