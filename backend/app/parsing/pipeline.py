"""Step 1: parse inputs -> resume.json, jd.json, projects.json in a new run folder."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path

from app import config
from app.llm.base import LLMClient
from app.parsing.extract import (
    ALLOWED_JD_TYPES,
    ALLOWED_RESUME_TYPES,
    ExtractionError,
    extract_file,
    extract_text,
)
from app.parsing.projects import parse_project_context
from app.parsing.structure import _norm, company_matches, structure_jd, structure_resume
from app.schemas.models import ParseResult, ParseWarning, ProjectContext, Resume


def _slug(s: str | None, fallback: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", s or "").strip("-")
    return s[:30] or fallback


def new_run_dir(company: str | None, role: str | None) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = f"{stamp}_{_slug(company, 'company')}_{_slug(role, 'role')}"
    config.runs_dir().mkdir(parents=True, exist_ok=True)
    for n in range(1, 100):  # two runs in the same second (e.g. a double tap) get -2, -3, ...
        d = config.runs_dir() / (base if n == 1 else f"{base}-{n}")
        try:
            d.mkdir(exist_ok=False)
            return d
        except FileExistsError:
            continue
    raise RuntimeError("Couldn't create a run folder")


def load_project_context() -> ProjectContext:
    from app.context import storage
    return parse_project_context(storage.read_text("template"))


def cross_check_employers(resume: Resume, ctx: ProjectContext) -> list[ParseWarning]:
    """Saved answers (projects) for a job that isn't on this resume are simply not used for it:
    that's normal when you keep several resumes, so it isn't a warning. You can see and remove
    saved answers in My info -> Saved answers."""
    return []


def _jobs_list(companies: list[str]) -> str:
    return "its jobs: " + ", ".join(companies) if companies else "it has no jobs with a company name"


def cross_check_note_companies(resume: Resume) -> list[ParseWarning]:
    """Each "Company:" block in your experience notes should match a job on the resume,
    otherwise its facts can't go under that job."""
    from app.context import notes
    companies = [j.company for j in resume.experience if j.company]
    out: list[ParseWarning] = []
    for line in notes.read_raw("experience").splitlines():
        m = re.match(r"^\s*(?:company|employer)\s*[:\-]\s*(.+)$", line, re.IGNORECASE)
        if not m or "[" in m.group(1):
            continue
        name = m.group(1).strip()
        if name and not any(company_matches(name, c) for c in companies):
            out.append(ParseWarning(
                source="projects", code="employer_not_on_resume",
                message=(f"Your experience notes have 'Company: {name}', which doesn't match any job on this "
                         f"resume ({_jobs_list(companies)}). Notes under it can't be used for this resume. "
                         "If it's one of those jobs, write the name the same way in My info; if it's a job "
                         "this resume leaves out, you can ignore this."),
            ))
    return out


def context_warnings(resume: Resume, ctx: ProjectContext) -> list[ParseWarning]:
    """Checks of your My info notes / template against this resume."""
    out = [ParseWarning(source="projects", code=f"missing_{i.field}", message=i.message) for i in ctx.issues]
    out += cross_check_employers(resume, ctx)
    out += cross_check_note_companies(resume)
    from app.context import notes
    if not ctx.projects and not any(notes.all_facts().values()):
        out.append(ParseWarning(
            source="projects", code="no_projects",
            message=("Your experience and skills notes (My info page) are empty, so Matcher can only "
                     "reword what's already on your resume. Add your experience there to get new bullets."),
        ))
    return out


async def parse_inputs(
    llm: LLMClient,
    resume_name: str,
    resume_bytes: bytes,
    jd_name: str | None = None,
    jd_bytes: bytes | None = None,
    jd_text: str | None = None,
) -> tuple[ParseResult, Path]:
    """The JD comes as pasted text (what the app sends) or as a file (.docx/PDF, API only)."""
    # 1) deterministic extraction (raises ExtractionError on unusable input)
    resume_x = extract_file(resume_name, resume_bytes, ALLOWED_RESUME_TYPES)
    if jd_text is not None and jd_text.strip():
        jd_x = extract_text(jd_text)
        jd_name, jd_bytes = "jd.txt", jd_text.encode("utf-8")
    elif jd_bytes:
        jd_x = extract_file(jd_name or "jd", jd_bytes, ALLOWED_JD_TYPES)
    else:
        raise ExtractionError("no_jd", "Paste the job description.")

    warnings: list[ParseWarning] = [
        ParseWarning(source="resume", code=c, message=m) for c, m in resume_x.warnings
    ] + [ParseWarning(source="jd", code=c, message=m) for c, m in jd_x.warnings]

    # 2) LLM structuring (resume and JD in parallel)
    (resume, rw), (jd, jw) = await asyncio.gather(
        structure_resume(llm, resume_x.text),
        structure_jd(llm, jd_x.text),
    )
    warnings += rw + jw

    # 3) project context (deterministic)
    ctx = load_project_context()
    warnings += context_warnings(resume, ctx)

    # 4) save the run
    run_dir = new_run_dir(jd.company, jd.title)
    result = ParseResult(run_id=run_dir.name, resume=resume, jd=jd, projects=ctx, warnings=warnings)
    (run_dir / "inputs").mkdir()
    (run_dir / "inputs" / Path(resume_name).name).write_bytes(resume_bytes)
    (run_dir / "inputs" / Path(jd_name).name).write_bytes(jd_bytes)
    (run_dir / "resume_text.txt").write_text(resume_x.text, encoding="utf-8")
    (run_dir / "jd_text.txt").write_text(jd_x.text, encoding="utf-8")
    for name, obj in (("resume.json", resume), ("jd.json", jd), ("projects.json", ctx)):
        (run_dir / name).write_text(obj.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "parse_warnings.json").write_text(
        json.dumps([w.model_dump() for w in warnings], indent=2), encoding="utf-8"
    )
    return result, run_dir
