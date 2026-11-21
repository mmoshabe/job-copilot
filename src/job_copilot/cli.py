"""jobs: command-line job application copilot.

    jobs add <url | file | ->  [--url LINK]   fetch a job post, score fit against your CV, track it
    jobs list [--status applied]               tracked jobs, best fit first
    jobs show <id>                             requirement-by-requirement breakdown
    jobs letter <id>                           write a fact-checked cover letter
    jobs status <id> applied                   update status (applied -> follow-up reminder)
    jobs due                                   follow-ups due today or overdue
    jobs snooze <id> <days>                    push a follow-up back
    jobs note <id> "text"                      add a note
"""

from __future__ import annotations

import argparse
import re
import sys

from .agents import assess, write_letter
from .config import Settings
from .llm import LLM, LLMError
from .schemas import FitAssessment, Met
from .scoring import fit_score, recommendation
from .sources import SourceError, fetch_job
from .tracker import STATUSES, Tracker

ICON = {Met.yes: "✅", Met.partial: "🟡", Met.no: "❌"}


def _cv(s: Settings) -> str:
    if not s.cv_path.exists():
        sys.exit(f"No CV found at {s.cv_path}. Save your CV as text/markdown there (data/ is private and gitignored), "
                 "or set JOBS_CV=/path/to/cv.md.")
    return s.cv_path.read_text()


def _print_fit(job_id: int, fit: FitAssessment, score: int, rec: str) -> None:
    print(f"\n#{job_id}  {fit.title} @ {fit.company}  ({fit.location or 'location not stated'})")
    print(f"Fit {score}/100 -> {rec.upper()}")
    print(f"{fit.summary}\n")
    for r in fit.requirements:
        ev = f"  ← {r.evidence[:90]}" if r.evidence else ""
        print(f"  {ICON[r.met]} [{r.kind.value}] {r.text}{ev}")
    for d in fit.dealbreakers:
        print(f"  ⛔ dealbreaker: {d}")


def cmd_add(a, s: Settings, t: Tracker) -> None:
    try:
        text, url = fetch_job(a.source)
    except SourceError as e:
        sys.exit(str(e))
    url = a.url or url
    try:
        r = assess(LLM(), s.model, _cv(s), text)
    except LLMError as e:
        sys.exit(f"assessment failed: {e}")
    fit = r.parsed
    score, rec = fit_score(fit), recommendation(fit)
    job_id = t.add(company=fit.company, title=fit.title, location=fit.location, url=url, score=score,
                   recommendation=rec, assessment=fit.model_dump(mode="json"), job_text=text)
    _print_fit(job_id, fit, score, rec)
    print(f"\n(cost ${r.cost_usd:.3f})  Next: jobs letter {job_id}   or   jobs status {job_id} skipped")


def cmd_show(a, s, t: Tracker) -> None:
    j = t.get(a.id) or sys.exit(f"no job #{a.id}")
    fit = FitAssessment.model_validate(t.raw(a.id)[0])
    _print_fit(j.id, fit, j.score, j.recommendation)
    print(f"\nStatus: {j.status}" + (f" (applied {j.applied_on}, follow up {j.follow_up_on})" if j.applied_on else ""))
    if j.url:
        print(f"Link: {j.url}")
    if j.letter_path:
        print(f"Letter: {j.letter_path}")
    if j.notes:
        print("Notes:\n" + j.notes)


def cmd_letter(a, s: Settings, t: Tracker) -> None:
    j = t.get(a.id) or sys.exit(f"no job #{a.id}")
    assessment, text = t.raw(a.id)
    fit = FitAssessment.model_validate(assessment)
    try:
        res = write_letter(LLM(), s.model, _cv(s), text, fit, s.max_letter_revisions)
    except LLMError as e:
        sys.exit(f"letter failed: {e}")
    s.letters_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", f"{j.company}-{j.title}".lower()).strip("-")[:60]
    path = s.letters_dir / f"{j.id}-{slug}.md"
    warn = ""
    if not res.grounded:
        warn = "> ⚠️ FACT-CHECK FAILED: fix these before sending:\n" + "".join(f"> - {u}\n" for u in res.check.unsupported_claims) + "\n"
    path.write_text(warn + res.letter.body + "\n")
    t.set_letter(j.id, str(path))
    print(res.letter.body)
    print("\n" + ("✅ Every claim is backed by your CV." if res.grounded
                  else "⚠️  Unsupported claims: edit before sending:\n" + "\n".join(f"  - {u}" for u in res.check.unsupported_claims)))
    print(f"Saved: {path}   ({res.revisions} revision(s), cost ${res.cost_usd:.3f})")


def cmd_list(a, s, t: Tracker) -> None:
    jobs = t.list(a.status)
    if not jobs:
        print("No jobs tracked yet. Start with: jobs add <url or file>")
    for j in jobs:
        fu = f"  follow up {j.follow_up_on}" if j.follow_up_on else ""
        print(f"#{j.id:<3} {j.score:>3}/100 {j.recommendation:<7} {j.status:<9} {j.title} @ {j.company}{fu}")


def cmd_status(a, s: Settings, t: Tracker) -> None:
    t.get(a.id) or sys.exit(f"no job #{a.id}")
    try:
        t.set_status(a.id, a.status, s.follow_up_days)
    except ValueError as e:
        sys.exit(str(e))
    j = t.get(a.id)
    print(f"#{j.id} -> {j.status}" + (f"; follow-up reminder on {j.follow_up_on}" if j.follow_up_on else ""))


def cmd_due(a, s, t: Tracker) -> None:
    due = t.due()
    if not due:
        print("Nothing due. 🎉")
    for j in due:
        print(f"#{j.id}  follow up with {j.company} ({j.title}): applied {j.applied_on}, due {j.follow_up_on}"
              f"\n     then: jobs status {j.id} interview|rejected   or   jobs snooze {j.id} 7")


def main() -> None:
    p = argparse.ArgumentParser(prog="jobs", description="Job application copilot")
    sub = p.add_subparsers(dest="cmd", required=True)
    x = sub.add_parser("add"); x.add_argument("source"); x.add_argument("--url")
    x = sub.add_parser("show"); x.add_argument("id", type=int)
    x = sub.add_parser("letter"); x.add_argument("id", type=int)
    x = sub.add_parser("list"); x.add_argument("--status", choices=STATUSES)
    x = sub.add_parser("status"); x.add_argument("id", type=int); x.add_argument("status", choices=STATUSES)
    sub.add_parser("due")
    x = sub.add_parser("snooze"); x.add_argument("id", type=int); x.add_argument("days", type=int)
    x = sub.add_parser("note"); x.add_argument("id", type=int); x.add_argument("text")
    a = p.parse_args()

    s = Settings()
    t = Tracker(s.db_path)
    if a.cmd == "snooze":
        t.snooze(a.id, a.days); print(f"#{a.id} follow-up moved {a.days} day(s) out")
    elif a.cmd == "note":
        t.add_note(a.id, a.text); print("noted")
    else:
        {"add": cmd_add, "show": cmd_show, "letter": cmd_letter, "list": cmd_list,
         "status": cmd_status, "due": cmd_due}[a.cmd](a, s, t)
