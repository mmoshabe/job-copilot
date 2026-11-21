# Job Copilot playbook

How the copilot turns "a job exists somewhere" into "the strongest possible application, and an interview you're ready for". It's written as an algorithm, so each step has inputs, rules, outputs and a quality check. The prompts that carry out the AI steps are in [`src/job_copilot/instructions/`](src/job_copilot/instructions/), and every result records which version of the instructions produced it.

## Principles

1. **Evidence is the ceiling.** Your CV, portfolio and project notes are the only source of claims. Every AI output is checked against them, and anything unsupported is flagged or removed. A strong application that's true beats a perfect one that isn't. See [`grounding.md`](src/job_copilot/instructions/grounding.md).
2. **Code decides, AI judges.** Scores, routing, budgets and ATS checks are computed in code (stable, testable, free). The AI judges what needs judgement: does this experience meet this requirement, how would a recruiter read this?
3. **Four perspectives, one candidate.** Every application is judged as a **recruiter** (does it pass the screen?), a **hiring manager** (can they do the job?), a **business owner** (is the hire worth it?) and **you** (is the job worth it?).
4. **Spend where it matters.** Free steps run on everything; paid steps run only where they can change an outcome, in order of urgency.
5. **Automatic by default; manual is optional.** Every step runs on schedule. Buttons exist to re-run or override, never as a requirement.

## The pipeline

```
discover ──► free filter ──► score fit ──► tailor resume ◄─ ATS targets
   (daily)     (title, location)  (per requirement)     │
                                                        ▼
                                  ATS check (free) ──► cover letter ──► fact-check
                                                        │
                                                        ▼
                               panel review: recruiter · hiring manager · business owner · applicant
                                                        │  top fixes (evidence-only)
                                                        ▼
                                  revised resume / letter ──► ATS re-check ──► ready to apply
                                                                                    │ you submit
                                                                                    ▼
                                         status "interview" ──► interview pack ──► follow-ups
```

### 1. Discover (daily, free)
- **Sources:** Remotive, Himalayas (worldwide + South Africa), RemoteOK, and the Greenhouse/Lever companies on your watchlist. Official APIs only; LinkedIn and Indeed are never scraped.
- **Filter** on target titles, exclusions and locations you can actually work from. Every job keeps the **reason** it was kept or dropped.
- **Check:** the Discovered page shows the reason for each job; if good jobs are being dropped, adjust Settings.

### 2. Score fit (≈ $0.05 per job)
- The AI lists every requirement as **must** or **nice**, judges it **met / partial / no** against the evidence, and quotes the evidence. It also flags hard **dealbreakers** (work permit, on-site abroad, required licence).
- **Code** computes the score (musts weigh double, partial = half) and the recommendation (*apply* ≥ 70 with no missing musts; *stretch* ≥ 50 with at most one; any dealbreaker means *skip*).
- **Check:** an evidence quote on every "met".

### 3. ATS (free, every resume)
ATS software ranks by literal keyword overlap and by whether it can parse the file.
- **Keywords:** lexicon terms in the post (weighted by frequency and must-have status) plus the assessed requirements.
- **Each keyword is one of:** in the resume (covered) · missing but in your evidence (**add**: the resume writer is told to use it word for word) · missing and not in your evidence (**gap**: never added; shown as something to learn or build).
- **Format checks:** contact email in the header, standard headings, dates on every role, headline matches the job title, 1-2 pages, no tables or images, at least 30% of bullets quantified.
- **Score** = 70% weighted keyword coverage + 30% format. The **honest ceiling** shows the best score your evidence can reach; the gap between the two is wording, and the gap above the ceiling is skills.

### 4. Tailored resume + cover letter (≈ $0.15-0.40 each)
- Select, reorder and reword from the evidence; never invent. Numbers are kept exact; when the CV and portfolio disagree, the more conservative figure is used and the difference is noted.
- **Fact-check** after every draft. Unsupported claims trigger a rewrite (up to 2); if any remain, the document is saved with a warning banner.
- **Outputs:** the document, "what changed and why", and honest gaps with suggestions.

### 5. Panel review (≈ $0.50 including revisions; expert mode)
[`panel.md`](src/job_copilot/instructions/panel.md). One review, four perspectives:

| Perspective | Asks | Typical fixes |
|---|---|---|
| 🔎 Recruiter | Knockout criteria? Keywords in the post's own wording? Relevant title visible in the top third? Letter says "why this role" up front? | Mirror the job title, surface must-have keywords, explain short tenures |
| 🛠 Hiring manager | Evidence depth on the core work? A project that proves the hardest requirement? Judgement signals? | Lead with the most relevant role, add the project link, replace generic bullets with specific incidents |
| 💼 Business owner | Outcomes in money, time, SLA or customers? Ownership? Risk? | Surface the numbers (95% SLA, 30% faster, 20% fewer repeat incidents), show end-to-end ownership |
| 🙋 You | Fits your direction? Red flags in the post? What's your unfair advantage here? | Lead with the advantage, or skip the job |

The review also maps **requirement → the project or role that proves it**, lists **red flags**, and produces up to 6 **top fixes**, each doable with the evidence alone. The resume (plus any missing ATS "add" keywords) and the letter are then revised with those fixes and fact-checked again. The ATS score before and after is recorded.

### 6. Apply (you)
Submitting is deliberately yours: forms differ, bot checks exist, and the application goes out in your name. Mark the job **applied** to start a follow-up reminder (7 days).

### 7. Interview pack (≈ $0.25; expert mode, when you mark "interview")
[`interview.md`](src/job_copilot/instructions/interview.md):
- **Company brief**: facts from the post, inferences labelled, plus 3 things to verify.
- **Knowledge base**: 5-8 topics to study, each linked to your closest real experience; thin areas first.
- **Talking points**: 30-second STAR stories for the requirements that matter most, real details only.
- **Mini projects**: a weekend build per gap, to close it honestly before the interview.
- **Likely questions by interviewer** (recruiter, hiring manager, business owner), each with what they're really checking, an answer outline and the evidence it uses.
- **Questions to ask each of them**, and red flags to probe.

### 8. Follow up
A daily digest (dashboard + Slack #jobs-copilot) lists follow-ups due, stale applications and the next best action.

## Daily order of work and budget

Each morning run spends, in this order, until its budget (default $0.35, ~$0.75 recommended with expert mode) or the monthly cap ($5) is reached:

1. Interview packs for jobs at the interview stage *(expert mode)*: the most time-critical
2. Resume + letter for strong matches (fit ≥ 75)
3. Panel review + revision for strong matches that have documents *(expert mode)*
4. Score new leads, best free-filter match first

Anything not reached continues the next day. A lock stops two runs overlapping.

## Improving the instructions safely

1. Edit a file in `instructions/` (the grounding rules apply to every step).
2. The version hash changes automatically, and new results show `instructions vXXXXXXXX`.
3. Run `uv run python evals/run_eval.py` (paid, about $0.90) and compare: recommendations must stay correct and **fabricated claims must stay at 0**.
4. Keep the change only if the eval holds, the same discipline as the support-triage project.
