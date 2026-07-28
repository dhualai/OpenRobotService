"""
Airflow DAG: 每小时调用 AI 工单摘要接口

触发频率: 每小时整点 (0 * * * *)
功能: 调用 POST /api/ai/task/summarize 触发 AI 自动扫描活跃工单并生成摘要

部署说明:
    1. 将本文件放入 Airflow 的 dags 目录 (AIRFLOW__CORE__DAGS_FOLDER)
    2. 下方 AI_SERVICE_URL 变量改为实际服务地址
    3. 确保 Airflow 环境安装了 requests 依赖 (pip install requests)
"""

import time
import requests
from datetime import datetime

from airflow import DAG
from airflow.operators.python import PythonOperator


# ─── 配置区 ───────────────────────────────────────────────
# TODO: 替换为实际的 AI 服务地址
AI_SERVICE_URL = "http://127.0.0.1:8401/api/ai/task/summarize"

# 请求超时 (秒)
REQUEST_TIMEOUT = 600

# 请求重试次数 (含首次)
MAX_RETRIES = 3
RETRY_DELAY = 30

# ─── 业务逻辑 ─────────────────────────────────────────────


def call_summarize_api(**context):
    """调用 AI 摘要接口，带重试机制"""
    log = context.get("ti", None)
    execution_date = context.get("execution_date", datetime.utcnow())

    print(f"[{execution_date}] 开始调用 AI 摘要接口: {AI_SERVICE_URL}")

    last_exception = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            print(f"  第 {attempt}/{MAX_RETRIES} 次请求...")
            response = requests.post(
                AI_SERVICE_URL,
                json={},
                headers={"Content-Type": "application/json"},
                timeout=REQUEST_TIMEOUT,
            )

            print(f"  HTTP {response.status_code} | 耗时 {response.elapsed.total_seconds():.2f}s")

            if response.status_code == 200:
                data = response.json()
                if data.get("code") == 0:
                    print(f"  ✅ 摘要任务执行成功: {data.get('data')}")
                    return data
                else:
                    last_exception = Exception(f"业务错误: {data.get('message', '未知错误')}")
            else:
                last_exception = Exception(
                    f"HTTP {response.status_code}: {response.text[:500]}"
                )

        except requests.exceptions.Timeout:
            last_exception = TimeoutError(f"请求超时 (> {REQUEST_TIMEOUT}s)")
            print(f"  ⚠️  超时: {last_exception}")

        except requests.exceptions.ConnectionError as e:
            last_exception = ConnectionError(f"连接失败: {e}")
            print(f"  ⚠️  连接失败: {last_exception}")

        except requests.exceptions.RequestException as e:
            last_exception = e
            print(f"  ⚠️  请求异常: {e}")

        if attempt < MAX_RETRIES:
            print(f"  等待 {RETRY_DELAY}s 后重试...")
            time.sleep(RETRY_DELAY)

    raise RuntimeError(f"摘要接口调用失败，已重试 {MAX_RETRIES} 次。最后错误: {last_exception}")


# ─── DAG 定义 ──────────────────────────────────────────────

default_args = {
    "owner": "ai-platform",
    "depends_on_past": False,
    "retries": 2,
    "retry_delay": 60,
}

with DAG(
    dag_id="ai_hourly_summarize",
    default_args=default_args,
    description="每小时调用 AI 工单摘要接口，自动扫描活跃工单生成摘要",
    schedule="0 * * * *",
    start_date=datetime(2026, 7, 28),
    catchup=False,
    tags=["ai", "summarize", "hourly"],
    max_active_runs=1,
) as dag:

    PythonOperator(
        task_id="trigger_ai_summarize",
        python_callable=call_summarize_api,
    )
