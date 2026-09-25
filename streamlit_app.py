"""Weekly Nutrition Tracker — Streamlit version (for Streamlit Community Cloud).

Uses the same modules as the Flask app (nutrition_tracker/); only the user interface differs.

Storage is picked automatically:
- Google Sheets when [gcp_service_account] and [google_sheets] are set in Streamlit secrets (hosted);
- otherwise the local Excel log and targets CSV in data/ (handy for running locally).

Run locally:  streamlit run streamlit_app.py
"""
import os
import time
import uuid
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Weekly Nutrition Tracker", page_icon="🥗", layout="wide")


def _secret(*path, default=None):
    """Read a Streamlit secret without failing when no secrets are configured (local runs)."""
    try:
        node = st.secrets
        for p in path:
            node = node[p]
        return node
    except Exception:
        return default


# Cloud servers run on UTC; APP_TIMEZONE (e.g. "Asia/Kolkata") makes "today" match the user's day.
_tz = _secret("APP_TIMEZONE") or os.getenv("APP_TIMEZONE")
if _tz and hasattr(time, "tzset"):
    os.environ["TZ"] = str(_tz)
    time.tzset()

# LLM settings may come from Streamlit secrets; config.load_settings() reads them from the environment.
for _k in ("LLM_API_KEY", "NVIDIA_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "INSIGHTS_MODEL", "NUTRITION_PROVIDER",
           "LLM_TIMEOUT_SECONDS"):
    if _secret(_k) is not None:
        os.environ[_k] = str(_secret(_k))

from config import load_settings  # noqa: E402  (must run after the secrets are copied)
from nutrition_tracker.daily import summarize_day  # noqa: E402
from nutrition_tracker.food_table import load_food_table  # noqa: E402
from nutrition_tracker.insights import InsightsService  # noqa: E402
from nutrition_tracker.llm_client import ChatClient, LLMError  # noqa: E402
from nutrition_tracker.nutrients import CsvTargets, apply_goal_updates, load_presets  # noqa: E402
from nutrition_tracker.nutrition_api import (DemoNutritionProvider, NutritionError,  # noqa: E402
                                             build_nutrition_provider)
from nutrition_tracker.sample_data import format_items, seed  # noqa: E402
from nutrition_tracker.storage import MEAL_TYPES, SAMPLE_MARKER, ExcelMealStore, StorageError  # noqa: E402
from nutrition_tracker.weekly import summarize_week, week_bounds, weekly_trends  # noqa: E402

settings = load_settings()
ss = st.session_state
USE_SHEETS = _secret("gcp_service_account") is not None and _secret("google_sheets") is not None
PRESET_LABELS = {"reference_dv": "Reference (FDA Daily Values)", "adult_female": "Adult female (19–50)",
                 "adult_male": "Adult male (19–50)"}
EXAMPLES = [
    "2 eggs, 2 slices of whole wheat toast, 1 banana and 200 ml milk",
    "1 cup brown rice, 1 cup dal and a bowl of mixed vegetables",
    "150 g grilled salmon, 1 sweet potato and 1 cup spinach",
]
WEEK_STATUS_ICON = {"Reached": "✅", "On track": "🔵", "Behind": "🟠", "Far behind": "🔴", "Not started": "⚪",
                    "Over limit": "🟣"}
DAY_TONE_ICON = {"good": "✅", "ok": "🔵", "warn": "🟠", "bad": "🔴", "over": "🟣"}


# ============================================================================ services & caching

@st.cache_resource(show_spinner=False)
def _spreadsheet():
    import gspread

    client = gspread.service_account_from_dict(
        dict(_secret("gcp_service_account")), scopes=["https://www.googleapis.com/auth/spreadsheets"])
    cfg = _secret("google_sheets")
    if cfg.get("spreadsheet_url"):
        return client.open_by_url(cfg["spreadsheet_url"])
    return client.open_by_key(cfg["spreadsheet_key"])


def _targets_repo():
    if USE_SHEETS:
        from nutrition_tracker.sheets_storage import SheetsTargets
        return SheetsTargets(_spreadsheet(), settings.targets_path)
    return CsvTargets(settings.targets_path)


@st.cache_data(ttl=300, show_spinner=False)
def load_nutrients_cached():
    return _targets_repo().load()


def get_store(nutrients=None):
    nutrients = nutrients or load_nutrients_cached()
    if USE_SHEETS:
        from nutrition_tracker.sheets_storage import GoogleSheetsMealStore
        return GoogleSheetsMealStore(_spreadsheet(), nutrients)
    return ExcelMealStore(settings.meals_path, nutrients)


@st.cache_data(ttl=120, show_spinner=False)
def all_meals_cached() -> list[dict]:
    return get_store().list_meals()


def meals_between(start: date, end: date) -> list[dict]:
    s, e = start.isoformat(), end.isoformat()
    return [m for m in all_meals_cached() if s <= m["date"] <= e]


@st.cache_data(show_spinner=False)
def foods_cached():
    return load_food_table(settings.food_table_path, load_nutrients_cached())


@st.cache_resource(show_spinner="Setting up your meal log…")
def ensure_log_ready() -> bool:
    """Create the meal log on first run and fill it with sample meals."""
    store = get_store()
    if store.ensure_workbook():
        seed(store, DemoNutritionProvider(foods_cached()), date.today())
        return True
    return False


def llm_enabled() -> bool:
    return bool(settings.llm_api_key) and settings.nutrition_provider != "demo"


