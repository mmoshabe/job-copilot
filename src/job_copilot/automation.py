"""The daily agent: discover → (docs for strong matches) → score new leads → digest.

Runs from Vercel Cron (or "Run now" on the dashboard). It is bounded three ways:
- per-run budget (AutomationConfig.run_budget_usd), on top of the monthly cap
- time (stops before the serverless time limit; the next run continues the queue)
- a lock, so a cron run and a manual run can't double-spend
Order matters: documents for strong matches come before scoring more leads, so a tight
budget goes to the jobs you're most likely to apply to."""

from __future__ import annotations

import json
import os
import time
from typing import Callable

import httpx

from .discovery import AutomationConfig, discover
from .portfolio import fetch_portfolio, portfolio_markdown
from .llm import LLMError
from .service import Copilot, NoProfile
from .suggestions import suggestions
from .tracker import BudgetExceeded

EST = {"assess": 0.06, "resume": 0.25, "letter": 0.15}  # conservative per-action estimates (USD)


def config(c: Copilot) -> AutomationConfig:
    return AutomationConfig.from_dict(json.loads(c.t.profile("automation") or "{}"))


def refresh_portfolio(c: Copilot, fetch: Callable | None = None) -> str:
    """Keep portfolio evidence in sync with the live portfolio repo -- no manual push needed."""
    if not c.s.portfolio_url:
        return "off"
    try:
        md = portfolio_markdown((fetch or fetch_portfolio)(c.s.portfolio_url))
    except (httpx.HTTPError, ValueError) as e:
        return f"error: {type(e).__name__}"
    if not md.strip() or c.t.profile("portfolio") == md:
        return "unchanged"
    c.t.set_profile("portfolio", md)
    return "updated"


def run(c: Copilot, trigger: str = "cron", *, discover_fn: Callable | None = None, portfolio_fetch: Callable | None = None,
        time_budget_s: float = 240, clock: Callable[[], float] = time.monotonic) -> dict:
    cfg = config(c)
    run_id = c.t.start_run(trigger)
    if run_id is None:
        return {"skipped": "another run started in the last 10 minutes"}
    t0, spent = clock(), 0.0
    report: dict = {"trigger": trigger, "sources": {}, "found": 0, "new_leads": 0, "new_matches": 0,
                    "assessed": [], "docs": [], "errors": [], "stopped": None, "spent_usd": 0.0}

    def can(action: str) -> bool:
        if clock() - t0 > time_budget_s:
            report["stopped"] = report["stopped"] or "time limit (continues next run)"
            return False
        if spent + EST[action] > cfg.run_budget_usd:
            report["stopped"] = report["stopped"] or f"run budget ${cfg.run_budget_usd:.2f} reached (continues next run)"
            return False
        return True

    try:
        # 0. pick up portfolio changes (free)
        report["portfolio"] = refresh_portfolio(c, portfolio_fetch or fetch_portfolio)

        # 1. discover (free)
        leads, report["sources"] = (discover_fn or discover)(cfg)
        report["found"] = len(leads)
        for lead in leads:
            if c.t.add_lead(lead):
                report["new_leads"] += 1
                report["new_matches"] += 1 if lead.keep else 0

        # 2. documents for strong matches you haven't acted on yet
        strong = [j for j in c.t.list("new")
                  if j.recommendation == "apply" and j.score >= cfg.auto_docs_min_score and not (j.has_resume and j.has_letter)]
        for j in strong:
            for kind in ("resume", "letter"):
                if (kind == "resume" and j.has_resume) or (kind == "letter" and j.has_letter):
                    continue
                if not can(kind):
                    break
                try:
                    res = c.resume(j.id) if kind == "resume" else c.letter(j.id)
                    spent += res.cost_usd
                    report["docs"].append({"job_id": j.id, "kind": kind, "role": f"{j.title} @ {j.company}",
                                           "grounded": res.grounded})
                except LLMError as e:
                    report["errors"].append(f"{kind} #{j.id}: {e}")

        # 3. score the best queued leads
        for lead in c.t.queued_leads(cfg.assess_per_run):
            if not can("assess"):
                break
            if len(lead["text"]) < 300:
                c.t.mark_lead(lead["id"], "too_short")
                continue
            try:
                r = c.add_job(text=f"{lead['title']} at {lead['company']}\n\n{lead['text']}", url=lead["url"])
                spent += r.cost_usd
                c.t.mark_lead(lead["id"], "assessed", r.job_id)
                report["assessed"].append({"job_id": r.job_id, "role": f"{r.fit.title} @ {r.fit.company}",
                                           "score": r.score, "rec": r.recommendation})
            except LLMError as e:
                report["errors"].append(f"assess lead {lead['id']}: {e}")
    except BudgetExceeded as e:
        report["stopped"] = str(e)
    except NoProfile as e:
        report["stopped"] = str(e)

    report["spent_usd"] = round(spent, 4)
    report["month_spend_usd"] = round(c.t.month_spend(), 4)
    report["todo"] = [s.text for s in suggestions(c.t)[:5]]
    report["digest"] = digest(report, c.s.monthly_budget_usd)
    c.t.finish_run(run_id, report)
    c.t.log("agent run", f"{report['new_matches']} new matches, {len(report['assessed'])} scored, "
                         f"{len(report['docs'])} docs, ${report['spent_usd']:.2f}")
    notify(report["digest"])
    return report


def digest(r: dict, monthly_budget: float) -> str:
    lines = [f"🧭 Job copilot daily run: {r['new_matches']} new matching jobs (of {r['found']} checked)."]
    if r["assessed"]:
        lines.append("Scored:")
        lines += [f"  • {a['score']}/100 {a['rec']}: {a['role']}" for a in sorted(r["assessed"], key=lambda a: -a["score"])]
    if r["docs"]:
        lines.append("Ready to send (resume/letter written and fact-checked):")
        lines += [f"  • {d['kind']}: {d['role']}" + ("" if d["grounded"] else " ⚠️ check flagged claims") for d in r["docs"]]
    if r.get("todo"):
        lines.append("Your to-do:")
        lines += [f"  • {t}" for t in r["todo"]]
    if r.get("stopped"):
        lines.append(f"Paused: {r['stopped']}")
    lines.append(f"AI spend: ${r['spent_usd']:.2f} today, ${r.get('month_spend_usd', 0):.2f} of ${monthly_budget:.0f} this month.")
    url = os.environ.get("JOBS_PUBLIC_URL")
    if url:
        lines.append(url)
    return "\n".join(lines)


def notify(text: str) -> None:
    """Optional Slack digest (JOBS_SLACK_WEBHOOK_URL). The dashboard always shows it too."""
    hook = os.environ.get("JOBS_SLACK_WEBHOOK_URL")
    if not hook:
        return
    try:
        httpx.post(hook, json={"text": text}, timeout=10)
    except httpx.HTTPError:
        pass
