"""Nutrition calculation layer: free-text meal description -> amounts of each nutrient.

Every provider exposes the same interface:

    provider.name                              -> label shown in the UI and saved with the meal
    provider.estimate(description, nutrients)  -> NutritionEstimate

Each item carries its own nutrient amounts (when the provider can supply them), so the UI can
let the user remove an item or change its amount and re-total the meal without another API call.

To plug in a different API or model, write another class with that interface and
return it from build_nutrition_provider().
"""
import re
from dataclasses import asdict, dataclass, field

from .food_table import Food
from .llm_client import ChatClient, LLMError, extract_json
from .nutrients import Nutrient


class NutritionError(Exception):
    """The meal couldn't be interpreted (bad input rather than an outage)."""


@dataclass
class NutritionEstimate:
    # [{"food", "quantity", "grams", "nutrients": {key: amount} or None}]
    items: list[dict]
    nutrients: dict[str, float]    # meal totals: nutrient key -> amount in that nutrient's unit
    source: str
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _to_float(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
        return float(match.group()) if match else None
    return None


def _sum_items(items: list[dict], nutrients: list[Nutrient]) -> dict[str, float]:
    return {n.key: sum((it["nutrients"] or {}).get(n.key, 0.0) for it in items) for n in nutrients}


# --------------------------------------------------------------------------- LLM provider

NUTRITION_SYSTEM_PROMPT = (
    "You are a nutrition analysis engine. You estimate the nutrient content of meals described "
    "in plain English using standard food composition data (e.g. USDA FoodData Central; the Indian "
    "Food Composition Tables for Indian dishes). You always respond with a single JSON object and nothing else."
)


class LLMNutritionProvider:
    def __init__(self, client: ChatClient):
        self.client = client
        self.name = f"{client.provider_label} · {client.model}"

    def estimate(self, description: str, nutrients: list[Nutrient]) -> NutritionEstimate:
        nutrient_lines = "\n".join(f'- "{n.key}": {n.name} in {n.unit}' for n in nutrients)
        example = ", ".join(f'"{n.key}": 0' for n in nutrients[:3])
        prompt = f'''Meal description: """{description}"""

Steps:
1. Split the meal into individual food items. For each, interpret the stated quantity and estimate its
   weight in grams (use ml for drinks). If a quantity is vague or missing, assume one typical adult serving.
2. For EACH item, estimate the amount of every nutrient below in that item's portion, using typical
   preparation for that food.

Nutrients (use exactly these keys; values are plain numbers in the stated unit):
{nutrient_lines}

Respond with JSON in exactly this shape:
{{"items": [{{"food": "...", "quantity": "...", "grams": 0,
             "nutrients": {{{example}, ... all {len(nutrients)} keys ...}}}}],
  "assumptions": "one short sentence about any assumptions made"}}

If the text does not describe food or drink, respond with {{"error": "short explanation"}} instead.'''

        data = extract_json(self.client.chat(NUTRITION_SYSTEM_PROMPT, prompt, temperature=0.1, max_tokens=6000))
        if data.get("error"):
            raise NutritionError(str(data["error"]))

        items, seen_keys = [], set()
        for raw_item in data.get("items") or []:
            if not isinstance(raw_item, dict):
                continue
            raw = raw_item.get("nutrients")
            values = None
            if isinstance(raw, dict):
                values = {}
                for n in nutrients:
                    v = _to_float(raw.get(n.key))
                    if v is not None:
                        seen_keys.add(n.key)
                    values[n.key] = max(v or 0.0, 0.0)
            items.append({
                "food": str(raw_item.get("food", "")),
                "quantity": str(raw_item.get("quantity", "")),
                "grams": _to_float(raw_item.get("grams")),
                "nutrients": values,
            })

        if items and all(it["nutrients"] is not None for it in items):
            totals = _sum_items(items, nutrients)
        elif isinstance(data.get("nutrients"), dict):
            # Model only gave meal totals: keep them; items can't be adjusted individually.
            totals = {}
            for n in nutrients:
                v = _to_float(data["nutrients"].get(n.key))
                if v is not None:
                    seen_keys.add(n.key)
                totals[n.key] = max(v or 0.0, 0.0)
            for it in items:
                it["nutrients"] = None
        else:
            raise LLMError("LLM reply did not include nutrient values")
        if not seen_keys:
            raise LLMError("LLM reply did not include any nutrient values")

        warnings = []
        missing = [n.name for n in nutrients if n.key not in seen_keys]
        if missing:
            warnings.append(f"The model did not return values for: {', '.join(missing)} (recorded as 0).")
        if data.get("assumptions"):
            warnings.append(f"Assumptions: {data['assumptions']}")
        return NutritionEstimate(items=items, nutrients=totals, source=self.name, warnings=warnings)


# --------------------------------------------------------------------------- Offline demo provider

_WORD_NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                 "half": 0.5, "½": 0.5, "¼": 0.25}
_ARTICLES = {"a", "an"}
_QTY = r"(?P<qty>\d+(?:[.,/]\d+)?|½|¼|(?:a|an|one|two|three|four|five|six|half)\b)"
_UNIT = (r"(?P<unit>kg|g|grams?|gms?|ml|millilit(?:er|re)s?|l|lit(?:er|re)s?|oz|ounces?|cups?|bowls?|"
         r"glass(?:es)?|plates?|tbsp|tablespoons?|tsp|teaspoons?|slices?|pieces?|pcs?|handfuls?|scoops?|"
         r"servings?|fillets?|cans?)")
