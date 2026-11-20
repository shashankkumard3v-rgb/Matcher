"""Data shapes shared by every step (resume.json, jd.json, projects.json)."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ---------- Resume ----------

class Contact(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[str] = None
    links: list[str] = Field(default_factory=list)


class Experience(BaseModel):
    title: Optional[str] = None
    company: Optional[str] = None
    location: Optional[str] = None
    start: Optional[str] = None  # "YYYY-MM" when known, else as written
    end: Optional[str] = None    # "present" for current roles
    bullets: list[str] = Field(default_factory=list)


class Education(BaseModel):
    degree: Optional[str] = None
    institution: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    details: Optional[str] = None


class ResumeProject(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    bullets: list[str] = Field(default_factory=list)
    tech: list[str] = Field(default_factory=list)


class Resume(BaseModel):
    contact: Contact = Field(default_factory=Contact)
    summary: Optional[str] = None
    experience: list[Experience] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)  # awards, wins, recognitions
    languages: list[str] = Field(default_factory=list)  # spoken languages
    projects: list[ResumeProject] = Field(default_factory=list)
    # page layout only: which Skills line a skill goes on, when you've said so ("Jira" -> "DevOps")
    skill_kinds: dict[str, str] = Field(default_factory=dict)
    # page layout only: the skills the job asks for, kept first when a Skills line must be shortened
    skill_focus: list[str] = Field(default_factory=list)
    # page layout only: how relevant each skill is for this job (higher stays when a line is too long)
    skill_rank: dict[str, float] = Field(default_factory=dict)
    # skills you asked (in the resume chat) to show on the page even if Matcher would leave them off
    skill_keep: list[str] = Field(default_factory=list)
    unparsed: list[str] = Field(
        default_factory=list,
        description="Text the parser could not place in a section.",
    )


# ---------- Job description ----------

class JobDescription(BaseModel):
    title: Optional[str] = None
    company: Optional[str] = None
    seniority: Optional[str] = None  # e.g. junior / mid / senior / lead
    must_have: list[str] = Field(default_factory=list)
    nice_to_have: list[str] = Field(default_factory=list)
    years_required: Optional[float] = None
    location: Optional[str] = None
    work_auth: Optional[str] = None
    domain: Optional[str] = None
    responsibilities: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)  # spoken languages the JD requires
    capabilities: list[str] = Field(default_factory=list)  # ids from config/capabilities.yaml

    @field_validator("must_have", "nice_to_have", mode="after")
    @classmethod
    def _split_mixed_lists(cls, v: list[str]) -> list[str]:
        """'Go, Python, Java, Kubernetes, AWS or Azure' is one language AND Kubernetes AND one
        cloud, not one of all six. Also fixes JDs saved before this rule existed."""
        from app.scoring.synonyms import split_mixed
        out: list[str] = []
        for s in v:
            for part in split_mixed(s):
                if part.lower() not in {x.lower() for x in out}:
                    out.append(part)
        return out


# ---------- Project context (your experience bank) ----------

class Project(BaseModel):
    id: str
    name: str
    employer: Optional[str] = None
    role: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    team_size: Optional[str] = None
    problem: Optional[str] = None
    what_i_did: list[str] = Field(default_factory=list)
    tech: list[str] = Field(default_factory=list)
    results: list[str] = Field(default_factory=list)
    notes: Optional[str] = None


class ProjectIssue(BaseModel):
    project_id: str
    field: str
    message: str


class Profile(BaseModel):
    """Facts about you that aren't a project (from the template's ## Profile block)."""
    languages: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)  # e.g. https://leetcode.com/u/you


class ProjectContext(BaseModel):
    projects: list[Project] = Field(default_factory=list)
    profile: Profile = Field(default_factory=Profile)
    issues: list[ProjectIssue] = Field(default_factory=list)


# ---------- Step 1 output ----------

class ParseWarning(BaseModel):
    source: str   # "resume" | "jd" | "projects"
    code: str     # machine-readable, e.g. "scanned_pdf", "unverified_bullet"
    message: str


class ParseResult(BaseModel):
    run_id: str
    resume: Resume
    jd: JobDescription
    projects: ProjectContext
    warnings: list[ParseWarning] = Field(default_factory=list)


# ---------- Step 2 output ----------

class SkillHit(BaseModel):
    skill: str
    status: str   # "evidenced" (used in a bullet) | "listed_only" | "missing"
    credit: float  # 0..1 share of that skill's points earned
    mentions: int = 0
    note: Optional[str] = None
    matched: Optional[str] = None  # for "Java or Go or C++": the one your resume shows