def insights_service() -> InsightsService:
    client = (ChatClient(settings.llm_base_url, settings.llm_api_key, settings.insights_model,
                         settings.llm_timeout_seconds) if llm_enabled() else None)
    return InsightsService(client, foods_cached(), load_nutrients_cached())


def data_changed() -> None:
    """Meals changed: drop cached meals and any insights computed from the old data."""
    all_meals_cached.clear()
    ss.insights_week = {}
    ss.insights_day = {}


def goals_changed() -> None:
    load_nutrients_cached.clear()
    foods_cached.clear()
    data_changed()


# ============================================================================ state & small helpers

today = date.today()
for key, value in {"estimate": None, "editing": None, "flash": [], "insights_week": {}, "insights_day": {},
                   "day": today, "week": today, "meals_week": today, "goals_ver": 0}.items():
    ss.setdefault(key, value)


for key in ("log_text", "log_date", "log_type"):
    if key in ss:
        ss[key] = ss[key]


def flash(kind: str, text: str) -> None:
    ss.flash.append((kind, text))


def show_flash() -> None:
    for kind, text in ss.flash:
        getattr(st, kind)(text)
    ss.flash = []


def fmt(v) -> str:
    n = float(v or 0)
    if abs(n) >= 100:
        return f"{round(n):,}"
    if abs(n) >= 10:
        return f"{n:.1f}".rstrip("0").rstrip(".")
    return (f"{n:.2f}".rstrip("0").rstrip(".")) or "0"


def nice_date(d, fmt_str="%a %d %b") -> str:
    d = date.fromisoformat(d) if isinstance(d, str) else d
    return d.strftime(fmt_str)


def week_label(start: date, end: date) -> str:
    prefix = "This week · " if start <= today <= end else ""
    return f"{prefix}{start.strftime('%d %b')} – {end.strftime('%d %b %Y')}"


def nutrient_table(values: dict, nutrients, only_key: bool) -> pd.DataFrame:
    rows = [n for n in nutrients if n.is_key] if only_key else nutrients
    return pd.DataFrame({"Nutrient": [n.name for n in rows], "Amount": [fmt(values.get(n.key, 0)) for n in rows],
                         "Unit": [n.unit for n in rows]})


def filter_rows(rows: list[dict], choice: str) -> list[dict]:
    if choice == "Key nutrients":
        key = [r for r in rows if r["is_key"]]
        return key or rows
    if choice in (None, "All"):
        return rows
    return [r for r in rows if r["category"] == choice]


def sort_rows(rows: list[dict], sort: str) -> list[dict]:
    if sort == "Lowest % first":
        return sorted(rows, key=lambda r: r["pct"])
    if sort == "Highest % first":
        return sorted(rows, key=lambda r: -r["pct"])
    return rows


def filter_controls(prefix: str, nutrients):
    cats = ["Key nutrients", "All"] + list(dict.fromkeys(n.category for n in nutrients))
    c1, c2 = st.columns([3, 1])
    choice = c1.segmented_control("Show", cats, default="Key nutrients", key=f"{prefix}_filter",
                                  label_visibility="collapsed")
    sort = c2.selectbox("Sort", ["Group by category", "Lowest % first", "Highest % first"], key=f"{prefix}_sort",
                        label_visibility="collapsed")
    return choice, sort


PROGRESS = st.column_config.ProgressColumn(format="%.0f%%", min_value=0, max_value=100)


# ============================================================================ meal actions (shared)

def from_saved_meal(m: dict, relog: bool) -> dict:
    source = m["source"].replace(SAMPLE_MARKER, "").strip() if relog else m["source"]
    return {"id": f"saved-{m['id']}-{relog}", "description": m["description"], "source": source,
            "nutrients": dict(m["nutrients"]), "items": [], "items_editable": False,
            "saved_items_text": m["items_text"],
            "warnings": [f"Using the nutrition saved with this meal on {nice_date(m['date'])}. "
                         f"Change the description and recalculate to update it."]}


def start_edit(m: dict) -> None:
    ss.editing = m
    ss.estimate = from_saved_meal(m, relog=False)
    ss.pending_log = {"text": m["description"], "date": date.fromisoformat(m["date"]), "type": m["meal_type"]}
    st.switch_page(PAGES["log"])


def log_again(m: dict) -> None:
    ss.editing = None
    ss.estimate = from_saved_meal(m, relog=True)
    ss.pending_log = {"text": m["description"], "date": today, "type": None}
    flash("info", "Loaded a previous meal — check the date and meal type, then save.")
    st.switch_page(PAGES["log"])


def go_to_day(d) -> None:
    ss.day = min(date.fromisoformat(d) if isinstance(d, str) else d, today)
    ss.pop("day_pick", None)
    st.switch_page(PAGES["daily"])


@st.dialog("Delete this meal?")
def confirm_delete(m: dict) -> None:
    st.write(f"**{m['meal_type']} · {nice_date(m['date'])}**")
    st.write(m["description"])
    c1, c2 = st.columns(2)
    if c1.button("Delete", type="primary", width="stretch"):
        try:
            if get_store().delete_meal(m["id"]):
                if ss.editing and ss.editing["id"] == m["id"]:
                    ss.editing, ss.estimate = None, None
                flash("success", "Meal deleted.")
            else:
                flash("warning", "That meal was already deleted.")
        except StorageError as e:
            flash("error", str(e))
        data_changed()
        st.rerun()
    if c2.button("Cancel", width="stretch"):
        st.rerun()


