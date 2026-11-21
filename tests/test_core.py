"""Offline tests: scoring, tracker, sources and the letter fact-check loop. No API calls."""

from __future__ import annotations

from datetime import date

import pytest

from job_copilot.agents import write_letter
from job_copilot.llm import LLMResult
from job_copilot.schemas import Claim, CoverLetter, FitAssessment, LetterCheck, Requirement
from job_copilot.scoring import fit_score, recommendation
from job_copilot.sources import SourceError, fetch_job, html_to_text
from job_copilot.tracker import Tracker


def req(kind, met, text="x"):
    return Requirement(text=text, kind=kind, met=met, evidence="")


def fit(reqs, dealbreakers=()):
    return FitAssessment(company="C", title="T", location="", requirements=reqs,
                         dealbreakers=list(dealbreakers), summary="")


# ---- scoring ----------------------------------------------------------------

def test_score_weights_musts_double():
    f = fit([req("must", "yes"), req("nice", "no")])  # 2 of 3 weighted points
    assert fit_score(f) == 67


def test_recommendation_rules():
    assert recommendation(fit([req("must", "yes")] * 3 + [req("nice", "partial")])) == "apply"
    assert recommendation(fit([req("must", "yes")] * 3 + [req("must", "no")])) == "stretch"
    assert recommendation(fit([req("must", "no")] * 3 + [req("must", "yes")])) == "skip"
    assert recommendation(fit([req("must", "yes")] * 4, dealbreakers=["US work authorisation"])) == "skip"


def test_no_requirements_scores_zero():
    assert fit_score(fit([])) == 0


# ---- tracker ------------------------------------------------------------------

@pytest.fixture
def tracker(tmp_path):
    return Tracker(tmp_path / "t.sqlite3")


def add(t, url="https://x/1", score=80):
    return t.add(company="Acme", title="Dev", location="JHB", url=url, score=score,
                 recommendation="apply", assessment={}, job_text="post")


def test_tracker_dedupes_by_url(tracker):
    assert add(tracker) == add(tracker)
    assert len(tracker.list()) == 1


def test_applied_sets_follow_up_and_due(tracker):
    j = add(tracker)
    tracker.set_status(j, "applied", follow_up_days=7, today=date(2026, 11, 1))
    assert tracker.get(j).follow_up_on == "2026-11-08"
    assert tracker.due(today=date(2026, 11, 7)) == []
    assert [x.id for x in tracker.due(today=date(2026, 11, 8))] == [j]
    tracker.set_status(j, "rejected", follow_up_days=7)
    assert tracker.due(today=date(2026, 12, 1)) == []  # closed jobs stop nagging


def test_invalid_status_rejected(tracker):
    with pytest.raises(ValueError):
        tracker.set_status(add(tracker), "ghosted", 7)


def test_list_sorted_by_score(tracker):
    add(tracker, "https://x/1", 40)
    add(tracker, "https://x/2", 90)
    assert [j.score for j in tracker.list()] == [90, 40]


# ---- sources ----------------------------------------------------------------

def test_html_to_text_strips_scripts_and_tags():
    assert html_to_text("<script>evil()</script><h1>Role</h1><p>Python &amp; SQL</p>") == "Role\nPython & SQL"


def test_blocked_sites_refused():
    with pytest.raises(SourceError, match="paste|Copy"):
        fetch_job("https://www.linkedin.com/jobs/view/123")


def test_file_source(tmp_path):
    f = tmp_path / "job.txt"
    f.write_text("Junior dev")
    assert fetch_job(str(f)) == ("Junior dev", None)


# ---- letter loop ------------------------------------------------------------

class FakeLLM:
    def __init__(self, letters, checks):
        self.q = {"CoverLetter": list(letters), "LetterCheck": list(checks)}

    def structured(self, *, model, system, user, schema, **_):
        return LLMResult(parsed=self.q[schema.__name__].pop(0), model=model, latency_ms=1, cost_usd=0.01, request_id=None)


LETTER = CoverLetter(body="Dear [Hiring Manager] ...", claims=[Claim(claim="2 years Python", cv_evidence="2 years")])


def test_letter_revises_when_fact_check_fails():
    llm = FakeLLM([LETTER, LETTER], [LetterCheck(unsupported_claims=["led a team"], issues=[], score=0.4),
                                     LetterCheck(unsupported_claims=[], issues=[], score=0.9)])
    r = write_letter(llm, "m", "cv", "job", fit([]), max_revisions=1)
    assert r.grounded and r.revisions == 1 and r.cost_usd == pytest.approx(0.04)


def test_letter_flags_when_still_ungrounded():
    bad = LetterCheck(unsupported_claims=["8 years Kubernetes"], issues=[], score=0.3)
    r = write_letter(FakeLLM([LETTER, LETTER], [bad, bad]), "m", "cv", "job", fit([]), max_revisions=1)
    assert not r.grounded and r.check.unsupported_claims == ["8 years Kubernetes"]