class ScoreDimension(BaseModel):
    key: str
    label: str
    method: str          # "deterministic" | "llm"
    weight: float        # max points
    points: float        # earned
    level: Optional[int] = None  # LLM anchored level 0/25/50/75/100
    reason: str = ""
    evidence: list[str] = Field(default_factory=list)

    @property
    def lost(self) -> float:
        return round(self.weight - self.points, 2)


class ScoreResult(BaseModel):
    total: float
    deterministic_points: float
    llm_points: float
    dimensions: list[ScoreDimension]
    must_have: list[SkillHit] = Field(default_factory=list)
    nice_to_have: list[SkillHit] = Field(default_factory=list)
    years_found: Optional[float] = None
    top_losses: list[str] = Field(
        default_factory=list, description="Dimension keys losing the most points, worst first")
    warnings: list[str] = Field(default_factory=list)
    pins: dict = Field(default_factory=dict, description="Versions used, for reproducibility")


# ---------- Context updates (proposed, then accepted by you) ----------

class ContextProposal(BaseModel):
    id: str
    source: str                       # "question:<id>" or "free_text"
    user_text: str
    action: str                       # add_project | update_project | add_profile | none
    summary: str = ""
    project: Optional[Project] = None
    profile: Optional[Profile] = None
    before: str = ""                  # template block before the change
    after: str = ""                   # template block after the change
    missing_info: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    status: str = "pending"           # pending | accepted | rejected
    question: Optional[str] = None    # the question it answers, if any
    chat: list[str] = Field(default_factory=list)   # what you told the AI to change, in order


# ---------- Step 3: apply gate ----------

class Knockout(BaseModel):
    id: str                 # "missing_must_haves" | "years_short" | "language:<x>"
    message: str
    status: str = "active"  # active | overridden


class GateQuestion(BaseModel):
    id: str                 # stable: "skill:kafka", "listed:postgresql", "years", "language:german"
    kind: str               # missing_skill | listed_only | years_short | missing_language
    text: str
    why: str
    points_at_stake: float = 0
    status: str = "open"    # open | answered | resolved | skipped
    answer: Optional[str] = None
    proposal_id: Optional[str] = None


class LossLine(BaseModel):
    key: str
    label: str
    lost: float
    reason: str
    evidence: list[str] = Field(default_factory=list)
    to_gain: str = ""       # what would need to be true to win these points back


class GateExplanation(BaseModel):
    how_reached: str
    top_losses: list[LossLine] = Field(default_factory=list)
    missing_must_haves: list[str] = Field(default_factory=list)
    points_below_floor: float = 0


class Override(BaseModel):
    target: str             # a knockout id or "verdict"
    reason: str = ""
    at: str


class FoundEvidence(BaseModel):
    """A requirement your resume doesn't show yet, but your notes do (found by the AI, checked by code)."""
    skill: str                 # the JD requirement
    job: int                   # resume job it belongs to
    job_label: str = ""
    bullet: str                # an honest bullet built only from the quotes, naming the requirement
    quotes: list[str] = Field(default_factory=list)   # your notes lines that prove it


class GateState(BaseModel):
    run_id: str
    current_score: float
    ceiling_score: float
    thresholds: dict
    base_verdict: str       # from the numbers + knockouts, before overrides
    verdict: str            # APPLY | APPLY_WITH_RISK | DONT_APPLY | OVERRIDDEN
    knockouts: list[Knockout] = Field(default_factory=list)
    questions: list[GateQuestion] = Field(default_factory=list)
    proposals: list[ContextProposal] = Field(default_factory=list)
    overrides: list[Override] = Field(default_factory=list)
    explanation: GateExplanation
    found_in_notes: list[FoundEvidence] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    needs_recompute: bool = False
    decision: Optional[str] = None      # proceed | stop
    decision_reason: Optional[str] = None


# ---------- Step 4: improve loop ----------

class Problem(BaseModel):
    id: str                 # e.g. "must:kafka", "impact", "relevance"
    dimension: str          # score dimension key
    label: str
    points_lost: float
    cause: str
    fix_by: str             # "ai" (facts exist) | "you" (facts missing) | "none" (can't fix by rewriting)
    detail: str = ""
    subject: Optional[str] = None  # e.g. the skill name


class LoopQuestion(BaseModel):
    id: str
    problem_id: str
    text: str
    why: str
    points_at_stake: float = 0
    status: str = "open"    # open | answered | skipped
    answer: Optional[str] = None
    proposal_id: Optional[str] = None