def meal_card(m: dict, nutrients, prefix: str) -> None:
    label = f"**{m['meal_type']}** · {m['description']} — {fmt(m['nutrients'].get('energy_kcal'))} kcal"
    with st.expander(label):
        if m["items_text"]:
            st.caption(f"**Interpreted as:** {m['items_text']}")
        st.caption(f"**Estimated by:** {m['source'] or '—'} · **Logged:** {m['logged_at'][:16].replace('T', ' ')}")
        show_all = st.toggle("Show all nutrients", key=f"{prefix}_all_{m['id']}")
        st.dataframe(nutrient_table(m["nutrients"], nutrients, only_key=not show_all), hide_index=True,
                     width="stretch")
        if not m["id"]:
            st.caption("Added directly in the spreadsheet — it gets an ID the next time the log is read.")
            return
        c1, c2, c3 = st.columns(3)
        if c1.button("↻ Log again", key=f"{prefix}_again_{m['id']}", width="stretch"):
            log_again(m)
        if c2.button("✎ Edit", key=f"{prefix}_edit_{m['id']}", width="stretch"):
            start_edit(m)
        if c3.button("🗑 Delete", key=f"{prefix}_del_{m['id']}", width="stretch"):
            confirm_delete(m)


def render_insights(d: dict, is_today: bool = False) -> None:
    if d.get("notice"):
        st.warning(d["notice"])
    st.markdown(f"#### {d['summary']}")
    if d["focus"]:
        st.markdown("##### Nutrients to focus on")
        cols = st.columns(min(3, len(d["focus"])))
        for i, f in enumerate(d["focus"]):
            with cols[i % len(cols)].container(border=True):
                pct = f" — {round(f['pct'])}%" if f.get("pct") is not None else ""
                st.markdown(f"**{f['nutrient']}**{pct}")
                st.caption(f["why_it_matters"])
                if f["food_sources"]:
                    st.markdown("Good sources: " + " ".join(f":green-background[{s}]" for s in f["food_sources"]))
    if d["simple_changes"]:
        title = ("Ideas for the rest of today" if is_today else "Ideas") if d.get("scope") == "day" \
            else "Simple changes to try"
        st.markdown(f"##### {title}")
        st.markdown("\n".join(f"- {s}" for s in d["simple_changes"]))
    if d["if_gaps_persist"]:
        st.markdown("##### If these gaps persist *(general information)*")
        st.markdown("\n".join(f"- {s}" for s in d["if_gaps_persist"]))
    st.warning(f"**Not medical advice.** {d['disclaimer']}")
    if d.get("source"):
        st.caption(f"Generated by: {d['source']}")


# ============================================================================ page: Log Meal

def _totals(est: dict, edited: pd.DataFrame | None, nutrients) -> dict:
    if not est["items_editable"] or edited is None:
        return est["nutrients"]
    totals = {n.key: 0.0 for n in nutrients}
    for i, it in enumerate(est["items"]):
        row = edited.iloc[i]
        if not row["Include"]:
            continue
        grams = float(row["Grams"] or 0)
        scale = grams / it["grams"] if it["grams"] else 1
        for k in totals:
            totals[k] += (it["nutrients"] or {}).get(k, 0.0) * scale
    return totals


def _items_text(est: dict, edited: pd.DataFrame | None) -> str:
    if not est["items"]:
        return est.get("saved_items_text", "")
    parts = []
    for i, it in enumerate(est["items"]):
        grams, include = it["grams"], True
        if edited is not None and est["items_editable"]:
            include, grams = bool(edited.iloc[i]["Include"]), float(edited.iloc[i]["Grams"] or 0)
        if not include:
            continue
        adjusted = ", adjusted" if it["grams"] and round(grams) != round(it["grams"]) else ""
        g = f" (~{round(grams)} g{adjusted})" if grams else ""
        parts.append(f"{it['quantity']} → {it['food']}{g}" if it["quantity"] else f"{it['food']}{g}")
    return "; ".join(parts)


def _reset_log() -> None:
    if ss.editing:  # back to logging new meals for today
        ss.log_date = today
    ss.estimate, ss.editing = None, None
    ss.log_text = ""


def _use_example(text: str) -> None:
    ss.log_text = text


def _set_log_date(offset: int) -> None:
    ss.log_date = today + timedelta(days=offset)


def _pick_recent() -> None:
    meal_id = ss.get("recent_pick")
    ss.recent_pick = None
    m = next((m for m in all_meals_cached() if m["id"] == meal_id), None)
    if m:
        ss.editing = None
        ss.estimate = from_saved_meal(m, relog=True)
        ss.log_text = m["description"]
        ss.log_date = today


