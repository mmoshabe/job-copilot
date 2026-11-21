"""Expert layer: four-perspective panel review and interview preparation.

Prompts live in instructions/*.md (versioned by content hash) so the "how" is readable,
reviewable and improvable without touching code. Every prompt is prefixed with
instructions/grounding.md -- the rules that apply to everything."""

from __future__ import annotations

import hashlib
from enum import Enum
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

from .llm import LLM, LLMResult
from .schemas import FitAssessment

DIR = Path(__file__).parent / "instructions"


@lru_cache
def instructions(name: str) -> tuple[str, str]:
    """(prompt, version). Version = short hash of grounding + the named file."""
    text = (DIR / "grounding.md").read_text() + "\n\n" + (DIR / f"{name}.md").read_text()
    return text, hashlib.sha256(text.encode()).hexdigest()[:8]


# -- panel review ----------------------------------------------------------------------------

class Perspective(str, Enum):
    recruiter = "recruiter"
    hiring_manager = "hiring_manager"
    business_owner = "business_owner"
    applicant = "applicant"


class PerspectiveReview(BaseModel):
    perspective: Perspective
    score: int = Field(description="1-10")
    verdict: str = Field(description="advance / maybe / reject (applicant: apply / caveats / skip)")
    strengths: list[str]
    concerns: list[str]
    fixes: list[str]


class ProjectProof(BaseModel):
    requirement: str
    proof: str = Field(description="Project or role from the evidence that proves it")
    evidence: str = Field(description="The evidence line")
    talking_point: str


class Fix(BaseModel):
    target: str = Field(description="resume or letter")
    fix: str


class PanelReview(BaseModel):
    perspectives: list[PerspectiveReview]
    supporting_projects: list[ProjectProof]
    top_fixes: list[Fix] = Field(description="At most 6, highest impact first; evidence-only edits")
    red_flags: list[str]
    summary: str = Field(description="Two sentences: overall readiness and the single most important change")


def review_panel(llm: LLM, model: str, evidence: str, job: str, fit: FitAssessment, resume: str,
                 letter: str | None, ats: dict | None) -> tuple[LLMResult[PanelReview], str]:
    system, version = instructions("panel")
    ats_txt = ""
    if ats:
        ats_txt = (f"\n\n<ats_report score='{ats['score']}' coverage='{ats['keyword_coverage']}%'>\n"
                   f"missing but supported by evidence: {', '.join(ats['add']) or 'none'}\n"
                   f"missing and NOT supported (gaps): {', '.join(ats['gaps']) or 'none'}\n"
                   f"format issues: {', '.join(c['check'] for c in ats['format'] if not c['ok']) or 'none'}\n</ats_report>")
    user = (f"{evidence}\n\n<job_post>\n{job}\n</job_post>\n\n<assessment>\n{fit.model_dump_json(indent=1)}\n</assessment>"
            f"\n\n<resume>\n{resume}\n</resume>\n\n<cover_letter>\n{letter or '(not written yet)'}\n</cover_letter>{ats_txt}")
    return llm.structured(model=model, system=system, user=user, schema=PanelReview, effort="medium",
                          max_tokens=12000), version


# -- interview pack ------------------------------------------------------------------------------

class Interviewer(str, Enum):
    recruiter = "recruiter"
    hiring_manager = "hiring_manager"
    business_owner = "business_owner"


class Topic(BaseModel):
    topic: str
    why: str
    study_notes: str
    your_experience: str = Field(description="Closest real experience from the evidence, or 'none yet'")


class TalkingPoint(BaseModel):
    requirement: str
    proof: str
    star: str = Field(description="30-second STAR answer from real details only")


class MiniProject(BaseModel):
    gap: str
    project: str = Field(description="Buildable in a weekend")
    shows: str


class Question(BaseModel):
    interviewer: Interviewer
    question: str
    why: str = Field(description="What they're really checking")
    answer_outline: str
    evidence: str = Field(description="Which evidence the answer draws on, or 'gap: honest strategy'")


class AskThem(BaseModel):
    interviewer: Interviewer
    question: str


class InterviewPack(BaseModel):
    company_brief: list[str] = Field(description="Facts from the post; inferences prefixed with 'Inference:'")
    verify_before: list[str]
    knowledge: list[Topic]
    talking_points: list[TalkingPoint]
    mini_projects: list[MiniProject]
    questions: list[Question]
    ask_them: list[AskThem]
    red_flags: list[str]


def interview_pack(llm: LLM, model: str, evidence: str, job: str, fit: FitAssessment,
                   resume: str | None) -> tuple[LLMResult[InterviewPack], str]:
    system, version = instructions("interview")
    user = (f"{evidence}\n\n<job_post>\n{job}\n</job_post>\n\n<assessment>\n{fit.model_dump_json(indent=1)}\n</assessment>"
            f"\n\n<resume_sent>\n{resume or '(base CV)'}\n</resume_sent>")
    return llm.structured(model=model, system=system, user=user, schema=InterviewPack, effort="medium",
                          max_tokens=16000), version
