"""The daily agent and discovery filter -- offline: fake sources, fake LLM, fake clock."""

from __future__ import annotations

from dataclasses import replace

import httpx
import pytest

from job_copilot import automation
from job_copilot.config import Settings
from job_copilot.discovery import AutomationConfig, Lead, discover, prefilter
from job_copilot.service import Copilot
from job_copilot.tracker import Tracker
from test_service import FakeLLM

LONG = "We need API, webhook, SQL and Python skills. Zendesk and ServiceNow a plus. " * 10


def lead(title="Technical Support Engineer", loc="South Africa", url="https://x/1", text=LONG):
    return Lead("test", title, "Acme", loc, url, text)


# -- prefilter ----------------------------------------------------------------------------

def test_prefilter_keeps_target_roles_in_reachable_locations():
    cfg = AutomationConfig()
    assert prefilter(lead(), cfg).keep
    assert prefilter(lead(loc="Worldwide"), cfg).keep
    assert prefilter(lead(loc="Austria, Germany, South Africa, United Kingdom"), cfg).keep


@pytest.mark.parametrize("title,loc,reason", [
    ("Technical Support Engineer", "United States", "location"),
    ("Technical Support Engineer", "Remote, US only", "location"),
    ("Sales Development Rep", "Worldwide", "title"),
    ("Director of Support Engineering", "Worldwide", "title excluded"),
])
def test_prefilter_rejects_with_reason(title, loc, reason):
    l = prefilter(lead(title=title, loc=loc), AutomationConfig())
    assert not l.keep and l.reason.startswith(reason)


def test_discover_survives_a_failing_source():
    def handler(req: httpx.Request):
        if "remotive" in req.url.host:
            return httpx.Response(500)
        if "remoteok" in req.url.host:
            return httpx.Response(200, json=[{"legal": "notice"}, {"position": "Support Engineer", "company": "R",
                                                                 "location": "", "url": "https://r/1", "description": LONG}])
        return httpx.Response(200, json={"jobs": []})
    leads, status = discover(AutomationConfig(), httpx.Client(transport=httpx.MockTransport(handler)))
    assert status["remotive"].startswith("error") and status["remoteok"] == "1 found"
    assert len(leads) == 1 and leads[0].keep and leads[0].location == "Worldwide"


# -- the run --------------------------------------------------------------------------------

@pytest.fixture
def c(tmp_path):
    s = replace(Settings(), cv_path=tmp_path / "none.md", database_url=None, monthly_budget_usd=5)
    cp = Copilot(s, Tracker(tmp_path / "db.sqlite3"), FakeLLM())
    cp.t.set_profile("cv", "# Test Person\nSQL")
    return cp


@pytest.fixture(autouse=True)
def no_network_portfolio(monkeypatch):
    monkeypatch.setattr(automation, "fetch_portfolio", lambda url: {})


def fake_discover(leads):
    return lambda cfg: ([prefilter(l, cfg) for l in leads], {"test": f"{len(leads)} found"})


def test_run_discovers_scores_and_reports(c):
    r = automation.run(c, discover_fn=fake_discover([lead(url=f"https://x/{i}") for i in range(3)]
                                                    + [lead(loc="United States", url="https://x/us")]))
    assert r["new_leads"] == 4 and r["new_matches"] == 3
    assert len(r["assessed"]) == 3 and c.t.lead_counts() == {"assessed": 3, "filtered": 1}
    assert "3 new matching jobs" in r["digest"] and c.t.runs()[0]["report"]["found"] == 4


def test_second_run_skips_known_jobs(c):
    d = fake_discover([lead()])
    automation.run(c, discover_fn=d)
    c.t.finish_run(c.t.runs()[0]["id"], {})  # (already finished; just to be explicit)
    r = automation.run(c, "manual", discover_fn=d)
    assert r.get("skipped") or (r["new_leads"] == 0 and r["assessed"] == [])


def test_run_budget_limits_spend(c):
    c.t.set_profile("automation", '{"run_budget_usd": 0.09}')  # fake actual $0.02 each + $0.06 estimate: room for 2
    r = automation.run(c, discover_fn=fake_discover([lead(url=f"https://x/{i}") for i in range(5)]))
    assert len(r["assessed"]) == 2 and "run budget" in r["stopped"]
    assert c.t.lead_counts()["queued"] == 3  # picked up next run


def test_docs_for_strong_matches_come_first(c):
    j = c.t.add(company="A", title="Eng", location="", url="u", score=90, recommendation="apply",
                assessment=c.llm.structured(model="m", system="", user="", schema=__import__("job_copilot.schemas", fromlist=["FitAssessment"]).FitAssessment).parsed.model_dump(mode="json"),
                job_text="post")
    c.t.set_profile("automation", '{"run_budget_usd": 0.45}')
    r = automation.run(c, discover_fn=fake_discover([lead()]))
    assert [d["kind"] for d in r["docs"]] == ["resume", "letter"]
    assert c.t.get(j).has_resume and c.t.get(j).has_letter


def test_time_limit_stops_cleanly(c):
    ticks = iter(range(0, 10_000, 100))
    r = automation.run(c, discover_fn=fake_discover([lead(url=f"https://x/{i}") for i in range(5)]),
                       time_budget_s=150, clock=lambda: next(ticks))
    assert "time limit" in r["stopped"] and len(r["assessed"]) < 5


def test_concurrent_run_is_refused(c):
    c.t.start_run("cron")  # unfinished run just started
    assert "skipped" in automation.run(c, "manual", discover_fn=fake_discover([lead()]))


def test_portfolio_refresh_updates_only_on_change(c):
    from job_copilot.portfolio import parse_data_ts
    src = 'export const site = { name: "N", role: "R", location: "JHB", status: "open" };\n' \
          'export const experience = [{ role: "Founder", org: "X", dates: "2026", points: ["Built it, // not a comment"] }];\n' \
          'export const nav = [{ label: "skip me" }];'
    data = parse_data_ts(src)
    assert set(data) == {"site", "experience"} and data["experience"][0]["points"] == ["Built it, // not a comment"]
    assert automation.refresh_portfolio(c, lambda url: data) == "updated"
    assert automation.refresh_portfolio(c, lambda url: data) == "unchanged"
    assert "### Founder, X (2026)" in c.t.profile("portfolio")