def _save_meal(payload: dict) -> None:
    editing = ss.editing
    if ss.log_text.strip() != payload["description"]:
        flash("error", "You changed the description after calculating. **Recalculate** before saving so the "
                       "nutrition matches.")
        return
    if ss.log_date > today:
        flash("error", "Meals can't be logged for a future date.")
        return
    payload = {**payload, "meal_date": ss.log_date, "meal_type": ss.log_type}
    try:
        store = get_store()
        if editing:
            if not store.update_meal(editing["id"], **payload):
                flash("error", "That meal no longer exists — it may have been deleted.")
                return
        else:
            store.add_meal(**payload)
    except StorageError as e:
        flash("error", str(e))
        return
    data_changed()
    _reset_log()
    ss.day = ss.week = ss.meals_week = payload["meal_date"]

    nutrients = load_nutrients_cached()
    by_key = {n.key: n for n in nutrients}
    start, end = week_bounds(payload["meal_date"])
    day = summarize_day(meals_between(start, end), nutrients, payload["meal_date"], today)
    rows = {r["key"]: r for r in day["rows"]}
    pct = lambda k: round(payload["nutrients"].get(k, 0) / by_key[k].daily_reference * 100) if k in by_key else 0
    over = ", ".join(rows[k]["nutrient"] for k in day["over_limit"])
    when = "Today so far" if day["is_today"] else f"On {nice_date(day['date'])}"
    flash("success", f"**{'Changes saved' if editing else 'Meal saved'}.** This meal gives you **{pct('energy_kcal')}%** of "
                     f"your daily calories and **{pct('protein_g')}%** of your protein.  \n{when}: "
                     f"**{day['met_count']}/{day['nutrient_count']}** daily goals met"
                     f"{f' · ⚠️ over the limit: {over}' if over else ''}. Open **Daily Tracker** for details.")


def page_log():
    nutrients = load_nutrients_cached()
    pending = ss.pop("pending_log", None)
    if pending:
        ss.log_text, ss.log_date = pending["text"], pending["date"]
        if pending["type"]:
            ss.log_type = pending["type"]
    if "log_text" not in ss:
        ss.log_text = ss.estimate["description"] if ss.estimate else ""
    ss.setdefault("log_date", today)
    if "log_type" not in ss:
        hour = datetime.now().hour
        ss.log_type = "Breakfast" if hour < 11 else "Lunch" if hour < 16 else "Dinner" if hour < 21 else "Snack"

    st.title("Log a meal")
    show_flash()
    if ss.editing:
        m = ss.editing
        c1, c2 = st.columns([4, 1])
        c1.info(f"✎ Editing **{m['meal_type']}** on **{nice_date(m['date'])}** — change the date, meal or "
                f"description, then save.")
        c2.button("Cancel editing", on_click=_reset_log, width="stretch")

    left, right = st.columns(2, gap="large")
    with left:
        c1, c2 = st.columns(2)
        c1.date_input("Date", key="log_date", max_value=today, format="YYYY-MM-DD")
        c2.selectbox("Meal", MEAL_TYPES, key="log_type")
        q1, q2, _ = st.columns([1, 1.2, 4])
        q1.button("Today", on_click=_set_log_date, args=(0,), type="tertiary")
        q2.button("Yesterday", on_click=_set_log_date, args=(-1,), type="tertiary")
        st.text_area("What did you eat? Include how much.", key="log_text", height=110,
                     placeholder="e.g. 2 eggs, 2 slices of whole wheat toast, 1 banana and 200 ml milk")

        recent, seen = [], set()
        for m in sorted(all_meals_cached(), key=lambda m: (m["date"], m["logged_at"]), reverse=True):
            if m["id"] and m["description"].lower() not in seen:
                seen.add(m["description"].lower())
                recent.append(m)
            if len(recent) >= 6:
                break
        if recent:
            labels = {m["id"]: m["description"] if len(m["description"]) < 40 else m["description"][:38] + "…"
                      for m in recent}
            st.pills("Log again", list(labels), format_func=lambda i: f"↻ {labels[i]}", key="recent_pick",
                     on_change=_pick_recent)
        else:
            st.caption("Try an example:")
            for i, ex in enumerate(EXAMPLES):
                st.button(ex, key=f"example_{i}", on_click=_use_example, args=(ex,), type="tertiary")

        est = ss.estimate
        stale = bool(est) and ss.log_text.strip() != est["description"]
        if st.button("Recalculate nutrition" if stale else "Calculate nutrition", type="primary", width="stretch"):
            text = ss.log_text.strip()
            if not text:
                st.error("Please describe what you ate.")
            elif len(text) > 1000:
                st.error("Please keep the description under 1000 characters.")
            else:
                try:
                    with st.spinner("Estimating nutrients…"):
                        result = build_nutrition_provider(settings, foods_cached()).estimate(text, nutrients)
                    ss.estimate = {"id": f"est-{uuid.uuid4().hex[:6]}", "description": text,
                                   "source": result.source, "warnings": result.warnings,
                                   "nutrients": result.nutrients, "items": result.items,
                                   "items_editable": bool(result.items) and all(
                                       it["nutrients"] and it["grams"] for it in result.items)}
                    est, stale = ss.estimate, False
                except NutritionError as e:
                    st.error(str(e))
                except LLMError as e:
                    st.error(f"The nutrition service failed: {e}")

    with right:
        if not est:
            with st.container(border=True):
                st.markdown("#### How it works")
                st.markdown("1. **Describe** a meal — foods and amounts.\n"
                            "2. **Calculate** — an AI nutrition service estimates the nutrients.\n"
                            "3. **Review** — remove anything misread or adjust amounts.\n"
                            "4. **Save** — the meal is added to your log.\n"
                            "5. **Track** — see each day and your week against your goals.\n"
                            "6. **Improve** — get suggestions for your biggest gaps.")
            return
        with st.container(border=True):
            st.markdown(f"#### Review before saving  \n:gray-background[{est['source'] or 'Saved meal'}]")
            edited = None
            if est["items_editable"]:
                st.caption("Check how your meal was understood. Untick anything that's wrong or change the grams "
                           "— totals update instantly.")
                df = pd.DataFrame({"Include": [True] * len(est["items"]),
                                   "Food": [it["food"] for it in est["items"]],
                                   "Grams": [float(round(it["grams"])) for it in est["items"]],
                                   "Quantity": [it["quantity"] for it in est["items"]]})
                edited = st.data_editor(df, hide_index=True, width="stretch", key=f"items_{est['id']}",
                                        disabled=["Food", "Quantity"],
                                        column_config={"Grams": st.column_config.NumberColumn(min_value=1, step=1)})
            elif est["items"]:
                st.markdown("\n".join(f"- {it['food']} — {it['quantity']}" for it in est["items"]))
                st.caption("To change something, edit the description and recalculate.")
            elif est.get("saved_items_text"):
                st.caption(f"**Interpreted as:** {est['saved_items_text']}")
            for w in est["warnings"]:
                st.warning(w)

            totals = _totals(est, edited, nutrients)
            by_key = {n.key: n for n in nutrients}
            macro_cols = st.columns(4)
            for col, k in zip(macro_cols, ["energy_kcal", "protein_g", "carbs_g", "fat_g"]):
                if k in by_key:
                    col.metric(f"{by_key[k].name} ({by_key[k].unit})", fmt(totals.get(k)))
            show_all = st.toggle("Show all nutrients", key="est_all")
            st.dataframe(nutrient_table(totals, nutrients, only_key=not show_all), hide_index=True, width="stretch")

            nothing = est["items_editable"] and edited is not None and not edited["Include"].any()
            if stale:
                st.error("You changed the description after calculating. **Recalculate** before saving so the "
                         "nutrition matches.")
            if nothing:
                st.error("All items are unticked — tick one or discard this meal.")
            payload = {"meal_date": ss.log_date, "meal_type": ss.log_type, "description": est["description"],
                       "items_text": _items_text(est, edited)[:4000], "source": est["source"][:200],
                       "nutrients": totals}
            b1, b2 = st.columns(2)
            b1.button("Save changes" if ss.editing else "Save meal", type="primary", width="stretch",
                      disabled=stale or nothing or ss.log_date > today, on_click=_save_meal, args=(payload,))
            b2.button("Discard", width="stretch", on_click=_reset_log)


