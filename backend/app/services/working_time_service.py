"""工作日历与工作时间计算服务。

职责：把「命中即发」的通知时刻，换算成「接收人所在时区的工作时间」内的时刻。

规则（已与需求方确认）：
- 时区：用户上报 > 系统默认 `Asia/Shanghai`（公司/部门级覆盖暂不实现）
- 工作时段：公司级可配置，单段（不支持午休分段），无夜班
- 工作日：公司可配置（默认周一至周五）+ 法定节假日排除 + 调休补班
- 临期预警（notify_type=9）：截止在非工作时间 → 提前到**截止前最后一个工作时段末尾 -30min**
  （若该时刻已过 → 不再补发，交由逾期提醒接管）
- 逾期提醒（notify_type=6）：截止后 → 顺延到**截止后第一个工作时段开始**
- 节假日：命中即顺延到节后首个工作日

所有对外接口入参/出参的时间均为 **UTC**（naive 视作 UTC，与项目 DB 约定一致）。
"""
from __future__ import annotations

import logging
import time as _time
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, Optional, Set
from zoneinfo import ZoneInfo

from app.core.database import db_manager
from app.models.organization import Company
from app.models.work_calendar import CompanyHoliday

logger = logging.getLogger(__name__)


# ── 常量 ───────────────────────────────────────────────
SYSTEM_DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_WORK_START = time(9, 0)
DEFAULT_WORK_END = time(18, 0)
DEFAULT_WORKDAYS: Set[int] = {1, 2, 3, 4, 5}

# 「工作时段末尾」提前量：下班前 30 分钟发，避免卡在下班点用户已离岗
TAIL_OFFSET_MINUTES = 30

# 向前/向后查找工作日的最大天数，防御非法配置导致死循环
MAX_LOOKAHEAD_DAYS = 60

# 通知类型分流
NOTIFY_TYPE_WARNING = 9          # 临期预警
NOTIFY_TYPE_OVERDUE = 6          # 逾期提醒
IMMEDIATE_NOTIFY_TYPES = {1, 3, 7, 8, 10}   # 立即发：催办/状态变更/转派/@提醒/机器人告警
SCHEDULED_NOTIFY_TYPES = {2, 4, 5, 6, 9}    # 排程发：每日统计/旧逾期/新工单/逾期/临期预警

# 公司日历进程内缓存（公司数量少、变更低频，避免逐条通知查库）
_CALENDAR_CACHE: Dict[str, Dict[str, Any]] = {}
_CALENDAR_CACHE_TS: Dict[str, float] = {}
CALENDAR_TTL_SECONDS = 60


