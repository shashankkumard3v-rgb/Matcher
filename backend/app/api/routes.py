"""HTTP API. The phone/web app (frontend) talks to these endpoints."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app import config
from app.llm.base import LLMClient, LLMError
from app.llm.factory import make_llm, provider_name
from app.parsing.extract import ExtractionError
from app.parsing.pipeline import load_project_context, parse_inputs
from app.parsing.projects import parse_project_context
from app.schemas.models import ParseResult, ProjectContext
from app import runs
from app.schemas.models import ScoreResult
from app.scoring.scorer import ScoreError, score_resume
from app.skills.loader import SkillError, all_skills

router = APIRouter()

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def get_llm() -> LLMClient:
    try:
        return make_llm()
    except LLMError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/skills")
async def list_skills():
    """The step instructions (SKILL.md files), the active provider and how skills run on it."""
    from app.config import models_config
    from app.llm.factory import available
    try:
        provider = provider_name()
    except LLMError as e:
        provider = f"none ({e})"
    if provider == "claude":
        setting = str(models_config().get("claude", {}).get("agent_skills", "auto")).lower()
        how = ("SKILL.md sent as a prompt (agent_skills: off)" if setting == "off" else
               "Claude Agent Skills" + (" (falls back to prompts if Skills aren't enabled)"
                                        if setting == "auto" else ""))
    else:
        how = "SKILL.md (+ extra files in its folder) sent as the prompt"
    try:
        skills = all_skills()
    except SkillError as e:
        raise HTTPException(500, str(e))
    return {
        "provider": provider,
        "available_providers": available(),
        "how": how,
        "skills": [{"name": s.name, "step": s.step, "description": s.description,
                    "hash": s.content_hash} for s in skills],
    }


@router.post("/skills/sync")
async def sync_skills(llm: LLMClient = Depends(get_llm)):
    """Claude mode only: upload new/changed skills to your Anthropic account now."""
    if getattr(llm, "mode", "") != "skills":
        raise HTTPException(400, "Skill upload only applies when using the Claude API key.")
    try:
        return await llm.sync_all()
    except (LLMError, SkillError) as e:
        raise HTTPException(502, str(e))
    except Exception as e:
        raise HTTPException(502, f"Skill upload failed: {e}")


async def _read(upload: UploadFile) -> bytes:
    data = await upload.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"{upload.filename} is larger than 10 MB.")
    return data


@router.post("/parse", response_model=ParseResult)
async def parse(
    resume: UploadFile = File(..., description="Your resume (PDF)"),
    jd_text: str | None = Form(None, description="Job description, pasted as text"),
    jd: UploadFile | None = File(None, description="Optional instead of jd_text: a .docx or PDF"),
    llm: LLMClient = Depends(get_llm),
):
    """Step 1: parse the resume, the job description (pasted text) and your project context."""
    try:
        result, _ = await parse_inputs(
            llm, resume.filename or "resume.pdf", await _read(resume),
            jd_name=(jd.filename if jd else None), jd_bytes=(await _read(jd) if jd else None),
            jd_text=jd_text,
        )
    except ExtractionError as e:
        raise HTTPException(status_code=422, detail={"code": e.code, "message": str(e)})
    except LLMError as e:
        raise HTTPException(status_code=502, detail={"code": "llm_failed", "message": str(e)})
    return result


class ContextText(BaseModel):
    text: str


class ContextResponse(BaseModel):
    text: str
    parsed: ProjectContext


@router.get("/context", response_model=ContextResponse)
async def get_context():
    """Your project-context file, plus how Matcher reads it."""
    from app.context import storage
    return ContextResponse(text=storage.read_text("template"), parsed=load_project_context())


@router.post("/context/preview", response_model=ProjectContext)
async def preview_context(body: ContextText):
    """Check edited text before saving (shows missing fields etc.)."""
    return parse_project_context(body.text)


@router.put("/context", response_model=ContextResponse)
async def save_context(body: ContextText):
    """Save the project-context file. A backup of the previous version is kept."""
    from app.context import storage
    storage.write_text("template", body.text)   # the previous version is kept as a backup
    return ContextResponse(text=body.text, parsed=parse_project_context(body.text))


class ScoreResponse(BaseModel):
    run_id: str
    file: str
    score: ScoreResult
    history: list[dict] = []


class ScoreBody(BaseModel):
    again: bool = False   # "Score again": a careful re-check (the AI judge reads it 3 times)


AGAIN_SAMPLES = 3


def score_history(d) -> list[dict]:
    out = []
    for f in runs.score_files(d):
        s = _read_json(f) or {}
        out.append({"n": int(f.stem.split("_")[1]), "total": s.get("total"),
                    "rules": s.get("deterministic_points"), "ai": s.get("llm_points")})
    return out


@router.post("/runs/{run_id}/score", response_model=ScoreResponse)
async def score_run(run_id: str, body: ScoreBody | None = None, llm: LLMClient = Depends(get_llm)):
    """Score the run's resume against its JD (blind judge, pinned per run).
    With again=true it's a careful re-check: the judge reads it 3 times and the median of each
    part counts. The newest score becomes the run's current score everywhere (Step 1 is told to
    recalculate). Once improving has started, versions are scored in Step 2 instead."""
    try:
        d = runs.run_dir(run_id)
    except runs.RunNotFound as e:
        raise HTTPException(404, str(e))
    again = bool(body and body.again)
    if again and (d / "loop.json").exists():
        raise HTTPException(409, {"code": "improving", "message":
            "You've started improving this resume: each version has its own score in Step 2, and "
            "Step 3 has 'Check the score again (fresh)'."})
    pins = runs.load_pins(d)
    try:
        result = await score_resume(
            llm, runs.load_resume(d), runs.load_jd(d),
            format_flags=runs.original_format_flags(d), pins=pins,
            samples=AGAIN_SAMPLES if again else None,
        )
    except ScoreError as e:
        raise HTTPException(409, {"code": "settings_changed", "message": str(e)})
    except LLMError as e:
        raise HTTPException(502, {"code": "llm_failed", "message": str(e)})
    if pins is None:
        runs.save_pins(d, result.pins)
    name = runs.save_score(d, result)
    g = gate_mod.load_state(d)
    if g is not None and g.current_score != result.total:
        g.needs_recompute = True        # Step 1 was worked out from the old score
        gate_mod.save_state(d, g)
    return ScoreResponse(run_id=run_id, file=name, score=result, history=score_history(d))


# ---------- Step 3: interactive apply gate ----------

from app.gate import gate as gate_mod  # noqa: E402
from app.schemas.models import ContextProposal, GateState  # noqa: E402


def _gate_errors(e: Exception):
    if isinstance(e, gate_mod.GateError):
        code = 404 if e.code.startswith("no_") else 409
        return HTTPException(code, {"code": e.code, "message": str(e)})
    if isinstance(e, ScoreError):
        return HTTPException(409, {"code": "settings_changed", "message": str(e)})
    if isinstance(e, LLMError):
        return HTTPException(502, {"code": "llm_failed", "message": str(e)})
    raise e


def _run(run_id: str):
    try:
        return runs.run_dir(run_id)
    except runs.RunNotFound as e:
        raise HTTPException(404, str(e))


class AnswerBody(BaseModel):
    question_id: str
    answer: str


class TextBody(BaseModel):
    text: str


class ProposalBody(BaseModel):
    accept: bool
    edited: ContextProposal | None = None  # optional: your edited version


class OverrideBody(BaseModel):
    target: str          # a knockout id, or "verdict"
    reason: str = ""


class DecideBody(BaseModel):
    decision: str        # "proceed" | "stop"
    reason: str = ""


@router.post("/runs/{run_id}/gate", response_model=GateState)
async def gate_compute(run_id: str, llm: LLMClient = Depends(get_llm)):
    """Compute (or recompute) the verdict, questions and explanation."""
    try:
        return await gate_mod.compute(llm, _run(run_id))
    except Exception as e:
        raise _gate_errors(e)


@router.get("/runs/{run_id}/gate", response_model=GateState)
async def gate_get(run_id: str):
    state = gate_mod.load_state(_run(run_id))
    if state is None:
        raise HTTPException(404, {"code": "no_gate", "message": "Gate not computed yet."})
    return state


@router.post("/runs/{run_id}/gate/answer", response_model=GateState)
async def gate_answer(run_id: str, body: AnswerBody, llm: LLMClient = Depends(get_llm)):
    """Answer a question. Your answer becomes a proposed template change to accept or reject."""
    try:
        return await gate_mod.answer(llm, _run(run_id), body.question_id, body.answer)
    except Exception as e:
        raise _gate_errors(e)


@router.post("/runs/{run_id}/gate/skip/{question_id}", response_model=GateState)
async def gate_skip(run_id: str, question_id: str):
    try:
        return gate_mod.skip(_run(run_id), question_id)
    except Exception as e:
        raise _gate_errors(e)


@router.post("/runs/{run_id}/gate/context", response_model=GateState)
async def gate_context(run_id: str, body: TextBody, llm: LLMClient = Depends(get_llm)):
    """Tell Matcher anything else about your work; it becomes a proposed template change."""
    try:
        return await gate_mod.add_context(llm, _run(run_id), body.text)
    except Exception as e:
        raise _gate_errors(e)


@router.post("/runs/{run_id}/gate/proposals/{proposal_id}", response_model=GateState)
async def gate_proposal(run_id: str, proposal_id: str, body: ProposalBody):
    """Accept (writes your template) or reject a proposed change."""
    try:
        return gate_mod.resolve_proposal(_run(run_id), proposal_id, body.accept, body.edited)
    except Exception as e:
        raise _gate_errors(e)


@router.post("/runs/{run_id}/gate/override", response_model=GateState)
async def gate_override(run_id: str, body: OverrideBody):
    """Switch off a knockout, or the whole verdict."""
    try:
        return gate_mod.override(_run(run_id), body.target, body.reason)
    except Exception as e:
        raise _gate_errors(e)


@router.post("/runs/{run_id}/gate/decide", response_model=GateState)
async def gate_decide(run_id: str, body: DecideBody):
    """Proceed to improving, or stop. Proceeding past DON'T APPLY needs a reason."""
    try:
        return gate_mod.decide(_run(run_id), body.decision, body.reason)
    except Exception as e:
        raise _gate_errors(e)