# ============================================================================ page: My Meals

def _shift(key: str, days: int) -> None:
    ss[key] = ss[key] + timedelta(days=days)


def _week_nav(key: str) -> tuple[date, date]:
    start, end = week_bounds(ss[key])
    c1, c2, c3 = st.columns([1, 4, 1])
    c1.button("◀ Previous", key=f"{key}_prev", on_click=_shift, args=(key, -7), width="stretch")
    c2.markdown(f"<h3 style='text-align:center;margin:0'>{week_label(start, end)}</h3>", unsafe_allow_html=True)
    c3.button("Next ▶", key=f"{key}_next", on_click=_shift, args=(key, 7), width="stretch", disabled=end >= today)
    return start, end


@st.dialog("Sample meals")
def confirm_sample(action: str) -> None:
    st.write("Remove all sample meals?" if action == "remove" else
             "Replace the sample meals with a fresh set for last week and this week?")
    st.caption("Your own meals are always kept.")
    if st.button("Remove sample meals" if action == "remove" else "Reload sample meals", type="primary"):
        try:
            store = get_store()
            removed = store.delete_sample_meals()
            added = seed(store, DemoNutritionProvider(foods_cached()), today) if action == "reload" else 0
            flash("success", f"Removed {removed} sample meals." if action == "remove" else f"Loaded {added} sample meals.")
        except StorageError as e:
            flash("error", str(e))
        data_changed()
        st.rerun()


def page_meals():
    nutrients = load_nutrients_cached()
    st.title("My meals")
    show_flash()
    start, end = _week_nav("meals_week")
    meals = meals_between(start, end)
    st.caption(f"{len(meals)} meal{'s' if len(meals) != 1 else ''} logged · stored in {get_store(nutrients).location}. "
               f"Open a meal to see its nutrients, log it again, edit it or delete it.")
    if not meals:
        st.info("No meals logged this week.")
    by_day: dict[str, list] = {}
    for m in meals:
        by_day.setdefault(m["date"], []).append(m)
    for d in sorted(by_day, reverse=True):
        kcal = sum(m["nutrients"].get("energy_kcal", 0) for m in by_day[d])
        c1, c2 = st.columns([3, 1])
        c1.subheader(nice_date(d, "%A %d %b"))
        c1.caption(f"{len(by_day[d])} meals · {fmt(kcal)} kcal")
        if c2.button("Daily tracker →", key=f"meals_day_{d}", width="stretch"):
            go_to_day(d)
        for m in by_day[d]:
            meal_card(m, nutrients, "meals")

    st.divider()
    with st.container(border=True):
        st.markdown("#### Sample meals")
        st.caption("The app ships with demo meals (marked “sample data”) so the dashboards have something to show. "
                   "These buttons only touch the sample meals — your own meals are always kept.")
        c1, c2, _ = st.columns([1, 1, 2])
        if c1.button("Remove sample meals", width="stretch"):
            confirm_sample("remove")
        if c2.button("Reload sample meals", width="stretch"):
            confirm_sample("reload")


# ============================================================================ page: Daily Tracker

def _set_day(d: date) -> None:
    ss.day = min(d, today)
    ss.pop("day_pick", None)


