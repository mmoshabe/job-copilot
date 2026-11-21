"""Service, suggestions, resume rendering and budget guard -- offline, fake LLM."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from job_copilot.agents import render_resume
from job_copilot.config import Settings
from job_copilot.llm import LLMResult
from job_copilot.portfolio import portfolio_markdown
from job_copilot.schemas import (CoverLetter, FitAssessment, LetterCheck, Requirement, ResumeProject, ResumeRole,
                                 TailoredResume)
from job_copilot.service import Copilot, NoProfile
from job_copilot.suggestions import skill_gaps, suggestions
from job_copilot.tracker import BudgetExceeded, Tracker

FIT = FitAssessment(company="Acme", title="Support Engineer", location="Remote", summary="good",
                    dealbreakers=[], requirements=[
                        Requirement(text="3+ years of Kubernetes", kind="must", met="no", evidence=""),
                        Requirement(text="SQL", kind="must", met="yes", evidence="SQL Server"),
                        Requirement(text="ServiceNow", kind="nice", met="yes", evidence="ServiceNow")])
RESUME = TailoredResume(headline="Support Engineer", summary="Sum.", skills=["SQL"], education=["Diploma"],
                        certifications=[], changes=["Led with incident work"], gaps=["Kubernetes: build a small lab"],
                        experience=[ResumeRole(role="Dev", org="Körber", dates="2022", bullets=["Resolved 20+ incidents/week"])],
                        projects=[ResumeProject(name="ai-support-triage", line="Triage with evals")])
OK = LetterCheck(unsupported_claims=[], issues=[], score=0.9)


class FakeLLM:
    def __init__(self):
        self.calls = []

    def structured(self, *, model, system, user, schema, **_):
        self.calls.append(schema.__name__)
        out = {"FitAssessment": FIT, "CoverLetter": CoverLetter(body="Dear [Hiring Manager]", claims=[]),
               "TailoredResume": RESUME, "LetterCheck": OK}[schema.__name__]
        return LLMResult(parsed=out, model=model, latency_ms=1, cost_usd=0.02, request_id=None)


@pytest.fixture
def copilot(tmp_path):
    s = replace(Settings(), cv_path=tmp_path / "none.md", database_url=None, monthly_budget_usd=1.0)
    c = Copilot(s, Tracker(tmp_path / "db.sqlite3"), FakeLLM())
    c.t.set_profile("cv", "# Test Person\nSQL Server, ServiceNow")
    return c


def test_add_job_records_cost_and_history(copilot):
    r = copilot.add_job(text="Support Engineer at Acme ...")
    assert r.score == 60 and copilot.t.month_spend() == pytest.approx(0.02)
    assert copilot.t.events(r.job_id)[0]["kind"] == "added"


def test_resume_saved_with_changes_and_gaps(copilot):
    j = copilot.add_job(text="post").job_id
    res = copilot.resume(j)
    text, check = copilot.t.doc(j, "resume")
    assert res.grounded and "# Test Person" in text and "Resolved 20+ incidents/week" in text
    assert check["gaps"] == ["Kubernetes: build a small lab"]
    assert copilot.t.get(j).has_resume


def test_budget_blocks_paid_actions(copilot):
    copilot.t.record_usage("assess", "m", 1.5)
    with pytest.raises(BudgetExceeded):
        copilot.add_job(text="post")
    assert copilot.llm.calls == []  # nothing was spent


def test_no_cv_is_a_clear_error(tmp_path):
    s = replace(Settings(), cv_path=tmp_path / "none.md", database_url=None)
    with pytest.raises(NoProfile):
        Copilot(s, Tracker(tmp_path / "db.sqlite3"), FakeLLM()).add_job(text="post")


def test_suggestions_and_gaps(copilot):
    t = copilot.t
    a = t.add(company="A", title="Eng", location="", url="u1", score=90, recommendation="apply",
              assessment=FIT.model_dump(mode="json"), job_text="")
    b = t.add(company="B", title="Ops", location="", url="u2", score=80, recommendation="apply",
              assessment=FIT.model_dump(mode="json"), job_text="")
    t.set_status(b, "applied", follow_up_days=7, today=date(2026, 11, 1))
    s = suggestions(t, today=date(2026, 11, 10))
    kinds = [(x.kind, x.job_id) for x in s]
    assert ("follow_up", b) in kinds and ("resume", a) in kinds
    assert s[0].priority == 1
    assert skill_gaps(t)[0][0] == "Kubernetes"  # "3+ years of Kubernetes" normalised


def test_render_resume_sections():
    md = render_resume(RESUME, "Test Person")
    assert md.startswith("# Test Person") and "## Selected projects" in md and "## Certifications" not in md


def test_portfolio_markdown():
    md = portfolio_markdown({"site": {"name": "N", "role": "R", "location": "JHB", "status": "open"},
                             "experience": [{"role": "Founder", "org": "X", "dates": "2026", "points": ["Built it"]}],
                             "work": [{"name": "P", "tag": "SaaS", "desc": "Does things", "href": None}]})
    assert "### Founder, X (2026)" in md and "- **P**: SaaS. Does things" in md
