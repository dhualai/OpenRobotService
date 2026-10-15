import logging
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.wecom_sheet import ExternalRecord


logger = logging.getLogger(__name__)
SOURCE_KEY = "zl_question_robots"
REPORT_TIME_FIELD = "上报时间(自动生成，无需填写)"
BUSINESS_TIMEZONE = timezone(timedelta(hours=8))
EXCEL_EPOCH = datetime(1899, 12, 30, tzinfo=BUSINESS_TIMEZONE)


def parse_report_time(value: Any) -> Optional[datetime]:
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if re.fullmatch(r"\d{10}(?:\.\d+)?|\d{13}", text):
            timestamp = float(text)
            if len(text.split(".")[0]) == 13:
                timestamp /= 1000
            return datetime.fromtimestamp(timestamp, BUSINESS_TIMEZONE)
        if re.fullmatch(r"\d{1,7}(?:\.\d+)?", text):
            serial = float(text)
            if not 61 <= serial < 2958466:
                return None
            return EXCEL_EPOCH + timedelta(days=serial)
        normalized = text.replace("/", "-")
        try:
            parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
            for pattern in ("%Y-%m-%d %I:%M %p", "%Y-%m-%d %I:%M:%S %p", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    parsed = datetime.strptime(normalized.upper(), pattern)
                    break
                except ValueError:
                    continue
            if parsed is None:
                return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=BUSINESS_TIMEZONE)
        return parsed.astimezone(BUSINESS_TIMEZONE)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "、".join(filter(None, (_text(item) for item in value)))
    if isinstance(value, dict):
        return _text(value.get("text"))
    return str(value).strip()


async def get_questions(
    db: AsyncSession, report_date: date, page: int = 1, page_size: int = 20,
) -> Dict[str, Any]:
    raw_report_value = func.coalesce(
        func.nullif(func.trim(cast(ExternalRecord.values[REPORT_TIME_FIELD].as_string(), String(128))), ""),
        func.trim(cast(ExternalRecord.values["上报时间"].as_string(), String(128))),
    )
    report_value = raw_report_value
    source_filter = ExternalRecord.source_key == SOURCE_KEY
    last_sync_at = (await db.execute(
        select(func.max(ExternalRecord.pulled_at)).where(source_filter)
    )).scalar_one_or_none()

    candidates = []
    for offset in (-1, 0, 1):
        try:
            nearby_date = report_date + timedelta(days=offset)
        except OverflowError:
            continue
        for separator in ("-", "/"):
            for month in {str(nearby_date.month), f"{nearby_date.month:02d}"}:
                for day in {str(nearby_date.day), f"{nearby_date.day:02d}"}:
                    prefix = separator.join((f"{nearby_date.year:04d}", month, day))
                    candidates.append(report_value.like(f"{prefix}%"))
    start = int(datetime.combine(report_date, time.min, BUSINESS_TIMEZONE).timestamp())
    end = start + 86400
    for factor in (1, 1000):
        candidates.append((report_value >= str(start * factor)) & (report_value < str(end * factor)))
    excel_day = (report_date - EXCEL_EPOCH.date()).days
    candidates.extend((report_value == str(excel_day), report_value.like(f"{excel_day}.%")))

    rows = (await db.execute(
        select(ExternalRecord.id, ExternalRecord.record_id, ExternalRecord.values)
        .where(source_filter, or_(*candidates))
    )).mappings().all()
    matched = []
    invalid = 0
    for row in rows:
        values = row["values"]
        if not isinstance(values, dict):
            invalid += 1
            continue
        raw_time = _text(values.get(REPORT_TIME_FIELD)) or _text(values.get("上报时间"))
        reported_at = parse_report_time(raw_time)
        if reported_at is None:
            invalid += 1
            continue
        if reported_at.date() != report_date:
            continue
        matched.append((reported_at, row["id"], {
            "record_id": row["record_id"],
            "reported_at": (
                reported_at.date().isoformat()
                if re.fullmatch(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", str(raw_time).strip())
                else reported_at.isoformat()
            ),
            "problem_description": _text(values.get("问题描述")),
            "solution_description": _text(values.get("问题解决描述")),
            "vehicle_number": _text(values.get("车辆编号")),
            "vehicle_model": _text(values.get("车型")),
            "vehicle_info": _text(values.get("车辆编号和车型")),
        }))
    if invalid:
        logger.warning("Question dashboard skipped %s invalid records for %s", invalid, report_date)
    matched.sort(key=lambda item: (item[0], item[1]), reverse=True)
    skip = (page - 1) * page_size
    return {
        "report_date": report_date.isoformat(),
        "total": len(matched),
        "page": page,
        "page_size": page_size,
        "items": [item[2] for item in matched[skip:skip + page_size]],
        "last_sync_at": last_sync_at.replace(tzinfo=timezone.utc).isoformat() if last_sync_at else None,
    }
