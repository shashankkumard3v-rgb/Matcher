"""My resumes: the saved resumes of the signed-in user (MongoDB), and starting a run from one.

A saved resume = the structured resume + its text + the strict blind profile (profile.py) +
the original file (GridFS). Runs take their own copy, so deleting a saved resume never breaks
a run.
"""
from __future__ import annotations

import json
from pathlib import Path

from app import config
from app.data.store import get_data_store
from app.library.match import MatchResult, match
from app.library.profile import PROFILE_VERSION, ResumeProfile, build_profile, capability_vocab
from app.llm.base import LLMClient
from app.parsing.extract import extract_file
from app.parsing.structure import structure_jd, structure_resume
from app.schemas.models import JobDescription, ParseResult, ParseWarning, Resume
from app.scoring.render import render_resume

ALLOWED = {".pdf"}
MAX_NAME = 80


class LibraryError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _uid() -> str:
    return config.current_user_id() or "local"


def _clean_name(name: str | None, fallback: str) -> str:
    name = (name or "").strip()[:MAX_NAME]
    return name or fallback


def _outdated(doc: dict) -> bool:
    p = doc.get("profile") or {}
    return p.get("version", 0) < PROFILE_VERSION or p.get("capabilities_version", 0) != capability_vocab()[0]


def summary(doc: dict) -> dict:
    """What the list shows: no big text, top skills and capabilities."""
    p = doc.get("profile") or {}
    return {
        "id": doc["id"], "name": doc.get("name", ""), "notes": doc.get("notes", ""),
        "created_at": doc.get("created_at"), "updated_at": doc.get("updated_at"),
        "source": doc.get("source", {}), "has_file": bool(doc.get("file_id")),
        "skills": p.get("skills", []), "capabilities": p.get("capabilities", []),
        "outdated": _outdated(doc),
    }


def list_resumes() -> list[dict]:
    return [summary(d) for d in get_data_store().list_resumes(_uid())]


def get(rid: str) -> dict:
    d = get_data_store().get_resume(_uid(), rid)
    if d is None:
        raise LibraryError("no_resume", "That saved resume doesn't exist (or isn't yours).")
    return d


async def add_upload(llm: LLMClient, filename: str, data: bytes, name: str | None, notes: str = "") -> dict:
    x = extract_file(filename, data, ALLOWED)          # scanned/empty PDFs are refused here
    resume, _ = await structure_resume(llm, x.text)
    profile = await build_profile(llm, resume)
    store = get_data_store()
    fid = store.put_file(_uid(), Path(filename).name, data, "application/pdf")
    rid = store.insert_resume(_uid(), {
        "name": _clean_name(name, Path(filename).stem), "notes": (notes or "").strip(),
        "source": {"type": "upload", "filename": Path(filename).name},
        "resume": resume.model_dump(), "text": x.text, "format_warnings": [c for c, _ in x.warnings],
        "profile": profile.model_dump(), "file_id": fid,
    })
    return summary(get(rid))


