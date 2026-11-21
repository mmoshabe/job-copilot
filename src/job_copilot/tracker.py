"""Storage: jobs, history events, profile (CV + portfolio evidence) and LLM spend.

Postgres when DATABASE_URL is set (deployed, Neon); otherwise a local SQLite file in
data/ (private, gitignored). Same SQL for both, with `?` placeholders translated."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

STATUSES = ("new", "applied", "interview", "offer", "rejected", "withdrawn", "skipped")
CLOSED = ("rejected", "withdrawn", "skipped", "offer")


class BudgetExceeded(Exception):
    pass


def _tables(pg: bool) -> list[str]:
    pk = "SERIAL PRIMARY KEY" if pg else "INTEGER PRIMARY KEY AUTOINCREMENT"
    return [
        f"""CREATE TABLE IF NOT EXISTS jobs (
            id {pk}, company TEXT, title TEXT, location TEXT, url TEXT,
            score INTEGER, recommendation TEXT, assessment TEXT, job_text TEXT,
            status TEXT DEFAULT 'new', applied_on TEXT, follow_up_on TEXT,
            letter TEXT, letter_check TEXT, resume TEXT, resume_check TEXT,
            notes TEXT DEFAULT '', added_on TEXT)""",
        f"""CREATE TABLE IF NOT EXISTS events (
            id {pk}, job_id INTEGER, kind TEXT, detail TEXT, at TEXT)""",
        """CREATE TABLE IF NOT EXISTS profile (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)""",
        f"""CREATE TABLE IF NOT EXISTS llm_usage (
            id {pk}, action TEXT, model TEXT, job_id INTEGER, cost_usd REAL, at TEXT)""",
        f"""CREATE TABLE IF NOT EXISTS leads (
            id {pk}, source TEXT, title TEXT, company TEXT, location TEXT, url TEXT UNIQUE, text TEXT,
            posted TEXT, score REAL, keep INTEGER, reason TEXT, state TEXT, job_id INTEGER, found_at TEXT)""",
        f"""CREATE TABLE IF NOT EXISTS runs (
            id {pk}, started TEXT, finished TEXT, trigger TEXT, report TEXT)""",
    ]


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
    notes: str
    added_on: str
    has_letter: bool
    has_resume: bool


class _Rows:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list:
        return self._rows


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Tracker:
    _COLS = ("id, company, title, location, url, score, recommendation, status, applied_on, follow_up_on, "
             "notes, added_on, letter IS NOT NULL, resume IS NOT NULL")

    def __init__(self, path: Path | None = None, database_url: str | None = None) -> None:
        self._lock = threading.Lock()
        self._pg = bool(database_url)
        self._url = database_url
        if self._pg:
            self.db = self._connect_pg()
        else:
            path = path or Path(":memory:")
            if str(path) != ":memory:":
                path.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(path, check_same_thread=False)
        for t in _tables(self._pg):
            self._x(t)

    # -- low level -------------------------------------------------------------

    def _connect_pg(self):
        import psycopg

        return psycopg.connect(self._url, autocommit=True)

    def _x(self, sql: str, params: tuple = ()):
        if not self._pg:
            with self._lock:
                cur = self.db.execute(sql, params)
                rows = cur.fetchall() if cur.description else []  # read before commit (RETURNING)
                self.db.commit()
                return _Rows(rows)
        import psycopg

        sql = sql.replace("?", "%s")
        with self._lock:
            try:
                return self.db.execute(sql, params)
            except psycopg.OperationalError:  # Neon drops idle connections
                self.db = self._connect_pg()
                return self.db.execute(sql, params)

    def _job(self, r) -> Job:
        return Job(*r[:12], bool(r[12]), bool(r[13]))

    # -- events ------------------------------------------------------------------

    def log(self, kind: str, detail: str = "", job_id: int | None = None) -> None:
        self._x("INSERT INTO events (job_id, kind, detail, at) VALUES (?, ?, ?, ?)", (job_id, kind, detail, _now()))

    def events(self, job_id: int | None = None, limit: int = 200) -> list[dict]:
        q = "SELECT e.at, e.kind, e.detail, e.job_id, j.company, j.title FROM events e LEFT JOIN jobs j ON j.id = e.job_id"
        p: tuple = ()
        if job_id is not None:
            q, p = q + " WHERE e.job_id = ?", (job_id,)
        rows = self._x(q + f" ORDER BY e.id DESC LIMIT {int(limit)}", p).fetchall()
        return [dict(zip(("at", "kind", "detail", "job_id", "company", "title"), r)) for r in rows]

    # -- jobs ----------------------------------------------------------------------

    def add(self, *, company, title, location, url, score, recommendation, assessment: dict, job_text,
            today: date | None = None) -> int:
        if url:
            row = self._x("SELECT id FROM jobs WHERE url = ?", (url,)).fetchone()
            if row:
                return row[0]
        row = self._x(
            "INSERT INTO jobs (company, title, location, url, score, recommendation, assessment, job_text, added_on)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (company, title, location, url, score, recommendation, json.dumps(assessment), job_text,
             (today or date.today()).isoformat()),
        ).fetchone()
        self.log("added", f"fit {score}/100 → {recommendation}", row[0])
        return row[0]

    def get(self, job_id: int) -> Job | None:
        r = self._x(f"SELECT {self._COLS} FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._job(r) if r else None

    def raw(self, job_id: int) -> tuple[dict, str]:
        a, t = self._x("SELECT assessment, job_text FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return json.loads(a), t

    def all_assessments(self) -> list[tuple[Job, dict]]:
        rows = self._x(f"SELECT {self._COLS}, assessment FROM jobs").fetchall()
        return [(self._job(r[:14]), json.loads(r[14])) for r in rows]

    def list(self, status: str | None = None) -> list[Job]:
        q, p = f"SELECT {self._COLS} FROM jobs", ()
        if status:
            q, p = q + " WHERE status = ?", (status,)
        return [self._job(r) for r in self._x(q + " ORDER BY score DESC, id", p).fetchall()]

    def set_status(self, job_id: int, status: str, follow_up_days: int, today: date | None = None) -> None:
        if status not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        today = today or date.today()
        if status == "applied":
            self._x("UPDATE jobs SET status = ?, applied_on = ?, follow_up_on = ? WHERE id = ?",
                    (status, today.isoformat(), (today + timedelta(days=follow_up_days)).isoformat(), job_id))
        elif status in CLOSED:
            self._x("UPDATE jobs SET status = ?, follow_up_on = NULL WHERE id = ?", (status, job_id))
        else:
            self._x("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))
        self.log("status", status, job_id)

    def snooze(self, job_id: int, days: int, today: date | None = None) -> None:
        d = ((today or date.today()) + timedelta(days=days)).isoformat()
        self._x("UPDATE jobs SET follow_up_on = ? WHERE id = ?", (d, job_id))
        self.log("snoozed", f"follow-up moved to {d}", job_id)

    def add_note(self, job_id: int, note: str) -> None:
        self._x("UPDATE jobs SET notes = notes || ? WHERE id = ?", (f"{date.today()}: {note}\n", job_id))
        self.log("note", note[:120], job_id)

    def due(self, today: date | None = None) -> list[Job]:
        t = (today or date.today()).isoformat()
        return [self._job(r) for r in self._x(
            f"SELECT {self._COLS} FROM jobs WHERE follow_up_on IS NOT NULL AND follow_up_on <= ? ORDER BY follow_up_on",
            (t,)).fetchall()]

    # -- documents (letter / tailored resume) ----------------------------------------

    def save_doc(self, job_id: int, kind: str, text: str, check: dict) -> None:
        if kind not in ("letter", "resume"):
            raise ValueError(kind)
        self._x(f"UPDATE jobs SET {kind} = ?, {kind}_check = ? WHERE id = ?", (text, json.dumps(check), job_id))
        flagged = len(check.get("unsupported_claims", []))
        self.log(kind, "fact-check passed" if not flagged else f"{flagged} unsupported claim(s) flagged", job_id)

    def doc(self, job_id: int, kind: str) -> tuple[str | None, dict]:
        if kind not in ("letter", "resume"):
            raise ValueError(kind)
        text, check = self._x(f"SELECT {kind}, {kind}_check FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return text, json.loads(check) if check else {}

    # -- profile (CV + portfolio evidence) -------------------------------------------

    def set_profile(self, key: str, value: str) -> None:
        if self.profile(key) == value:
            return  # unchanged: don't touch the timestamp or log noise
        self._x("INSERT INTO profile (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (key, value, _now()))
        self.log("profile", f"{key} updated")

    def profile(self, key: str) -> str | None:
        r = self._x("SELECT value FROM profile WHERE key = ?", (key,)).fetchone()
        return r[0] if r else None

    def profile_updated(self, key: str) -> str | None:
        r = self._x("SELECT updated_at FROM profile WHERE key = ?", (key,)).fetchone()
        return r[0] if r else None

    # -- spend -------------------------------------------------------------------------

    def record_usage(self, action: str, model: str, cost_usd: float, job_id: int | None = None) -> None:
        self._x("INSERT INTO llm_usage (action, model, job_id, cost_usd, at) VALUES (?, ?, ?, ?, ?)",
                (action, model, job_id, cost_usd, _now()))

    def month_spend(self) -> float:
        start = date.today().replace(day=1).isoformat()
        r = self._x("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_usage WHERE at >= ?", (start,)).fetchone()
        return float(r[0])

    def check_budget(self, monthly_budget_usd: float) -> None:
        spent = self.month_spend()
        if spent >= monthly_budget_usd:
            raise BudgetExceeded(f"Monthly AI budget reached (${spent:.2f} of ${monthly_budget_usd:.2f}). "
                                 "Raise JOBS_MONTHLY_BUDGET_USD to continue.")

    # -- discovery: leads and runs ----------------------------------------------------------

    def known_url(self, url: str) -> bool:
        return bool(self._x("SELECT 1 FROM leads WHERE url = ?", (url,)).fetchone()
                    or self._x("SELECT 1 FROM jobs WHERE url = ?", (url,)).fetchone())

    def add_lead(self, lead) -> bool:
        """Store a discovered lead once (by URL). Returns True if it was new."""
        if not lead.url or self.known_url(lead.url):
            return False
        self._x("INSERT INTO leads (source, title, company, location, url, text, posted, score, keep, reason, state, found_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (lead.source, lead.title, lead.company, lead.location, lead.url, lead.text, lead.posted, lead.score,
                 1 if lead.keep else 0, lead.reason, "queued" if lead.keep else "filtered", _now()))
        return True

    def queued_leads(self, limit: int) -> list[dict]:
        rows = self._x("SELECT id, title, company, url, text, score FROM leads WHERE state = 'queued' "
                       "ORDER BY score DESC, id LIMIT ?", (limit,)).fetchall()
        return [dict(zip(("id", "title", "company", "url", "text", "score"), r)) for r in rows]

    def mark_lead(self, lead_id: int, state: str, job_id: int | None = None) -> None:
        self._x("UPDATE leads SET state = ?, job_id = ? WHERE id = ?", (state, job_id, lead_id))

    def leads(self, state: str | None = None, limit: int = 300) -> list[dict]:
        q, p = "SELECT id, source, title, company, location, url, score, reason, state, job_id, found_at FROM leads", ()
        if state:
            q, p = q + " WHERE state = ?", (state,)
        rows = self._x(q + f" ORDER BY id DESC LIMIT {int(limit)}", p).fetchall()
        return [dict(zip(("id", "source", "title", "company", "location", "url", "score", "reason", "state", "job_id",
                          "found_at"), r)) for r in rows]

    def lead_counts(self) -> dict[str, int]:
        return {k: v for k, v in self._x("SELECT state, COUNT(*) FROM leads GROUP BY state").fetchall()}

    def start_run(self, trigger: str) -> int | None:
        """Returns a run id, or None if another run started in the last 10 minutes (no double spend)."""
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(timespec="seconds")
        if self._x("SELECT 1 FROM runs WHERE finished IS NULL AND started > ?", (cutoff,)).fetchone():
            return None
        return self._x("INSERT INTO runs (started, trigger) VALUES (?, ?) RETURNING id", (_now(), trigger)).fetchone()[0]

    def finish_run(self, run_id: int, report: dict) -> None:
        self._x("UPDATE runs SET finished = ?, report = ? WHERE id = ?", (_now(), json.dumps(report), run_id))

    def runs(self, limit: int = 20) -> list[dict]:
        rows = self._x(f"SELECT id, started, finished, trigger, report FROM runs ORDER BY id DESC LIMIT {int(limit)}").fetchall()
        return [{"id": r[0], "started": r[1], "finished": r[2], "trigger": r[3],
                 "report": json.loads(r[4]) if r[4] else None} for r in rows]

    def spend_since(self, iso: str) -> float:
        return float(self._x("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_usage WHERE at >= ?", (iso,)).fetchone()[0])