# ---------- Step 4: interactive improve loop (text only) ----------

from app.loop import loop as loop_mod  # noqa: E402
from app.schemas.models import LoopState  # noqa: E402


def _loop_errors(e: Exception):
    if isinstance(e, loop_mod.LoopError):
        code = 404 if e.code.startswith("no_") and e.code not in ("no_suggestions",) else 409
        return HTTPException(code, {"code": e.code, "message": str(e)})
    return _gate_errors(e)


class ChangeBody(BaseModel):
    action: str               # accept | reject | edit | revise
    text: str | None = None   # for edit: your wording
    feedback: str | None = None  # for revise: what to change


class VersionBody(BaseModel):
    version: int | None = None


async def _loop_call(fn, *args):
    try:
        out = fn(*args)
        if hasattr(out, "__await__"):
            out = await out
        return out
    except Exception as e:
        raise _loop_errors(e)


class TargetBody(BaseModel):
    target: float | None = None   # e.g. 80, 85, 90, 95 (default from config/weights.yaml)


@router.post("/runs/{run_id}/loop/start", response_model=LoopState)
async def loop_start(run_id: str, body: TargetBody | None = None, llm: LLMClient = Depends(get_llm)):
    """Begin improving (needs the gate decision 'proceed'). Opens round 1."""
    return await _loop_call(loop_mod.start, llm, _run(run_id), body.target if body else None)


