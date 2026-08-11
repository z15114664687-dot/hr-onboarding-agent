from __future__ import annotations

from datetime import date, datetime, timedelta


def utc_now() -> datetime:
    """Return current UTC datetime."""

    return datetime.utcnow()


def days_overdue(due_date: date, today: date | None = None) -> int:
    """Return positive overdue days, or zero when not overdue."""

    current = today or date.today()
    return max((current - due_date).days, 0)


def add_business_days(start_date: date, days: int) -> date:
    """Add business days, treating Monday to Friday as workdays."""

    if days < 0:
        raise ValueError("days must be non-negative")
    current = start_date
    remaining = days
    while remaining > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


def add_months(start_date: date, months: int) -> date:
    """Add calendar months while clamping to the target month's last day."""

    if months < 0:
        raise ValueError("months must be non-negative")
    month_index = start_date.month - 1 + months
    year = start_date.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start_date.day, _last_day_of_month(year, month))
    return date(year, month, day)


def _last_day_of_month(year: int, month: int) -> int:
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    return (next_month - timedelta(days=1)).day
