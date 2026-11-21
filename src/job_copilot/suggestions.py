"""Suggestions computed from the tracker -- free, no LLM calls."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date

from .tracker import CLOSED, Tracker

_YEARS = re.compile(r"\b\d+\s*\+?\s*(?:-\s*\d+\s*)?years?( of)?\b", re.I)
_FILLER = re.compile(r"\b(experience (with|in)|knowledge of|proficiency in|strong|solid|hands-on|working|familiarity with|professional)\b", re.I)


@dataclass
class Suggestion:
    kind: str       # follow_up | apply | stale | gap | letter | resume | start
    priority: int   # 1 = do today
    text: str
    job_id: int | None = None


def _norm(req: str) -> str:
    s = _FILLER.sub("", _YEARS.sub("", req)).strip(" ,.-:;")
    return re.sub(r"\s+", " ", s).capitalize()


def skill_gaps(t: Tracker, top: int = 6) -> list[tuple[str, int]]:
    """Requirements most often missing across jobs worth applying to (not skipped/closed)."""
    c: Counter[str] = Counter()
    for job, a in t.all_assessments():
        if job.status in CLOSED or job.recommendation == "skip":
            continue
        for r in a.get("requirements", []):
            if r.get("met") == "no":
                c[_norm(r["text"])] += 2 if r.get("kind") == "must" else 1
    return [(k, v) for k, v in c.most_common(top) if k]


def suggestions(t: Tracker, today: date | None = None, stale_days: int = 21) -> list[Suggestion]:
    today = today or date.today()
    out: list[Suggestion] = []
    jobs = t.list()
    if not jobs:
        return [Suggestion("start", 1, "Add your first job post: paste a Greenhouse/Lever link or the job text.")]
    for j in t.due(today):
        out.append(Suggestion("follow_up", 1, f"Follow up with {j.company} about {j.title} (applied {j.applied_on}).", j.id))
    for j in jobs:
        if j.status == "new" and j.recommendation == "apply":
            if not j.has_resume:
                out.append(Suggestion("resume", 2, f"Strong fit ({j.score}/100): tailor your resume for {j.title} at {j.company}.", j.id))
            elif not j.has_letter:
                out.append(Suggestion("letter", 2, f"Write the cover letter for {j.title} at {j.company}, then apply.", j.id))
            else:
                out.append(Suggestion("apply", 1, f"Resume and letter ready: apply to {j.title} at {j.company} and mark it applied.", j.id))
        elif j.status == "new" and j.recommendation == "stretch":
            out.append(Suggestion("apply", 3, f"Stretch role ({j.score}/100): {j.title} at {j.company}. Apply if the gaps are learnable fast.", j.id))
        if j.status == "applied" and j.applied_on and (today - date.fromisoformat(j.applied_on)).days >= stale_days:
            out.append(Suggestion("stale", 3, f"No response from {j.company} in {stale_days}+ days. Send one last follow-up, then mark it rejected.", j.id))
    gaps = skill_gaps(t)
    if gaps:
        names = ", ".join(g for g, _ in gaps[:3])
        out.append(Suggestion("gap", 3, f"Most common gaps in jobs you're targeting: {names}. A small project in one of these would lift several scores."))
    return sorted(out, key=lambda s: s.priority)
