"""Google Sheets storage — used by the hosted Streamlit app, where local files don't survive restarts.

One spreadsheet holds everything, laid out like the local Excel log so a non-developer can read it:
  Meals        one row per meal (same columns as data/nutrition_log.xlsx)
  Column Keys  which nutrient each Meals column holds (renaming a nutrient keeps its history)
  Targets      daily goals, weekly targets, upper limits, key flags (same columns as weekly_targets.csv)

GoogleSheetsMealStore has the same methods as storage.ExcelMealStore, and SheetsTargets the same as
nutrients.CsvTargets, so the rest of the app doesn't care which backend is in use.
"""
import threading
import uuid
from datetime import date, datetime

from gspread.exceptions import APIError, WorksheetNotFound
from gspread.utils import ValueInputOption, ValueRenderOption, rowcol_to_a1

from .nutrients import Nutrient, load_nutrients, nutrient_rows, nutrients_from_rows
from .storage import (INFO_COLUMNS, KEYS_SHEET, MEALS_SHEET, SAMPLE_MARKER, StorageError, nutrient_columns,
                      parse_meal_rows)

TARGETS_SHEET = "Targets"
CELL_LIMIT = 45000  # Google Sheets allows 50,000 characters per cell
RAW = ValueInputOption.raw
UNFORMATTED = ValueRenderOption.unformatted

# One writer at a time within this app process (Streamlit runs every session in one process).
_write_lock = threading.RLock()


def _clip(text: str) -> str:
    return text if len(text) <= CELL_LIMIT else text[:CELL_LIMIT] + "…"


def _api_error(action: str, e: Exception) -> StorageError:
    return StorageError(f"Google Sheets couldn't {action} ({e}). Check the sheet is shared with the service "
                        f"account and try again.")


def _worksheet(spreadsheet, title: str, rows: int, cols: int):
    """Return (worksheet, created)."""
    try:
        return spreadsheet.worksheet(title), False
    except WorksheetNotFound:
        return spreadsheet.add_worksheet(title=title, rows=rows, cols=cols), True