@router.post("/runs/{run_id}/loop/target", response_model=LoopState)
async def loop_target(run_id: str, body: TargetBody):
    """Change the score you're aiming for."""
    if body.target is None:
        raise HTTPException(422, {"code": "bad_target", "message": "Give a target, e.g. 85."})
    return await _loop_call(loop_mod.set_target, _run(run_id), body.target)


@router.get("/runs/{run_id}/loop", response_model=LoopState)
async def loop_get(run_id: str):
    s = loop_mod.load_state(_run(run_id))
    if s is None:
        raise HTTPException(404, {"code": "no_loop", "message": "Loop not started."})
    return s


@router.get("/runs/{run_id}/loop/versions/{n}")
async def loop_version(run_id: str, n: int):
    """A version as text plus its score."""
    d = _run(run_id)
    p = d / "versions" / f"resume_v{n}.txt"
    if not p.exists():
        raise HTTPException(404, {"code": "no_version", "message": f"No version v{n}."})
    score = json.loads((d / "versions" / f"score_v{n}.json").read_text(encoding="utf-8"))
    return {"version": n, "text": p.read_text(encoding="utf-8"), "score": score}


@router.get("/runs/{run_id}/loop/bullets")
async def loop_bullets(run_id: str, version: int | None = None):
    """Every bullet in a version: what it earns for this job, where it came from (resume, or
    which round and My info section), and how many bullets each job may have."""
    from app.loop import bullets as bullets_mod
    d = _run(run_id)
    s = loop_mod.load_state(d)
    if s is None:
        raise HTTPException(404, {"code": "no_loop", "message": "Loop not started."})
    n = s.current_version if version is None else version
    if not any(v.n == n for v in s.versions):
        raise HTTPException(404, {"code": "no_version", "message": f"No version v{n}."})
    score_p = d / "versions" / f"score_v{n}.json"
    score = ScoreResult.model_validate_json(score_p.read_text(encoding="utf-8")) if score_p.exists() else None
    return bullets_mod.analyze(loop_mod.load_version(d, n), runs.load_jd(d), score, loop_mod._cfg(),
                               original=loop_mod.load_version(d, 0), state=s, version=n)


