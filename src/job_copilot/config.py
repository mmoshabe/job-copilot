from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)
load_dotenv(PROJECT_ROOT / ".env.local", override=False)  # DATABASE_URL from `vercel env pull`

# USD per 1M tokens: (input, output, cache_read)
PRICES: dict[str, tuple[float, float, float]] = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}

DATA_DIR = Path(os.environ.get("JOBS_DATA_DIR", str(PROJECT_ROOT / "data")))  # private, gitignored


@dataclass(frozen=True)
class Settings:
    model: str = field(default_factory=lambda: os.environ.get("JOBS_MODEL", "claude-opus-5-5"))
    cv_path: Path = field(default_factory=lambda: Path(os.environ.get("JOBS_CV", str(DATA_DIR / "cv.md"))))
    db_path: Path = field(default_factory=lambda: DATA_DIR / "applications.sqlite3")
    letters_dir: Path = field(default_factory=lambda: DATA_DIR / "letters")
    follow_up_days: int = field(default_factory=lambda: int(os.environ.get("JOBS_FOLLOW_UP_DAYS", "7")))
    max_letter_revisions: int = 2
    database_url: str | None = field(default_factory=lambda: os.environ.get("DATABASE_URL"))
    monthly_budget_usd: float = field(default_factory=lambda: float(os.environ.get("JOBS_MONTHLY_BUDGET_USD", "5")))
    portfolio_data: Path = field(default_factory=lambda: Path(os.environ.get(
        "JOBS_PORTFOLIO_DATA", str(PROJECT_ROOT.parent.parent / "portfolio" / "lib" / "data.ts"))))