class GoogleSheetsMealStore:
    def __init__(self, spreadsheet, nutrients: list[Nutrient]):
        self.sh = spreadsheet
        self.nutrients = nutrients

    @property
    def location(self) -> str:
        return f"Google Sheet “{self.sh.title}”"

    # ------------------------------------------------------------------ structure
    def _meals_ws(self):
        ws, created = _worksheet(self.sh, MEALS_SHEET, rows=1000, cols=len(INFO_COLUMNS) + len(self.nutrients) + 5)
        return ws, created

    def ensure_workbook(self) -> bool:
        """Create the Meals / Column Keys sheets if needed. Returns True if the meal log was just created."""
        with _write_lock:
            try:
                ws, created = self._meals_ws()
                if created or not ws.row_values(1):
                    ws.update(values=[INFO_COLUMNS + [n.label for n in self.nutrients]], range_name="A1",
                              value_input_option=RAW)
                    ws.freeze(rows=1)
                    self._write_keymap({n.key: n.label for n in self.nutrients})
                    return True
                return False
            except APIError as e:
                raise _api_error("set up the meal log", e) from e

    def _read_keymap(self) -> dict[str, str]:
        try:
            rows = self.sh.worksheet(KEYS_SHEET).get_values()
        except WorksheetNotFound:
            return {}
        return {str(r[0]): str(r[1]) for r in rows[1:] if len(r) >= 2 and r[0] and r[1]}

    def _write_keymap(self, mapping: dict[str, str]) -> None:
        ws, _ = _worksheet(self.sh, KEYS_SHEET, rows=max(100, len(mapping) + 10), cols=2)
        rows = [["Nutrient key", "Column header on the Meals sheet"]] + [[k, v] for k, v in mapping.items()]
        ws.update(values=rows, range_name="A1", value_input_option=RAW)

    def _prepare_for_write(self):
        """Make sure every nutrient has a column (renaming headers if a nutrient was renamed)."""
        self.ensure_workbook()
        ws, _ = self._meals_ws()
        header = ws.row_values(1)
        keymap = self._read_keymap()
        cols = nutrient_columns(header, keymap, self.nutrients)
        changed = False
        for n in self.nutrients:
            if n.key not in cols:
                header.append(n.label)
                cols[n.key] = len(header) - 1
                changed = True
            elif header[cols[n.key]] != n.label:
                header[cols[n.key]] = n.label
                changed = True
        if changed:
            if ws.col_count < len(header):
                ws.resize(cols=len(header) + 5)
            ws.update(values=[header], range_name="A1", value_input_option=RAW)
        new_keymap = {**keymap, **{n.key: n.label for n in self.nutrients}}
        if new_keymap != keymap:
            self._write_keymap(new_keymap)
        return ws, header, cols

    # ------------------------------------------------------------------ rows
    def _row(self, header: list, cols: dict, meal: dict) -> list:
        row = [""] * len(header)
        info = {
            "Meal ID": meal["id"], "Date": meal["date"].isoformat(), "Meal Type": meal["meal_type"],
            "Meal Description": _clip(meal["description"]), "Food Items & Quantities": _clip(meal["items_text"]),
            "Estimated By": _clip(meal["source"]), "Logged At": meal["logged_at"].isoformat(sep=" "),
        }
        for name, value in info.items():
            if name in header:
                row[header.index(name)] = value
        for n in self.nutrients:
            row[cols[n.key]] = round(float(meal["nutrients"].get(n.key, 0.0)), 3)
        return row

    def _new_meal(self, meal_date: date, meal_type: str, description: str, items_text: str,
                  source: str, nutrients: dict) -> dict:
        return {"id": uuid.uuid4().hex[:8], "date": meal_date, "meal_type": meal_type, "description": description,
                "items_text": items_text, "source": source, "logged_at": datetime.now().replace(microsecond=0),
                "nutrients": {n.key: float(nutrients.get(n.key, 0.0)) for n in self.nutrients}}

    @staticmethod
    def _public(meal: dict) -> dict:
        return {**meal, "date": meal["date"].isoformat(), "logged_at": meal["logged_at"].isoformat()}

    def _values(self, ws) -> list[list]:
        return ws.get_values(value_render_option=UNFORMATTED)

    @staticmethod
    def _find_row(values: list[list], meal_id: str) -> int | None:
        """1-based sheet row of the meal, or None."""
        if not values or "Meal ID" not in values[0]:
            return None
        i = values[0].index("Meal ID")
        for n, r in enumerate(values[1:], start=2):
            if i < len(r) and str(r[i]) == meal_id:
                return n
        return None

    # ------------------------------------------------------------------ writes
    def add_meals(self, entries: list[dict]) -> list[dict]:
        meals = [self._new_meal(**e) for e in entries]
        with _write_lock:
            try:
                ws, header, cols = self._prepare_for_write()
                ws.append_rows([self._row(header, cols, m) for m in meals], value_input_option=RAW,
                               table_range="A1")
            except APIError as e:
                raise _api_error("save the meal", e) from e
        return [self._public(m) for m in meals]

    def add_meal(self, *, meal_date: date, meal_type: str, description: str, items_text: str,
                 source: str, nutrients: dict[str, float]) -> dict:
        return self.add_meals([dict(meal_date=meal_date, meal_type=meal_type, description=description,
                                    items_text=items_text, source=source, nutrients=nutrients)])[0]

    def update_meal(self, meal_id: str, *, meal_date: date, meal_type: str, description: str, items_text: str,
                    source: str, nutrients: dict[str, float]) -> dict | None:
        with _write_lock:
            try:
                ws, header, cols = self._prepare_for_write()
                values = self._values(ws)
                row = self._find_row(values, meal_id)
                if not row:
                    return None
                meal = self._new_meal(meal_date, meal_type, description, items_text, source, nutrients)
                meal["id"] = meal_id
                old = values[row - 1]
                if "Logged At" in header and header.index("Logged At") < len(old):
                    try:
                        meal["logged_at"] = datetime.fromisoformat(str(old[header.index("Logged At")]))
                    except ValueError:
                        pass
                ws.update(values=[self._row(header, cols, meal)], range_name=f"A{row}", value_input_option=RAW)
            except APIError as e:
                raise _api_error("update the meal", e) from e
        return self._public(meal)

    def _delete_rows(self, ws, rows: list[int]) -> None:
        """Delete several rows in ONE request (bottom-up), so it's all-or-nothing."""
        requests = [{"deleteDimension": {"range": {"sheetId": ws.id, "dimension": "ROWS",
                                                   "startIndex": r - 1, "endIndex": r}}}
                    for r in sorted(rows, reverse=True)]
        self.sh.batch_update({"requests": requests})

    def delete_meal(self, meal_id: str) -> bool:
        with _write_lock:
            try:
                ws, _ = self._meals_ws()
                row = self._find_row(self._values(ws), meal_id)
                if not row:
                    return False
                self._delete_rows(ws, [row])
            except APIError as e:
                raise _api_error("delete the meal", e) from e
        return True

    def delete_sample_meals(self) -> int:
        with _write_lock:
            try:
                ws, _ = self._meals_ws()
                values = self._values(ws)
                if not values or "Estimated By" not in values[0]:
                    return 0
                i = values[0].index("Estimated By")
                rows = [n for n, r in enumerate(values[1:], start=2)
                        if i < len(r) and str(r[i]).endswith(SAMPLE_MARKER)]
                if rows:
                    self._delete_rows(ws, rows)
                return len(rows)
            except APIError as e:
                raise _api_error("remove the sample meals", e) from e

    def _backfill_ids(self, ws, values: list[list]) -> None:
        """Give rows typed into the sheet by hand a Meal ID, so they can be edited/deleted safely."""
        header = values[0]
        if "Meal ID" not in header or "Date" not in header:
            return
        id_i, date_i = header.index("Meal ID"), header.index("Date")
        updates = []
        for n, r in enumerate(values[1:], start=2):
            has_date = date_i < len(r) and r[date_i] not in ("", None)
            has_id = id_i < len(r) and r[id_i] not in ("", None)
            if has_date and not has_id:
                updates.append({"range": rowcol_to_a1(n, id_i + 1), "values": [[uuid.uuid4().hex[:8]]]})
        if updates:
            ws.batch_update(updates, value_input_option=RAW)

    # ------------------------------------------------------------------ reads
    def list_meals(self, start: date | None = None, end: date | None = None) -> list[dict]:
        try:
            ws, created = self._meals_ws()
            if created:
                return []
            values = self._values(ws)
            meals, missing_ids = parse_meal_rows(values, self._read_keymap(), self.nutrients, start, end)
            if missing_ids:
                with _write_lock:
                    self._backfill_ids(ws, values)
                meals, _ = parse_meal_rows(self._values(ws), self._read_keymap(), self.nutrients, start, end)
            return meals
        except APIError as e:
            raise _api_error("read the meal log", e) from e


