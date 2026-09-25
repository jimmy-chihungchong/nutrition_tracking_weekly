"""Daily tracking: one day's meals compared with the daily goals.

Daily goals are the `daily_reference` column of data/weekly_targets.csv; nutrients with a
`daily_upper_limit` (e.g. sodium) are flagged "Over limit" when the day's intake exceeds it.
"""
from datetime import date, timedelta

from .nutrients import Nutrient
from .weekly import week_bounds

LOWEST_N = 5
# Status by % of the daily goal: (minimum %, label, colour tone used by the UI)
DAILY_LEVELS = [(100, "Met", "good"), (75, "Almost", "ok"), (50, "Low", "warn"), (0, "Very low", "bad")]


def _level(pct: float) -> tuple[str, str]:
    for minimum, label, tone in DAILY_LEVELS:
        if pct >= minimum:
            return label, tone
    return DAILY_LEVELS[-1][1], DAILY_LEVELS[-1][2]


def day_rows(day_meals: list[dict], nutrients: list[Nutrient]) -> list[dict]:
    rows = []
    for n in nutrients:
        consumed = sum(m["nutrients"].get(n.key, 0.0) for m in day_meals)
        goal = n.daily_reference
        pct = consumed / goal * 100 if goal else 0.0
        over = bool(n.daily_upper_limit) and consumed > n.daily_upper_limit
        status, tone = ("Over limit", "over") if over else _level(pct)
        rows.append({
            "key": n.key, "nutrient": n.name, "unit": n.unit, "category": n.category, "is_key": n.is_key,
            "daily_goal": goal, "daily_upper_limit": n.daily_upper_limit,
            "consumed": round(consumed, 3), "pct": round(pct, 1),
            "remaining": round(max(goal - consumed, 0.0), 3),
            "met": consumed >= goal and not over, "over_limit": over,
            "status": status, "tone": tone,
        })
    return rows


def summarize_day(meals: list[dict], nutrients: list[Nutrient], day: date, today: date) -> dict:
    """`meals` should cover at least the Monday–Sunday week containing `day` (used for the week strip)."""
    iso = day.isoformat()
    day_meals = [m for m in meals if m["date"] == iso]
    rows = day_rows(day_meals, nutrients)

    start, _ = week_bounds(day)
    week = []
    for i in range(7):
        d = start + timedelta(days=i)
        d_meals = [m for m in meals if m["date"] == d.isoformat()]
        d_rows = day_rows(d_meals, nutrients)
        energy = next((r for r in d_rows if r["key"] == "energy_kcal"), None)
        week.append({
            "date": d.isoformat(),
            "meals_logged": len(d_meals),
            "met_count": sum(r["met"] for r in d_rows),
            "over_count": sum(r["over_limit"] for r in d_rows),
            "energy_pct": energy["pct"] if energy else None,
            "is_future": d > today,
        })

    by_pct = sorted(rows, key=lambda r: r["pct"])
    return {
        "date": iso,
        "is_today": day == today,
        "is_future": day > today,
        "meals": day_meals,
        "meals_logged": len(day_meals),
        "met_count": sum(r["met"] for r in rows),
        "nutrient_count": len(rows),
        "rows": rows,
        "lowest": [r["key"] for r in by_pct[:LOWEST_N] if not r["met"] and not r["over_limit"]],
        "very_low": [r["key"] for r in by_pct if r["status"] == "Very low"],
        "over_limit": [r["key"] for r in rows if r["over_limit"]],
        "week": week,
    }
