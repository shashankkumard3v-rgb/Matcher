"""API for My resumes and the JD-first new run."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from app.api.routes import MAX_UPLOAD_BYTES, _run, get_llm
from app.library import library as lib
from app.llm.base import LLMClient, LLMError
from app.parsing.extract import ExtractionError
from app.schemas.models import JobDescription, ParseResult

router = APIRouter()


def _err(e: Exception) -> HTTPException:
    if isinstance(e, lib.LibraryError):
        return HTTPException(404 if e.code.startswith("no_") else 409, {"code": e.code, "message": str(e)})
    if isinstance(e, ExtractionError):
        return HTTPException(422, {"code": e.code, "message": str(e)})
    if isinstance(e, LLMError):
        return HTTPException(502, {"code": "llm_failed", "message": str(e)})
    raise e


@router.get("/resumes")
async def resumes_list():
    """Your saved resumes, newest first, with their skill and capability levels."""
    return lib.list_resumes()


@router.post("/resumes")
async def resumes_upload(file: UploadFile = File(...), name: str = Form(""), notes: str = Form(""),
                         llm: LLMClient = Depends(get_llm)):
    """Upload a resume PDF: it is read, profiled (strict, blind) and saved to My resumes."""
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, {"code": "too_big", "message": "The file is larger than 10 MB."})
    try:
        return await lib.add_upload(llm, file.filename or "resume.pdf", data, name, notes)
    except Exception as e:
        raise _err(e) from e


@router.get("/resumes/{rid}")
async def resumes_get(rid: str):
    try:
        d = lib.get(rid)
    except Exception as e:
        raise _err(e) from e
    return {**lib.summary(d), "resume": d.get("resume"), "profile": d.get("profile")}


class ResumeUpdate(BaseModel):
    name: str | None = None
    notes: str | None = None


@router.patch("/resumes/{rid}")
async def resumes_update(rid: str, body: ResumeUpdate):
    try:
        return lib.update(rid, body.name, body.notes)
    except Exception as e:
        raise _err(e) from e


@router.delete("/resumes/{rid}")
async def resumes_delete(rid: str):
    try:
        lib.delete(rid)
    except Exception as e:
        raise _err(e) from e
    return {"ok": True}


@router.post("/resumes/{rid}/profile")
async def resumes_reprofile(rid: str, llm: LLMClient = Depends(get_llm)):
    """Build the description again (e.g. after the capability list changed)."""
    try:
        return await lib.rebuild_profile(llm, rid)
    except Exception as e:
        raise _err(e) from e


@router.get("/resumes/{rid}/file")
async def resumes_file(rid: str):
    try:
        name, data, ctype = lib.file(rid)
    except Exception as e:
        raise _err(e) from e
    return Response(data, media_type=ctype, headers={"Content-Disposition": f'attachment; filename="{name}"'})


class SaveFromRun(BaseModel):
    version: int | None = None
    name: str | None = None
    notes: str = ""


@router.post("/runs/{run_id}/save-resume")
async def runs_save_resume(run_id: str, body: SaveFromRun, llm: LLMClient = Depends(get_llm)):
    """Save a version of this run (the downloaded one by default) to My resumes."""
    try:
        return await lib.add_from_run(llm, _run(run_id), body.version, body.name, body.notes)
    except Exception as e:
        raise _err(e) from e


class MatchBody(BaseModel):
    jd_text: str


@router.post("/match")
async def match_jd(body: MatchBody, llm: LLMClient = Depends(get_llm)):
    """Read a pasted job description and rank your saved resumes for it (quick, code-only score)."""
    try:
        return await lib.rank_for_jd(llm, body.jd_text)
    except Exception as e:
        raise _err(e) from e


class StartBody(BaseModel):
    resume_id: str
    jd: JobDescription
    jd_text: str


@router.post("/runs/start", response_model=ParseResult)
async def runs_start(body: StartBody):
    """Start a run with the saved resume you picked (no upload, nothing re-read)."""
    try:
        return lib.start_run(body.resume_id, body.jd, body.jd_text)
    except Exception as e:
        raise _err(e) from e
