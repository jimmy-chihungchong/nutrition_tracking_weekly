""""Improve My Diet" (weekly) and "Improve today" (daily): nutrient gaps -> general, food-based suggestions.

Uses the LLM when configured; otherwise (or if the call fails) builds rule-based
suggestions from the demo food table so the section always works.
"""
from .food_table import Food
from .llm_client import ChatClient, LLMError, extract_json
from .nutrients import Nutrient

DISCLAIMER = (
    "These suggestions are general information based on estimated intake, not medical advice or a diagnosis. "
    "Nutrient estimates from this prototype are approximate and not clinically validated. Speak to a doctor or "
    "registered dietitian before making significant dietary changes or taking supplements."
)

# Nutrients we never tell people to eat *more* of, even if they look low.
DO_NOT_PUSH = {"sodium_mg"}
MAX_FOCUS = 6

# Offline fallback text: (why it matters, general note on prolonged shortfall).
NUTRIENT_NOTES = {
    "energy_kcal": ("Fuels everything your body does.", "consistently eating too little energy can contribute to fatigue and unintended weight loss."),
    "protein_g": ("Builds and repairs muscle, skin and enzymes.", "a long-term shortfall may be associated with muscle loss and slower recovery."),
    "carbs_g": ("The body's main quick energy source, especially for the brain and exercise.", "very low intake over time can be associated with low energy for some people."),
    "fiber_g": ("Supports digestion, fullness and healthy cholesterol and blood sugar.", "a low-fibre diet over time is commonly associated with constipation and poorer gut health."),
    "fat_g": ("Provides energy and helps absorb vitamins A, D, E and K.", "very low fat intake over time may reduce absorption of fat-soluble vitamins."),
    "omega3_g": ("Supports heart, brain and eye health.", "low intake over time is generally associated with less favourable heart-health markers."),
    "omega6_g": ("An essential fat involved in skin health and cell membranes.", "true deficiency is uncommon but can be associated with dry, scaly skin."),
    "vit_a_ug": ("Supports vision, immunity and skin.", "a prolonged shortfall can be associated with poor night vision and weaker immunity."),
    "vit_c_mg": ("Supports immunity, wound healing and iron absorption.", "a prolonged shortfall can be associated with fatigue, easy bruising and slow wound healing."),
    "vit_d_ug": ("Helps absorb calcium for bones; supports muscles and immunity.", "a prolonged shortfall can be associated with weaker bones and muscle aches. Sunlight is also a major source."),
    "vit_e_mg": ("An antioxidant that protects cells.", "deficiency is uncommon but may be associated with nerve and muscle problems."),
    "vit_k_ug": ("Needed for normal blood clotting and bone health.", "a prolonged shortfall can be associated with easy bruising or bleeding."),
    "thiamin_mg": ("Helps turn food into energy; supports nerves.", "a prolonged shortfall can be associated with fatigue, irritability and nerve problems."),
    "riboflavin_mg": ("Helps release energy from food; supports skin and eyes.", "a prolonged shortfall can be associated with cracked lips and a sore throat."),
    "niacin_mg": ("Supports energy metabolism, skin and nerves.", "a prolonged shortfall can be associated with skin, digestive and nerve symptoms."),
    "pantothenic_mg": ("Helps make and break down fats and energy.", "deficiency is rare but may be associated with fatigue and numbness."),
    "vit_b6_mg": ("Supports protein metabolism, mood and immunity.", "a prolonged shortfall can be associated with anaemia and skin rashes."),
    "biotin_ug": ("Supports metabolism of fats, carbohydrates and protein.", "deficiency is rare but may be associated with hair thinning and skin rashes."),
    "folate_ug": ("Needed for making new cells and DNA; especially important before and during pregnancy.", "a prolonged shortfall can be associated with a type of anaemia and tiredness."),
    "vit_b12_ug": ("Supports nerves and red blood cells; found mainly in animal foods.", "a prolonged shortfall can be associated with anaemia, fatigue and nerve problems, especially on plant-based diets."),
    "choline_mg": ("Supports liver function, brain health and cell membranes.", "a prolonged shortfall may be associated with fat build-up in the liver."),
    "calcium_mg": ("Builds and maintains bones and teeth; supports muscles and nerves.", "a prolonged shortfall can be associated with lower bone density over time."),
    "iron_mg": ("Carries oxygen in the blood.", "a prolonged shortfall can be associated with iron-deficiency anaemia, tiredness and breathlessness."),
    "magnesium_mg": ("Involved in muscle and nerve function, blood sugar and bone health.", "a prolonged shortfall may be associated with muscle cramps and fatigue."),
    "phosphorus_mg": ("Works with calcium to build bones; involved in energy production.", "deficiency is uncommon but may be associated with weakness and bone pain."),
    "potassium_mg": ("Supports healthy blood pressure, muscles and nerves.", "low intake over time is associated with higher blood pressure."),
    "sodium_mg": ("Helps balance fluids and supports nerves.", "low intake is rarely a concern; most people eat more than they need."),
    "zinc_mg": ("Supports immunity, wound healing, taste and smell.", "a prolonged shortfall can be associated with weaker immunity and slow wound healing."),
    "copper_mg": ("Helps form red blood cells and supports nerves and immunity.", "deficiency is uncommon but may be associated with anaemia."),
    "manganese_mg": ("Supports bone formation and metabolism.", "deficiency is rare."),
    "selenium_ug": ("An antioxidant mineral that supports thyroid function and immunity.", "a prolonged shortfall may be associated with thyroid and immune effects."),
    "iodine_ug": ("Needed to make thyroid hormones.", "a prolonged shortfall can be associated with thyroid problems such as goitre. Iodised salt is a common source."),
}

