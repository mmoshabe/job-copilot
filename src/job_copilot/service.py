"""One place for every action, shared by the CLI and the web dashboard.

Every paid (LLM) action checks the monthly budget first and records its cost afterwards."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .agents import DocResult, assess, tailor_resume, write_letter
from .ats import ats_report, ats_targets
from .expert import interview_pack, review_panel
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

    def candidate_contact(self) -> str:
        """The CV header line with the email (location · email · links) for the resume header."""
        cv = self.t.profile("cv") or (self.s.cv_path.read_text() if self.s.cv_path.exists() else "")
        for line in cv.splitlines()[:6]:
            if "@" in line:
                return line.strip()
        return ""

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

    def _doc(self, job_id: int, kind: str, feedback: list[str] | None = None) -> DocResult:
        if not self.t.get(job_id):
            raise KeyError(job_id)
        assessment, text = self.t.raw(job_id)
        fit = FitAssessment.model_validate(assessment)
        ev = self.evidence()
        self._guard()
        if kind == "letter":
            res = write_letter(self.llm, self.s.model, ev, text, fit, self.s.max_letter_revisions, feedback=feedback)
        else:
            res = tailor_resume(self.llm, self.s.model, ev, text, fit, self.candidate_name(), self.s.max_letter_revisions,
                                contact=self.candidate_contact(), ats_targets=ats_targets(text, ev, fit), feedback=feedback)
        self.t.record_usage(kind, self.s.model, res.cost_usd, job_id)
        check = res.check.model_dump()
        if kind == "resume":
            check["changes"], check["gaps"] = res.doc.changes, res.doc.gaps
        self.t.save_doc(job_id, kind, res.text, check)
        if kind == "resume":
            self.t.save_json(job_id, "ats", ats_report(res.text, text, ev, fit, fit.title))
        return res

    def letter(self, job_id: int) -> DocResult:
        return self._doc(job_id, "letter")

    def resume(self, job_id: int) -> DocResult:
        return self._doc(job_id, "resume")

    # -- expert layer --------------------------------------------------------------------------

    def review(self, job_id: int) -> dict:
        """Four-perspective panel review, then one evidence-only revision of the resume (and letter)."""
        j = self.t.get(job_id)
        if not j:
            raise KeyError(job_id)
        if not j.has_resume:
            self.resume(job_id)
        assessment, text = self.t.raw(job_id)
        fit = FitAssessment.model_validate(assessment)
        ev = self.evidence()
        resume, _ = self.t.doc(job_id, "resume")
        letter, _ = self.t.doc(job_id, "letter")
        ats_before = self.t.get_json(job_id, "ats") or ats_report(resume, text, ev, fit, fit.title)
        self._guard()
        r, version = review_panel(self.llm, self.s.model, ev, text, fit, resume, letter, ats_before)
        self.t.record_usage("panel", self.s.model, r.cost_usd, job_id)
        panel = r.parsed
        cost = r.cost_usd
        resume_fixes = [f.fix for f in panel.top_fixes if f.target.lower().startswith("resume")]
        letter_fixes = [f.fix for f in panel.top_fixes if f.target.lower().startswith("letter")]
        if ats_before.get("add"):
            resume_fixes.append("Include these job keywords that the evidence supports, in this exact wording: "
                                + ", ".join(ats_before["add"]))
        revised = []
        if resume_fixes:
            cost += self._doc(job_id, "resume", feedback=resume_fixes).cost_usd
            revised.append("resume")
        if letter_fixes and letter:
            cost += self._doc(job_id, "letter", feedback=letter_fixes).cost_usd
            revised.append("letter")
        ats_after = self.t.get_json(job_id, "ats")
        result = {**panel.model_dump(mode="json"), "instructions_version": version, "revised": revised,
                  "ats_before": ats_before["score"], "ats_after": (ats_after or ats_before)["score"],
                  "cost_usd": round(cost, 4)}
        self.t.save_json(job_id, "panel", result)
        avg = sum(p.score for p in panel.perspectives) / max(len(panel.perspectives), 1)
        self.t.log("panel review", f"avg {avg:.1f}/10; ATS {result['ats_before']}→{result['ats_after']}; revised {', '.join(revised) or 'nothing'}", job_id)
        return result

    def prep(self, job_id: int) -> dict:
        """Interview pack: company brief, knowledge base, talking points, questions by interviewer."""
        if not self.t.get(job_id):
            raise KeyError(job_id)
        assessment, text = self.t.raw(job_id)
        fit = FitAssessment.model_validate(assessment)
        ev = self.evidence()
        resume, _ = self.t.doc(job_id, "resume")
        self._guard()
        r, version = interview_pack(self.llm, self.s.model, ev, text, fit, resume)
        self.t.record_usage("prep", self.s.model, r.cost_usd, job_id)
        result = {**r.parsed.model_dump(mode="json"), "instructions_version": version, "cost_usd": round(r.cost_usd, 4)}
        self.t.save_json(job_id, "prep", result)
        self.t.log("interview prep", f"{len(r.parsed.questions)} questions, {len(r.parsed.knowledge)} topics", job_id)
        return result
