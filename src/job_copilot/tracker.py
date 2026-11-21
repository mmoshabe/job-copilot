"""Application tracker: a local SQLite file in data/ (private, gitignored)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

STATUSES = ("new", "applied", "interview", "offer", "rejected", "withdrawn", "skipped")

_SCHEMA = """CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT, title TEXT, location TEXT, url TEXT,
    score INTEGER, recommendation TEXT, assessment TEXT, job_text TEXT,
    status TEXT DEFAULT 'new', applied_on TEXT, follow_up_on TEXT,
    letter_path TEXT, notes TEXT DEFAULT '', added_on TEXT)"""


@dataclass
class Job:
    id: int
    company: str
    title: str
    location: str
    url: str | None
    score: int
    recommendation: str
    status: str
    applied_on: str | None
    follow_up_on: str | None
    letter_path: str | None
    notes: str
    added_on: str


class Tracker:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute(_SCHEMA)
        self.db.commit()

    def add(self, *, company, title, location, url, score, recommendation, assessment: dict, job_text, today: date | None = None) -> int:
        if url:
            row = self.db.execute("SELECT id FROM jobs WHERE url = ?", (url,)).fetchone()
            if row:
                return row[0]  # already tracked: don't duplicate
        cur = self.db.execute(
            "INSERT INTO jobs (company, title, location, url, score, recommendation, assessment, job_text, added_on)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (company, title, location, url, score, recommendation, json.dumps(assessment), job_text,
             (today or date.today()).isoformat()),
        )
        self.db.commit()
        return cur.lastrowid

    def _row(self, r) -> Job:
        return Job(*r)

    _COLS = "id, company, title, location, url, score, recommendation, status, applied_on, follow_up_on, letter_path, notes, added_on"

    def get(self, job_id: int) -> Job | None:
        r = self.db.execute(f"SELECT {self._COLS} FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row(r) if r else None

    def raw(self, job_id: int) -> tuple[dict, str]:
        a, t = self.db.execute("SELECT assessment, job_text FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return json.loads(a), t

    def list(self, status: str | None = None) -> list[Job]:
        q, p = f"SELECT {self._COLS} FROM jobs", ()
        if status:
            q, p = q + " WHERE status = ?", (status,)
        return [self._row(r) for r in self.db.execute(q + " ORDER BY score DESC, id", p)]

    def set_status(self, job_id: int, status: str, follow_up_days: int, today: date | None = None) -> None:
        if status not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        today = today or date.today()
        if status == "applied":
            self.db.execute("UPDATE jobs SET status = ?, applied_on = ?, follow_up_on = ? WHERE id = ?",
                            (status, today.isoformat(), (today + timedelta(days=follow_up_days)).isoformat(), job_id))
        elif status in ("rejected", "withdrawn", "skipped", "offer"):
            self.db.execute("UPDATE jobs SET status = ?, follow_up_on = NULL WHERE id = ?", (status, job_id))
        else:
            self.db.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))
        self.db.commit()

    def snooze(self, job_id: int, days: int, today: date | None = None) -> None:
        d = ((today or date.today()) + timedelta(days=days)).isoformat()
        self.db.execute("UPDATE jobs SET follow_up_on = ? WHERE id = ?", (d, job_id))
        self.db.commit()

    def set_letter(self, job_id: int, path: str) -> None:
        self.db.execute("UPDATE jobs SET letter_path = ? WHERE id = ?", (path, job_id))
        self.db.commit()

    def add_note(self, job_id: int, note: str) -> None:
        self.db.execute("UPDATE jobs SET notes = notes || ? WHERE id = ?", (f"{date.today()}: {note}\n", job_id))
        self.db.commit()

    def due(self, today: date | None = None) -> list[Job]:
        t = (today or date.today()).isoformat()
        return [self._row(r) for r in self.db.execute(
            f"SELECT {self._COLS} FROM jobs WHERE follow_up_on IS NOT NULL AND follow_up_on <= ? ORDER BY follow_up_on", (t,))]
