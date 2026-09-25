"""Weekly Nutrition Tracker — core modules.

nutrients.py      nutrient definitions + weekly targets (loaded from CSV)
llm_client.py     generic OpenAI-compatible chat client (NVIDIA NIM by default)
nutrition_api.py  meal text -> 32 nutrient values (LLM provider + offline demo provider)
storage.py        Excel workbook meal log
weekly.py         weekly aggregation and gap analysis
insights.py       "Improve My Diet" suggestions (LLM, with offline fallback)
sample_data.py    demo meals so the dashboard has something to show
"""
