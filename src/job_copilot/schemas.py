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
    unsupported_claims: list[str] = Field(description="Statements about the candidate that the CV does not support, including inflated numbers, titles, tools or years")
    issues: list[str] = Field(description="Concrete edits: tone, length, generic phrases, missing link to the job")
    score: float = Field(description="0-1 overall quality")
