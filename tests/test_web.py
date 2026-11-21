"""Dashboard: auth, every page renders, add-job and document flows. Fake LLM, temp DB."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from job_copilot import web
from job_copilot.config import Settings
from job_copilot.service import Copilot
from job_copilot.tracker import Tracker
from test_service import FakeLLM


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBS_SESSION_SECRET", "s" * 64)
    monkeypatch.setenv("JOBS_DASHBOARD_PASSWORD", "pw")
    s = replace(Settings(), cv_path=tmp_path / "none.md", database_url=None, monthly_budget_usd=5)
    c = Copilot(s, Tracker(tmp_path / "db.sqlite3"), FakeLLM())
    c.t.set_profile("cv", "# Test Person\nSQL Server")
    c.t.set_profile("portfolio", "# Portfolio\n## Experience\n- Built things")
    monkeypatch.setattr(web, "copilot", lambda: c)
    return TestClient(web.app)


def login(client):
    assert client.post("/login", data={"password": "pw"}, follow_redirects=False).status_code == 303


def test_everything_requires_login(client):
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"
    assert client.post("/jobs", data={"text": "x"}).status_code == 401
    assert client.get("/health").json() == {"ok": True}


def test_wrong_password(client, monkeypatch):
    monkeypatch.setattr(web.time, "sleep", lambda s: None)
    r = client.post("/login", data={"password": "nope"}, follow_redirects=False)
    assert r.headers["location"] == "/login?error=1" and "jc_session" not in r.cookies


def test_forged_cookie_rejected(client):
    client.cookies.set("jc_session", "9999999999.deadbeef")
    assert client.get("/", follow_redirects=False).status_code == 303


def test_full_flow(client):
    login(client)
    assert "Add a job" in client.get("/").text
    r = client.post("/jobs", data={"text": "Support Engineer at Acme", "url": ""}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/jobs/1")
    page = client.get("/jobs/1").text
    assert "Support Engineer" in page and "3+ years of Kubernetes" in page

    client.post("/jobs/1/resume", follow_redirects=False)
    page = client.get("/jobs/1").text
    assert "Fact-check passed" in page and "Kubernetes: build a small lab" in page
    assert "Resolved 20+ incidents/week" in client.get("/jobs/1/resume/print").text
    assert client.get("/jobs/1/resume.md").headers["content-disposition"].startswith("attachment")

    client.post("/jobs/1/status", data={"status": "applied"})
    assert "Follow up on" in client.get("/jobs/1").text
    for path in ("/", "/jobs", "/jobs?status=applied", "/history", "/profile"):
        assert client.get(path).status_code == 200, path
    assert "AI spend: $0.06" in client.get("/").text  # assess + resume draft + check


def test_html_is_escaped(client):
    login(client)
    client.post("/profile", data={"key": "projects", "value": "<script>alert(1)</script>"})
    assert "<script>alert(1)</script>" not in client.get("/profile").text


def test_profile_portfolio_not_editable(client):
    login(client)
    r = client.post("/profile", data={"key": "portfolio", "value": "fake"}, follow_redirects=False)
    assert "err=" in r.headers["location"]


def test_cron_requires_secret(client, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "cron-s3cret")
    assert client.get("/cron/run").status_code == 401
    assert client.get("/cron/run", headers={"Authorization": "Bearer wrong"}).status_code == 401
    monkeypatch.setattr(web.automation, "run", lambda c, trigger: {"found": 0, "new_matches": 0, "assessed": [],
                                                                    "docs": [], "stopped": None, "spent_usd": 0.0})
    r = client.get("/cron/run", headers={"Authorization": "Bearer cron-s3cret"})
    assert r.status_code == 200 and r.json()["found"] == 0


def test_agent_pages_render(client, monkeypatch):
    login(client)
    monkeypatch.setattr(web.automation, "discover",
                        lambda cfg, client=None: ([], {"remotive": "0 found"}))
    monkeypatch.setattr(web.automation, "fetch_portfolio", lambda url: {})
    import job_copilot.automation as auto
    r = client.post("/run", follow_redirects=False)
    assert "Run+done" in r.headers["location"] or "Run done" in r.headers["location"]
    for path in ("/", "/leads", "/leads?state=", "/settings"):
        assert client.get(path).status_code == 200, path
    assert "Your job agent" in client.get("/").text


def test_settings_save_and_validate(client):
    login(client)
    form = {k: "" for k in web._LISTS} | {"titles": "support engineer\nai engineer", "assess_per_run": "3",
                                         "run_budget_usd": "0.2", "auto_docs_min_score": "80", "min_prefilter_score": "3"}
    client.post("/settings", data=form)
    cfg = web.automation.config(web.copilot())
    assert cfg.titles == ["support engineer", "ai engineer"] and cfg.assess_per_run == 3
    bad = client.post("/settings", data=form | {"run_budget_usd": "99"}, follow_redirects=False)
    assert "err=" in bad.headers["location"]


def test_expert_sections_and_buttons(client, monkeypatch):
    from test_expert import ExpertLLM
    login(client)
    web.copilot()._llm = ExpertLLM()
    client.post("/jobs", data={"text": "Support Engineer. Python, SQL, REST API, Docker.", "url": ""})
    for kind in ("review", "prep"):
        r = client.post(f"/jobs/1/{kind}", follow_redirects=False)
        assert r.status_code == 303 and "err=" not in r.headers["location"], kind
    page = client.get("/jobs/1").text
    for text in ("ATS check", "Panel review", "Hiring manager", "Business owner", "Interview prep",
                 "Containerise a FastAPI app", "What does success look like in 90 days?"):
        assert text in page, text
    assert client.post("/jobs/1/bogus").status_code == 404


def test_expert_mode_setting(client):
    login(client)
    form = {k: "" for k in web._LISTS} | {"assess_per_run": "5", "run_budget_usd": "0.4", "auto_docs_min_score": "75",
                                         "min_prefilter_score": "3", "expert_mode": "on"}
    r = client.post("/settings", data=form, follow_redirects=False)
    assert web.automation.config(web.copilot()).expert_mode is True
    assert "expert+mode+needs" in r.headers["location"] or "expert%20mode" in r.headers["location"] or "needs+about" in r.headers["location"]


def test_resumes_page(client):
    login(client)
    web.copilot().t.set_profile("resume_ai", "# Test Person\n**AI Implementation Specialist**")
    page = client.get("/resumes").text
    assert "AI roles" in page and "Not uploaded yet" in page  # other three missing
    assert "AI Implementation Specialist" in client.get("/resumes/ai/print").text
    client.post("/resumes/data", data={"value": "# Test Person\n**Data Analyst**"})
    assert "Data Analyst" in client.get("/resumes/data.md").text
    assert client.get("/resumes/bogus/print").status_code == 404
