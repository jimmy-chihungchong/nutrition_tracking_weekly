"""App settings, read from environment variables (or a local .env file)."""
import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"


def _load_dotenv(path: Path) -> None:
    """Tiny .env reader so we don't need an extra dependency. Real env vars win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    # "auto" = use the LLM when an API key is set, otherwise the offline demo table.
    # "llm"  = always use the LLM.  "demo" = always use the offline demo table.
    nutrition_provider: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    insights_model: str
    llm_timeout_seconds: int
    targets_path: Path
    presets_path: Path
    food_table_path: Path
    meals_path: Path


def load_settings() -> Settings:
    _load_dotenv(BASE_DIR / ".env")
    model = os.getenv("LLM_MODEL", "meta/llama-3.3-70b-instruct")
    return Settings(
        nutrition_provider=os.getenv("NUTRITION_PROVIDER", "auto").lower(),
        llm_base_url=os.getenv("LLM_BASE_URL", "https://integrate.api.nvidia.com/v1"),
        llm_api_key=os.getenv("LLM_API_KEY") or os.getenv("NVIDIA_API_KEY", ""),
        llm_model=model,
        insights_model=os.getenv("INSIGHTS_MODEL", model),
        llm_timeout_seconds=int(os.getenv("LLM_TIMEOUT_SECONDS", "90")),
        targets_path=Path(os.getenv("TARGETS_FILE", DATA_DIR / "weekly_targets.csv")),
        presets_path=Path(os.getenv("PRESETS_FILE", DATA_DIR / "goal_presets.csv")),
        food_table_path=Path(os.getenv("FOOD_TABLE_FILE", DATA_DIR / "demo_food_table.csv")),
        meals_path=Path(os.getenv("MEALS_FILE", DATA_DIR / "nutrition_log.xlsx")),
    )
