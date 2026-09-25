# Weekly Nutrition Tracker (prototype)

Log meals in plain English, get estimates for 32 nutrients, and track them day by day and across the week.
The app shows where your gaps are and gives AI suggestions for closing them.

| Tab | What you can do |
|---|---|
| **Log Meal** | Describe a meal, review how it was understood (remove items, change amounts), save it and see its impact on your day. One-click **Log again** for recent meals; **Ctrl + Enter** to calculate. |
| **My Meals** | Browse your log week by week. Open a meal for its nutrients, then **log it again**, **edit** it or **delete** it. Remove or reload the sample meals. |
| **Daily Tracker** | One day against your daily goals, with a Mon–Sun strip, over-limit warnings and **Improve today** suggestions. |
| **Weekly Progress** | The week against weekly targets: gaps, strongest and weakest nutrients, full table, and a **last 4 weeks** trend. |
| **Improve My Diet** | AI suggestions based on the week's actual gaps. |
| **Goals** | Edit daily goals, weekly targets, upper limits and key nutrients, or apply a preset (reference, adult female, adult male). |

> **Prototype only.** Nutrient values are AI/reference estimates and are **not clinically validated**.
> Nothing in the app is medical advice.

## Two ways to run it

| Version | Command | Storage | Use it for |
|---|---|---|---|
| **Streamlit** (`streamlit_app.py`) | `streamlit run streamlit_app.py` | Excel file in `data/`: permanent on your computer, **temporary on Streamlit Cloud** (reset on every restart; download a backup from the sidebar). Optionally Google Sheets for permanent hosted storage. | Hosting online on Streamlit Community Cloud. See **[DEPLOY_STREAMLIT.md](DEPLOY_STREAMLIT.md)**. |
| **Flask** (`app.py`) | `python app.py` → http://127.0.0.1:5000 | Excel file in `data/` | Running on your own computer |

Both use the same `nutrition_tracker/` modules, so calculations, goals, statuses and insights are identical.

### Run the Flask version

**Windows:** double-click `start.bat`. The first run sets everything up, then the app opens at http://127.0.0.1:5000.

**Manually:**
```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # macOS/Linux: .venv/bin/pip
.venv/Scripts/python app.py
```

On first start the app creates `data/nutrition_log.xlsx` and fills it with **sample meals** for last week and
for this week up to today, so the dashboard works right away. Sample meals are marked *(sample data)*. Use
**Remove sample meals** or **Reload sample meals** at the bottom of **My Meals**; both leave your own meals alone.

## Turn on the AI (optional)

Without an API key the app runs in **demo mode**. Meals are estimated from a small offline food table
(`data/demo_food_table.csv`, about 40 common foods), and insights are rule-based. To use an LLM:

1. Get an API key from [build.nvidia.com](https://build.nvidia.com) (NVIDIA NIM).
2. Copy `.env.example` to `.env` and set `LLM_API_KEY=nvapi-...`.
3. Restart the app. The header shows which nutrition engine is active.

Any **OpenAI-compatible** endpoint works (OpenAI, Together, Groq, OpenRouter, local Ollama/vLLM). Change
`LLM_BASE_URL`, `LLM_MODEL` and `LLM_API_KEY`. If the LLM call fails, the app falls back to demo estimates
and tells you so.

## Where the data lives

| File | What it is | Edit it? |
|---|---|---|
| `data/nutrition_log.xlsx` | The meal log. Sheet **Meals**: one row per meal (date, meal type, your description, how it was interpreted, which engine estimated it, and one column per nutrient). Sheet **Column Keys**: which nutrient each column holds, so renaming a nutrient keeps its history. | Yes, in Excel. **Close it before logging in the app**; while it's open the app refuses to save, so your spreadsheet can't overwrite new meals. |
| `data/weekly_targets.csv` | The 32 nutrients with their units, **daily goal** (`daily_reference`), **weekly target** (`weekly_target`), optional **daily upper limit** (`daily_upper_limit`) and **key nutrient** flag (`key_nutrient`). The app re-reads this file on every request. | Yes, on the **Goals** screen or in Excel. |
| `data/goal_presets.csv` | Daily goal presets shown on the Goals screen (reference DV, adult female, adult male). | Optional. Add a column to add a preset. |
| `data/demo_food_table.csv` | Offline food table (per 100 g) for demo mode and offline insights | Optional |

Weekly targets start at an adult reference daily value × 7 (mostly FDA Daily Values; the source is in the
`reference_basis` column). Upper limits are NIH Tolerable Upper Intake Levels, 2300 mg for sodium, and a 120%
ceiling for calories. They're generic reference values, not personalised requirements.

**Safety of your data:**
- One save at a time, even with several tabs open.
- Saves write to a temporary file first and then replace the workbook, so a failed save can't corrupt it.
- Nothing is written while the workbook is open in Excel or LibreOffice.
- Rows you type into the sheet by hand get a Meal ID automatically, so they can be edited and deleted.

## How it works

```
Browser UI (templates/, static/)
   │  free-text meal
   ▼
nutrition_api.py ── LLM provider (llm_client.py → NVIDIA NIM / any OpenAI-compatible API)
   │                └ demo provider (offline food table)
   │  32 nutrient values
   ▼
storage.py ── data/nutrition_log.xlsx
   ▼
daily.py / weekly.py ── day and Monday–Sunday totals vs data/weekly_targets.csv
   ▼
Dashboards ──► insights.py ── LLM "Improve today" / "Improve My Diet" (or rule-based fallback)
```

| Layer | File | To replace it… |
|---|---|---|
| User interface | `templates/index.html`, `static/` | Talks only to the JSON API in `app.py` |
| Nutrition calculation | `nutrition_tracker/nutrition_api.py` | Add a class with `name` + `estimate()` and return it from `build_nutrition_provider()` |
| Data storage (hosted) | `nutrition_tracker/sheets_storage.py` | Google Sheets version of the same store and of the targets file |
| Data storage | `nutrition_tracker/storage.py` | Implement `add_meal(s)` / `update_meal` / `list_meals` / `delete_meal` / `delete_sample_meals` (e.g. for Google Sheets) |
| Goals | `nutrition_tracker/nutrients.py` | Loads and saves the targets CSV and presets |
| Weekly calculations | `nutrition_tracker/weekly.py` | Pure functions, no I/O |
| Daily calculations | `nutrition_tracker/daily.py` | Pure functions, no I/O |
| AI insights | `nutrition_tracker/insights.py` | Uses the same `ChatClient` |

**Status logic:** for the current week, progress is compared to where you'd expect to be by now (for example,
day 3 of 7 ≈ 43%). *Reached* = 100% or more. *On track* = at least 85% of that pace. *Behind* = 50–85%.
*Far behind* = under 50%, which is what the dashboard shows as "substantially below target". Past weeks are
judged against the full weekly target. *Over limit* means more than 7 × the daily upper limit that week.

**Daily tracker:** each nutrient is compared with its daily goal. *Met* = 100% or more, *Almost* = 75–99%,
*Low* = 50–74%, *Very low* = under 50%, *Over limit* = above the daily upper limit.

**Reviewing a meal:** both nutrition engines return nutrients per food item, so removing an item or changing its
grams re-totals the meal instantly, with no second API call. If a model only returns meal totals, you adjust by
editing the description and recalculating.

## Assumptions and limits

- A single user; there are no accounts or login.
- Weeks run Monday to Sunday.
- LLM estimates vary between runs and depend on how clear the description is. Demo mode only recognises the
  foods in its table and skips anything else (it tells you what it skipped).
- Liquids in ml are treated as roughly 1 g/ml.
- Sodium is tracked against an adequate-intake target but is never recommended as something to increase.
- Meals can't be logged for future dates.
- If the app shows "open in a spreadsheet program" but the file isn't open, delete the leftover
  `.~lock.nutrition_log.xlsx#` (LibreOffice) or `~$nutrition_log.xlsx` (Excel) file in `data/`.
