"""Requirements your resume doesn't show, but your notes do - in other words.

The rules-based scoring only sees keywords: a note like "moved the email sender to Celery
workers" doesn't say "asynchronous programming", so the requirement looked missing, the best
possible score ignored it and you were asked about work you had already written down.

This step asks the AI, once per run (cached), which of YOUR notes lines show each missing
requirement, and to write one honest bullet from those lines that names it. Code then checks
everything: the lines must exist, the bullet must name the requirement, may not add a number
the lines don't have, and must fit the word limit. Anything that fails is dropped.

The results feed the best possible score (gate), skip questions you already answered in your
notes, and become the first facts the improve loop works with.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from app.llm.base import LLMClient
from app.schemas.models import FoundEvidence, JobDescription, ProjectContext, Resume, ScoreResult

SKILL = "matcher-find-evidence"
FILE = "evidence.json"
GAP_STATUSES = ("missing", "close", "partial", "listed_only")
log = logging.getLogger("matcher.evidence")

_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def gaps(score: ScoreResult) -> list[str]:
    """JD requirements the resume doesn't fully show yet (must-haves first)."""
    return [h.skill for h in [*score.must_have, *score.nice_to_have] if h.status in GAP_STATUSES]


def note_lines(ctx: ProjectContext) -> list[tuple[str, str | None, str]]:
    """(id, job/heading, text) for every line you wrote: experience notes, skills notes, saved answers."""
    from app.context import notes
    out: list[tuple[str, str | None, str]] = []
    for kind in ("experience", "skills"):
        for head, line in notes.entries(notes.read_raw(kind)):
            if head != line and len(line.split()) >= 3:
                out.append((f"N{len(out) + 1}", head, line))
    for p in ctx.projects:
        for line in [*p.what_i_did, *p.results]:
            if len(line.split()) >= 3:
                out.append((f"N{len(out) + 1}", p.employer, line))
    return out


def _input(jd: JobDescription, resume: Resume, want: list[str], lines) -> str:
    jobs = [{"job": i, "title": j.title, "company": j.company} for i, j in enumerate(resume.experience)]
    return "\n\n".join([
        "JOB: " + " · ".join(x for x in (jd.title, jd.company) if x),
        "GAPS (the resume doesn't show these yet):\n" + json.dumps(want, ensure_ascii=False),
        "RESUME JOBS:\n" + json.dumps(jobs, ensure_ascii=False),
        "NOTES:\n" + "\n".join(f"{i} [{h or 'no job given'}] {t}" for i, h, t in lines),
    ])


def _key(want: list[str], lines, resume: Resume) -> str:
    from app.skills.loader import load_skill
    blob = json.dumps([want, [list(x) for x in lines], [(j.title, j.company) for j in resume.experience],
                       load_skill(SKILL).content_hash], ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:20]


def _shows(skill: str, text: str) -> str | None:
    """The option of the requirement this text shows (directly or through a related skill/concept)."""
    from app.scoring.deterministic import _option
    from app.scoring.synonyms import alternatives
    for opt in alternatives(skill):
        credit, status, *_ = _option(opt, text, "", 0.5)
        if status == "evidenced":
            return opt
    return None


def _job_for(heads: list[str | None], resume: Resume, ai_job) -> int | None:
    from app.parsing.structure import company_matches
    for h in heads:
        if not h:
            continue
        name = re.sub(r"^\s*(company|employer|section)\s*[:\-]\s*", "", h, flags=re.IGNORECASE)
        for i, j in enumerate(resume.experience):
            if j.company and company_matches(j.company, name):
                return i
    if isinstance(ai_job, int) and 0 <= ai_job < len(resume.experience):
        return ai_job
    return None


def check(raw: dict, want: list[str], lines, resume: Resume, word_cap: int) -> list[FoundEvidence]:
    """Keeps only what code can verify (see the module docstring)."""
    from app.scoring.synonyms import canonical
    by_id = {i: (h, t) for i, h, t in lines}
    asked = {canonical(g).lower(): g for g in want}
    out: list[FoundEvidence] = []
    done: set[str] = set()
    for f in (raw.get("found") or []) if isinstance(raw, dict) else []:
        if not isinstance(f, dict):
            continue
        gap = asked.get(canonical(str(f.get("gap") or "")).lower())
        ids = [i for i in (f.get("notes") or []) if isinstance(i, str) and i in by_id][:3]
        bullet = re.sub(r"\s+", " ", str(f.get("bullet") or "")).strip().lstrip("-•* ").strip()
        if not gap or gap in done or not ids or not bullet:
            continue
        quotes = [by_id[i][1] for i in ids]
        if not _shows(gap, bullet):
            log.info("evidence for %s dropped: the bullet doesn't name it", gap)
            continue
        said = {n.replace(",", "") for q in quotes for n in _NUM.findall(q)}
        if {n.replace(",", "") for n in _NUM.findall(bullet)} - said:
            log.info("evidence for %s dropped: a number the notes don't have", gap)
            continue
        if len(bullet.split()) > word_cap:
            continue
        job = _job_for([by_id[i][0] for i in ids], resume, f.get("job"))
        if job is None:
            continue
        j = resume.experience[job]
        out.append(FoundEvidence(skill=gap, job=job, bullet=bullet[0].upper() + bullet[1:], quotes=quotes,
                                 job_label=" - ".join(x for x in (j.title, j.company) if x)))
        done.add(gap)
    return out


async def find(llm: LLMClient, d: Path, resume: Resume, jd: JobDescription, score: ScoreResult,
               ctx: ProjectContext, word_cap: int = 32) -> list[FoundEvidence]:
    """Cached per run: the AI is only asked again when the gaps, your notes or the jobs change.
    Never fails the caller: if the AI can't be reached, nothing is found."""
    want = gaps(score)
    lines = note_lines(ctx)
    if not want or not lines:
        return []
    key = _key(want, lines, resume)
    f = d / FILE
    try:
        cached = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        if cached.get("key") == key:
            return [FoundEvidence(**x) for x in cached.get("found", [])]
    except (OSError, ValueError, TypeError):
        pass
    try:
        raw = await llm.run_skill(SKILL, _input(jd, resume, want, lines))
    except Exception as e:      # the AI being busy must not block the gate
        log.warning("evidence step failed: %s", e)
        return []
    found = check(raw, want, lines, resume, word_cap)
    f.write_text(json.dumps({"key": key, "found": [x.model_dump() for x in found]}, indent=1,
                            ensure_ascii=False), encoding="utf-8")
    return found


def load(d: Path) -> list[FoundEvidence]:
    try:
        data = json.loads((d / FILE).read_text(encoding="utf-8"))
        return [FoundEvidence(**x) for x in data.get("found", [])]
    except (OSError, ValueError, TypeError):
        return []


def still_needed(found: list[FoundEvidence], resume: Resume) -> list[FoundEvidence]:
    """The ones this version of the resume still doesn't show."""
    from app.scoring.render import evidence_text
    ev = evidence_text(resume)
    return [f for f in found if f.job < len(resume.experience) and not _shows(f.skill, ev)]
