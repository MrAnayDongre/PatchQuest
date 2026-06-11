"""Schedule next-run calculation for all schedule types."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def compute_next_run(
    schedule_type: str,
    schedule_expr: str | None,
    tz: str = "UTC",
    from_time: datetime | None = None,
) -> str:
    zone = ZoneInfo(tz)
    now = from_time or datetime.now(zone)

    if schedule_type == "one_shot":
        if schedule_expr:
            return schedule_expr
        return (now + timedelta(minutes=1)).isoformat()

    if schedule_type == "interval":
        minutes = _parse_interval_minutes(schedule_expr or "60")
        next_dt = now + timedelta(minutes=minutes)
        return next_dt.isoformat()

    if schedule_type == "daily":
        time_str = schedule_expr or "09:00"
        hour, minute = _parse_time(time_str)
        next_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if next_dt <= now:
            next_dt += timedelta(days=1)
        return next_dt.isoformat()

    if schedule_type == "weekly":
        parts = (schedule_expr or "1,09:00").split(",")
        day_part = parts[0].strip() if parts else "1"
        time_part = parts[1].strip() if len(parts) > 1 else "09:00"
        target_days = _parse_weekdays(day_part)
        hour, minute = _parse_time(time_part)

        for offset in range(1, 8):
            candidate = now + timedelta(days=offset)
            if candidate.weekday() in target_days:
                next_dt = candidate.replace(hour=hour, minute=minute, second=0, microsecond=0)
                return next_dt.isoformat()

        next_dt = now + timedelta(days=7)
        return next_dt.replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat()

    if schedule_type == "cron":
        return _compute_cron_next(schedule_expr or "0 9 * * *", zone, now)

    return (now + timedelta(hours=1)).isoformat()


def _parse_interval_minutes(expr: str) -> int:
    expr = expr.strip().lower()
    if expr.endswith("h"):
        return int(expr[:-1]) * 60
    if expr.endswith("d"):
        return int(expr[:-1]) * 1440
    if expr.endswith("m"):
        return int(expr[:-1])
    return int(expr)


def _parse_time(time_str: str) -> tuple[int, int]:
    parts = time_str.strip().split(":")
    return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0


def _parse_weekdays(day_str: str) -> set[int]:
    day_map = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
    days = set()
    for part in day_str.split("+"):
        part = part.strip().lower()
        if part in day_map:
            days.add(day_map[part])
        elif part == "weekday" or part == "weekdays":
            days.update({0, 1, 2, 3, 4})
        elif part == "weekend":
            days.update({5, 6})
        elif part.isdigit():
            days.add(int(part) % 7)
    return days or {0}


def _compute_cron_next(expr: str, zone: ZoneInfo, now: datetime) -> str:
    try:
        from croniter import croniter
        cron = croniter(expr, now)
        next_dt = cron.get_next(datetime)
        return next_dt.isoformat()
    except ImportError:
        pass

    # Fallback: parse simple cron (minute hour * * *)
    parts = expr.split()
    if len(parts) >= 2:
        try:
            minute = int(parts[0]) if parts[0] != "*" else 0
            hour = int(parts[1]) if parts[1] != "*" else 9
            next_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if next_dt <= now:
                next_dt += timedelta(days=1)
            return next_dt.isoformat()
        except ValueError:
            pass

    return (now + timedelta(hours=1)).isoformat()