def _day_picked() -> None:
    if ss.get("day_pick"):
        ss.day = min(ss.day_pick, today)


def page_daily():
    nutrients = load_nutrients_cached()
    st.title("Daily tracker")
    show_flash()
    ss.day = min(ss.day, today)
    c1, c2, c3, c4 = st.columns([1, 2, 1, 1])
    c1.button("◀ Previous day", on_click=_set_day, args=(ss.day - timedelta(days=1),), width="stretch")
    ss.day_pick = ss.day
    c2.date_input("Day", key="day_pick", max_value=today, on_change=_day_picked, label_visibility="collapsed",
                  format="YYYY-MM-DD")
    c3.button("Next day ▶", on_click=_set_day, args=(ss.day + timedelta(days=1),), width="stretch",
              disabled=ss.day >= today)
    c4.button("Today", on_click=_set_day, args=(today,), width="stretch", disabled=ss.day == today)

    start, end = week_bounds(ss.day)
    d = summarize_day(meals_between(start, end), nutrients, ss.day, today)
    rows = {r["key"]: r for r in d["rows"]}
    st.subheader("Today" if d["is_today"] else nice_date(d["date"], "%A %d %B %Y"))

    strip = st.columns(7)
    for col, w in zip(strip, d["week"]):
        wd = date.fromisoformat(w["date"])
        label = f"{wd.strftime('%a %d')}  \n{w['met_count']}/{d['nutrient_count']} met{' ⚠️' if w['over_count'] else ''}" \
            if w["meals_logged"] else f"{wd.strftime('%a %d')}  \n—"
        col.button(label, key=f"strip_{w['date']}", on_click=_set_day, args=(wd,), width="stretch",
                   type="primary" if w["date"] == d["date"] else "secondary", disabled=w["is_future"])

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Meals logged", d["meals_logged"], border=True)
    m2.metric("Daily goals met", f"{d['met_count']}/{d['nutrient_count']}", border=True)
    if "energy_kcal" in rows:
        e = rows["energy_kcal"]
        m3.metric(f"Calories · {fmt(e['consumed'])} of {fmt(e['daily_goal'])} kcal", f"{round(e['pct'])}%",
                  border=True)
    if "protein_g" in rows:
        p = rows["protein_g"]
        m4.metric(f"Protein · {fmt(p['consumed'])} of {fmt(p['daily_goal'])} g", f"{round(p['pct'])}%",
                  border=True)

    if not d["meals_logged"]:
        st.info("No meals logged on this day — log a meal to see how the day stacks up against your daily goals.")
    else:
        if d["is_today"]:
            st.caption("Today is still in progress — these numbers will change as you log more meals.")
        if d["very_low"]:
            st.error("**🚨 Well below the daily goal (under 50%):**  \n" + " ".join(
                f":red-background[{rows[k]['nutrient']} {round(rows[k]['pct'])}% · {fmt(rows[k]['remaining'])} "
                f"{rows[k]['unit']} to go]" for k in d["very_low"]))
        else:
            st.success("✅ Every nutrient is at least half-way to its daily goal.")
        if d["over_limit"]:
            st.warning("**⚠️ Over the daily upper limit:**  \n" + " ".join(
                f":violet-background[{rows[k]['nutrient']} {round(rows[k]['pct'])}% of goal]" for k in d["over_limit"]))

    st.markdown("#### Daily goals")
    choice, sort = filter_controls("day", nutrients)
    table = sort_rows(filter_rows(d["rows"], choice), sort)
    st.dataframe(pd.DataFrame({
        "Nutrient": [r["nutrient"] for r in table],
        "Daily goal": [f"{fmt(r['daily_goal'])} {r['unit']}" for r in table],
        "Consumed": [f"{fmt(r['consumed'])} {r['unit']}" for r in table],
        "% of goal": [r["pct"] for r in table],
        "Remaining": [f"+{fmt(r['consumed'] - r['daily_upper_limit'])} {r['unit']} over limit" if r["over_limit"]
                      else "—" if r["met"] else f"{fmt(r['remaining'])} {r['unit']}" for r in table],
        "Status": [f"{DAY_TONE_ICON.get(r['tone'], '')} {r['status']}" for r in table],
    }), hide_index=True, width="stretch", column_config={"% of goal": PROGRESS})
    st.caption("Met ≥ 100% · Almost ≥ 75% · Low ≥ 50% · Very low < 50% · Over limit = above the daily upper limit. "
               "Change goals on the Goals page.")

    with st.container(border=True):
        st.markdown(f"#### {'Improve today' if d['is_today'] else 'Ideas for a day like this'}")
        st.caption("Suggestions for your next meal or snack, based on today's gaps." if d["is_today"]
                   else "What could have been added or swapped on this day.")
        if st.button("Suggest what to eat" if d["is_today"] else "Suggest improvements", type="primary",
                     disabled=not d["meals_logged"]):
            with st.spinner("Looking at this day's gaps…"):
                ss.insights_day[d["date"]] = insights_service().generate_day(d)
        if d["date"] in ss.insights_day:
            render_insights(ss.insights_day[d["date"]], is_today=d["is_today"])

    st.markdown("#### Meals on this day")
    if not d["meals"]:
        st.caption("No meals logged on this day.")
    for m in d["meals"]:
        meal_card(m, nutrients, "daily")


# ============================================================================ page: Weekly Progress

