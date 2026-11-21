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