INSIGHTS_SYSTEM_PROMPT = (
    "You are a friendly, evidence-based nutrition educator helping a non-expert understand their nutrient "
    "gaps. You give general informational suggestions focused on whole foods, never a diagnosis or personalised "
    "medical advice, and never supplement doses. You always respond with a single JSON object and nothing else."
)

# How to cut back on nutrients that are over their upper limit (offline fallback).
CUT_BACK_TIPS = {
    "sodium_mg": "Sodium is over its limit — go easy on salty snacks, processed meats, instant noodles, pickles and "
                 "restaurant food, and taste before adding salt.",
    "energy_kcal": "Calories are above your goal — smaller portions of rice, bread and fried foods, and fewer sugary "
                   "drinks, are easy places to start.",
}
WEEK_STATUSES = ("Far behind", "Behind")
DAY_STATUSES = ("Very low", "Low")


def _num(v: float) -> str:
    return f"{v:,.0f}" if abs(v) >= 100 else f"{v:.3g}"


def _focus_rows(rows: list[dict], statuses: tuple[str, ...]) -> list[dict]:
    behind = [r for r in rows if r["status"] in statuses and r["key"] not in DO_NOT_PUSH]
    return sorted(behind, key=lambda r: r["pct"])[:MAX_FOCUS]


def _empty(summary: str) -> dict:
    return {"source": "", "summary": summary, "focus": [], "simple_changes": [], "if_gaps_persist": [],
            "disclaimer": DISCLAIMER}


