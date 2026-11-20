"""企业微信项目数据周期同步 DAG。

替代前端在 ProjectDetail.tsx 中的 5 分钟轮询（fetchLiveWecom + setInterval）。
DAG 周期性触发后端 `/api/tasks/sources/wecom/projects/sync` 接口，由后端
`WecomProjectAdapter.sync_projects()` 完成从企微 Smartsheet 拉取 → 字段映射 →
upsert project 表 → 自动授权调度对接人，幂等可重复执行。

设计要点：
  - 频率与前端原 AUTO_SYNC_INTERVAL（5 * 60 * 1000 ms）保持一致：每 5 分钟一次
  - 使用 admin 账户登录获取 JWT，与既有 notification_dag.py 共用鉴权方式
  - 复用后端既有的「Airflow / 手动共用此入口」语义（见 integrations/api.py 注释）
  - catchup=False，避免回填历史；单条 DAG Run 失败不影响下一周期
  - 同步结果以 XCom 透传，便于 Airflow UI 直接查看 created/updated/skipped 等指标
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
ADMIN_USERNAME = os.getenv("ORS_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ORS_ADMIN_PASSWORD", "usp2026@EP")

# 与前端 AUTO_SYNC_INTERVAL 对齐：5 分钟
SYNC_INTERVAL_MINUTES = 30

# 东八区
TZ_SHANGHAI = timezone(timedelta(hours=8))

# 同步接口路径（见 backend/app/integrations/api.py）
WECOM_SYNC_PATH = "/api/tasks/sources/wecom/projects/sync"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── DAG 定义 ──────────────────────────────────────────
@dag(
    dag_id="wecom_projects_sync",
    description="企业微信项目数据周期同步（替代前端 5 分钟轮询）",
    schedule=f"*/{SYNC_INTERVAL_MINUTES} * * * *",  # 每 5 分钟
    start_date=datetime(2026, 1, 1, tzinfo=TZ_SHANGHAI),
    catchup=False,
    tags=["wecom", "projects", "sync"],
)
def wecom_projects_sync_dag():

    @task
    def login() -> str:
        """用 admin 账户登录，返回 access_token"""
        resp = requests.post(
            f"{API_BASE_URL}/api/auth/login",
            json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
            timeout=30,
        )
        resp.raise_for_status()
        token = resp.json()["access_token"]
        logger.info("admin 登录成功")
        return token

    @task
    def trigger_sync(token: str) -> dict:
        """触发后端 wecom 项目同步接口，返回同步统计指标。

        后端流程：拉取企微 Smartsheet → 字段映射 → upsert project 表 → 自动授权对接人
        该接口幂等，重复触发只会刷新数据，不会产生重复记录。
        """
        headers = {"Authorization": f"Bearer {token}"}
        resp = requests.post(
            f"{API_BASE_URL}{WECOM_SYNC_PATH}",
            headers=headers,
            timeout=120,  # 企微拉取可能较慢，给 2 分钟
        )
        if resp.status_code != 200:
            logger.error(f"wecom 同步接口返回非 200: {resp.status_code} {resp.text}")
            raise AirflowFailException(
                f"wecom 同步触发失败: HTTP {resp.status_code} {resp.text}"
            )

        body = resp.json()
        # 后端返回 {code, message, data: {...stats}}
        if body.get("code") not in (200, 0):
            logger.error(f"wecom 同步业务失败: {body}")
            raise AirflowFailException(f"wecom 同步业务失败: {body}")

        stats = body.get("data") or {}
        logger.info(
            "wecom 同步完成: fetched=%s created=%s updated=%s skipped=%s "
            "filtered=%s pending=%s authorized=%s errors=%d",
            stats.get("fetched"),
            stats.get("created"),
            stats.get("updated"),
            stats.get("skipped"),
            stats.get("filtered"),
            stats.get("pending"),
            stats.get("authorized"),
            len(stats.get("errors") or []),
        )
        if stats.get("errors"):
            # 单条记录失败由后端隔离，不阻塞 DAG；仅记录前 5 条便于排查
            for err in stats["errors"][:5]:
                logger.warning(f"wecom 同步单条错误: {err}")
        return stats

    # DAG 任务依赖
    token = login()
    trigger_sync(token)


dag_instance = wecom_projects_sync_dag()
