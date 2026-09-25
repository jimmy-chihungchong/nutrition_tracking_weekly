"""Weekly Nutrition Tracker — web server.

Serves the single-page UI and a small JSON API that connects the layers:
UI -> nutrition_api (LLM / demo) -> storage (Excel) -> weekly/daily (aggregation) -> insights (LLM / offline).
"""
import math
from datetime import date, timedelta
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from config import BASE_DIR, load_settings
from nutrition_tracker.daily import summarize_day
from nutrition_tracker.food_table import load_food_table
from nutrition_tracker.insights import InsightsService
from nutrition_tracker.llm_client import ChatClient, LLMError
from nutrition_tracker.nutrients import apply_goal_updates, load_nutrients, load_presets, save_nutrients
from nutrition_tracker.nutrition_api import DemoNutritionProvider, NutritionError, build_nutrition_provider
from nutrition_tracker.sample_data import format_items, seed
from nutrition_tracker.storage import MEAL_TYPES, SAMPLE_MARKER, ExcelMealStore, StorageError
from nutrition_tracker.weekly import summarize_week, week_bounds, weekly_trends

MAX_DESCRIPTION = 1000
MAX_ITEMS_TEXT = 4000
RECENT_DAYS = 60
RECENT_LIMIT = 8
PRESET_LABELS = {"reference_dv": "Reference (FDA Daily Values)", "adult_female": "Adult female (19–50)",
                 "adult_male": "Adult male (19–50)"}

settings = load_settings()
app = Flask(__name__)
app.json.sort_keys = False


def services():
    """Build the services per request so edits to the targets CSV apply without a restart."""
    nutrients = load_nutrients(settings.targets_path)
    foods = load_food_table(settings.food_table_path, nutrients)
    store = ExcelMealStore(settings.meals_path, nutrients)
    return nutrients, foods, store


def _llm_enabled() -> bool:
    return bool(settings.llm_api_key) and settings.nutrition_provider != "demo"


def _parse_date(value: str | None) -> date:
    if not value:
        return date.today()
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid date: {value!r} (expected YYYY-MM-DD)")


def _json_body() -> dict:
    body = request.get_json(silent=True)
    if body is None:
        body = {} if not request.get_data() else None
    if not isinstance(body, dict):
        raise ValueError("The request body must be a JSON object.")
    return body


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(BASE_DIR).as_posix()
    except ValueError:
        return str(path)


def error(message: str, status: int = 400):
    return jsonify({"error": message}), status


def _insights_client() -> ChatClient | None:
    if not _llm_enabled():
        return None
    return ChatClient(settings.llm_base_url, settings.llm_api_key, settings.insights_model,
                      settings.llm_timeout_seconds)


@app.errorhandler(StorageError)
def _storage_error(e):
    return error(str(e), 409)


@app.errorhandler(ValueError)
def _value_error(e):
    return error(str(e), 400)


@app.errorhandler(PermissionError)
def _permission_error(e):
    return error(str(e), 409)


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/config")
def api_config():
    nutrients, foods, _ = services()
    provider = build_nutrition_provider(settings, foods)
    return jsonify({
        "nutrients": [n.to_dict() for n in nutrients],
        "meal_types": MEAL_TYPES,
        "today": date.today().isoformat(),
        "nutrition_engine": provider.name,
        "llm_enabled": _llm_enabled(),
        "meals_file": _display_path(settings.meals_path),
        "targets_file": _display_path(settings.targets_path),
    })


# ------------------------------------------------------------------ meals

@app.post("/api/estimate")
def api_estimate():
    description = str(_json_body().get("description") or "").strip()
    if not description:
        return error("Please describe what you ate.")
    if len(description) > MAX_DESCRIPTION:
        return error(f"Please keep the description under {MAX_DESCRIPTION} characters.")
    nutrients, foods, _ = services()
    provider = build_nutrition_provider(settings, foods)
    try:
        estimate = provider.estimate(description, nutrients)
    except NutritionError as e:
        return error(str(e), 422)
    except LLMError as e:
        return error(f"The nutrition service failed: {e}", 502)
    result = estimate.to_dict()
    result["items_text"] = format_items(estimate.items)
    return jsonify(result)


def _validated_meal(body: dict, nutrients) -> dict:
    """Check a meal sent by the UI; raises ValueError with a user-facing message."""
    meal_date = _parse_date(body.get("date"))
    if meal_date > date.today():
        raise ValueError("Meals can't be logged for a future date.")
    meal_type = body.get("meal_type", "")
    if meal_type not in MEAL_TYPES:
        raise ValueError(f"Meal type must be one of: {', '.join(MEAL_TYPES)}")
    description = str(body.get("description") or "").strip()
    if not description:
        raise ValueError("Meal description is required.")
    if len(description) > MAX_DESCRIPTION:
        raise ValueError(f"Please keep the description under {MAX_DESCRIPTION} characters.")
    values = body.get("nutrients")
    if not isinstance(values, dict) or not values:
        raise ValueError("Nutrient values are missing — calculate nutrition first.")
    clean = {}
    for n in nutrients:
        raw = values.get(n.key, 0)
        try:
            v = float(raw or 0)
        except (TypeError, ValueError):
            raise ValueError(f"Invalid value for {n.name}.")
        if isinstance(raw, bool) or not math.isfinite(v):
            raise ValueError(f"Invalid value for {n.name}.")
        clean[n.key] = max(v, 0.0)
    return dict(meal_date=meal_date, meal_type=meal_type, description=description,
                items_text=str(body.get("items_text") or "")[:MAX_ITEMS_TEXT],
                source=str(body.get("source") or "")[:200], nutrients=clean)


