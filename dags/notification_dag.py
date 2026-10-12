"""工单截止时间预警 + 逾期通知 DAG（排程版）。

定期扫描处于 NEW / IN_PROGRESS / PENDING_REQUESTED 状态的工单，识别「需要提醒」的逻辑事件，
交给后端 **按接收人所在时区的工作时间** 排程发送（Outbox），DAG 本身不再直接推送。

规则 1 — 临期预警（未逾期，不区分优先级，每单预警两次）：
  - 第一次预警：距离截止时间 24~25 小时 → notify_type=9（occurrence=warn24h）
  - 第二次预警：距离截止时间 60~120 分钟 → notify_type=9（occurrence=warn1h）
  - 每个窗口宽 1 小时，DAG 每小时执行 1 次，天然每窗口最多命中 1 次
  - 若触发时已错过「截止前最后一个工作时段末尾」，后端不再补发（status=skipped），
    交由截止后的逾期提醒接管——例：周五 23:00 截止，warn24h 排到周五 17:30 发出，
    warn1h 在周五 22:00 触发时该时刻已过，跳过

规则 2 — 逾期（now >= deadline_at）：
  - 逾期天数 N = floor(逾期小时数 / 24)
  - 命中窗口：逾期小时数 ∈ [24N, 24N+1)，保证每个逾期天只命中一次
  - 逾期天数 = 0（0~24h）：通知受理人 → notify_type=6
  - 逾期天数 ≥ 1（≥24h）：通知 受理人 + 受理人上级（user.supervisor_id）→ notify_type=6
  - occurrence=day{N}，后端据此做幂等去重

发送时机由后端决定（working_time_service），规则：
  - 临期预警：截止在非工作时间 → 提前到「截止前最后一个工作时段末尾 -30min」
    例：周五 23:00 截止 → 周五 17:30 发
  - 逾期提醒：截止后 → 顺延到「截止后第一个工作时段开始」
    例：周五 23:00 截止且已逾期 → 周一 09:00 发
  - 节假日命中 → 顺延到节后首个工作日
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import requests
from airflow.decorators import dag, task

# ── 配置 ──────────────────────────────────────────────
API_BASE_URL = os.getenv("ORS_API_BASE_URL", "http://127.0.0.1:8400")
ADMIN_USERNAME = os.getenv("ORS_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ORS_ADMIN_PASSWORD", "usp2026@EP")

# 东八区
TZ_SHANGHAI = timezone(timedelta(hours=8))

# ── 临期预警窗口（不区分优先级，所有工单两次预警） ──
# 第一次预警：距截止 24~25 小时
NORMAL_MIN_HOURS = 24
NORMAL_MAX_HOURS = 25
# 第二次预警：距截止 60~120 分钟
URGENT_MIN_MINUTES = 60
URGENT_MAX_MINUTES = 120

# 需要扫描的工单状态（已挂起 pending 不参与提醒；暂停请求中仍持续计时）
ACTIVE_STATUSES = "new,in_progress,pending_requested"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── 工具函数 ──────────────────────────────────────────
def _parse_deadline(deadline_raw) -> datetime | None:
    """解析 API 返回的截止时间字符串为东八区 aware datetime"""
    if not deadline_raw:
        return None
    try:
        dt = datetime.fromisoformat(str(deadline_raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            # DB 存的是 naive UTC（见 backend convert_to_shanghai_time），
            # API 返回不带时区，这里按 UTC 解释后再转东八区
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(TZ_SHANGHAI)
    except Exception as e:
        logger.warning(f"解析截止时间失败: {deadline_raw}, {e}")
        return None


def _get_user_supervisor(token: str, username: str) -> str | None:
    """通过 API 查询用户的 supervisor_id，返回 supervisor 的 username"""
    if not username:
        return None
    try:
        resp = requests.get(
            f"{API_BASE_URL}/api/admin/users/{username}/detail",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        if resp.status_code != 200:
            logger.warning(f"查询用户 {username} 上级失败: {resp.status_code}")
            return None
        data = resp.json()
        return data.get("supervisor_id")  # supervisor_id 就是 user.username (id 字段)
    except Exception as e:
        logger.error(f"查询用户 {username} 上级异常: {e}")
        return None


def _send_cuiban(
    token: str,
    ticket_id: int,
    notify_type: int,
    target_user: str | list[str] | None = None,
    occurrence: str | None = None,
) -> str:
    """调用 cuiban-notification 接口；target_user 为 None 时自动用工单处理人。

    返回 "ok" / "skipped" / "error"：
      - ok      ：已受理并排程
      - skipped ：后端判定该提醒已错过有效时点（如临期预警的「最后一个工作时段末尾」
                  已过），不予补发，交由截止后的逾期提醒接管
      - error   ：调用失败
    """
    if isinstance(target_user, list):
        results = [
            _send_cuiban_single(token, ticket_id, notify_type, u, occurrence)
            for u in target_user
        ]
        if "ok" in results:
            return "ok"
        return "skipped" if results and all(r == "skipped" for r in results) else "error"
    return _send_cuiban_single(token, ticket_id, notify_type, target_user, occurrence)


def _send_cuiban_single(
    token: str,
    ticket_id: int,
    notify_type: int,
    assigned_to: str | None,
    occurrence: str | None = None,
) -> str:
    try:
        body: dict = {"ticket_id": ticket_id, "notify_type": notify_type}
        if assigned_to:
            body["assigned_to"] = assigned_to
        if occurrence:
            body["occurrence"] = occurrence
        resp = requests.post(
            f"{API_BASE_URL}/api/tasks/cuiban-notification",
            headers={"Authorization": f"Bearer {token}"},
            json=body,
            timeout=30,
        )
        if resp.status_code == 200:
            try:
                scheduled = resp.json().get("scheduled") or []
                if scheduled and all(s.get("status") == "skipped" for s in scheduled):
                    return "skipped"
            except Exception:
                pass
            return "ok"
        logger.error(
            f"工单 {ticket_id} notify_type={notify_type} assigned_to={assigned_to} "
            f"发送失败: {resp.status_code} {resp.text}"
        )
        return "error"
    except Exception as e:
        logger.error(f"工单 {ticket_id} notify_type={notify_type} 发送异常: {e}")
        return "error"


# ── DAG 定义 ──────────────────────────────────────────
@dag(
    dag_id="ticket_deadline_notification",
    description="工单截止时间预警 + 逾期升级通知",
    schedule="0 * * * *",  # 每整点执行一次
    start_date=datetime(2026, 1, 1, tzinfo=TZ_SHANGHAI),
    catchup=False,
    tags=["ticket", "notification"],
)
def ticket_deadline_notification_dag():

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
    def fetch_active_tasks(token: str) -> list[dict]:
        """分页拉取所有 NEW/IN_PROGRESS/PENDING 工单"""
        headers = {"Authorization": f"Bearer {token}"}
        all_items: list[dict] = []
        page = 1
        while True:
            resp = requests.get(
                f"{API_BASE_URL}/api/tasks/",
                headers=headers,
                params={"status": ACTIVE_STATUSES, "page": page, "size": 100},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("items", [])
            all_items.extend(items)
            total_pages = data.get("pages", 1)
            if page >= total_pages or not items:
                break
            page += 1
        logger.info(f"拉取到 {len(all_items)} 条活跃工单")
        return all_items

    @task
    def check_and_notify(token: str, tasks: list[dict]) -> dict:
        """检查每条工单，识别「临期预警 / 逾期」事件并交给后端排程发送。

        DAG 只负责**识别事件**，不再判断具体几点发送——发送时刻由后端按接收人
        所在时区的工作时间计算（working_time_service），重复与否由 occurrence 幂等键保证。
        """
        now = datetime.now(TZ_SHANGHAI)

        stats = {
            "warning_sent": 0,
            "warning_skipped": 0,
            "overdue_assignee": 0,
            "overdue_escalate": 0,
            "overdue_skipped": 0,
            "skipped_no_deadline": 0,
        }

        for t in tasks:
            ticket_id = t.get("id")
            deadline_raw = t.get("deadline_at")
            assigned_to = t.get("assigned_to")

            if not deadline_raw:
                stats["skipped_no_deadline"] += 1
                continue

            deadline = _parse_deadline(deadline_raw)
            if not deadline:
                continue

            minutes_to_deadline = (deadline - now).total_seconds() / 60

            # ──────── 1) 临期预警（未逾期，不区分优先级，每单预警两次） ────────
            # 窗口宽 1 小时，DAG 每小时执行 1 次，每窗口天然最多命中 1 次
            if minutes_to_deadline > 0:
                hours_to_deadline = minutes_to_deadline / 60
                # 两个窗口：24~25h（第一次）、60~120min（第二次）
                windows = [
                    ("warn24h", NORMAL_MIN_HOURS <= hours_to_deadline < NORMAL_MAX_HOURS),
                    ("warn1h", URGENT_MIN_MINUTES <= minutes_to_deadline < URGENT_MAX_MINUTES),
                ]
                for occurrence, in_window in windows:
                    if not in_window:
                        continue
                    result = _send_cuiban(token, ticket_id, 9, None, occurrence)
                    if result == "ok":
                        stats["warning_sent"] += 1
                        logger.info(
                            f"[预警-{occurrence}] 工单 {ticket_id} "
                            f"距截止 {minutes_to_deadline:.0f} 分钟，已排程"
                        )
                    elif result == "skipped":
                        stats["warning_skipped"] += 1
                        logger.info(
                            f"[预警-{occurrence}] 工单 {ticket_id} "
                            f"已过最后工作时段末尾，跳过（由逾期提醒接管）"
                        )

            # ──────── 2) 逾期（now >= deadline，即 minutes_to_deadline <= 0） ────────
            else:
                overdue_hours = -minutes_to_deadline / 60
                overdue_day = int(overdue_hours // 24)
                escalated = overdue_day >= 1

                # 每个逾期天只在其 [24N, 24N+1) 小时窗口内命中一次；
                # 配合 occurrence=day{N} 幂等键，重复执行不会重复提醒
                if not (24 * overdue_day <= overdue_hours < 24 * overdue_day + 1):
                    continue

                notify_targets: list[str] = []
                if assigned_to:
                    notify_targets.append(assigned_to)

                if escalated and assigned_to:
                    supervisor = _get_user_supervisor(token, assigned_to)
                    if supervisor and supervisor not in notify_targets:
                        notify_targets.append(supervisor)

                if not notify_targets:
                    continue

                result = _send_cuiban(token, ticket_id, 6, notify_targets, f"day{overdue_day}")
                if result == "ok":
                    if escalated:
                        stats["overdue_escalate"] += 1
                    else:
                        stats["overdue_assignee"] += 1
                    logger.info(
                        f"[逾期{'升级' if escalated else ''} day={overdue_day}] 工单 {ticket_id} "
                        f"逾期 {overdue_hours:.1f}h，已排程，通知对象: {notify_targets}"
                    )
                elif result == "skipped":
                    stats["overdue_skipped"] += 1

        logger.info(f"DAG 执行完成: {stats}")
        return stats

    # DAG 任务依赖
    token = login()
    tasks = fetch_active_tasks(token)
    check_and_notify(token, tasks)


dag_instance = ticket_deadline_notification_dag()