class ChangeSource(BaseModel):
    type: str               # resume | template | you
    project_id: Optional[str] = None
    quote: str
    verified: bool = False


class Change(BaseModel):
    id: str
    kind: str               # replace_bullet | add_bullet | remove_bullet | merge_bullets | set_summary | add_skill
    job: Optional[int] = None
    bullet: Optional[int] = None
    bullet2: Optional[int] = None     # merge_bullets: the second bullet folded into `bullet`
    job_label: str = ""
    old: Optional[str] = None
    old2: Optional[str] = None        # merge_bullets: the second bullet's current text
    new: Optional[str] = None
    fixes: list[str] = Field(default_factory=list)
    why: str = ""
    sources: list[ChangeSource] = Field(default_factory=list)
    truth_ok: bool = True
    truth_issues: list[str] = Field(default_factory=list)
    status: str = "pending"  # pending | accepted | rejected | blocked
    lines_delta: Optional[int] = None  # fit rounds: lines this edit adds (+) or saves (-)
    points_delta: Optional[float] = None  # exact change in the rules-based points (computed by code)
    impact: list[str] = Field(default_factory=list)  # plain facts about what this edit does (code-made)
    edited_by_you: bool = False
    feedback_history: list[str] = Field(default_factory=list)
    from_notes: Optional[str] = None  # which My info section the facts come from


class Version(BaseModel):
    n: int
    file: str
    score: float
    score_file: str
    note: str = ""


class Round(BaseModel):
    n: int
    purpose: str = "improve"   # improve | fit (one-page fit agent)
    layout: dict = Field(default_factory=dict)  # fit rounds: what the page looks like
    base_version: int
    score_before: float
    problems: list[Problem] = Field(default_factory=list)
    questions: list[LoopQuestion] = Field(default_factory=list)
    proposal_ids: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)     # your style input for this round
    changes: list[Change] = Field(default_factory=list)
    plan: dict = Field(default_factory=dict)  # split mode: what was decided per bullet (keep/reword/...)
    stage: str = "questions"   # questions | review | applied | closed
    closed_from: Optional[str] = None  # the stage it was in when you downloaded mid-round
    result_version: Optional[int] = None
    score_after: Optional[float] = None


class LoopState(BaseModel):
    run_id: str
    target: float
    current_version: int = 0
    versions: list[Version] = Field(default_factory=list)
    rounds: list[Round] = Field(default_factory=list)
    proposals: list[ContextProposal] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)   # target_reached | max_rounds | plateau | score_dropped
    recommendation: str = ""
    finished: bool = False
    final_version: Optional[int] = None


# ---------- Step 5: export ----------

class TrimOption(BaseModel):
    id: str                 # "j<job>b<bullet>"
    job: int
    job_label: str
    text: str
    value: float            # higher = more useful for this JD
    est_lines: int
    reason: str
    suggested: bool = False


class AtsCheck(BaseModel):
    id: str
    label: str
    level: str          # pass | warn | fail | info
    detail: str = ""


class FreshScore(BaseModel):
    """A re-score of the exported file from scratch: read like an ATS, scored by the blind judge."""
    n: int
    at: str
    version: int
    score: ScoreResult
    read_back: dict = Field(default_factory=dict)   # what the parser found: name, jobs, bullets...
    differences: list[str] = Field(default_factory=list)  # vs. the version that was exported
    loop_score: Optional[float] = None


class ChatTurn(BaseModel):
    """One message in the resume chat (Step 3)."""
    role: str                     # you | ai
    text: str
    done: list[str] = Field(default_factory=list)      # what was changed
    refused: list[str] = Field(default_factory=list)   # what wasn't, and why
    version: Optional[int] = None                       # the version this made


class ExportState(BaseModel):
    run_id: str
    version: int
    status: str             # fitted | overflow | underfilled
    pages: int
    fill: float
    overflow_lines: int = 0
    free_lines: int = 0
    renderer: str
    look: dict
    trim_options: list[TrimOption] = Field(default_factory=list)
    unused_facts: list[str] = Field(default_factory=list)
    message: str = ""
    files: dict = Field(default_factory=dict)   # docx / pdf / report -> file name
    ats: list[AtsCheck] = Field(default_factory=list)
    history: list[str] = Field(default_factory=list)
    chat: list[ChatTurn] = Field(default_factory=list)
    skills_left_off: list[str] = Field(default_factory=list)   # to keep Skills to 4 one-line rows