@app.get("/api/meals")
def api_list_meals():
    _, _, store = services()
    start, end = week_bounds(_parse_date(request.args.get("date")))
    return jsonify({"week_start": start.isoformat(), "week_end": end.isoformat(),
                    "meals": store.list_meals(start, end)})


@app.post("/api/meals")
def api_save_meal():
    nutrients, _, store = services()
    meal = store.add_meal(**_validated_meal(_json_body(), nutrients))
    return jsonify(meal), 201


@app.put("/api/meals/<meal_id>")
def api_update_meal(meal_id):
    nutrients, _, store = services()
    meal = store.update_meal(meal_id, **_validated_meal(_json_body(), nutrients))
    if not meal:
        return error("Meal not found — it may have been deleted.", 404)
    return jsonify(meal)


@app.delete("/api/meals/<meal_id>")
def api_delete_meal(meal_id):
    _, _, store = services()
    if not store.delete_meal(meal_id):
        return error("Meal not found.", 404)
    return jsonify({"deleted": meal_id})


@app.get("/api/recent-meals")
def api_recent_meals():
    """Most recent distinct meals (by description) for one-click 'log again'."""
    _, _, store = services()
    today = date.today()
    meals = store.list_meals(today - timedelta(days=RECENT_DAYS), today)
    seen, recent = set(), []
    for m in sorted(meals, key=lambda m: (m["date"], m["logged_at"]), reverse=True):
        key = m["description"].strip().lower()
        if key in seen:
            continue
        seen.add(key)
        recent.append(m)
        if len(recent) >= RECENT_LIMIT:
            break
    return jsonify({"meals": recent})


# ------------------------------------------------------------------ progress

@app.get("/api/weekly")
def api_weekly():
    nutrients, _, store = services()
    day = _parse_date(request.args.get("date"))
    start, end = week_bounds(day)
    return jsonify(summarize_week(store.list_meals(start, end), nutrients, day, date.today()))


@app.get("/api/trends")
def api_trends():
    nutrients, _, store = services()
    day = _parse_date(request.args.get("date"))
    start, end = week_bounds(day)
    meals = store.list_meals(start - timedelta(days=21), end)
    return jsonify(weekly_trends(meals, nutrients, day, date.today(), weeks=4))


@app.get("/api/daily")
def api_daily():
    nutrients, _, store = services()
    day = _parse_date(request.args.get("date"))
    start, end = week_bounds(day)
    return jsonify(summarize_day(store.list_meals(start, end), nutrients, day, date.today()))


@app.post("/api/insights")
def api_insights():
    body = _json_body()
    nutrients, foods, store = services()
    day = _parse_date(body.get("date"))
    start, end = week_bounds(day)
    meals = store.list_meals(start, end)
    service = InsightsService(_insights_client(), foods, nutrients)
    if body.get("scope") == "day":
        return jsonify(service.generate_day(summarize_day(meals, nutrients, day, date.today())))
    return jsonify(service.generate(summarize_week(meals, nutrients, day, date.today())))


# ------------------------------------------------------------------ goals

@app.get("/api/goals")
def api_goals():
    nutrients = load_nutrients(settings.targets_path)
    presets = load_presets(settings.presets_path)
    return jsonify({
        "nutrients": [n.to_dict() for n in nutrients],
        "presets": [{"id": k, "label": PRESET_LABELS.get(k, k), "daily": v} for k, v in presets.items()],
        "targets_file": _display_path(settings.targets_path),
    })


@app.put("/api/goals")
def api_save_goals():
    body = _json_body()
    nutrients = load_nutrients(settings.targets_path)
    new = apply_goal_updates(nutrients, body.get("goals") or [])
    save_nutrients(settings.targets_path, new)
    return jsonify({"saved": len(body.get("goals") or []), "nutrients": [n.to_dict() for n in new]})


# ------------------------------------------------------------------ sample data

@app.post("/api/sample-data")
def api_sample_data():
    """'reload' replaces only the sample meals; 'remove' deletes them. The user's own meals are never touched."""
    action = _json_body().get("action", "reload")
    if action not in ("reload", "remove"):
        raise ValueError("action must be 'reload' or 'remove'.")
    _, foods, store = services()
    removed = store.delete_sample_meals()
    added = seed(store, DemoNutritionProvider(foods), date.today()) if action == "reload" else 0
    return jsonify({"removed": removed, "added": added})


def _seed_if_new():
    _, foods, store = services()
    if not settings.meals_path.exists():
        count = seed(store, DemoNutritionProvider(foods), date.today())
        print(f"Created {settings.meals_path} with {count} sample meals ({SAMPLE_MARKER}).")


if __name__ == "__main__":
    _seed_if_new()
    mode = "LLM: " + settings.llm_model if _llm_enabled() else "DEMO MODE (no LLM API key set)"
    print(f"Weekly Nutrition Tracker — {mode}\nOpen http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
