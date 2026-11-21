"""ATS checker, panel review, interview prep and the expert stages of the daily run -- offline."""

from __future__ import annotations

from dataclasses import replace

import pytest

from job_copilot import automation
from job_copilot.ats import ats_report, ats_targets, format_checks, keywords
from job_copilot.config import Settings
from job_copilot.expert import (AskThem, InterviewPack, PanelReview, PerspectiveReview, ProjectProof, Fix, Question,
                                Topic, TalkingPoint, MiniProject, instructions)
from job_copilot.llm import LLMResult
from job_copilot.service import Copilot
from job_copilot.tracker import Tracker
from test_service import FIT, FakeLLM

JOB = "Support Engineer. Must have Python, SQL and REST API experience. Docker and Kubernetes a plus. Zendesk."
EVIDENCE = "<cv>Python, SQL Server, Zendesk admin, built REST APIs</cv>"
RESUME = """# Test Person
Johannesburg · test@example.com
**Support Engineer**

## Skills
Python · SQL

## Experience
### Dev, Acme (2022 – 2024)
- Resolved 20+ incidents per week
- Wrote runbooks

## Education
- Diploma (2020)
"""


# -- ATS --------------------------------------------------------------------------------------

def test_keywords_and_honest_split():
    terms = [k["term"] for k in keywords(JOB, FIT)]
    assert {"python", "sql", "rest api", "docker", "kubernetes", "zendesk"} <= set(terms)
    r = ats_report(RESUME, JOB, EVIDENCE, FIT, "Support Engineer")
    assert "zendesk" in r["add"] and "rest api" in r["add"]          # supported -> may be added
    assert "docker" in r["gaps"] and "kubernetes" in r["gaps"]       # unsupported -> never stuffed
    assert r["honest_ceiling"] > r["keyword_coverage"]
    assert "docker" not in ats_targets(JOB, EVIDENCE, FIT)


def test_aliases_and_word_boundaries():
    r = ats_report("Worked with Postgres and k8s daily. test@x.io", "PostgreSQL and Kubernetes required", "", None)
    covered = {k["term"] for k in r["keywords"] if k["in_resume"]}
    assert {"postgresql", "kubernetes"} <= covered
    assert not any(k["term"] == "go" and k["in_resume"] for k in ats_report("I love Google", "Go developer", "", None)["keywords"])


def test_format_checks():
    ok = {c["check"]: c["ok"] for c in format_checks(RESUME, "Support Engineer")}
    assert ok["Contact email in the header"] and ok["Standard section headings"] and ok["Every role has dates"]
    bad = {c["check"]: c["ok"] for c in format_checks("# X\n| a | b |\n|---|---|\n### Role, Co", "Data Scientist")}
    assert not bad["No tables or images"] and not bad["Every role has dates"] and not bad["Contact email in the header"]


# -- expert agents -------------------------------------------------------------------------------

PANEL = PanelReview(
    perspectives=[PerspectiveReview(perspective=p, score=7, verdict="advance", strengths=["s"], concerns=["c"], fixes=["f"])
                  for p in ("recruiter", "hiring_manager", "business_owner", "applicant")],
    supporting_projects=[ProjectProof(requirement="SQL", proof="Körber", evidence="30% faster", talking_point="tp")],
    top_fixes=[Fix(target="resume", fix="Move the 30% SQL result to the first bullet"),
               Fix(target="letter", fix="Name the company in sentence one")],
    red_flags=[], summary="Ready after fixes.")
PACK = InterviewPack(
    company_brief=["Sells software", "Inference: growing support team"], verify_before=["Check product"],
    knowledge=[Topic(topic="Zendesk", why="their helpdesk", study_notes="macros, triggers", your_experience="admin")],
    talking_points=[TalkingPoint(requirement="SQL", proof="Körber", star="S/T/A/R")],
    mini_projects=[MiniProject(gap="Docker", project="Containerise a FastAPI app", shows="Docker basics")],
    questions=[Question(interviewer="recruiter", question="Why us?", why="motivation", answer_outline="...", evidence="cv")],
    ask_them=[AskThem(interviewer="hiring_manager", question="What does success look like in 90 days?")], red_flags=[])