def page_weekly():
    nutrients = load_nutrients_cached()
    st.title("Weekly progress")
    show_flash()
    start, end = _week_nav("week")
    w = summarize_week(meals_between(start, end), nutrients, ss.week, today)
    rows = {r["key"]: r for r in w["rows"]}
    st.caption(f"Day {w['days_elapsed']} of 7 — to be on pace you'd be at about {round(w['expected_pct'])}% of each "
               f"weekly target." if w["is_current_week"] else "Completed week — compared against full weekly targets.")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Meals logged", w["meals_logged"], border=True)
    m2.metric("Days with meals", f"{w['days_logged']}/7", border=True)
    m3.metric("Weekly targets reached", f"{w['reached_count']}/{w['nutrient_count']}", border=True)
    m4.metric("Nutrients far behind", len(w["substantially_below"]), border=True)

    if not w["meals_logged"]:
        st.info("No meals logged this week.")
    elif w["substantially_below"]:
        st.error("**🚨 Biggest gaps — substantially below target** (less than half of where you'd expect to be "
                 "at this point in the week):  \n" + " ".join(
                     f":red-background[{rows[k]['nutrient']} {round(rows[k]['pct'])}% · {fmt(rows[k]['remaining'])} "
                     f"{rows[k]['unit']} to go]" for k in w["substantially_below"]))
    else:
        st.success("✅ No major gaps — every nutrient is at least half-way to where it should be.")
    if w["over_limit"]:
        st.warning("**⚠️ Over the weekly upper limit:**  \n" + " ".join(
            f":violet-background[{rows[k]['nutrient']} {round(rows[k]['pct'])}% of target]" for k in w["over_limit"]))

    def mini(keys):
        return pd.DataFrame({"Nutrient": [rows[k]["nutrient"] for k in keys], "%": [rows[k]["pct"] for k in keys]})

    c1, c2 = st.columns(2)
    with c1.container(border=True):
        st.markdown("##### 💪 Strongest nutrients")
        st.dataframe(mini(w["strongest"]), hide_index=True, width="stretch", column_config={"%": PROGRESS})
    with c2.container(border=True):
        st.markdown("##### ⚠️ Weakest nutrients")
        st.dataframe(mini(w["weakest"]), hide_index=True, width="stretch", column_config={"%": PROGRESS})

    st.markdown("#### Nutrients this week")
    choice, sort = filter_controls("week", nutrients)
    table = sort_rows(filter_rows(w["rows"], choice), sort)
    st.dataframe(pd.DataFrame({
        "Nutrient": [r["nutrient"] for r in table],
        "Weekly target": [f"{fmt(r['weekly_target'])} {r['unit']}" for r in table],
        "Consumed": [f"{fmt(r['consumed'])} {r['unit']}" for r in table],
        "% complete": [r["pct"] for r in table],
        "Remaining": [f"+{fmt(r['consumed'] - r['weekly_upper_limit'])} {r['unit']} over limit" if r["over_limit"]
                      else "—" if r["reached"] else f"{fmt(r['remaining'])} {r['unit']}" for r in table],
        "Status": [f"{WEEK_STATUS_ICON.get(r['status'], '')} {r['status']}" for r in table],
    }), hide_index=True, width="stretch", column_config={"% complete": PROGRESS})

    st.markdown("#### Last 4 weeks")
    st.caption("% of weekly target for your key nutrients — is it getting better?")
    t = weekly_trends(meals_between(start - timedelta(days=21), end), nutrients, ss.week, today)
    trend_rows = [r for r in t["rows"] if r["is_key"]] or t["rows"]

    def cell(c, wk):
        if not wk["meals_logged"]:
            return "—"
        icon = "🟣" if c["over_limit"] else "✅" if c["pct"] >= 100 else "🔵" if c["pct"] >= 75 else \
            "🟠" if c["pct"] >= 50 else "🔴"
        return f"{icon} {round(c['pct'])}%"

    headers = [nice_date(wk["week_start"], "%d %b") + (" (so far)" if wk["is_current_week"] else "") for wk in t["weeks"]]
    trend_df = pd.DataFrame({"Nutrient": [r["nutrient"] for r in trend_rows]})
    for i, h in enumerate(headers):
        trend_df[h] = [cell(r["cells"][i], t["weeks"][i]) for r in trend_rows]
    st.dataframe(trend_df, hide_index=True, width="stretch")

    if st.button("Get AI insights on these gaps →", type="primary"):
        st.switch_page(PAGES["insights"])


# ============================================================================ page: Improve My Diet

def page_insights():
    nutrients = load_nutrients_cached()
    st.title("Improve my diet")
    show_flash()
    start, end = week_bounds(ss.week)
    st.caption(f"Based on the week of {start.strftime('%d %b')} – {end.strftime('%d %b')} "
               f"(change the week on Weekly Progress).")
    if st.button("Analyze my week", type="primary"):
        with st.spinner("Looking at your weekly gaps…"):
            summary = summarize_week(meals_between(start, end), nutrients, ss.week, today)
            ss.insights_week[start.isoformat()] = insights_service().generate(summary)
    result = ss.insights_week.get(start.isoformat())
    if result:
        render_insights(result)
    else:
        st.info("Get suggestions based on the nutrient gaps for this week.")


# ============================================================================ page: Goals

