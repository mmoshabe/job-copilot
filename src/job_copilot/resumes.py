"""Master resumes per role family: AI, support, engineering, data.

Each master is the candidate's chosen positioning for a kind of role (written from the same
evidence). A job is matched to a family by its title, and its tailored resume starts from
that master instead of from the raw CV -- so an AI implementation role leads with automation
and AI assistants, a data role with SQL/Power BI results, and so on."""

from __future__ import annotations

import re

FAMILIES = {
    "ai": "AI roles (implementation, automation, AI assistants)",
    "support": "Support roles (technical / application / customer support)",
    "engineering": "Engineer / developer roles",
    "data": "Data roles (analyst, BI, reporting)",
}

_SIGNALS = {
    "ai": [r"\bai\b", r"artificial intelligence", r"machine learning", r"\bml\b", r"\bllm", r"gen ?ai", r"prompt",
           r"automation", r"implementation", r"\bagents?\b", r"chatbot", r"no-?code"],
    "data": [r"\bdata\b", r"analyst", r"analytics", r"\bbi\b", r"business intelligence", r"reporting", r"insights",
             r"dashboard"],
    "support": [r"support", r"customer success", r"help ?desk", r"service desk", r"technical account", r"incident",
                r"\bnoc\b", r"customer care"],
    "engineering": [r"engineer", r"developer", r"software", r"full[- ]?stack", r"back[- ]?end", r"front[- ]?end",
                    r"integration", r"devops", r"\bsre\b", r"programmer"],
}
_TIE_ORDER = ["ai", "data", "support", "engineering"]  # "Data Engineer" -> data, "Support Engineer" -> support


def family_for(title: str, text: str = "") -> str | None:
    """Best-matching family from the job title (the description breaks ties). None if nothing matches."""
    t, body = title.lower(), text.lower()[:3000]
    scores = {f: 3 * sum(bool(re.search(p, t)) for p in pats) + 0.1 * sum(bool(re.search(p, body)) for p in pats)
              for f, pats in _SIGNALS.items()}
    best = max(_TIE_ORDER, key=lambda f: (scores[f] >= 3, scores[f], -_TIE_ORDER.index(f)))
    return best if scores[best] >= 3 else None


def profile_key(family: str) -> str:
    return f"resume_{family}"
