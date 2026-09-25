"""Weekly aggregation: sum a Monday–Sunday week of meals and compare with the weekly targets.

For the current week we also compute a "pace" benchmark: after 3 of 7 days, being at
~43% of a weekly target is on track. Past weeks are judged against the full 100%.
Nutrients with an upper limit (e.g. sodium) are flagged "Over limit" when the week's
intake exceeds 7 × the daily upper limit.
"""
from datetime import date, timedelta

from .nutrients import Nutrient

TOP_N = 5
# Status thresholds, as a fraction of the expected progress for this point in the week.
ON_TRACK_RATIO = 0.85
FAR_BEHIND_RATIO = 0.5


def week_bounds(any_day: date) -> tuple[date, date]:
    start = any_day - timedelta(days=any_day.weekday())
    return start, start + timedelta(days=6)


def _status(pct: float, expected_pct: float, over_limit: bool) -> str:
    if over_limit:
        return "Over limit"
    if pct >= 100:
        return "Reached"
    if expected_pct <= 0:
        return "Not started"
    ratio = pct / expected_pct
    if ratio >= ON_TRACK_RATIO:
        return "On track"
    if ratio >= FAR_BEHIND_RATIO:
        return "Behind"
    return "Far behind"


def _days_elapsed(start: date, end: date, today: date) -> int:
    if today < start:
        return 0
    if today > end:
        return 7
    return (today - start).days + 1


def summarize_week(meals: list[dict], nutrients: list[Nutrient], any_day: date, today: date) -> dict:
    start, end = week_bounds(any_day)
    week_meals = [m for m in meals if start.isoformat() <= m["date"] <= end.isoformat()]
    days_elapsed = _days_elapsed(start, end, today)
    expected_pct = days_elapsed / 7 * 100

    rows = []
    for n in nutrients:
        consumed = sum(m["nutrients"].get(n.key, 0.0) for m in week_meals)
        pct = consumed / n.weekly_target * 100 if n.weekly_target else 0.0
        limit = n.weekly_upper_limit
        over = bool(limit) and consumed > limit
        rows.append({
            "key": n.key,
            "nutrient": n.name,
            "unit": n.unit,
            "category": n.category,
            "is_key": n.is_key,
            "weekly_target": n.weekly_target,
            "weekly_upper_limit": limit,
            "consumed": round(consumed, 3),
            "pct": round(pct, 1),
            "remaining": round(max(n.weekly_target - consumed, 0.0), 3),
            "reached": consumed >= n.weekly_target,
            "over_limit": over,
            "status": _status(pct, expected_pct, over),
        })

    by_pct = sorted(rows, key=lambda r: r["pct"])
    within = [r for r in by_pct if not r["over_limit"]]
    return {
        "week_start": start.isoformat(),
        "week_end": end.isoformat(),
        "is_current_week": start <= today <= end,
        "days_elapsed": days_elapsed,
        "expected_pct": round(expected_pct, 1),
        "meals_logged": len(week_meals),
        "days_logged": len({m["date"] for m in week_meals}),
        "reached_count": sum(r["reached"] and not r["over_limit"] for r in rows),
        "nutrient_count": len(rows),
        "rows": rows,
        "strongest": [r["key"] for r in reversed(within[-TOP_N:])],
        "weakest": [r["key"] for r in within[:TOP_N]],
        "substantially_below": [r["key"] for r in by_pct if r["status"] == "Far behind"],
        "over_limit": [r["key"] for r in rows if r["over_limit"]],
    }


def weekly_trends(meals: list[dict], nutrients: list[Nutrient], any_day: date, today: date, weeks: int = 4) -> dict:
    """% of weekly target per week for the last `weeks` weeks ending with the week containing `any_day`."""
    last_start, _ = week_bounds(any_day)
    starts = [last_start - timedelta(days=7 * i) for i in reversed(range(weeks))]
    columns = []
    for s in starts:
        e = s + timedelta(days=6)
        wk = [m for m in meals if s.isoformat() <= m["date"] <= e.isoformat()]
        columns.append({
            "week_start": s.isoformat(),
            "week_end": e.isoformat(),
            "meals_logged": len(wk),
            "is_current_week": s <= today <= e,
            "days_elapsed": _days_elapsed(s, e, today),
            "totals": {n.key: sum(m["nutrients"].get(n.key, 0.0) for m in wk) for n in nutrients},
        })
    rows = []
    for n in nutrients:
        cells = []
        for col in columns:
            consumed = col["totals"][n.key]
            limit = n.weekly_upper_limit
            cells.append({
                "pct": round(consumed / n.weekly_target * 100, 1) if n.weekly_target else 0.0,
                "over_limit": bool(limit) and consumed > limit,
            })
        rows.append({"key": n.key, "nutrient": n.name, "category": n.category, "is_key": n.is_key, "cells": cells})
    for col in columns:
        del col["totals"]
    return {"weeks": columns, "rows": rows}