async def add_from_run(llm: LLMClient, run_dir: Path, version: int | None, name: str | None,
                       notes: str = "") -> dict:
    from app.export import export as export_mod
    from app.loop import loop as loop_mod
    s = loop_mod.load_state(run_dir)
    if s is None:
        raise LibraryError("no_loop", "Improve the resume first; then save a version.")
    v = s.final_version if version is None and s.final_version is not None else (
        s.current_version if version is None else version)
    if not any(x.n == v for x in s.versions):
        raise LibraryError("no_version", f"No version v{v} in this run.")
    resume = loop_mod.load_version(run_dir, v)
    profile = await build_profile(llm, resume)
    jd = json.loads((run_dir / "jd.json").read_text(encoding="utf-8"))
    store = get_data_store()
    fid = None
    exp = export_mod.load_state(run_dir)
    if exp is not None and exp.version == v and "docx" in exp.files:   # keep the Word file too
        p = run_dir / "export" / exp.files["docx"]
        if p.exists():
            fid = store.put_file(_uid(), p.name, p.read_bytes(),
                                 "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    fallback = " - ".join(x for x in (jd.get("title"), jd.get("company")) if x) or f"Resume v{v}"
    rid = store.insert_resume(_uid(), {
        "name": _clean_name(name, fallback), "notes": (notes or "").strip(),
        "source": {"type": "run", "run_id": run_dir.name, "version": v,
                   "job": {"title": jd.get("title"), "company": jd.get("company")}},
        "resume": resume.model_dump(), "text": render_resume(resume), "format_warnings": [],
        "profile": profile.model_dump(), "file_id": fid,
    })
    return summary(get(rid))


def update(rid: str, name: str | None = None, notes: str | None = None) -> dict:
    fields = {}
    if name is not None:
        fields["name"] = _clean_name(name, get(rid).get("name", "Resume"))
    if notes is not None:
        fields["notes"] = notes.strip()
    if fields and not get_data_store().update_resume(_uid(), rid, fields):
        raise LibraryError("no_resume", "That saved resume doesn't exist (or isn't yours).")
    return summary(get(rid))


async def rebuild_profile(llm: LLMClient, rid: str) -> dict:
    d = get(rid)
    profile = await build_profile(llm, Resume.model_validate(d["resume"]))
    get_data_store().update_resume(_uid(), rid, {"profile": profile.model_dump()})
    return summary(get(rid))


def delete(rid: str) -> None:
    if not get_data_store().delete_resume(_uid(), rid):
        raise LibraryError("no_resume", "That saved resume doesn't exist (or isn't yours).")


def file(rid: str) -> tuple[str, bytes, str]:
    d = get(rid)
    f = get_data_store().get_file(_uid(), d["file_id"]) if d.get("file_id") else None
    if f is None:
        raise LibraryError("no_file", "This saved resume has no file.")
    return f


# ---------- JD first: rank the saved resumes, then start a run from the one you pick ----------

async def rank_for_jd(llm: LLMClient, jd_text: str) -> dict:
    from app.parsing.extract import extract_text
    x = extract_text(jd_text)                         # too short / too long are refused here
    jd, warnings = await structure_jd(llm, x.text)
    results: list[MatchResult] = []
    for d in get_data_store().list_resumes(_uid()):
        r = match(ResumeProfile.model_validate(d.get("profile") or {}), Resume.model_validate(d["resume"]), jd)
        r.resume_id, r.name = d["id"], d.get("name", "")
        results.append(r)
    results.sort(key=lambda r: -r.score)
    return {"jd": jd.model_dump(), "jd_text": x.text, "warnings": [w.model_dump() for w in warnings],
            "results": [r.model_dump() for r in results]}


def start_run(rid: str, jd: JobDescription, jd_text: str) -> ParseResult:
    """A new run from a saved resume: no upload, no re-reading of the resume."""
    from app.parsing.pipeline import context_warnings, load_project_context, new_run_dir
    d = get(rid)
    resume = Resume.model_validate(d["resume"])
    ctx = load_project_context()
    warnings = [ParseWarning(source="resume", code=c, message="Layout issue found when this resume was uploaded.")
                for c in d.get("format_warnings", [])] + context_warnings(resume, ctx)
    run_dir = new_run_dir(jd.company, jd.title)
    (run_dir / "inputs").mkdir()
    f = get_data_store().get_file(_uid(), d["file_id"]) if d.get("file_id") else None
    if f is not None:
        (run_dir / "inputs" / Path(f[0]).name).write_bytes(f[1])
    (run_dir / "inputs" / "jd.txt").write_text(jd_text, encoding="utf-8")
    (run_dir / "resume_text.txt").write_text(d.get("text") or render_resume(resume), encoding="utf-8")
    (run_dir / "jd_text.txt").write_text(jd_text, encoding="utf-8")
    for name, obj in (("resume.json", resume), ("jd.json", jd), ("projects.json", ctx)):
        (run_dir / name).write_text(obj.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "parse_warnings.json").write_text(json.dumps([w.model_dump() for w in warnings], indent=2),
                                                 encoding="utf-8")
    (run_dir / "library.json").write_text(json.dumps({"resume_id": rid, "name": d.get("name")}), encoding="utf-8")
    return ParseResult(run_id=run_dir.name, resume=resume, jd=jd, projects=ctx, warnings=warnings)
