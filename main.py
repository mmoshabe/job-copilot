"""Vercel entry point: exposes the dashboard's FastAPI app."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from job_copilot.web import app  # noqa: E402,F401
