from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Kind(str, Enum):
    must = "must"
    nice = "nice"


class Met(str, Enum):
    yes = "yes"
    partial = "partial"
    no = "no"


class Requirement(BaseModel):
    text: str = Field(description="The requirement, in a few words")
    kind: Kind = Field(description="must = stated as required; nice = advantageous / bonus")
    met: Met
    evidence: str = Field(description="Quote or close paraphrase from the CV that shows it; empty if none")


class FitAssessment(BaseModel):
    company: str
    title: str
    location: str = Field(description="City / remote / hybrid as stated; empty if not stated")
    requirements: list[Requirement]
    dealbreakers: list[str] = Field(description="Hard blockers stated in the post that the CV doesn't meet (e.g. required citizenship, licence, degree)")
    summary: str = Field(description="Two sentences: why this is or isn't a good match")


class Claim(BaseModel):
    claim: str = Field(description="A factual statement about the candidate made in the letter")
    cv_evidence: str = Field(description="The CV text that supports it; empty if none")


class CoverLetter(BaseModel):
    body: str = Field(description="The cover letter, ready to paste, no placeholders except [Hiring Manager] if no name is known")
    claims: list[Claim]


class LetterCheck(BaseModel):
    unsupported_claims: list[str] = Field(description="Statements about the candidate that the evidence (CV, portfolio, projects) does not support, including inflated numbers, titles, tools or years")
    issues: list[str] = Field(description="Concrete edits: tone, length, generic phrases, missing link to the job")
    score: float = Field(description="0-1 overall quality")


class ResumeRole(BaseModel):
    role: str
    org: str
    dates: str
    bullets: list[str] = Field(description="Most relevant first; reworded only within what the evidence says")


class ResumeProject(BaseModel):
    name: str
    line: str = Field(description="One line on what it is and why it's relevant to this job")


class TailoredResume(BaseModel):
    headline: str = Field(description="Target-role headline; must be truthful to the evidence")
    summary: str = Field(description="3-4 sentences aimed at this job")
    skills: list[str] = Field(description="Only skills present in the evidence, most relevant first")
    experience: list[ResumeRole]
    projects: list[ResumeProject] = Field(description="2-4 projects from the evidence most relevant to this job")
    education: list[str]
    certifications: list[str]
    changes: list[str] = Field(description="What you changed versus the base CV and why, one line each")
    gaps: list[str] = Field(description="Job requirements the evidence doesn't meet, each with an honest suggestion")
