"""Nutrient definitions, daily goals, weekly targets and upper limits.

Targets are data, not code. Locally they live in data/weekly_targets.csv; the hosted Streamlit
version keeps them in a "Targets" worksheet (see sheets_storage.py). Both use the same columns
and go through the same functions here, and are re-read on every request so edits apply immediately.
"""
import csv
import math
import os
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path

REQUIRED_COLUMNS = {"key", "nutrient", "unit", "category", "weekly_target"}
CSV_COLUMNS = ["key", "nutrient", "unit", "category", "daily_reference", "weekly_target",
               "daily_upper_limit", "key_nutrient", "reference_basis"]


@dataclass(frozen=True)
class Nutrient:
    key: str
    name: str
    unit: str
    category: str
    daily_reference: float          # daily goal
    weekly_target: float
    daily_upper_limit: float | None  # amount per day above which intake is flagged "Over limit"
    is_key: bool                     # shown in the default "Key nutrients" view
    reference_basis: str

    @property
    def label(self) -> str:
        """Default column header in the Excel meal log, e.g. 'Protein (g)'."""
        return f"{self.name} ({self.unit})"

    @property
    def weekly_upper_limit(self) -> float | None:
        return self.daily_upper_limit * 7 if self.daily_upper_limit else None

    def to_dict(self) -> dict:
        return {**asdict(self), "label": self.label}


def _optional_float(value) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    value = str(value if value is not None else "").replace(",", "").strip()  # tolerate "2,000"
    return float(value) if value else None


def nutrients_from_rows(rows: list[dict], source_name: str) -> list[Nutrient]:
    """Build nutrients from table rows (dicts keyed by CSV_COLUMNS), from a CSV file or a worksheet."""
    if rows:
        missing = REQUIRED_COLUMNS - set(rows[0].keys())
        if missing:
            raise ValueError(f"{source_name} is missing columns: {', '.join(sorted(missing))}")
    nutrients = []
    for row in rows:
        if not str(row.get("key") or "").strip():
            continue
        weekly = _optional_float(row["weekly_target"])
        if weekly is None:
            raise ValueError(f"{source_name}: '{row['key']}' has no weekly_target")
        nutrients.append(Nutrient(
            key=str(row["key"]).strip(),
            name=str(row["nutrient"]).strip(),
            unit=str(row["unit"]).strip(),
            category=str(row["category"]).strip(),
            daily_reference=_optional_float(row.get("daily_reference")) or weekly / 7,
            weekly_target=weekly,
            daily_upper_limit=_optional_float(row.get("daily_upper_limit")),
            is_key=str(row.get("key_nutrient") or "").strip().lower() in ("yes", "y", "true", "1"),
            reference_basis=str(row.get("reference_basis") or "").strip(),
        ))
    if not nutrients:
        raise ValueError(f"No nutrients found in {source_name}")
    return nutrients


def load_nutrients(path: Path) -> list[Nutrient]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path.name} is missing columns: {', '.join(sorted(missing))}")
        return nutrients_from_rows(list(reader), path.name)


def _num(v: float | None) -> str:
    if v is None:
        return ""
    return f"{v:g}" if abs(v) < 1e6 else f"{v:.0f}"


def nutrient_rows(nutrients: list[Nutrient]) -> list[list[str]]:
    """Header + one row per nutrient, in CSV_COLUMNS order (for the CSV file or a worksheet)."""
    rows = [list(CSV_COLUMNS)]
    for n in nutrients:
        rows.append([n.key, n.name, n.unit, n.category, _num(n.daily_reference), _num(n.weekly_target),
                     _num(n.daily_upper_limit), "yes" if n.is_key else "", n.reference_basis])
    return rows


def apply_goal_updates(nutrients: list[Nutrient], updates: list[dict]) -> list[Nutrient]:
    """Validate goal edits ({key, daily_reference, weekly_target, daily_upper_limit, is_key}) and apply them.

    Raises ValueError with a user-facing message if anything is invalid.
    """
    by_key = {u.get("key"): u for u in updates if isinstance(u, dict)}
    if not by_key:
        raise ValueError("No goals to save.")
    labels = {"daily_reference": "daily goal", "weekly_target": "weekly target", "daily_upper_limit": "upper limit"}

    def number(g, field, name, optional=False):
        raw = g.get(field)
        if optional and (raw is None or str(raw).strip() == "" or (isinstance(raw, float) and math.isnan(raw))):
            return None
        try:
            v = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{name}: enter a number for the {labels[field]}.")
        if not math.isfinite(v) or v <= 0:
            raise ValueError(f"{name}: the {labels[field]} must be greater than 0.")
        return v

    new = []
    for n in nutrients:
        g = by_key.get(n.key)
        if not g:
            new.append(n)
            continue
        daily = number(g, "daily_reference", n.name)
        weekly = number(g, "weekly_target", n.name)
        limit = number(g, "daily_upper_limit", n.name, optional=True)
        if limit is not None and limit < daily:
            raise ValueError(f"{n.name}: the upper limit can't be lower than the daily goal.")
        new.append(replace(n, daily_reference=daily, weekly_target=weekly, daily_upper_limit=limit,
                           is_key=bool(g.get("is_key"))))
    return new


class CsvTargets:
    """Targets stored in a local CSV file (the Flask app and local Streamlit runs)."""

    def __init__(self, path: Path):
        self.path = Path(path)

    @property
    def location(self) -> str:
        return f"data/{self.path.name}"

    def load(self) -> list[Nutrient]:
        return load_nutrients(self.path)

    def save(self, nutrients: list[Nutrient]) -> None:
        save_nutrients(self.path, nutrients)


def save_nutrients(path: Path, nutrients: list[Nutrient]) -> None:
    """Write the targets CSV atomically (temp file + rename) so a crash never leaves it half-written."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".csv.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerows(nutrient_rows(nutrients))
        os.replace(tmp, path)
    except PermissionError as e:
        os.unlink(tmp)
        raise PermissionError(f"Couldn't save {path.name} — close it in Excel and try again.") from e


def load_presets(path: Path) -> dict[str, dict[str, float]]:
    """Goal presets: {preset_name: {nutrient_key: daily_goal}} from data/goal_presets.csv."""
    if not path.exists():
        return {}
    presets: dict[str, dict[str, float]] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        names = [c for c in (reader.fieldnames or []) if c != "key"]
        for row in reader:
            for name in names:
                if (row.get(name) or "").strip():
                    presets.setdefault(name, {})[row["key"].strip()] = float(row[name])
    return presets