def _goals_frame(nutrients) -> pd.DataFrame:
    return pd.DataFrame({
        "key": [n.key for n in nutrients],
        "Nutrient": [f"{n.name} ({n.unit})" for n in nutrients],
        "Daily goal": [n.daily_reference for n in nutrients],
        "Weekly target": [n.weekly_target for n in nutrients],
        "Daily upper limit": [n.daily_upper_limit for n in nutrients],
        "Key nutrient": [n.is_key for n in nutrients],
    })


def _apply_preset(presets: dict) -> None:
    daily = presets.get(ss.get("preset_pick"))
    if not daily:
        return
    df = ss.goals_base.copy()
    df["Daily goal"] = [daily.get(k, v) for k, v in zip(df["key"], df["Daily goal"])]
    df["Weekly target"] = [round(v * 7, 2) for v in df["Daily goal"]]
    ss.goals_base = df
    ss.goals_ver += 1
    flash("info", f"Applied “{PRESET_LABELS.get(ss.preset_pick, ss.preset_pick)}” — review and press **Save goals** "
                  f"to keep it.")


def _undo_goals() -> None:
    ss.pop("goals_base", None)
    ss.goals_ver += 1


def page_goals():
    nutrients = load_nutrients_cached()
    presets = load_presets(settings.presets_path)
    st.title("My goals")
    show_flash()
    st.caption(f"Daily goals, weekly targets and upper limits used by the trackers. Saved to "
               f"{_targets_repo().location}. General reference values, not personalised medical advice.")
    if "goals_base" not in ss:
        ss.goals_base = _goals_frame(nutrients)

    c1, c2, c3 = st.columns([2, 1, 2])
    c1.selectbox("Preset", list(presets), format_func=lambda p: PRESET_LABELS.get(p, p), key="preset_pick",
                 index=None, placeholder="Choose a preset…", label_visibility="collapsed")
    c2.button("Apply preset", on_click=_apply_preset, args=(presets,), width="stretch")
    auto_weekly = c3.checkbox("Keep weekly target = 7 × daily goal", value=True)

    edited = st.data_editor(
        ss.goals_base, key=f"goals_editor_{ss.goals_ver}", hide_index=True, width="stretch",
        disabled=["key", "Nutrient"] + (["Weekly target"] if auto_weekly else []),
        column_config={
            "key": None,
            "Daily goal": st.column_config.NumberColumn(min_value=0.0, format="%g"),
            "Weekly target": st.column_config.NumberColumn(min_value=0.0, format="%g"),
            "Daily upper limit": st.column_config.NumberColumn(min_value=0.0, format="%g",
                                                               help="Leave empty if there is no upper limit"),
            "Key nutrient": st.column_config.CheckboxColumn(help="Shown by default in tables and meal cards"),
        })

    b1, b2, _ = st.columns([1, 1, 3])
    if b1.button("Save goals", type="primary", width="stretch"):
        updates = []
        for _, r in edited.iterrows():
            daily = r["Daily goal"]
            updates.append({"key": r["key"], "daily_reference": daily,
                            "weekly_target": round(daily * 7, 4) if auto_weekly and pd.notna(daily)
                            else r["Weekly target"],
                            "daily_upper_limit": None if pd.isna(r["Daily upper limit"]) else r["Daily upper limit"],
                            "is_key": bool(r["Key nutrient"])})
        try:
            new = apply_goal_updates(nutrients, updates)
            _targets_repo().save(new)
            goals_changed()
            ss.goals_base = _goals_frame(new)
            ss.goals_ver += 1
            flash("success", "✓ Goals saved. The trackers now use these values.")
            st.rerun()
        except (ValueError, PermissionError, StorageError) as e:
            st.error(str(e))
    b2.button("Undo changes", on_click=_undo_goals, width="stretch")


# ============================================================================ navigation

PAGES = {
    "log": st.Page(page_log, title="Log Meal", icon="🍽️", url_path="log", default=True),
    "meals": st.Page(page_meals, title="My Meals", icon="📋", url_path="meals"),
    "daily": st.Page(page_daily, title="Daily Tracker", icon="📅", url_path="daily"),
    "weekly": st.Page(page_weekly, title="Weekly Progress", icon="📊", url_path="weekly"),
    "insights": st.Page(page_insights, title="Improve My Diet", icon="💡", url_path="insights"),
    "goals": st.Page(page_goals, title="Goals", icon="⚙️", url_path="goals"),
}

try:
    ensure_log_ready()
except StorageError as e:
    st.error(str(e))
    st.stop()

with st.sidebar:
    st.markdown("### 🥗 Weekly Nutrition Tracker")
    st.caption(("🟢 Nutrition engine: " if llm_enabled() else "🟠 Demo mode: ")
               + build_nutrition_provider(settings, foods_cached()).name)
    if USE_SHEETS:
        st.caption("💾 Data: Google Sheets")
    else:
        st.caption("💾 Data: local Excel file (data/)")
        st.info("**Temporary storage.** When hosted on Streamlit Cloud, meals and goal changes are reset "
                "whenever the app restarts, sleeps or is updated, and everyone using the app shares the same "
                "data. Download a backup to keep a copy.")
        if settings.meals_path.exists():
            st.download_button("⬇ Download meal log (Excel)", data=settings.meals_path.read_bytes(),
                               file_name=f"nutrition_log_{today.isoformat()}.xlsx", width="stretch",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.divider()
    st.caption("Prototype for demonstration only. Nutrient values are AI/reference estimates and are not "
               "clinically validated; nothing here is medical advice.")

st.navigation(list(PAGES.values()), position="top").run()
