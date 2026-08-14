"""工单截止时间预警 + 逾期通知 DAG。

定期扫描处于 NEW / IN_PROGRESS / PENDING 状态的工单，按截止时间匹配规则发送通知：

规则 1 — 临期预警（未逾期，不区分优先级，每单预警两次）：
  - 第一次预警：距离截止时间 24~25 小时 → notify_type=9
  - 第二次预警：距离截止时间 60~120 分钟 → notify_type=9
  - 去重：{ticket_id_window: deadline_at}，两个窗口各自独立去重，截止时间变更后重新通知

规则 2 — 逾期（now > deadline_at）：
  - 逾期 < 24h：每日通知一次受理人 → notify_type=6（模板6：工单逾期提醒）
  - 逾期 ≥ 24h：每日通知一次 受理人 + 受理人上级（user.supervisor_id）→ notify_type=6
  - 去重：{ticket_id: date(YYYY-MM-DD)}，同一张工单同一天只通知一次

通知对象：
  - cuiban-notification 接口支持前端传 assigned_to 指定通知对象；逾期升级时额外查 supervisor
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date as _date
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from airflow.decorators import dag, task

# ── 配置 ──────────────────────────────────────────────
API_BASE_URL = os.getenv("ORS_API_BASE_URL", "http://127.0.0.1:8400")
ADMIN_USERNAME = os.getenv("ORS_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ORS_ADMIN_PASSWORD", "usp2026@EP")

# 去重记录文件
DEDUP_FILE = Path(os.getenv("ORS_DEDUP_FILE", "/data/apps/airflow/dags/notification_sent.json"))

# 东八区
TZ_SHANGHAI = timezone(timedelta(hours=8))

# ── 临期预警窗口（不区分优先级，所有工单两次预警） ──
# 第一次预警：距截止 24~25 小时
NORMAL_MIN_HOURS = 24
NORMAL_MAX_HOURS = 25
# 第二次预警：距截止 60~120 分钟
URGENT_MIN_MINUTES = 60
URGENT_MAX_MINUTES = 120

# 逾期升级阈值（小时）
OVERDUE_ESCALATE_HOURS = 24

# 需要扫描的工单状态
ACTIVE_STATUSES = "new,in_progress,pending"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── 工具函数 ──────────────────────────────────────────
def _load_dedup() -> dict:
    """加载去重记录：
    {
      "warning": {ticket_id(str): deadline_iso(str)},   // 临期预警
      "overdue": {ticket_id(str): date_str(YYYY-MM-DD)}  // 逾期每日
    }
    """
    try:
        if DEDUP_FILE.exists():
            data = json.loads(DEDUP_FILE.read_text(encoding="utf-8"))
            # 兼容旧版格式：若顶层直接是 warning 的数据则迁移
            if "warning" not in data and "overdue" not in data:
                return {"warning": data, "overdue": {}}
            return data
    except Exception as e:
        logger.warning(f"加载去重记录失败，将视为空: {e}")
    return {"warning": {}, "overdue": {}}


def _save_dedup(record: dict) -> None:
    """保存去重记录"""
    try:
        DEDUP_FILE.parent.mkdir(parents=True, exist_ok=True)
        DEDUP_FILE.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.error(f"保存去重记录失败: {e}")


def _parse_deadline(deadline_raw) -> datetime | None:
    """解析 API 返回的截止时间字符串为东八区 aware datetime"""
    if not deadline_raw:
        return None
    try:
        dt = datetime.fromisoformat(str(deadline_raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ_SHANGHAI)
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


def _send_cuiban(token: str, ticket_id: int, notify_type: int, target_user: str | list[str] | None = None) -> bool:
    """调用 cuiban-notification 接口；target_user 为 None 时自动用工单处理人"""
    payload: dict = {"ticket_id": ticket_id, "notify_type": notify_type}
    if target_user:
        if isinstance(target_user, list):
            # 传列表时用 assigned_to 指定一个，其他人通过 to_admin 不支持，
            # 这里改为逐个发（保证每个都能收到）
            ok = True
            for u in target_user:
                if not _send_cuiban_single(token, ticket_id, notify_type, u):
                    ok = False
            return ok
        return _send_cuiban_single(token, ticket_id, notify_type, target_user)
    return _send_cuiban_single(token, ticket_id, notify_type, None)


def _send_cuiban_single(token: str, ticket_id: int, notify_type: int, assigned_to: str | None) -> bool:
    try:
        body: dict = {"ticket_id": ticket_id, "notify_type": notify_type}
        if assigned_to:
            body["assigned_to"] = assigned_to
        resp = requests.post(
            f"{API_BASE_URL}/api/tasks/cuiban-notification",
            headers={"Authorization": f"Bearer {token}"},
            json=body,
            timeout=30,
        )
        if resp.status_code == 200:
            return True
        logger.error(
            f"工单 {ticket_id} notify_type={notify_type} assigned_to={assigned_to} "
            f"发送失败: {resp.status_code} {resp.text}"
        )
        return False
    except Exception as e:
        logger.error(f"工单 {ticket_id} notify_type={notify_type} 发送异常: {e}")
        return False


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
        """检查每条工单，分别处理临期预警 + 逾期升级"""
        now = datetime.now(TZ_SHANGHAI)
        today_str = now.strftime("%Y-%m-%d")
        dedup = _load_dedup()
        warning_dedup: dict = dedup.setdefault("warning", {})
        overdue_dedup: dict = dedup.setdefault("overdue", {})

        stats = {
            "warning_sent": 0,
            "warning_skipped_dedup": 0,
            "overdue_assignee": 0,
            "overdue_escalate": 0,
            "overdue_skipped_dedup": 0,
            "skipped_no_deadline": 0,
        }

        for t in tasks:
            ticket_id = t.get("id")
            priority = (t.get("priority") or "").lower()
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
            if minutes_to_deadline >= 0:
                hours_to_deadline = minutes_to_deadline / 60
                # 两个窗口：24~25h（第一次）、60~120min（第二次）
                windows = [
                    ("24h", NORMAL_MIN_HOURS <= hours_to_deadline < NORMAL_MAX_HOURS),
                    ("1h", URGENT_MIN_MINUTES <= minutes_to_deadline < URGENT_MAX_MINUTES),
                ]
                for win_tag, in_window in windows:
                    if not in_window:
                        continue
                    w_key = f"{ticket_id}_{win_tag}"
                    if warning_dedup.get(w_key) == str(deadline_raw):
                        stats["warning_skipped_dedup"] += 1
                        continue
                    if _send_cuiban(token, ticket_id, 9):
                        warning_dedup[w_key] = str(deadline_raw)
                        stats["warning_sent"] += 1
                        logger.info(
                            f"[预警-{win_tag}] 工单 {ticket_id} "
                            f"距截止 {minutes_to_deadline:.0f} 分钟，已通知"
                        )

            # ──────── 2) 逾期（now > deadline） ────────
            else:
                overdue_minutes = -minutes_to_deadline
                overdue_hours = overdue_minutes / 60

                o_key = str(ticket_id)
                escalated = overdue_hours >= OVERDUE_ESCALATE_HOURS

                # 去重记录格式: "YYYY-MM-DD:level"，level ∈ {normal, escalate}
                # 兼容旧格式 "YYYY-MM-DD"（视为 normal）
                last_raw = overdue_dedup.get(o_key, "")
                last_date, last_level = (last_raw.split(":", 1) + [""])[:2] if last_raw else ("", "")
                if last_level not in ("normal", "escalate"):
                    # 旧格式无 level，last_date 即整个值，level 视为已发普通
                    last_date = last_raw
                    last_level = "normal" if last_raw else ""

                # 去重判断：
                # - 今天已发升级 → 跳过（最高级别，无需再发）
                # - 今天已发普通，本次普通 → 跳过
                # - 今天已发普通，本次升级 → 允许（级别提升，上级需要知道）
                if last_date == today_str:
                    if last_level == "escalate":
                        stats["overdue_skipped_dedup"] += 1
                        continue
                    elif last_level == "normal" and not escalated:
                        stats["overdue_skipped_dedup"] += 1
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

                sent = _send_cuiban(token, ticket_id, 6, notify_targets)
                if sent:
                    new_level = "escalate" if escalated else "normal"
                    overdue_dedup[o_key] = f"{today_str}:{new_level}"
                    if escalated:
                        stats["overdue_escalate"] += 1
                    else:
                        stats["overdue_assignee"] += 1
                    logger.info(
                        f"[逾期{'升级' if escalated else ''}] 工单 {ticket_id} "
                        f"逾期 {overdue_hours:.1f}h，通知对象: {notify_targets}"
                    )

        _save_dedup(dedup)
        logger.info(f"DAG 执行完成: {stats}")
        return stats

    # DAG 任务依赖
    token = login()
    tasks = fetch_active_tasks(token)
    check_and_notify(token, tasks)


dag_instance = ticket_deadline_notification_dag()
