"""One place for every action, shared by the CLI and the web dashboard.

Every paid (LLM) action checks the monthly budget first and records its cost afterwards."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .agents import DocResult, assess, tailor_resume, write_letter
from .config import Settings
from .llm import LLM
from .portfolio import evidence, export_portfolio, portfolio_markdown
from .schemas import FitAssessment
from .scoring import fit_score, recommendation
from .sources import fetch_job
from .tracker import Tracker


class NoProfile(Exception):
    pass


@dataclass
class Added:
    job_id: int
    fit: FitAssessment
    score: int
    recommendation: str
    cost_usd: float


class Copilot:
    def __init__(self, settings: Settings | None = None, tracker: Tracker | None = None, llm: LLM | None = None) -> None:
        self.s = settings or Settings()
        self.t = tracker or Tracker(self.s.db_path, database_url=self.s.database_url)
        self._llm = llm

    @property
    def llm(self) -> LLM:
        if self._llm is None:
            self._llm = LLM()
        return self._llm

    # -- profile ---------------------------------------------------------------------

    def evidence(self) -> str:
        cv = self.t.profile("cv")
        if not cv and self.s.cv_path.exists():  # local fallback: data/cv.md
            cv = self.s.cv_path.read_text()
        if not cv:
            raise NoProfile("No CV yet. Add it on the Profile page (or save data/cv.md and run `jobs profile push`).")
        return evidence(cv, self.t.profile("portfolio"), self.t.profile("projects"))

    def candidate_name(self) -> str:
        cv = self.t.profile("cv") or (self.s.cv_path.read_text() if self.s.cv_path.exists() else "")
        m = re.search(r"^#\s+(.+)$", cv, re.M)
        return m.group(1).strip() if m else ""

    def sync_portfolio(self) -> int:
        md = portfolio_markdown(export_portfolio(self.s.portfolio_data))
        self.t.set_profile("portfolio", md)
        return len(md.split())

    # -- paid actions ---------------------------------------------------------------------

    def _guard(self) -> None:
        self.t.check_budget(self.s.monthly_budget_usd)

    def add_job(self, source: str | None = None, *, text: str | None = None, url: str | None = None) -> Added:
        if text is None:
            text, fetched_url = fetch_job(source)
            url = url or fetched_url
        ev = self.evidence()
        self._guard()
        r = assess(self.llm, self.s.model, ev, text)
        self.t.record_usage("assess", self.s.model, r.cost_usd)
        fit = r.parsed
        score, rec = fit_score(fit), recommendation(fit)
        job_id = self.t.add(company=fit.company, title=fit.title, location=fit.location, url=url, score=score,
                            recommendation=rec, assessment=fit.model_dump(mode="json"), job_text=text)
        return Added(job_id, fit, score, rec, r.cost_usd)

    def _doc(self, job_id: int, kind: str) -> DocResult:
        if not self.t.get(job_id):
            raise KeyError(job_id)
        assessment, text = self.t.raw(job_id)
        fit = FitAssessment.model_validate(assessment)
        ev = self.evidence()
        self._guard()
        if kind == "letter":
            res = write_letter(self.llm, self.s.model, ev, text, fit, self.s.max_letter_revisions)
        else:
            res = tailor_resume(self.llm, self.s.model, ev, text, fit, self.candidate_name(), self.s.max_letter_revisions)
        self.t.record_usage(kind, self.s.model, res.cost_usd, job_id)
        check = res.check.model_dump()
        if kind == "resume":
            check["changes"], check["gaps"] = res.doc.changes, res.doc.gaps
        self.t.save_doc(job_id, kind, res.text, check)
        return res

    def letter(self, job_id: int) -> DocResult:
        return self._doc(job_id, "letter")

    def resume(self, job_id: int) -> DocResult:
        return self._doc(job_id, "resume")
