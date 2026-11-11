"""What each resume bullet is worth for THIS job, where it came from, and how many fit.

- value:  JD must-have skills shown (3 each), nice-to-have (1.5), JD duties it covers (1 each,
          max 3), a measurable result (1), quoted by the blind judge as evidence (2).
- status: "strong" (value >= 4), "ok" (> 0), "rewordable" (earns nothing AS WORDED, but your
          notes about the same work show JD skills/duties: reword it first), "no_value" (adds
          nothing for this JD: replace with something relevant from My info, or remove).
- could_show: JD skills that notes about the same work mention but the bullet doesn't, i.e.
          what better wording could add (true facts only).
- origin: "resume" (on the uploaded resume), or the round that added it and, when it came from
          your notes, which My info section ("Experience notes > Finlytics Pvt Ltd").
- caps:   bullets allowed per job, by recency (config loop.max_bullets_per_job: int or list).
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

from app.parsing.structure import _norm
from app.schemas.models import JobDescription, LoopState, Resume, ScoreResult
from app.scoring.synonyms import count_mentions

_DIGIT = re.compile(r"\d")
_WORD = re.compile(r"[a-z][a-z0-9+#.]{3,}")
_STOP = {"with", "from", "that", "this", "your", "will", "have", "work", "team", "using", "build",
         "design", "develop", "ensure", "across", "other", "their", "about", "services", "service",
         "experience", "years", "strong", "systems", "working", "including", "such", "like"}


class BulletInfo(BaseModel):
    job: int
    bullet: int
    job_label: str
    text: str
    value: float
    status: str                       # strong | ok | no_value
    shows: list[str] = Field(default_factory=list)   # JD skills it shows
    duties: list[str] = Field(default_factory=list)  # JD responsibilities it covers
    has_number: bool = False
    judge_quoted: bool = False
    words: int = 0
    too_long: bool = False
    could_show: list[str] = Field(default_factory=list)    # JD skills a truthful rewording could add
    could_cover: list[str] = Field(default_factory=list)   # JD duties a rewording could cover
    related_notes: list[str] = Field(default_factory=list) # your notes about the same work
    origin: str = "resume"            # resume | added
    origin_label: str = "On your uploaded resume"
    from_notes: str | None = None     # "Experience notes > Finlytics Pvt Ltd"


class JobBullets(BaseModel):
    job: int
    job_label: str
    count: int
    cap: int
    no_value: int
    rewordable: int = 0


class Candidate(BaseModel):
    """A fact from your notes for this job that isn't on the resume yet."""
    job: int
    text: str
    value: float
    shows: list[str] = Field(default_factory=list)


class BulletReport(BaseModel):
    version: int
    word_cap: int = 32
    jobs: list[JobBullets]
    bullets: list[BulletInfo]
    candidates: list[Candidate] = Field(default_factory=list)


def caps_for(n_jobs: int, cfg: dict) -> list[int]:
    raw = cfg.get("max_bullets_per_job", [6, 5, 3])
    seq = [int(x) for x in raw] if isinstance(raw, list) else [int(raw)]
    return [seq[min(i, len(seq) - 1)] for i in range(n_jobs)]


def _duty_words(jd: JobDescription) -> list[tuple[str, set[str]]]:
    out = []
    for r in jd.responsibilities:
        words = {w for w in _WORD.findall(_norm(r)) if w not in _STOP}
        if words:
            out.append((r, words))
    return out


def _quoted(text: str, score: ScoreResult | None) -> bool:
    if score is None:
        return False
    t = _norm(text)
    for d in score.dimensions:
        for q in d.evidence or []:
            q = _norm(q)
            if len(q) >= 20 and (q in t or t in q):
                return True
    return False


def value_of(text: str, jd: JobDescription, score: ScoreResult | None = None) -> tuple[float, dict]:
    must = [s for s in jd.must_have if count_mentions(s, text)]
    nice = [s for s in jd.nice_to_have if count_mentions(s, text)]
    words = set(_WORD.findall(_norm(text)))
    duties = [r for r, w in _duty_words(jd) if len(w & words) >= 2 or len(w & words) / len(w) >= 0.5]
    num = bool(_DIGIT.search(text))
    quoted = _quoted(text, score)
    value = 3 * len(must) + 1.5 * len(nice) + min(len(duties), 3) + (1 if num else 0) + (2 if quoted else 0)
    return round(value, 2), {"shows": must + nice, "duties": duties, "has_number": num, "judge_quoted": quoted}


def status_of(value: float, info: dict) -> str:
    if not info["shows"] and not info["duties"] and not info["judge_quoted"]:
        return "no_value"   # a number alone doesn't make a bullet relevant to this job
    return "strong" if value >= 4 else "ok"