@router.post("/runs/{run_id}/loop/answer", response_model=LoopState)
async def loop_answer(run_id: str, body: AnswerBody, llm: LLMClient = Depends(get_llm)):
    return await _loop_call(loop_mod.answer, llm, _run(run_id), body.question_id, body.answer)


@router.post("/runs/{run_id}/loop/skip/{question_id}", response_model=LoopState)
async def loop_skip(run_id: str, question_id: str):
    return await _loop_call(loop_mod.skip, _run(run_id), question_id)


@router.post("/runs/{run_id}/loop/context", response_model=LoopState)
async def loop_context(run_id: str, body: TextBody, llm: LLMClient = Depends(get_llm)):
    return await _loop_call(loop_mod.add_context, llm, _run(run_id), body.text)


@router.post("/runs/{run_id}/loop/proposals/{proposal_id}", response_model=LoopState)
async def loop_proposal(run_id: str, proposal_id: str, body: ProposalBody):
    return await _loop_call(loop_mod.resolve_proposal, _run(run_id), proposal_id, body.accept, body.edited)


@router.post("/runs/{run_id}/loop/notes", response_model=LoopState)
async def loop_notes(run_id: str, body: TextBody):
    """Style input for this round's rewrite (tone, length, emphasis). Never used as facts."""
    return await _loop_call(loop_mod.add_note, _run(run_id), body.text)


@router.post("/runs/{run_id}/loop/suggest", response_model=LoopState)
async def loop_suggest(run_id: str, llm: LLMClient = Depends(get_llm)):
    """Get sourced, truth-checked edit suggestions for this round."""
    return await _loop_call(loop_mod.suggest, llm, _run(run_id))


@router.post("/runs/{run_id}/loop/changes/{change_id}", response_model=LoopState)
async def loop_change(run_id: str, change_id: str, body: ChangeBody, llm: LLMClient = Depends(get_llm)):
    """accept / reject / edit (your text) / revise (AI redoes it using your feedback)."""
    return await _loop_call(loop_mod.change_action, llm, _run(run_id), change_id,
                            body.action, body.text, body.feedback)