_CHUNK_RE = re.compile(rf"^\s*{_QTY}?\s*(?:{_UNIT}\b\.?)?\s*(?:of\s+)?(?P<food>.*)$", re.I)
# Quantity written after the food: "chicken 150g", "rice 2 cups", "milk 300 ml"
_TRAILING_QTY_RE = re.compile(rf"(?:^|\s)(?P<qty>\d+(?:[.,/]\d+)?|½|¼)\s*(?:{_UNIT}\b\.?)?\s*$", re.I)
_SPLIT_RE = re.compile(r",|;|\n|\+|\band\b|\bwith\b|&", re.I)


class DemoNutritionProvider:
    """Offline estimator using a small built-in food table. For demos without an API key."""

    name = "Offline demo food table"

    def __init__(self, foods: list[Food]):
        self.foods = foods
        # Longest keyword first so "whole wheat toast" wins over "toast", "almond milk" over "almond".
        self._keywords = sorted(((kw, f) for f in foods for kw in f.keywords), key=lambda kf: -len(kf[0]))

    def _find_foods(self, text: str) -> list[Food]:
        """All foods mentioned in a phrase, in order ("curd rice" -> curd, rice), without overlaps."""
        taken, found = [], []
        for kw, food in self._keywords:
            for m in re.finditer(rf"\b{re.escape(kw)}", text):
                if any(s < m.end() and m.start() < e for s, e in taken):
                    continue
                taken.append((m.start(), m.end()))
                found.append((m.start(), food))
        ordered = []
        for _, food in sorted(found, key=lambda pf: pf[0]):
            if food not in ordered:
                ordered.append(food)
        return ordered

    @staticmethod
    def _quantity(raw: str | None) -> float | None:
        if not raw:
            return None
        raw = raw.lower()
        if raw in _WORD_NUMBERS:
            return _WORD_NUMBERS[raw]
        if "/" in raw:
            num, den = raw.split("/")
            return float(num) / float(den) if float(den) else None
        return float(raw.replace(",", "."))

    @staticmethod
    def _grams(food: Food, qty: float | None, unit: str | None) -> float:
        unit = (unit or "").lower()
        if not unit:
            return food.serving_g if qty is None else qty * food.unit_g
        qty = 1 if qty is None else qty
        if unit.startswith("kg"):
            return qty * 1000
        if unit in ("g", "gm", "gms") or unit.startswith("gram"):
            return qty
        if unit == "ml" or unit.startswith("millilit"):
            return qty
        if unit == "l" or unit.startswith("lit"):
            return qty * 1000
        if unit in ("oz",) or unit.startswith("ounce"):
            return qty * 28.35
        if unit.startswith(("cup", "bowl")):
            return qty * food.cup_g
        if unit.startswith("glass"):
            return qty * 250
        if unit.startswith(("tbsp", "tablespoon")):
            return qty * 15
        if unit.startswith(("tsp", "teaspoon")):
            return qty * 5
        if unit.startswith(("handful", "scoop")):
            return qty * 30
        if unit.startswith(("serving", "plate")):
            return qty * food.serving_g
        return qty * food.unit_g  # slice, piece, fillet, can

    def estimate(self, description: str, nutrients: list[Nutrient]) -> NutritionEstimate:
        keys = {n.key for n in nutrients}
        parsed, unknown, notes = [], [], []
        for chunk in _SPLIT_RE.split(description):
            chunk = chunk.strip(" .")
            if not chunk:
                continue
            m = _CHUNK_RE.match(chunk)
            qty_raw, unit, food_text = m.group("qty"), m.group("unit"), m.group("food")
            if not qty_raw and not unit:
                t = _TRAILING_QTY_RE.search(food_text)
                if t:
                    qty_raw, unit, food_text = t.group("qty"), t.group("unit"), food_text[:t.start()]
            foods = self._find_foods(food_text.lower())
            if not foods:
                unknown.append(chunk)
                continue
            for i, food in enumerate(foods):
                if i == 0:
                    grams = self._grams(food, self._quantity(qty_raw), unit)
                    quantity = chunk
                    explicit = bool(qty_raw) and qty_raw.lower() not in _ARTICLES
                else:
                    grams, quantity, explicit = food.serving_g, f"{chunk} (1 serving assumed)", False
                parsed.append({"food": food, "quantity": quantity, "grams": grams, "explicit": explicit})

        # "an omelette with 3 eggs": the same food mentioned twice, once with a real amount -> count it once.
        items = []
        for p in parsed:
            same = [q for q in parsed if q["food"] is p["food"]]
            if not p["explicit"] and any(q["explicit"] for q in same):
                notes.append(f"'{p['quantity']}' counted once as part of the {p['food'].name.lower()} amount.")
                continue
            items.append({
                "food": p["food"].name, "quantity": p["quantity"], "grams": round(p["grams"]),
                "nutrients": {k: v for k, v in p["food"].nutrients_for(p["grams"]).items() if k in keys},
            })

        if not items:
            raise NutritionError(
                "Demo mode couldn't recognise any foods in that description. It only knows a small list of "
                "common foods — add an LLM API key for full free-text support."
            )
        warnings = ["Demo mode: rough estimates from a small offline food table (no LLM API key configured)."]
        if unknown:
            warnings.append(f"Not recognised in demo mode, so skipped: {', '.join(unknown)}.")
        warnings.extend(notes)
        return NutritionEstimate(items=items, nutrients=_sum_items(items, nutrients), source=self.name,
                                 warnings=warnings)


# --------------------------------------------------------------------------- Wiring

class FallbackProvider:
    """Try the primary provider; if the API is unreachable/broken, fall back to the demo table."""

    def __init__(self, primary, fallback):
        self.primary, self.fallback = primary, fallback
        self.name = primary.name

    def estimate(self, description: str, nutrients: list[Nutrient]) -> NutritionEstimate:
        try:
            return self.primary.estimate(description, nutrients)
        except LLMError as e:
            result = self.fallback.estimate(description, nutrients)
            result.warnings.insert(0, f"LLM unavailable ({e}). Used offline demo estimates instead.")
            return result


def build_nutrition_provider(settings, foods: list[Food]):
    demo = DemoNutritionProvider(foods)
    mode = settings.nutrition_provider
    if mode == "demo" or (mode == "auto" and not settings.llm_api_key):
        return demo
    llm = LLMNutritionProvider(ChatClient(
        settings.llm_base_url, settings.llm_api_key, settings.llm_model, settings.llm_timeout_seconds))
    return FallbackProvider(llm, demo) if mode == "auto" else llm
