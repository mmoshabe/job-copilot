"""Private web dashboard for the job copilot.

    uv run jobs web                     # http://localhost:8001

Login: JOBS_DASHBOARD_PASSWORD. Sessions: HMAC-signed cookie (JOBS_SESSION_SECRET), 14 days.
Every page except /login and /health requires a session; paid actions also respect the
monthly AI budget (JOBS_MONTHLY_BUDGET_USD)."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from functools import lru_cache
from pathlib import Path

import markdown as md
from fastapi import FastAPI, Form, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .llm import LLMError
from .schemas import FitAssessment
from .service import Copilot, NoProfile
from .sources import SourceError
from .suggestions import skill_gaps, suggestions
from .tracker import STATUSES, BudgetExceeded

app = FastAPI(title="Job Copilot", docs_url=None, redoc_url=None, openapi_url=None)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["md"] = lambda s: md.markdown(s or "", extensions=["sane_lists"])
COOKIE = "jc_session"
SESSION_SECONDS = 14 * 24 * 3600
PUBLIC = {"/login", "/health"}


@lru_cache
def copilot() -> Copilot:
    return Copilot()


# -- auth -------------------------------------------------------------------------------

def _secret() -> bytes:
    s = os.environ.get("JOBS_SESSION_SECRET")
    if not s:
        raise RuntimeError("JOBS_SESSION_SECRET not set")
    return s.encode()


def _sign(expiry: int) -> str:
    return f"{expiry}.{hmac.new(_secret(), str(expiry).encode(), hashlib.sha256).hexdigest()}"


def _valid(token: str | None) -> bool:
    try:
        expiry, sig = (token or "").split(".", 1)
        return int(expiry) > time.time() and hmac.compare_digest(sig, _sign(int(expiry)).split(".", 1)[1])
    except (ValueError, RuntimeError):
        return False


@app.middleware("http")
async def require_login(request: Request, call_next):
    if request.url.path not in PUBLIC and not _valid(request.cookies.get(COOKIE)):
        if request.method == "GET":
            return RedirectResponse("/login", status_code=303)
        return PlainTextResponse("login required", status_code=401)
    response = await call_next(request)
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str = ""):
    return templates.TemplateResponse(request, "login.html", {"error": error})


@app.post("/login")
async def login(password: str = Form(...)):
    expected = os.environ.get("JOBS_DASHBOARD_PASSWORD")
    if not expected or not hmac.compare_digest(password, expected):
        time.sleep(1)  # slow down guessing
        return RedirectResponse("/login?error=1", status_code=303)
    r = RedirectResponse("/", status_code=303)
    r.set_cookie(COOKIE, _sign(int(time.time()) + SESSION_SECONDS), max_age=SESSION_SECONDS,
                 httponly=True, secure=os.environ.get("VERCEL") == "1", samesite="lax")
    return r


@app.post("/logout")
def logout():
    r = RedirectResponse("/login", status_code=303)
    r.delete_cookie(COOKIE)
    return r


# -- pages ------------------------------------------------------------------------------

def _base(request: Request, **ctx) -> dict:
    c = copilot()
    return {"spent": c.t.month_spend(), "budget": c.s.monthly_budget_usd, "path": request.url.path,
            "flash": request.query_params.get("msg", ""), "error": request.query_params.get("err", ""), **ctx}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    t = copilot().t
    jobs = t.list()
    counts = {s: sum(j.status == s for j in jobs) for s in STATUSES}
    return templates.TemplateResponse(request, "home.html", _base(
        request, counts=counts, total=len(jobs), suggestions=suggestions(t)[:8], gaps=skill_gaps(t),
        due=t.due(), events=t.events(limit=12), top=[j for j in jobs if j.status == "new"][:5]))


@app.get("/jobs", response_class=HTMLResponse)
def jobs_page(request: Request, status: str = ""):
    t = copilot().t
    return templates.TemplateResponse(request, "jobs.html", _base(
        request, jobs=t.list(status or None), statuses=STATUSES, status=status))


def _redirect(url: str, msg: str = "", err: str = "") -> RedirectResponse:
    from urllib.parse import urlencode

    q = urlencode({k: v for k, v in (("msg", msg), ("err", err)) if v})
    return RedirectResponse(url + (f"?{q}" if q else ""), status_code=303)


@app.post("/jobs")
async def add_job(url: str = Form(""), text: str = Form("")):
    c = copilot()
    try:
        if text.strip():
            r = await run_in_threadpool(c.add_job, text=text.strip(), url=url.strip() or None)
        elif url.strip():
            r = await run_in_threadpool(c.add_job, url.strip())
        else:
            return _redirect("/", err="Paste a job link or the job text.")
    except (SourceError, NoProfile, BudgetExceeded, LLMError) as e:
        return _redirect("/", err=str(e))
    return _redirect(f"/jobs/{r.job_id}", msg=f"Scored {r.score}/100 → {r.recommendation} (cost ${r.cost_usd:.3f})")


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: int):
    t = copilot().t
    j = t.get(job_id)
    if not j:
        return _redirect("/jobs", err=f"No job #{job_id}")
    assessment, job_text = t.raw(job_id)
    letter, letter_check = t.doc(job_id, "letter")
    resume, resume_check = t.doc(job_id, "resume")
    return templates.TemplateResponse(request, "job.html", _base(
        request, j=j, fit=FitAssessment.model_validate(assessment), job_text=job_text, statuses=STATUSES,
        letter=letter, letter_check=letter_check, resume=resume, resume_check=resume_check,
        events=t.events(job_id)))


@app.post("/jobs/{job_id}/status")
def set_status(job_id: int, status: str = Form(...)):
    c = copilot()
    c.t.set_status(job_id, status, c.s.follow_up_days)
    msg = f"Marked {status}" + (f"; follow-up reminder in {c.s.follow_up_days} days" if status == "applied" else "")
    return _redirect(f"/jobs/{job_id}", msg=msg)


@app.post("/jobs/{job_id}/note")
def note(job_id: int, text: str = Form(...)):
    copilot().t.add_note(job_id, text)
    return _redirect(f"/jobs/{job_id}", msg="Note added")


@app.post("/jobs/{job_id}/snooze")
def snooze(job_id: int, days: int = Form(7)):
    copilot().t.snooze(job_id, days)
    return _redirect(f"/jobs/{job_id}", msg=f"Follow-up moved {days} days out")


@app.post("/jobs/{job_id}/{kind}")
async def generate(job_id: int, kind: str):
    if kind not in ("letter", "resume"):
        return PlainTextResponse("not found", status_code=404)
    c = copilot()
    try:
        res = await run_in_threadpool(c.letter if kind == "letter" else c.resume, job_id)
    except (NoProfile, BudgetExceeded, LLMError) as e:
        return _redirect(f"/jobs/{job_id}", err=str(e))
    note = "fact-check passed" if res.grounded else f"{len(res.check.unsupported_claims)} unsupported claim(s): edit before sending"
    return _redirect(f"/jobs/{job_id}#{kind}", msg=f"{kind.title()} ready: {note} (cost ${res.cost_usd:.3f})")


@app.get("/jobs/{job_id}/{kind}.md")
def download(job_id: int, kind: str):
    if kind not in ("letter", "resume"):
        return PlainTextResponse("not found", status_code=404)
    text, _ = copilot().t.doc(job_id, kind)
    j = copilot().t.get(job_id)
    name = f"{kind}-{j.company}-{j.title}".replace(" ", "-").replace("/", "-")[:80]
    return PlainTextResponse(text or "", headers={"Content-Disposition": f'attachment; filename="{name}.md"'})


@app.get("/jobs/{job_id}/resume/print", response_class=HTMLResponse)
def print_resume(request: Request, job_id: int):
    text, _ = copilot().t.doc(job_id, "resume")
    return templates.TemplateResponse(request, "print.html", {"body": text or "No resume yet."})


@app.get("/history", response_class=HTMLResponse)
def history(request: Request):
    return templates.TemplateResponse(request, "history.html", _base(request, events=copilot().t.events(limit=500)))


@app.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request):
    t = copilot().t
    return templates.TemplateResponse(request, "profile.html", _base(
        request, cv=t.profile("cv") or "", projects=t.profile("projects") or "", portfolio=t.profile("portfolio") or "",
        cv_at=t.profile_updated("cv"), projects_at=t.profile_updated("projects"), portfolio_at=t.profile_updated("portfolio")))


@app.post("/profile")
def save_profile(key: str = Form(...), value: str = Form(...)):
    if key not in ("cv", "projects"):
        return _redirect("/profile", err="Only the CV and project notes are editable here.")
    copilot().t.set_profile(key, value)
    return _redirect("/profile", msg=f"{'CV' if key == 'cv' else 'Project notes'} saved")