@router.post("/runs/{run_id}/loop/apply", response_model=LoopState)
async def loop_apply(run_id: str, llm: LLMClient = Depends(get_llm)):
    """Apply accepted edits -> new version -> blind re-score -> advice flags."""
    return await _loop_call(loop_mod.apply, llm, _run(run_id))


@router.post("/runs/{run_id}/loop/next", response_model=LoopState)
async def loop_next(run_id: str):
    return await _loop_call(loop_mod.next_round, _run(run_id))


@router.post("/runs/{run_id}/loop/rollback", response_model=LoopState)
async def loop_rollback(run_id: str, body: VersionBody):
    if body.version is None:
        raise HTTPException(422, "version is required")
    return await _loop_call(loop_mod.rollback, _run(run_id), body.version)


@router.post("/runs/{run_id}/loop/finish", response_model=LoopState)
async def loop_finish(run_id: str, body: VersionBody):
    """Finish, keeping the current version (or the version you name) for export."""
    return await _loop_call(loop_mod.finish, _run(run_id), body.version)


# ---------- Step 5: one-page Word export ----------

from fastapi.responses import FileResponse  # noqa: E402

from app.export import export as export_mod  # noqa: E402
from app.export.render import RenderError  # noqa: E402
from app.schemas.models import ExportState  # noqa: E402


def _export_errors(e: Exception):
    if isinstance(e, export_mod.ExportError):
        return HTTPException(404 if e.code == "no_export" else 409, {"code": e.code, "message": str(e)})
    if isinstance(e, RenderError):
        return HTTPException(500, {"code": "no_renderer", "message": str(e)})
    if isinstance(e, ExtractionError):
        return HTTPException(422, {"code": e.code, "message": str(e)})
    return _loop_errors(e)


class TrimBody(BaseModel):
    option_ids: list[str]


@router.post("/runs/{run_id}/export", response_model=ExportState)
async def export_build(run_id: str):
    """Build the one-page A4 Word resume from the loop's final version."""
    try:
        return await export_mod.build(_run(run_id))
    except Exception as e:
        raise _export_errors(e)


@router.get("/runs/{run_id}/export", response_model=ExportState)
async def export_get(run_id: str):
    s = export_mod.load_state(_run(run_id))
    if s is None:
        raise HTTPException(404, {"code": "no_export", "message": "Not exported yet."})
    return s


@router.post("/runs/{run_id}/export/trim", response_model=ExportState)
async def export_trim(run_id: str, body: TrimBody, llm: LLMClient = Depends(get_llm)):
    """Too long: cut the bullets you choose -> new version, blind re-score, rebuild."""
    try:
        return await export_mod.trim(llm, _run(run_id), body.option_ids)
    except Exception as e:
        raise _export_errors(e)


@router.post("/runs/{run_id}/export/reopen-loop", response_model=LoopState)
async def export_reopen(run_id: str):
    """Too short: go back to the improve loop to add content (a note says how much room)."""
    try:
        return export_mod.reopen_loop(_run(run_id))
    except Exception as e:
        raise _export_errors(e)


@router.get("/runs/{run_id}/export/preview.png")
async def export_preview(run_id: str):
    """Page 1 of the exported resume as an image, to look at before downloading."""
    try:
        path = export_mod.preview_png(_run(run_id))
    except Exception as e:
        raise _export_errors(e)
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-cache"})


@router.post("/runs/{run_id}/export/fresh-score")
async def export_fresh_score(run_id: str, llm: LLMClient = Depends(get_llm)):
    """Score the exported file again from scratch (read like an ATS, blind judge, no history)."""
    try:
        return await export_mod.fresh_score(llm, _run(run_id))
    except Exception as e:
        raise _export_errors(e)


@router.get("/runs/{run_id}/export/fresh-scores")
async def export_fresh_scores(run_id: str):
    return export_mod.fresh_scores(_run(run_id))


