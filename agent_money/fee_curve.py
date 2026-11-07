"""Fee-curve arithmetic for escalating-liability experiments.

Pure functions, no I/O, so the boundaries are cheap to test. Models the
PROJECT LAST STAND V2 rule set:

    DAILY_FEE(n)  = step * n
    TOTAL_FEES(n) = step * n * (n + 1) / 2
    free preparation window = FREE_HOURS from start, then billable day 1 begins

`n` never resets at a month boundary, which is the detail most
implementations get wrong when they derive the day count from a calendar.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from .money import MoneyError, parse

DEFAULT_STEP = Decimal("5.00")
FREE_HOURS = 168  # 7 x 24h


def daily_fee(n: int, step: Decimal = DEFAULT_STEP) -> Decimal:
    """Fee for billable day n. Day numbering starts at 1."""
    if n < 1:
        raise ValueError("billable day numbering starts at 1")
    return parse(step) * n


def lifetime_fees(n: int, step: Decimal = DEFAULT_STEP) -> Decimal:
    """Cumulative fees after n billable days: step*n*(n+1)/2."""
    if n < 0:
        raise ValueError("n must be >= 0")
    return parse(step) * n * (n + 1) / 2


def parse_iso8601(ts: str) -> datetime:
    """Parse ISO8601; naive input is treated as UTC rather than guessed."""
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def billable_days(start_ts: str, now_ts: Optional[str] = None,
                  free_hours: int = FREE_HOURS) -> int:
    """Completed billable days, i.e. days whose fee has already accrued.

    Zero during the free window. A fee accrues at the END of each completed 24h
    period, so a partial day does not count.
    """
    start = parse_iso8601(start_ts)
    now = parse_iso8601(now_ts) if now_ts else datetime.now(timezone.utc)
    billable_start = start + timedelta(hours=free_hours)
    if now < billable_start:
        return 0
    return int((now - billable_start) // timedelta(hours=24))


def preparation_remaining_seconds(start_ts: str, now_ts: Optional[str] = None,
                                  free_hours: int = FREE_HOURS) -> int:
    """Seconds left in the free window; 0 once preparation is over."""
    start = parse_iso8601(start_ts)
    now = parse_iso8601(now_ts) if now_ts else datetime.now(timezone.utc)
    return max(0, int((timedelta(hours=free_hours) - (now - start)).total_seconds()))


def month_key(ts: str, tz_name: str = "Europe/Zurich") -> str:
    """Settlement month key (YYYY-MM) in the settlement timezone.

    Boundary cases matter: 23:30 UTC on 31 January is already February in
    Zurich, so a UTC-derived month silently misattributes the settlement.
    """
    dt = parse_iso8601(ts)
    try:
        from zoneinfo import ZoneInfo

        local = dt.astimezone(ZoneInfo(tz_name))
    except Exception:
        local = dt.astimezone(timezone(timedelta(hours=1)))
    return f"{local.year:04d}-{local.month:02d}"


def runway_days(net_daily_revenue, start_ts: str, now_ts: Optional[str] = None,
                step: Decimal = DEFAULT_STEP) -> Optional[int]:
    """Days of runway at a constant net daily revenue before equity hits zero.

    Solves step*k*(k+1)/2 <= R*k for the largest integer k:

        k <= (R - step/2) / (step/2)

    Returns:
        0   - revenue cannot cover even the first billable day, so the position
              is insolvent immediately (e.g. R=4 against a 5/day curve).
        n>0 - that many days of cover.
        None - no revenue at all, so runway is undefined rather than zero.

    Note this is a constant-revenue model. Real revenue is lumpy, which is why
    the projection must be labelled non-authoritative.
    """
    n0 = billable_days(start_ts, now_ts)
    r = parse(net_daily_revenue)
    half_step = parse(step) / 2
    if r <= 0:
        return None
    if r <= half_step:
        return 0
    return max(0, int((r - half_step) / half_step) - n0)


def projection(start_ts: str, now_ts: Optional[str] = None,
               step: Decimal = DEFAULT_STEP) -> dict:
    """Fee-curve projection. Always labelled non-authoritative."""
    n = billable_days(start_ts, now_ts)
    return {
        "billable_days_completed": n,
        "projected_accrued_fees": str(lifetime_fees(n, step).quantize(Decimal("0.01"))),
        "next_daily_fee": str(daily_fee(n + 1, step)),
        "projection": True,
    }


__all__ = [
    "daily_fee", "lifetime_fees", "billable_days", "preparation_remaining_seconds",
    "month_key", "runway_days", "projection", "parse_iso8601", "MoneyError",
]