NUMERIC_TARGET_COLUMNS = {"daily_reference", "weekly_target", "daily_upper_limit"}


def _typed_target_rows(nutrients: list[Nutrient]) -> list[list]:
    """Targets table with numbers stored as numbers (easier to edit in Sheets than text)."""
    rows = nutrient_rows(nutrients)
    header = rows[0]
    numeric = [i for i, h in enumerate(header) if h in NUMERIC_TARGET_COLUMNS]
    for r in rows[1:]:
        for i in numeric:
            r[i] = float(r[i]) if r[i] != "" else ""
    return rows


class SheetsTargets:
    """Goals stored in the spreadsheet's "Targets" worksheet; seeded from the default CSV the first time."""

    def __init__(self, spreadsheet, default_csv):
        self.sh = spreadsheet
        self.default_csv = default_csv

    @property
    def location(self) -> str:
        return f"the “{TARGETS_SHEET}” sheet of Google Sheet “{self.sh.title}”"

    def load(self) -> list[Nutrient]:
        try:
            ws, created = _worksheet(self.sh, TARGETS_SHEET, rows=60, cols=12)
            values = [] if created else ws.get_values(value_render_option=UNFORMATTED)
            if len(values) < 2:
                nutrients = load_nutrients(self.default_csv)
                with _write_lock:
                    ws.update(values=_typed_target_rows(nutrients), range_name="A1", value_input_option=RAW)
                return nutrients
        except APIError as e:
            raise _api_error("read the goals", e) from e
        header = [str(h).strip() for h in values[0]]
        rows = [{h: (r[i] if i < len(r) else "") for i, h in enumerate(header)} for r in values[1:]]
        return nutrients_from_rows(rows, f"the {TARGETS_SHEET} sheet")

    def save(self, nutrients: list[Nutrient]) -> None:
        rows = _typed_target_rows(nutrients)
        with _write_lock:
            try:
                ws, _ = _worksheet(self.sh, TARGETS_SHEET, rows=60, cols=12)
                old_rows = len(ws.get_values())
                ws.update(values=rows, range_name="A1", value_input_option=RAW)
                if old_rows > len(rows):  # the list got shorter: blank out leftover rows
                    ws.batch_clear([f"A{len(rows) + 1}:Z{old_rows}"])
            except APIError as e:
                raise _api_error("save the goals", e) from e
