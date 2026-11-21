"""ATS (applicant tracking system) check -- deterministic, free, no LLM.

ATS software ranks resumes mostly by literal keyword overlap with the job post and by whether
it can parse the document. So we:
1. extract the post's keywords (a tech/support lexicon + the assessed requirements),
2. check each against the resume AND the candidate's evidence:
   - in resume                    -> covered
   - missing, but in the evidence -> "add": the resume writer may include it (truthfully)
   - missing, not in the evidence -> "gap": never stuffed in; shown as a skill gap
3. run parse/format checks (contact info, standard headings, dates, length, no tables).
"""

from __future__ import annotations

import re
from collections import Counter

from .schemas import FitAssessment, Kind
from .suggestions import _norm

# Terms ATS filters commonly key on for support / integration / automation / AI roles.
LEXICON = [
    "python", "sql", "javascript", "typescript", "react", "next.js", "node.js", "java", "go", "c#", "php", "ruby",
    "bash", "powershell", "linux", "windows server", "aws", "azure", "gcp", "docker", "kubernetes", "terraform",
    "ci/cd", "git", "github", "jenkins", "rest api", "graphql", "webhooks", "oauth", "saml", "sso", "json", "xml",
    "postgresql", "mysql", "sql server", "mongodb", "redis", "etl", "data pipelines", "power bi", "tableau", "ssrs",
    "excel", "servicenow", "jira", "zendesk", "freshdesk", "intercom", "salesforce", "hubspot", "gohighlevel",
    "zapier", "make", "n8n", "power automate", "workato", "mulesoft", "boomi", "stripe", "shopify", "plaid",
    "twilio", "pagerduty", "moogsoft", "datadog", "splunk", "grafana", "new relic", "sentry", "itil",
    "incident management", "root cause analysis", "troubleshooting", "sla", "escalation", "runbooks",
    "knowledge base", "documentation", "customer support", "technical support", "tier 2", "tier 3",
    "on-call", "integrations", "api integration", "system integration", "saas", "crm", "erp", "wms",
    "llm", "generative ai", "openai", "claude", "prompt engineering", "rag", "vector database", "pinecone",
    "machine learning", "automation", "workflow automation", "agile", "scrum", "stakeholder management",
    "customer success", "onboarding", "implementation", "solutions engineering", "pre-sales", "fintech",
    "e-commerce", "logistics", "healthcare", "payments", "security", "compliance", "gdpr", "popia", "soc 2",
]

ALIASES = {
    "postgresql": ["postgres", "postgresql"], "rest api": ["rest api", "rest apis", "restful"],
    "webhooks": ["webhook", "webhooks"], "ci/cd": ["ci/cd", "continuous integration"],
    "node.js": ["node.js", "nodejs", "node"], "next.js": ["next.js", "nextjs"],
    "sql server": ["sql server", "mssql", "t-sql"], "kubernetes": ["kubernetes", "k8s"],
    "root cause analysis": ["root cause analysis", "root cause", "rca"], "llm": ["llm", "llms", "large language model"],
    "make": ["make.com", "make (integromat)", "integromat"], "gohighlevel": ["gohighlevel", "highlevel", "ghl"],
    "integrations": ["integration", "integrations"], "troubleshooting": ["troubleshoot", "troubleshooting", "troubleshot"],
    "sla": ["sla", "slas"], "saas": ["saas"], "on-call": ["on-call", "on call"],
    "incident management": ["incident management", "incidents", "incident"],
}

_STD_HEADINGS = ("experience", "skills", "education")


def _variants(term: str) -> list[str]:
    return ALIASES.get(term, [term])


def _has(text: str, term: str) -> bool:
    t = text.lower()
    for v in _variants(term):
        if re.search(rf"(?<![\w.+#-]){re.escape(v)}(?![\w+#])", t):
            return True
    return False