class WorkingTimeService:
    """工作时间判定与排程时刻计算。"""

    # ── 时间工具 ────────────────────────────────────────
    @staticmethod
    def now_utc() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def to_utc(dt: Optional[datetime]) -> Optional[datetime]:
        """naive 视作 UTC（DB 约定），aware 统一转 UTC。"""
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    @staticmethod
    def to_naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
        """转 naive UTC 以便写入 DateTime 列。"""
        utc = WorkingTimeService.to_utc(dt)
        return utc.replace(tzinfo=None) if utc else None

    @staticmethod
    def resolve_timezone(user: Optional[Dict[str, Any]]) -> ZoneInfo:
        """解析接收人有效时区：用户上报值 > 系统默认。"""
        tz_name = (user or {}).get("timezone")
        if tz_name:
            try:
                return ZoneInfo(tz_name)
            except Exception:
                logger.warning(f"非法时区 {tz_name}，回落 {SYSTEM_DEFAULT_TIMEZONE}")
        return ZoneInfo(SYSTEM_DEFAULT_TIMEZONE)

    @staticmethod
    def _parse_hhmm(value: Optional[str], fallback: time) -> time:
        if not value:
            return fallback
        try:
            hh, mm = str(value).split(":")[:2]
            return time(int(hh), int(mm))
        except Exception:
            return fallback

    @staticmethod
    def _parse_workdays(value: Optional[str]) -> Set[int]:
        if not value:
            return set(DEFAULT_WORKDAYS)
        days = set()
        for part in str(value).split(","):
            part = part.strip()
            if part.isdigit():
                d = int(part)
                if 1 <= d <= 7:
                    days.add(d)
        return days or set(DEFAULT_WORKDAYS)

    # ── 公司日历 ────────────────────────────────────────
    @classmethod
    def get_calendar(cls, company_id: Optional[str]) -> Dict[str, Any]:
        """读取公司工作日历（含节假日），带 60s 进程内缓存。"""
        key = company_id or "__system__"
        now_ts = _time.monotonic()
        cached = _CALENDAR_CACHE.get(key)
        if cached and now_ts - _CALENDAR_CACHE_TS.get(key, 0) < CALENDAR_TTL_SECONDS:
            return cached

        cal: Dict[str, Any] = {
            "work_start": DEFAULT_WORK_START,
            "work_end": DEFAULT_WORK_END,
            "workdays": set(DEFAULT_WORKDAYS),
            "holidays": {},
        }

        if company_id:
            db = db_manager.get_db()
            try:
                company = db.query(Company).filter(Company.id == company_id).first()
                if company:
                    cal["work_start"] = cls._parse_hhmm(company.work_start, DEFAULT_WORK_START)
                    cal["work_end"] = cls._parse_hhmm(company.work_end, DEFAULT_WORK_END)
                    cal["workdays"] = cls._parse_workdays(company.workdays)

                rows = (
                    db.query(CompanyHoliday)
                    .filter(CompanyHoliday.company_id == company_id)
                    .all()
                )
                for r in rows:
                    if r.holiday_date:
                        cal["holidays"][r.holiday_date] = r.type or "holiday"
            except Exception as e:
                logger.error(f"读取公司工作日历失败 company_id={company_id}: {e}")
            finally:
                db.close()

        _CALENDAR_CACHE[key] = cal
        _CALENDAR_CACHE_TS[key] = now_ts
        return cal

    @classmethod
    def invalidate_calendar_cache(cls, company_id: Optional[str] = None) -> None:
        if company_id:
            _CALENDAR_CACHE.pop(company_id, None)
            _CALENDAR_CACHE_TS.pop(company_id, None)
        else:
            _CALENDAR_CACHE.clear()
            _CALENDAR_CACHE_TS.clear()

    @classmethod
    def is_working_day(cls, company_id: Optional[str], day: date) -> bool:
        """是否工作日：workdays 命中且非 holiday；workday_override 视为工作日。"""
        cal = cls.get_calendar(company_id)
        override = cal["holidays"].get(day)
        if override == "workday_override":
            return True
        if override == "holiday":
            return False
        return day.isoweekday() in cal["workdays"]

    @classmethod
    def is_working_time(cls, user: Optional[Dict[str, Any]], moment_utc: datetime) -> bool:
        """给定 UTC 时刻是否落在接收人的工作时间内。"""
        tz = cls.resolve_timezone(user)
        local = cls.to_utc(moment_utc).astimezone(tz)
        company_id = (user or {}).get("company_id")
        if not cls.is_working_day(company_id, local.date()):
            return False
        cal = cls.get_calendar(company_id)
        start = local.replace(hour=cal["work_start"].hour, minute=cal["work_start"].minute,
                              second=0, microsecond=0)
        end = local.replace(hour=cal["work_end"].hour, minute=cal["work_end"].minute,
                            second=0, microsecond=0)
        if end <= start:      # 配置非法（夜班/跨午夜不受支持）→ 视作全天非工作，避免误发
            return False
        return start <= local < end

    # ── 排程时刻计算 ────────────────────────────────────
    @classmethod
    def previous_working_tail(cls, user: Optional[Dict[str, Any]], target_utc: datetime) -> datetime:
        """返回 **不晚于 target** 的最近工作时刻，用于临期预警前置。

        - target 本身在工作时间内 → 立即发（target）
        - 否则取「target 当天或之前最近一个工作日的工作时段末尾 - TAIL_OFFSET」
          例：周五 23:00 截止 → 周五 17:30 发
        """
        target = cls.to_utc(target_utc)
        if cls.is_working_time(user, target):
            return target

        tz = cls.resolve_timezone(user)
        local = target.astimezone(tz)
        company_id = (user or {}).get("company_id")
        cal = cls.get_calendar(company_id)

        for offset in range(0, MAX_LOOKAHEAD_DAYS + 1):
            day = local.date() - timedelta(days=offset)
            if not cls.is_working_day(company_id, day):
                continue
            tail = (
                datetime.combine(day, cal["work_end"], tzinfo=tz)
                - timedelta(minutes=TAIL_OFFSET_MINUTES)
            )
            # tail 必须不晚于 target；offset=0 当天上班前的情形会落到前一个工作日
            if tail <= local:
                return tail.astimezone(timezone.utc)
        return target

    @classmethod
    def next_working_start(cls, user: Optional[Dict[str, Any]], target_utc: datetime) -> datetime:
        """返回 **不早于 target** 的最近工作时刻，用于逾期提醒后置。

        - target 本身在工作时间内 → 立即发（target）
        - 否则取「target 当天或之后最近一个工作日的工作时段开始」
          例：周五 23:00 截止 → 周一 09:00 发
        """
        target = cls.to_utc(target_utc)
        if cls.is_working_time(user, target):
            return target

        tz = cls.resolve_timezone(user)
        local = target.astimezone(tz)
        company_id = (user or {}).get("company_id")
        cal = cls.get_calendar(company_id)

        for offset in range(0, MAX_LOOKAHEAD_DAYS + 1):
            day = local.date() + timedelta(days=offset)
            if not cls.is_working_day(company_id, day):
                continue
            start = datetime.combine(day, cal["work_start"], tzinfo=tz)
            if start >= local:
                return start.astimezone(timezone.utc)
        return target

    @classmethod
    def plan_send_time(
        cls,
        user: Optional[Dict[str, Any]],
        notify_type: int,
        deadline_at: Optional[datetime] = None,
        now: Optional[datetime] = None,
    ) -> Optional[datetime]:
        """计算某条通知对该接收人的计划发送时刻（UTC aware）。

        返回 None 表示**无需再发**（当前已错过该提醒的有效时点，由后续提醒接管）：
        仅临期预警会出现——「截止前最后一个工作时段末尾」已过，此时补发「即将逾期」
        既落在非工作时间、又已失去提前量，故直接放弃，交由截止后的逾期提醒负责。

        其余情形：计划时刻若已过期一律收敛为「立即发」——宁可稍晚，也不能漏发。
        """
        now_utc = cls.to_utc(now) or cls.now_utc()

        if notify_type == NOTIFY_TYPE_WARNING and deadline_at:
            planned = cls.previous_working_tail(user, deadline_at)
            # 例：周五 23:00 截止 → 目标是周五 17:30；若第二轮预警在周五 22:00 才触发，
            # 目标时刻已过 → 不再发，等待截止后的逾期提醒（会顺延到周一 09:00）
            if planned < now_utc:
                return None
            return planned

        if notify_type == NOTIFY_TYPE_OVERDUE and deadline_at:
            planned = cls.next_working_start(user, deadline_at)
        elif cls.is_working_time(user, now_utc):
            planned = now_utc
        else:
            planned = cls.next_working_start(user, now_utc)

        return max(planned, now_utc)

    # ── 便捷入口 ────────────────────────────────────────
    @classmethod
    def get_user_context(cls, user_id: str) -> Optional[Dict[str, Any]]:
        """按 users.id 取用户字典（含 timezone / company_id）。"""
        if not user_id:
            return None
        try:
            return db_manager.get_user_by_id(user_id)
        except Exception as e:
            logger.error(f"查询用户失败 user_id={user_id}: {e}")
            return None

    @classmethod
    def should_schedule(cls, notify_type: int) -> bool:
        """该通知类型是否需要走工作时间排程。"""
        return notify_type in SCHEDULED_NOTIFY_TYPES and notify_type not in IMMEDIATE_NOTIFY_TYPES
