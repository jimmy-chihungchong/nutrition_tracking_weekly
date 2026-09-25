"""Sample meals so the weekly dashboard can be demonstrated immediately.

Seeds last week (all 7 days) and the current week up to today. Nutrients are computed
with the offline demo food table so no API key is needed.

    python -m nutrition_tracker.sample_data           # seed only if the log is empty
    python -m nutrition_tracker.sample_data --reset   # replace the sample meals (your own meals are kept)
"""
import sys
from datetime import date, timedelta

from .nutrition_api import DemoNutritionProvider
from .storage import SAMPLE_MARKER, ExcelMealStore
from .weekly import week_bounds

# One menu per weekday (Monday first). Deliberately light on oily fish, dairy and leafy greens
# on some days so the dashboard shows realistic gaps.
WEEKLY_MENU = [
    [("Breakfast", "2 eggs, 2 slices of whole wheat toast, 1 banana and 200 ml milk"),
     ("Lunch", "1 cup brown rice, 1 cup dal and 1 cup mixed vegetables"),
     ("Snack", "1 apple and a handful of almonds"),
     ("Dinner", "150 g grilled chicken, 1 cup broccoli and 1 potato")],
    [("Breakfast", "1 cup oats with 200 ml milk and 1 cup blueberries"),
     ("Lunch", "2 roti, 1 cup chickpeas curry and salad"),
     ("Dinner", "150 g salmon, 1 sweet potato and 1 cup spinach")],
    [("Breakfast", "1 cup greek yogurt, 1 banana and 1 tbsp peanut butter"),
     ("Lunch", "1 cup pasta with 1 tomato and 30 g cheese"),
     ("Dinner", "3 roti, 1 cup dal and 100 g paneer")],
    [("Breakfast", "2 slices of white bread, 2 eggs and 1 cup orange juice"),
     ("Lunch", "1 cup white rice, 150 g chicken curry and 1 cup mixed vegetables"),
     ("Snack", "1 orange"),
     ("Dinner", "100 g tofu, 1 cup brown rice and 1 cup broccoli")],
    [("Breakfast", "1 cup oats with 200 ml milk and 1 banana"),
     ("Lunch", "1 can tuna, 2 slices of whole wheat bread and salad"),
     ("Dinner", "2 roti, 1 cup lentils and 1 cup spinach")],
    [("Breakfast", "2 eggs, 1 avocado and 1 slice of whole wheat toast"),
     ("Lunch", "1 cup pasta, 100 g beef and 1 tomato"),
     ("Snack", "1 cup curd"),
     ("Dinner", "1 cup white rice, 1 cup dal and 1 carrot")],
    [("Breakfast", "1 cup greek yogurt and 1 cup blueberries"),
     ("Lunch", "1 cup brown rice, 100 g tofu and 1 cup mixed vegetables"),
     ("Dinner", "150 g salmon, 1 potato and 1 cup broccoli")],
]


def format_items(items: list[dict]) -> str:
    parts = []
    for it in items:
        grams = f" (~{it['grams']:.0f} g)" if isinstance(it.get("grams"), (int, float)) else ""
        parts.append(f"{it.get('quantity') or ''} → {it.get('food', '')}{grams}".strip(" →"))
    return "; ".join(parts)


def seed(store: ExcelMealStore, provider: DemoNutritionProvider, today: date) -> int:
    this_monday, _ = week_bounds(today)
    last_monday = this_monday - timedelta(days=7)
    days = [last_monday + timedelta(days=i) for i in range(7)]
    days += [this_monday + timedelta(days=i) for i in range(today.weekday() + 1)]
    entries = []
    for day in days:
        for meal_type, description in WEEKLY_MENU[day.weekday()]:
            est = provider.estimate(description, store.nutrients)
            entries.append(dict(meal_date=day, meal_type=meal_type, description=description,
                                items_text=format_items(est.items), source=f"{est.source} {SAMPLE_MARKER}",
                                nutrients=est.nutrients))
    store.add_meals(entries)
    return len(entries)


def main() -> None:
    from config import load_settings
    from .food_table import load_food_table
    from .nutrients import load_nutrients

    settings = load_settings()
    nutrients = load_nutrients(settings.targets_path)
    store = ExcelMealStore(settings.meals_path, nutrients)
    if "--reset" in sys.argv:
        store.delete_sample_meals()
    elif store.list_meals():
        print(f"{settings.meals_path.name} already has meals; use --reset to replace them with sample data.")
        return
    provider = DemoNutritionProvider(load_food_table(settings.food_table_path, nutrients))
    n = seed(store, provider, date.today())
    print(f"Added {n} sample meals to {settings.meals_path}")


if __name__ == "__main__":
    main()