def _notes_section(quote: str) -> str | None:
    """Which My info heading a quoted notes line sits under."""
    from app.context import notes
    q = notes._key(quote)
    if not q:
        return None
    for kind, label in (("experience", "Experience notes"), ("skills", "Skills notes")):
        for head, line in notes.entries(notes.read_raw(kind)):
            k = notes._key(line)
            if k and (k in q or q in k):
                return f"{label} > {head}" if head and head != line else label
    return None


def _origins(state: LoopState | None) -> dict[str, tuple[str, str | None]]:
    """bullet text (normalized) -> (label, My info section) for bullets added by the loop."""
    out: dict[str, tuple[str, str | None]] = {}
    if state is None:
        return out
    for r in state.rounds:
        for c in r.changes:
            if c.status != "accepted" or not c.new or c.kind not in ("add_bullet", "replace_bullet"):
                continue
            section = None
            for s in c.sources:
                if s.type in ("notes", "template") and s.quote:
                    section = _notes_section(s.quote) or (f"Project template > {s.project_id}" if s.type == "template" else None)
                    if section:
                        break
            what = "Added" if c.kind == "add_bullet" else "Rewritten"
            out[_norm(c.new)] = (f"{what} in round {r.n}" + (f" from My info ({section})" if section else ""), section)
    return out


def candidates(resume: Resume, jd: JobDescription, per_job: int = 12) -> list[Candidate]:
    """Best facts from your experience notes for each job (by company), not on the resume yet.
    These are the building blocks the AI can combine into stronger bullets."""
    from app.context import notes
    have = _norm(" ".join(b for j in resume.experience for b in j.bullets))
    pairs = notes.entries(notes.read_raw("experience"))
    out: list[Candidate] = []
    for j, job in enumerate(resume.experience):
        comp = _norm(job.company or "")
        if not comp:
            continue
        found = []
        for head, line in pairs:
            if head == line or len(line.split()) < 4:
                continue
            where = _norm(f"{head or ''} {line}")
            if comp not in where or _norm(line) in have:
                continue
            if re.match(r"^(role|dates?|location|team|product)\s*:", line, re.IGNORECASE):
                continue
            v, info = value_of(line, jd)
            found.append(Candidate(job=j, text=line, value=v, shows=info["shows"]))
        found.sort(key=lambda c: -c.value)
        out += found[:per_job]
    return out


def _content_words(text: str) -> set[str]:
    return {w for w in _WORD.findall(_norm(text)) if w not in _STOP}


def related_notes(bullet: str, company: str | None, pairs: list[tuple]) -> list[str]:
    """Notes lines for the same company that talk about the same work as this bullet."""
    comp = _norm(company or "")
    bw = _content_words(bullet)
    if not comp or not bw:
        return []
    out = []
    for head, line in pairs:
        if head == line or _norm(line) == _norm(bullet):
            continue
        if comp not in _norm(f"{head or ''} {line}"):
            continue
        shared = bw & _content_words(line)
        if len(shared) >= 2 or (shared and len(shared) / len(bw) >= 0.4):
            out.append(line)
    return out


def analyze(resume: Resume, jd: JobDescription, score: ScoreResult | None, cfg: dict,
            original: Resume | None = None, state: LoopState | None = None, version: int = 0) -> BulletReport:
    caps = caps_for(len(resume.experience), cfg)
    word_cap = int(cfg.get("max_words_per_bullet", 32))
    orig = {_norm(b) for j in (original or resume).experience for b in j.bullets}
    added = _origins(state)
    from app.context import notes
    pairs = notes.entries(notes.read_raw("experience"))
    infos, jobs = [], []
    for j, job in enumerate(resume.experience):
        label = " - ".join(x for x in (job.title, job.company) if x)
        dead = reword = 0
        for b, text in enumerate(job.bullets):
            value, info = value_of(text, jd, score)
            st = status_of(value, info)
            rel = related_notes(text, job.company, pairs)
            could, cover = [], []
            for line in rel:
                _, li = value_of(line, jd)
                could += [x for x in li["shows"] if x not in info["shows"] and x not in could]
                cover += [x for x in li["duties"] if x not in info["duties"] and x not in cover]
            if st == "no_value" and (could or cover):
                st = "rewordable"
            dead += st == "no_value"
            reword += st == "rewordable"
            n = sum(1 for w in text.split() if any(ch.isalnum() for ch in w))
            bi = BulletInfo(job=j, bullet=b, job_label=label, text=text, value=value, status=st,
                            words=n, too_long=n > word_cap, could_show=could, could_cover=cover[:3],
                            related_notes=rel[:3], **info)
            if _norm(text) not in orig:
                bi.origin = "added"
                bi.origin_label, bi.from_notes = added.get(_norm(text), ("Added by Matcher", None))
            infos.append(bi)
        jobs.append(JobBullets(job=j, job_label=label, count=len(job.bullets), cap=caps[j],
                               no_value=dead, rewordable=reword))
    return BulletReport(version=version, word_cap=word_cap, jobs=jobs, bullets=infos,
                        candidates=candidates(resume, jd))
