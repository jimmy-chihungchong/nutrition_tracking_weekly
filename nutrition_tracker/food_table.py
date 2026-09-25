"""Offline demo food table (data/demo_food_table.csv): approximate nutrients per 100 g.

Used by the demo nutrition provider, the sample data, and the offline insights fallback.
Values are rough reference figures for demonstration only.
"""
import csv
from dataclasses import dataclass
from pathlib import Path

from .nutrients import Nutrient


@dataclass(frozen=True)
class Food:
    name: str
    keywords: tuple[str, ...]
    unit_g: float      # weight of one piece / slice / item
    serving_g: float   # typical serving when no quantity is given
    cup_g: float       # weight of one cup / bowl
    per_100g: dict[str, float]

    def nutrients_for(self, grams: float) -> dict[str, float]:
        return {k: v * grams / 100 for k, v in self.per_100g.items()}


def load_food_table(path: Path, nutrients: list[Nutrient]) -> list[Food]:
    foods = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            missing = [n.key for n in nutrients if row.get(n.key) in (None, "")]
            if missing:
                raise ValueError(f"{path.name}: '{row['food']}' is missing {', '.join(missing)}")
            foods.append(Food(
                name=row["food"],
                keywords=tuple(k.strip().lower() for k in row["keywords"].split("|") if k.strip()),
                unit_g=float(row["unit_g"]),
                serving_g=float(row["serving_g"]),
                cup_g=float(row["cup_g"]),
                per_100g={n.key: float(row[n.key]) for n in nutrients},
            ))
    return foods