def keywords(job_text: str, fit: FitAssessment | None = None, limit: int = 25) -> list[dict]:
    """Ordered list of {term, must}: lexicon terms in the post (by frequency) + short requirement phrases."""
    counts = Counter()
    for term in LEXICON:
        n = sum(len(re.findall(rf"(?<![\w.+#-]){re.escape(v)}(?![\w+#])", job_text.lower())) for v in _variants(term))
        if n:
            counts[term] = n
    must_text = " ".join(r.text for r in (fit.requirements if fit else []) if r.kind is Kind.must).lower()
    out: dict[str, dict] = {}
    for term, _ in counts.most_common():
        out[term] = {"term": term, "must": _has(must_text, term)}
    for r in (fit.requirements if fit else []):
        phrase = _norm(r.text).lower()
        if 0 < len(phrase.split()) <= 3 and not any(_has(phrase, k) for k in out):
            out.setdefault(phrase, {"term": phrase, "must": r.kind is Kind.must})
    ranked = sorted(out.values(), key=lambda k: (not k["must"], -counts.get(k["term"], 1)))
    return ranked[:limit]


def format_checks(resume: str, job_title: str = "") -> list[dict]:
    low = resume.lower()
    roles = re.findall(r"^###\s+(.+)$", resume, re.M)
    bullets = re.findall(r"^\s*[-*]\s+(.+)$", resume, re.M)
    words = len(re.findall(r"\w+", resume))
    title_words = [w for w in re.findall(r"[a-z]+", job_title.lower()) if len(w) > 3]
    top = low[:400]
    checks = [
        ("Contact email in the header", bool(re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", resume[:500])), "ATS profiles start from the contact block"),
        ("Standard section headings", all(re.search(rf"^##\s+.*{h}", low, re.M) for h in _STD_HEADINGS), "Experience / Skills / Education"),
        ("Every role has dates", bool(roles) and all(re.search(r"(19|20)\d\d", r) for r in roles), f"{len(roles)} roles"),
        ("Headline matches the job title", not title_words or sum(w in top for w in title_words) >= max(1, len(title_words) // 2),
         f"job title: {job_title}"),
        ("Length 1-2 pages", 300 <= words <= 1100, f"{words} words"),
        ("No tables or images", not re.search(r"^\s*\|.*\|\s*$|^\s*\|?\s*:?-{3,}", resume, re.M) and "![" not in resume,
         "parsers often scramble them"),
        ("Bullets show numbers", bool(bullets) and sum(bool(re.search(r"\d", b)) for b in bullets) / len(bullets) >= 0.3,
         f"{sum(bool(re.search(r'[0-9]', b)) for b in bullets)}/{len(bullets)} bullets quantified"),
    ]
    return [{"check": c, "ok": ok, "detail": d} for c, ok, d in checks]


def ats_report(resume: str, job_text: str, evidence: str, fit: FitAssessment | None = None, job_title: str = "") -> dict:
    kws = []
    for k in keywords(job_text, fit):
        k = dict(k, in_resume=_has(resume, k["term"]), in_evidence=_has(evidence, k["term"]))
        kws.append(k)
    w = lambda k: 2 if k["must"] else 1  # noqa: E731
    total = sum(w(k) for k in kws) or 1
    covered = sum(w(k) for k in kws if k["in_resume"])
    achievable = sum(w(k) for k in kws if k["in_resume"] or k["in_evidence"])
    fmt = format_checks(resume, job_title)
    fmt_ratio = sum(c["ok"] for c in fmt) / len(fmt)
    return {
        "score": round(100 * (0.7 * covered / total + 0.3 * fmt_ratio)),
        "keyword_coverage": round(100 * covered / total),
        "honest_ceiling": round(100 * achievable / total),
        "keywords": kws,
        "add": [k["term"] for k in kws if not k["in_resume"] and k["in_evidence"]],
        "gaps": [k["term"] for k in kws if not k["in_resume"] and not k["in_evidence"]],
        "format": fmt,
    }


def ats_targets(job_text: str, evidence: str, fit: FitAssessment | None = None) -> list[str]:
    """Keywords the resume should contain, verbatim, because the evidence supports them."""
    return [k["term"] for k in keywords(job_text, fit) if _has(evidence, k["term"])]
