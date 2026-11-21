"""jobs: command-line job application copilot (same actions as the web dashboard).

    jobs add <url | file | ->  [--url LINK]   fetch a job post, score fit, track it         (paid)
    jobs letter <id>                           fact-checked cover letter                     (paid)
    jobs resume <id>                           fact-checked resume tailored to the job       (paid)
    jobs list [--status applied] | show <id> | status <id> applied | due | snooze <id> 7 | note <id> "…"
    jobs suggest                               what to do next (free)
    jobs profile push                          upload data/cv.md, data/projects.md and the portfolio
    jobs web                                   run the dashboard locally on http://localhost:8001
"""

from __future__ import annotations

import argparse
import re
import sys

from .llm import LLMError
from .schemas import FitAssessment, Met
from .service import Copilot, NoProfile
from .sources import SourceError
from .suggestions import skill_gaps, suggestions
from .tracker import STATUSES, BudgetExceeded

ICON = {Met.yes: "✅", Met.partial: "🟡", Met.no: "❌"}


def _print_fit(job_id: int, fit: FitAssessment, score: int, rec: str) -> None:
    print(f"\n#{job_id}  {fit.title} @ {fit.company}  ({fit.location or 'location not stated'})")
    print(f"Fit {score}/100 -> {rec.upper()}\n{fit.summary}\n")
    for r in fit.requirements:
        ev = f"  ← {r.evidence[:90]}" if r.evidence else ""
        print(f"  {ICON[r.met]} [{r.kind.value}] {r.text}{ev}")
    for d in fit.dealbreakers:
        print(f"  ⛔ dealbreaker: {d}")


def _save_local(c: Copilot, job_id: int, kind: str, text: str) -> str:
    j = c.t.get(job_id)
    c.s.letters_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", f"{j.company}-{j.title}".lower()).strip("-")[:60]
    path = c.s.letters_dir / f"{job_id}-{slug}-{kind}.md"
    path.write_text(text)
    return str(path)


def main() -> None:
    p = argparse.ArgumentParser(prog="jobs", description="Job application copilot")
    sub = p.add_subparsers(dest="cmd", required=True)
    x = sub.add_parser("add"); x.add_argument("source"); x.add_argument("--url")
    for name in ("show", "letter", "resume"):
        sub.add_parser(name).add_argument("id", type=int)
    sub.add_parser("list").add_argument("--status", choices=STATUSES)
    x = sub.add_parser("status"); x.add_argument("id", type=int); x.add_argument("status", choices=STATUSES)
    sub.add_parser("due"); sub.add_parser("suggest")
    x = sub.add_parser("snooze"); x.add_argument("id", type=int); x.add_argument("days", type=int)
    x = sub.add_parser("note"); x.add_argument("id", type=int); x.add_argument("text")
    x = sub.add_parser("profile"); x.add_argument("action", choices=["push"])
    x = sub.add_parser("web"); x.add_argument("--port", type=int, default=8001)
    a = p.parse_args()

    if a.cmd == "web":
        import uvicorn

        uvicorn.run("job_copilot.web:app", port=a.port, reload=False)
        return

    c = Copilot()
    t = c.t
    try:
        if a.cmd == "add":
            r = c.add_job(a.source, url=a.url)
            _print_fit(r.job_id, r.fit, r.score, r.recommendation)
            print(f"\n(cost ${r.cost_usd:.3f})  Next: jobs resume {r.job_id}  /  jobs letter {r.job_id}")
        elif a.cmd in ("letter", "resume"):
            t.get(a.id) or sys.exit(f"no job #{a.id}")
            res = c.letter(a.id) if a.cmd == "letter" else c.resume(a.id)
            print(res.text)
            if a.cmd == "resume":
                print("\nChanges:\n" + "\n".join(f"  - {x}" for x in res.doc.changes))
                print("Gaps:\n" + "\n".join(f"  - {x}" for x in res.doc.gaps))
            print("\n" + ("✅ Every claim is backed by your CV/portfolio." if res.grounded else
                          "⚠️  Unsupported claims: edit before sending:\n" + "\n".join(f"  - {u}" for u in res.check.unsupported_claims)))
            print(f"Saved: {_save_local(c, a.id, a.cmd, res.text)}   ({res.revisions} revision(s), cost ${res.cost_usd:.3f})")
        elif a.cmd == "show":
            j = t.get(a.id) or sys.exit(f"no job #{a.id}")
            _print_fit(j.id, FitAssessment.model_validate(t.raw(a.id)[0]), j.score, j.recommendation)
            print(f"\nStatus: {j.status}" + (f" (applied {j.applied_on}, follow up {j.follow_up_on})" if j.applied_on else ""))
            if j.notes:
                print("Notes:\n" + j.notes)
        elif a.cmd == "list":
            jobs = t.list(a.status)
            if not jobs:
                print("No jobs tracked yet. Start with: jobs add <url or file>")
            for j in jobs:
                fu = f"  follow up {j.follow_up_on}" if j.follow_up_on else ""
                print(f"#{j.id:<3} {j.score:>3}/100 {j.recommendation:<7} {j.status:<9} {j.title} @ {j.company}{fu}")
        elif a.cmd == "status":
            t.get(a.id) or sys.exit(f"no job #{a.id}")
            t.set_status(a.id, a.status, c.s.follow_up_days)
            j = t.get(a.id)
            print(f"#{j.id} -> {j.status}" + (f"; follow-up reminder on {j.follow_up_on}" if j.follow_up_on else ""))
        elif a.cmd == "due":
            due = t.due()
            print("Nothing due. 🎉" if not due else "\n".join(
                f"#{j.id}  follow up with {j.company} ({j.title}): applied {j.applied_on}" for j in due))
        elif a.cmd == "suggest":
            for s in suggestions(t):
                print(f"[{s.priority}] {s.text}")
            for g, n in skill_gaps(t):
                print(f"    gap: {g} (weight {n})")
        elif a.cmd == "snooze":
            t.snooze(a.id, a.days); print(f"#{a.id} follow-up moved {a.days} day(s) out")
        elif a.cmd == "note":
            t.add_note(a.id, a.text); print("noted")
        elif a.cmd == "profile":
            if c.s.cv_path.exists():
                t.set_profile("cv", c.s.cv_path.read_text()); print(f"CV uploaded from {c.s.cv_path}")
            projects = c.s.cv_path.parent / "projects.md"
            if projects.exists():
                t.set_profile("projects", projects.read_text()); print(f"Projects uploaded from {projects}")
            from .resumes import FAMILIES, profile_key
            for fam in FAMILIES:
                f = c.s.cv_path.parent / "resumes" / f"{fam}.md"
                if f.exists():
                    t.set_profile(profile_key(fam), f.read_text()); print(f"Master resume ({fam}) uploaded from {f}")
            try:
                print(f"Portfolio synced ({c.sync_portfolio()} words) from {c.s.portfolio_data}")
            except (FileNotFoundError, OSError) as e:
                print(f"Portfolio not synced: {e}")
            print("Target:", "Postgres (deployed dashboard)" if c.s.database_url else f"local SQLite {c.s.db_path}")
    except (SourceError, NoProfile, BudgetExceeded, LLMError) as e:
        sys.exit(str(e))