class ExpertLLM(FakeLLM):
    def structured(self, *, model, system, user, schema, **kw):
        if schema.__name__ in ("PanelReview", "InterviewPack"):
            self.calls.append(schema.__name__)
            self.last_system = system
            return LLMResult(parsed=PANEL if schema is PanelReview else PACK, model=model, latency_ms=1,
                             cost_usd=0.05, request_id=None)
        return super().structured(model=model, system=system, user=user, schema=schema, **kw)


@pytest.fixture
def c(tmp_path):
    s = replace(Settings(), cv_path=tmp_path / "none.md", database_url=None, monthly_budget_usd=5)
    cp = Copilot(s, Tracker(tmp_path / "db.sqlite3"), ExpertLLM())
    cp.t.set_profile("cv", "# Test Person\nJohannesburg · test@example.com\nSQL Server, Python")
    return cp


def test_instructions_are_versioned_and_grounded():
    text, v = instructions("panel")
    assert "Non-negotiable rules" in text and "Business owner" in text and len(v) == 8


def test_resume_gets_contact_and_ats_report(c):
    j = c.add_job(text=JOB).job_id
    c.resume(j)
    text, _ = c.t.doc(j, "resume")
    assert "test@example.com" in text.splitlines()[1]
    assert c.t.get_json(j, "ats")["score"] > 0


def test_review_runs_panel_then_revises_resume_and_letter(c):
    j = c.add_job(text=JOB).job_id
    c.letter(j)
    calls_before = list(c.llm.calls)
    r = c.review(j)
    new = c.llm.calls[len(calls_before):]
    assert new[0] == "TailoredResume" or new[0] == "PanelReview"  # resume drafted first if missing
    assert "PanelReview" in new and new.count("CoverLetter") == 1
    assert r["revised"] == ["resume", "letter"] and len(r["perspectives"]) == 4
    assert c.t.get_json(j, "panel")["instructions_version"] == instructions("panel")[1]
    assert c.t.events(j)[0]["kind"] == "panel review"


def test_prep_saved_on_job_row(c):
    j = c.add_job(text=JOB).job_id
    p = c.prep(j)
    assert c.t.get_json(j, "prep")["mini_projects"][0]["gap"] == "Docker" and p["cost_usd"] == 0.05


def test_expert_stages_off_by_default_and_on_when_switched(c, monkeypatch):
    monkeypatch.setattr(automation, "fetch_portfolio", lambda url: {})
    nothing = lambda cfg: ([], {})  # noqa: E731
    j = c.add_job(text=JOB).job_id
    c.t.set_status(j, "interview", follow_up_days=7)
    r = automation.run(c, discover_fn=nothing)
    assert r["preps"] == [] and c.t.get_json(j, "prep") is None
    c.t.set_profile("automation", '{"expert_mode": true, "run_budget_usd": 1.0}')
    c.t.finish_run(c.t.runs()[0]["id"], {})
    r = automation.run(c, "manual", discover_fn=nothing)
    assert r["preps"] and c.t.get_json(j, "prep") and "Interview prep ready" in r["digest"]


def test_make_the_tool_vs_make_the_verb():
    job = "No-code automation (Zapier, Make, n8n or similar). We make clients happy."
    assert "make" in [k["term"] for k in keywords(job)]
    assert ats_report("Built workflows in GoHighLevel, HubSpot and Make.", job, "", None)["keywords"]
    hits = {k["term"]: k["in_resume"] for k in ats_report("Built workflows in HubSpot and Make.", job, "", None)["keywords"]}
    assert hits["make"] is True
    assert {k["term"]: k["in_resume"] for k in ats_report("I make things work.", job, "", None)["keywords"]}["make"] is False


def test_business_role_keywords():
    terms = [k["term"] for k in keywords(open("/tmp/jc/job.txt").read())] if __import__("os").path.exists("/tmp/jc/job.txt") else []
    job = ("Maps and documents workflows, configures AI agents and no-code automations, trains non-technical staff. "
           "3+ years in business analysis, operations or process improvement.")
    terms = [k["term"] for k in keywords(job)]
    assert {"workflows", "ai agents", "no-code", "training", "non-technical", "business analysis", "operations",
            "process improvement"} <= set(terms)