@router.get("/runs/{run_id}/export/file/{kind}")
async def export_file(run_id: str, kind: str, inline: bool = False):
    """Download: kind = docx | pdf (preview) | report. inline=true opens the PDF in the browser."""
    d = _run(run_id)
    s = export_mod.load_state(d)
    if s is None or kind not in s.files:
        raise HTTPException(404, {"code": "no_file", "message": "No such file. Export first."})
    path = d / "export" / s.files[kind]
    media = {"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
             "pdf": "application/pdf", "report": "text/markdown"}[kind]
    return FileResponse(path, media_type=media, filename=path.name,
                        content_disposition_type="inline" if inline and kind == "pdf" else "attachment")



class FitApplyResponse(BaseModel):
    loop: LoopState
    export: ExportState


@router.post("/runs/{run_id}/export/fit/start", response_model=LoopState)
async def export_fit_start(run_id: str):
    """Agentic fit: opens a fit round (layout problems + questions). Then use the /loop
    endpoints to answer, add context or notes, get suggestions and review each edit."""
    try:
        return export_mod.fit_start(_run(run_id))
    except Exception as e:
        raise _export_errors(e)


@router.post("/runs/{run_id}/export/fit/apply", response_model=FitApplyResponse)
async def export_fit_apply(run_id: str, llm: LLMClient = Depends(get_llm)):
    """Apply accepted fit edits -> new version, blind re-score -> rebuild and measure the page."""
    try:
        out = await export_mod.fit_apply(llm, _run(run_id))
        return FitApplyResponse(**out)
    except Exception as e:
        raise _export_errors(e)



# ---------- runs (for the app's history screen) ----------

def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _run_summary(d) -> dict:
    jd = _read_json(d / "jd.json") or {}
    resume = _read_json(d / "resume.json") or {}
    files = runs.score_files(d)
    score = _read_json(files[-1]) if files else None
    gate = _read_json(d / "gate.json")
    loop = _read_json(d / "loop.json")
    export = _read_json(d / "export" / "export.json")
    if export and (not loop or loop.get("finished")):
        stage = f"export: {export.get('status')}"
    elif loop:
        stage = "finished" if loop.get("finished") else f"improving (round {len(loop.get('rounds', []))})"
    elif gate:
        stage = f"gate: {gate.get('verdict')}" + (f" ({gate['decision']})" if gate.get("decision") else "")
    elif score:
        stage = "scored"
    else:
        stage = "parsed"
    best = None
    if loop and loop.get("versions"):
        best = max(v["score"] for v in loop["versions"])
    return {
        "run_id": d.name,
        "created": d.name[:15],
        "job_title": jd.get("title"),
        "company": jd.get("company"),
        "candidate": (resume.get("contact") or {}).get("name"),
        "score": score.get("total") if score else None,
        "best_score": best,
        "stage": stage,
    }


@router.get("/runs")
async def list_runs():
    """All runs, newest first."""
    config.ensure_dirs()
    dirs = sorted((p for p in config.runs_dir().iterdir() if p.is_dir() and not p.name.startswith(".")),
                  key=lambda p: p.name, reverse=True)
    return [_run_summary(d) for d in dirs if (d / "jd.json").exists()]


@router.delete("/runs/{run_id}")
async def delete_run(run_id: str):
    """Delete a run and everything in it (versions, exports). Can't be undone."""
    try:
        runs.delete_run(run_id)
    except runs.RunNotFound as e:
        raise HTTPException(404, str(e))
    except OSError as e:   # e.g. the Word file is open in Word on Windows
        raise HTTPException(409, {"code": "in_use", "message":
            f"Couldn't delete everything: a file in this run is open in another program ({e.strerror}). "
            "Close it and try again."})
    return {"deleted": run_id}


@router.get("/runs/{run_id}")
async def get_run(run_id: str):
    """Everything the app needs to reopen a run."""
    d = _run(run_id)
    scores = sorted(d.glob("score_*.json"), key=lambda p: int(p.stem.split("_")[1]))
    return {
        **_run_summary(d),
        "resume": _read_json(d / "resume.json"),
        "jd": _read_json(d / "jd.json"),
        "projects": _read_json(d / "projects.json"),
        "warnings": _read_json(d / "parse_warnings.json") or [],
        "score": _read_json(scores[-1]) if scores else None,
        "score_history": score_history(d),
        "improving": (d / "loop.json").exists(),
    }



