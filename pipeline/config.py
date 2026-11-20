"""All configuration comes from environment variables (see .env.example)."""
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    database_url: str
    data_dir: Path
    rules_dir: Path
    reports_dir: Path
    source_tz: str                 # time zone of the source system; epoch values are converted to it
    # --- agents ---
    llm_provider: str              # none | anthropic | openai_compat | replay
    llm_model_small: str           # classification
    llm_model_strong: str          # rule proposal
    llm_base_url: str
    llm_api_key: str
    llm_temperature: str           # "" = do not send the parameter
    llm_token_budget: int          # hard cap on tokens per command
    replay_dir: Path
    batch_size: int                # values per classification call
    auto_apply_confidence: float   # mappings at or above this are applied as provisional
    classify_min_rows: int         # values rarer than this are held, not sent to the model
    count_tolerance: float         # allowed relative gap between an agent's claimed and measured row count
    approver: str
    console_api_token: str         # bearer token for the optional console API; empty = the API answers nothing


def load() -> Settings:
    return Settings(
        database_url=_env("DATABASE_URL", "postgresql://medallion:medallion@localhost:5432/medallion"),
        data_dir=Path(_env("DATA_DIR", str(ROOT / "data"))),
        rules_dir=Path(_env("RULES_DIR", str(ROOT / "rules"))),
        reports_dir=Path(_env("REPORTS_DIR", str(ROOT / "reports"))),
        source_tz=_env("SOURCE_TZ", "UTC"),
        llm_provider=_env("LLM_PROVIDER", "none"),
        llm_model_small=_env("LLM_MODEL_SMALL", ""),
        llm_model_strong=_env("LLM_MODEL_STRONG", ""),
        llm_base_url=_env("LLM_BASE_URL", ""),
        llm_api_key=_env("LLM_API_KEY", ""),
        llm_temperature=_env("LLM_TEMPERATURE", "0"),
        llm_token_budget=int(_env("LLM_TOKEN_BUDGET", "400000")),
        replay_dir=Path(_env("LLM_REPLAY_DIR", str(ROOT / "evals" / "recordings"))),
        batch_size=int(_env("CLASSIFY_BATCH_SIZE", "50")),
        auto_apply_confidence=float(_env("AUTO_APPLY_CONFIDENCE", "0.9")),
        classify_min_rows=int(_env("CLASSIFY_MIN_ROWS", "3")),
        count_tolerance=float(_env("DQ_COUNT_TOLERANCE", "0.05")),
        approver=_env("APPROVER", os.getenv("USER", "reviewer")),
        console_api_token=_env("CONSOLE_API_TOKEN", ""),
    )
