"""The fit score is computed in code from the requirement-by-requirement assessment,
so it is explainable and stable -- the model judges each requirement, never the total."""

from __future__ import annotations

from .schemas import FitAssessment, Kind, Met

WEIGHT = {Kind.must: 2.0, Kind.nice: 1.0}
CREDIT = {Met.yes: 1.0, Met.partial: 0.5, Met.no: 0.0}


def fit_score(a: FitAssessment) -> int:
    total = sum(WEIGHT[r.kind] for r in a.requirements)
    if total == 0:
        return 0
    got = sum(WEIGHT[r.kind] * CREDIT[r.met] for r in a.requirements)
    return round(100 * got / total)


def recommendation(a: FitAssessment) -> str:
    """apply / stretch / skip. Dealbreakers and missing must-haves dominate the score."""
    if a.dealbreakers:
        return "skip"
    musts = [r for r in a.requirements if r.kind is Kind.must]
    missing = sum(r.met is Met.no for r in musts)
    score = fit_score(a)
    if score >= 70 and missing == 0:
        return "apply"
    if score >= 50 and missing <= 1:
        return "stretch"
    return "skip"