# ---------- which models can my key use? ----------

@router.get("/providers/{provider}/models")
async def provider_models(provider: str):
    """Lists the models your API key can use (gemini, openrouter or openai)."""
    from app.llm.openai_compat import GeminiClient, OpenAICompatClient, OpenRouterClient
    from app.llm.openai_compat import ProviderHTTPError
    cls = {"gemini": GeminiClient, "openrouter": OpenRouterClient, "openai": OpenAICompatClient}.get(provider)
    if cls is None:
        raise HTTPException(404, "provider must be gemini, openrouter or openai")
    try:
        client = cls()
        available = await client.list_models()
    except ProviderHTTPError as e:
        raise HTTPException(502, {"code": "provider_error", "message": str(e)})
    except LLMError as e:
        raise HTTPException(400, {"code": "no_key", "message": str(e)})
    configured = sorted({m for st in client.steps.values() for m in (st or {}).get("models") or []})
    return {
        "provider": provider,
        "configured": [{"model": m, "available": m in available} for m in configured],
        "all_available": available,
    }


# ---------- your notes: experience.txt and skills.txt ----------

from fastapi import Form  # noqa: E402

from app.context import notes as notes_mod  # noqa: E402


def _notes_view(kind: str) -> dict:
    text = notes_mod.read_raw(kind)
    return {"kind": kind, "text": text, "facts": notes_mod.facts(text),
            "file": f"backend/context/{kind}.txt"}


@router.get("/context/notes")
async def get_notes():
    """Both notes files (raw text with comments) and the fact lines Matcher will use."""
    config.ensure_dirs()
    return {k: _notes_view(k) for k in notes_mod.KINDS}


@router.put("/context/notes/{kind}")
async def save_notes(kind: str, body: ContextText):
    """Replace a notes file with the text you send (a .bak of the old one is kept)."""
    try:
        notes_mod.save(kind, body.text)
    except notes_mod.NotesError as e:
        raise HTTPException(400, str(e))
    return _notes_view(kind)


@router.post("/context/notes/{kind}/upload")
async def upload_notes(kind: str, file: UploadFile = File(...), mode: str = Form("append")):
    """Upload a .txt (or .md) file into experience or skills notes: mode = append | replace."""
    name = (file.filename or "").lower()
    if not name.endswith((".txt", ".md")):
        raise HTTPException(400, "Upload a .txt (or .md) file.")
    raw = await file.read()
    if len(raw) > notes_mod.MAX_BYTES:
        raise HTTPException(413, "File is larger than 200 KB.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")  # Notepad's older default on Windows
    try:
        if mode == "replace":
            notes_mod.save(kind, text)
        elif mode == "append":
            notes_mod.append(kind, text)
        else:
            raise HTTPException(400, "mode must be append or replace")
    except notes_mod.NotesError as e:
        raise HTTPException(400, str(e))
    return _notes_view(kind)


# ---------- app auto-refresh ----------

def code_stamp() -> float:
    """Newest change to the server's own code, settings or skills (seconds)."""
    newest = 0.0
    for folder, pattern in ((config.BACKEND_DIR / "app", "*.py"), (config.CONFIG_DIR, "*.yaml"),
                            (config.BACKEND_DIR / "skills", "*.md")):
        for p in folder.rglob(pattern):
            try:
                newest = max(newest, p.stat().st_mtime)
            except OSError:
                pass
    return newest


LOADED_STAMP = code_stamp()   # what this running server was started with


@router.get("/app-version")
async def app_version():
    """'version' changes whenever frontend/dist is rebuilt (the open app reloads itself).
    'server_outdated': the server's code files changed on disk after it started, and it didn't
    restart (auto-restart off, or it missed the change) - the app asks you to restart it."""
    outdated = code_stamp() > LOADED_STAMP + 2
    index = config.FRONTEND_DIST / "index.html"
    if not index.exists():
        return {"version": None, "server_outdated": outdated}
    st = index.stat()
    return {"version": f"{st.st_mtime_ns}-{st.st_size}", "server_outdated": outdated}