class InsightsService:
    def __init__(self, client: ChatClient | None, foods: list[Food], nutrients: list[Nutrient]):
        self.client = client
        self.foods = foods
        self.nutrients = {n.key: n for n in nutrients}

    def _run(self, llm_fn, offline_fn) -> dict:
        if self.client:
            try:
                return llm_fn()
            except LLMError as e:
                result = offline_fn()
                result["notice"] = f"LLM unavailable ({e}). Showing offline suggestions instead."
                return result
        return offline_fn()

    def generate(self, summary: dict) -> dict:
        """Suggestions for a week (output of weekly.summarize_week)."""
        if summary["meals_logged"] == 0:
            return {**_empty("No meals logged for this week yet — log a few meals first."), "scope": "week"}
        result = self._run(lambda: self._llm_week(summary), lambda: self._offline(summary, "week"))
        return {**result, "scope": "week"}

    def generate_day(self, summary: dict) -> dict:
        """Suggestions for one day (output of daily.summarize_day)."""
        if summary["meals_logged"] == 0:
            return {**_empty("No meals logged on this day yet — log a meal first."), "scope": "day"}
        result = self._run(lambda: self._llm_day(summary), lambda: self._offline(summary, "day"))
        return {**result, "scope": "day", "if_gaps_persist": []}

    # ------------------------------------------------------------------ LLM
    def _parse_llm(self, data: dict, rows: list[dict]) -> dict:
        rows_by_name = {r["nutrient"].lower(): r for r in rows}
        focus = []
        for item in data.get("focus") or []:
            if not isinstance(item, dict):
                continue
            row = rows_by_name.get(str(item.get("nutrient", "")).lower())
            focus.append({
                "nutrient": item.get("nutrient", ""),
                "pct": row["pct"] if row else None,
                "status": row["status"] if row else "",
                "tone": row.get("tone", "") if row else "",
                "why_it_matters": item.get("why_it_matters", ""),
                "food_sources": [str(f) for f in item.get("food_sources") or []],
            })
        return {
            "source": f"{self.client.provider_label} · {self.client.model}",
            "summary": data.get("summary", ""),
            "focus": focus,
            "simple_changes": [str(s) for s in data.get("simple_changes") or []],
            "if_gaps_persist": [str(s) for s in data.get("if_gaps_persist") or []],
            "disclaimer": DISCLAIMER,
        }

    def _llm_week(self, summary: dict) -> dict:
        lines = []
        for r in sorted(summary["rows"], key=lambda r: r["pct"]):
            limit = f"; weekly upper limit {_num(r['weekly_upper_limit'])} {r['unit']}" if r["weekly_upper_limit"] else ""
            lines.append(f"- {r['nutrient']}: {r['pct']:.0f}% of weekly target "
                         f"({_num(r['consumed'])} of {_num(r['weekly_target'])} {r['unit']}{limit}) — {r['status']}")
        pace = (f"It is day {summary['days_elapsed']} of 7, so about {summary['expected_pct']:.0f}% of each weekly "
                f"target would be on pace." if summary["is_current_week"] else "This week is complete.")
        prompt = f"""Weekly nutrition summary ({summary['week_start']} to {summary['week_end']}).
{summary['meals_logged']} meals logged on {summary['days_logged']} days. {pace}

All nutrients, weakest first:
{chr(10).join(lines)}

Write suggestions based ONLY on these actual numbers. Focus on the {MAX_FOCUS} or fewer weakest nutrients whose status is
"Far behind" or "Behind". Do not encourage more sodium, and do not push nutrients that are already reached or on track.
If any nutrient is "Over limit", include one simple change that helps cut back on it. If very few meals are logged,
gently mention that logging all meals gives a more accurate picture.

Respond with JSON in exactly this shape:
{{"summary": "2-3 plain-English sentences on the overall picture this week",
  "focus": [{{"nutrient": "exact nutrient name from the list", "why_it_matters": "one sentence",
             "food_sources": ["food with a typical serving", "... 3 to 5 foods"]}}],
  "simple_changes": ["3 to 5 concrete, simple changes naming specific foods and which gaps each helps close"],
  "if_gaps_persist": ["Nutrient name: general informational note on possible effects of a prolonged shortfall, for up to 4 of the focus nutrients"]}}"""
        data = extract_json(self.client.chat(INSIGHTS_SYSTEM_PROMPT, prompt, temperature=0.4))
        return self._parse_llm(data, summary["rows"])

    def _llm_day(self, summary: dict) -> dict:
        lines = []
        for r in sorted(summary["rows"], key=lambda r: r["pct"]):
            limit = f"; upper limit {_num(r['daily_upper_limit'])} {r['unit']}" if r["daily_upper_limit"] else ""
            lines.append(f"- {r['nutrient']}: {r['pct']:.0f}% of daily goal "
                         f"({_num(r['consumed'])} of {_num(r['daily_goal'])} {r['unit']}{limit}) — {r['status']}")
        meals = "\n".join(f"- {m['meal_type']}: {m['description']}" for m in summary["meals"])
        if summary["is_today"]:
            task = ("The day is still in progress. Suggest what to eat for the REST OF TODAY (e.g. the next meal or a "
                    "snack) to close the biggest gaps without going over any upper limit.")
        else:
            task = ("This day is over. Suggest what could have been added or swapped that day, as ideas for similar "
                    "days in future.")
        prompt = f"""Daily nutrition summary for {summary['date']}.
Meals logged:
{meals}

All nutrients, weakest first:
{chr(10).join(lines)}

{task} Base suggestions ONLY on these numbers. Focus on the {MAX_FOCUS} or fewer weakest nutrients whose status is
"Very low" or "Low". Do not encourage more sodium. One day's shortfall is normal — do not talk about deficiency effects.

Respond with JSON in exactly this shape:
{{"summary": "1-2 plain-English sentences on how the day looks",
  "focus": [{{"nutrient": "exact nutrient name from the list", "why_it_matters": "one sentence",
             "food_sources": ["food with a typical serving", "... 3 to 5 foods"]}}],
  "simple_changes": ["2 to 4 concrete meal or snack ideas naming specific foods and amounts, and which gaps each closes"]}}"""
        data = extract_json(self.client.chat(INSIGHTS_SYSTEM_PROMPT, prompt, temperature=0.4))
        return self._parse_llm(data, summary["rows"])

    # ------------------------------------------------------------------ offline fallback
    def _share_per_serving(self, food: Food, key: str) -> float:
        """Fraction of the daily goal supplied by one serving of this food."""
        daily = self.nutrients[key].daily_reference
        return food.nutrients_for(food.serving_g).get(key, 0.0) / daily if daily else 0.0

    def _offline(self, summary: dict, scope: str) -> dict:
        rows = summary["rows"]
        focus_rows = _focus_rows(rows, WEEK_STATUSES if scope == "week" else DAY_STATUSES)
        over_rows = [r for r in rows if r.get("over_limit")]
        period = "this week" if scope == "week" else ("so far today" if summary.get("is_today") else "on this day")
        cut_back = [CUT_BACK_TIPS.get(r["key"], f"{r['nutrient']} is over its upper limit {period} — ease off the "
                                                f"foods highest in it.") for r in over_rows]
        if not focus_rows:
            return {"source": "Rule-based (offline)",
                    "summary": f"Nice work — no nutrients are notably behind their goal {period}.",
                    "focus": [], "simple_changes": cut_back or ["Keep up the variety you have."],
                    "if_gaps_persist": [], "disclaimer": DISCLAIMER}

        # Don't suggest foods that add a lot of a nutrient that is already over its limit.
        over_keys = {r["key"] for r in over_rows}
        allowed = [f for f in self.foods if not any(self._share_per_serving(f, k) >= 0.15 for k in over_keys)]

        focus = []
        for r in focus_rows:
            ranked = sorted(allowed, key=lambda f: -self._share_per_serving(f, r["key"]))
            sources = [f"{f.name} (~{f.serving_g:.0f} g serving)" for f in ranked[:4]
                       if self._share_per_serving(f, r["key"]) >= 0.05]
            focus.append({
                "nutrient": r["nutrient"], "pct": r["pct"], "status": r["status"], "tone": r.get("tone", ""),
                "why_it_matters": NUTRIENT_NOTES.get(r["key"], ("", ""))[0],
                "food_sources": sources,
            })

        # Greedily pick foods that are good sources (>=10% of the daily goal per serving) of the most gaps.
        remaining = {r["key"] for r in focus_rows}
        changes = []
        while remaining and len(changes) < 3:
            best, covers = None, set()
            for f in allowed:
                c = {k for k in remaining if self._share_per_serving(f, k) >= 0.10}
                if len(c) > len(covers):
                    best, covers = f, c
            if not best:
                break
            names = ", ".join(r["nutrient"] for r in focus_rows if r["key"] in covers)
            when = "on a few days this week" if scope == "week" else (
                "at your next meal or as a snack" if summary.get("is_today") else "on a day like this")
            changes.append(f"Add a serving of {best.name.lower()} (~{best.serving_g:.0f} g) {when} "
                           f"— it's a good source of {names}.")
            remaining -= covers
        changes.extend(cut_back)
        if scope == "week" and summary["is_current_week"] and summary["days_logged"] < summary["days_elapsed"]:
            changes.append(f"Only {summary['days_logged']} of {summary['days_elapsed']} days so far have meals logged "
                           f"— logging every meal gives a more accurate picture of your gaps.")

        worst = ", ".join(f"{r['nutrient']} ({r['pct']:.0f}%)" for r in focus_rows[:3])
        return {
            "source": "Rule-based (offline — add an LLM API key for AI-written insights)",
            "summary": f"Your weakest nutrients {period} are {worst}. The foods below are good sources of these gaps.",
            "focus": focus,
            "simple_changes": changes,
            "if_gaps_persist": [f"{r['nutrient']}: {NUTRIENT_NOTES[r['key']][1]}"
                                for r in focus_rows[:4] if r["key"] in NUTRIENT_NOTES] if scope == "week" else [],
            "disclaimer": DISCLAIMER,
        }
