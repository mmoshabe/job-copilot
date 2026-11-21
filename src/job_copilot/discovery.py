"""Find job posts automatically, then pre-filter them for free (no LLM).

Sources are official, free APIs meant for this:
- Remotive public API (remote jobs; asks for few calls a day, so we fetch whole categories once)
- Himalayas search API, worldwide + South Africa scopes (full descriptions)
- RemoteOK public API (latest remote jobs; attribution requested)
- Greenhouse / Lever job-board APIs for companies on your watchlist
LinkedIn / Indeed are not scraped.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

import httpx

from .sources import html_to_text

UA = "job-copilot/0.2 (personal job-search agent)"


@dataclass
class AutomationConfig:
    """Sensible defaults inferred from the portfolio; editing is optional."""
    titles: list[str] = field(default_factory=lambda: [
        "support engineer", "technical support", "integration engineer", "integrations engineer",
        "solutions engineer", "implementation engineer", "implementation specialist", "automation engineer",
        "customer success engineer", "application support", "ai engineer", "product support",
        "technical account manager", "support specialist"])
    exclude_titles: list[str] = field(default_factory=lambda: [
        "intern", "director", "vp", "head of", "principal", "staff ", "sales", "account executive",
        "recruiter", "marketing", "designer", "nurse", "teacher", "warehouse associate"])
    locations_ok: list[str] = field(default_factory=lambda: [
        "worldwide", "anywhere", "global", "remote", "south africa", "johannesburg", "africa", "emea",
        "gmt+2", "sast", "cape town", "pretoria"])
    locations_block: list[str] = field(default_factory=lambda: [
        "us only", "usa only", "u.s. only", "united states only", "canada only", "uk only", "eu only"])
    skills: list[str] = field(default_factory=lambda: [
        "servicenow", "pagerduty", "zendesk", "jira", "api", "webhook", "oauth", "sql", "postgres", "python",
        "integration", "hubspot", "gohighlevel", "make.com", "zapier", "typescript", "react", "llm", "claude",
        "openai", "automation", "root cause", "incident", "sla", "power bi", "etl", "saas", "stripe", "shopify"])
    remotive_categories: list[str] = field(default_factory=lambda: ["customer-support", "software-dev"])
    himalayas_queries: list[str] = field(default_factory=lambda: [
        "support engineer", "integration engineer", "solutions engineer", "implementation engineer"])
    greenhouse_boards: list[str] = field(default_factory=list)  # e.g. "gitlab" from boards.greenhouse.io/gitlab
    lever_companies: list[str] = field(default_factory=list)    # e.g. "netflix" from jobs.lever.co/netflix
    assess_per_run: int = 5
    run_budget_usd: float = 0.35
    auto_docs_min_score: int = 75
    min_prefilter_score: float = 3.0
    # Expert mode (off until you switch it on): panel review for strong matches, interview prep at interview stage.
    # Needs a bigger per-run budget (~$0.75) -- a review with revisions costs ~$0.50, a prep pack ~$0.25.
    expert_mode: bool = False

    @classmethod
    def from_dict(cls, d: dict | None) -> "AutomationConfig":
        base = cls()
        for k, v in (d or {}).items():
            if hasattr(base, k):
                setattr(base, k, v)
        return base

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Lead:
    source: str
    title: str
    company: str
    location: str
    url: str
    text: str
    posted: str = ""
    score: float = 0.0
    keep: bool = False
    reason: str = ""


# -- free pre-filter ---------------------------------------------------------------------

def prefilter(lead: Lead, cfg: AutomationConfig) -> Lead:
    title, loc = lead.title.lower(), (lead.location or "").lower()
    body = lead.text.lower()
    if any(x in f" {title} " for x in cfg.exclude_titles):
        lead.keep, lead.reason = False, "title excluded"
        return lead
    hits = [t for t in cfg.titles if t in title]
    if not hits:
        lead.keep, lead.reason = False, "title not a target role"
        return lead
    if any(b in loc for b in cfg.locations_block) or (loc and not any(o in loc for o in cfg.locations_ok)):
        lead.keep, lead.reason = False, f"location: {lead.location[:60]}"
        return lead
    skill_hits = [s for s in cfg.skills if re.search(rf"\b{re.escape(s)}\b", body)]
    score = 3.0 * len(hits) + min(len(skill_hits), 8) * 0.5
    if re.search(r"\b(senior|sr\.?|lead)\b", title):
        score -= 1.0
    lead.score = round(score, 1)
    lead.keep = score >= cfg.min_prefilter_score
    lead.reason = (f"matches {', '.join(hits)}; skills: {', '.join(skill_hits[:6]) or 'none'}"
                   if lead.keep else f"low match ({score:.1f})")
    return lead


# -- sources --------------------------------------------------------------------------------

def _get(client: httpx.Client, url: str, **params):
    r = client.get(url, params=params or None, headers={"User-Agent": UA}, timeout=25)
    r.raise_for_status()
    return r.json()


def remotive(client: httpx.Client, cfg: AutomationConfig) -> list[Lead]:
    out = []
    for cat in cfg.remotive_categories:
        for j in _get(client, "https://remotive.com/api/remote-jobs", category=cat).get("jobs", []):
            out.append(Lead("remotive", j.get("title", ""), j.get("company_name", ""),
                            j.get("candidate_required_location", ""), j.get("url", ""),
                            html_to_text(j.get("description", "")), j.get("publication_date", "")[:10]))
    return out


def himalayas(client: httpx.Client, cfg: AutomationConfig) -> list[Lead]:
    out = []
    scopes = [{"worldwide": "true"}, {"country": "ZA"}]  # roles you can actually take from South Africa
    for q in cfg.himalayas_queries:
        for scope in scopes:
            for j in _get(client, "https://himalayas.app/jobs/api/search", q=q, limit=20, **scope).get("jobs", []):
                loc = ", ".join(j.get("locationRestrictions") or []) or "Worldwide"
                out.append(Lead("himalayas", j.get("title", ""), j.get("companyName", ""), loc,
                                j.get("applicationLink") or j.get("guid", ""), html_to_text(j.get("description", "")),
                                str(j.get("pubDate", ""))[:10]))
    return out


def remoteok(client: httpx.Client, cfg: AutomationConfig) -> list[Lead]:
    """One call for the latest ~100 remote jobs (RemoteOK asks for attribution and light use)."""
    out = []
    for j in _get(client, "https://remoteok.com/api"):
        if not isinstance(j, dict) or not j.get("position"):
            continue  # first element is a legal notice
        out.append(Lead("remoteok", j.get("position", ""), j.get("company", ""), j.get("location") or "Worldwide",
                        j.get("url", ""), html_to_text(j.get("description", "")), str(j.get("date", ""))[:10]))
    return out


def greenhouse(client: httpx.Client, cfg: AutomationConfig) -> list[Lead]:
    out = []
    for board in cfg.greenhouse_boards:
        for j in _get(client, f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs", content="true").get("jobs", []):
            out.append(Lead("greenhouse", j.get("title", ""), board, (j.get("location") or {}).get("name", ""),
                            j.get("absolute_url", ""), html_to_text(j.get("content", "")), str(j.get("updated_at", ""))[:10]))
    return out


def lever(client: httpx.Client, cfg: AutomationConfig) -> list[Lead]:
    out = []
    for co in cfg.lever_companies:
        for j in _get(client, f"https://api.lever.co/v0/postings/{co}", mode="json"):
            cats = j.get("categories") or {}
            lists = "\n\n".join(f"{l['text']}\n{html_to_text(l['content'])}" for l in j.get("lists", []))
            out.append(Lead("lever", j.get("text", ""), co, cats.get("location", ""), j.get("hostedUrl", ""),
                            f"{j.get('descriptionPlain', '')}\n\n{lists}"))
    return out


SOURCES = {"remotive": remotive, "himalayas": himalayas, "remoteok": remoteok, "greenhouse": greenhouse, "lever": lever}


def discover(cfg: AutomationConfig, client: httpx.Client | None = None) -> tuple[list[Lead], dict[str, str]]:
    """All sources, pre-filtered. Returns (leads, per-source status). A failing source never stops the others."""
    client = client or httpx.Client(follow_redirects=True)
    leads, status = [], {}
    for name, fn in SOURCES.items():
        try:
            found = fn(client, cfg)
            leads += found
            status[name] = f"{len(found)} found"
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            status[name] = f"error: {type(e).__name__}"
    seen, unique = set(), []
    for l in leads:
        if l.url and l.url not in seen:
            seen.add(l.url)
            unique.append(prefilter(l, cfg))
    return unique, status
