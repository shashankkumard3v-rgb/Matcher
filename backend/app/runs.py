"""Reading and writing files inside a run folder (backend/runs/<run_id>/)."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from app import config
from app.schemas.models import JobDescription, Resume, ScoreResult

RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,120}$")


class RunNotFound(LookupError):
    pass


def run_dir(run_id: str) -> Path:
    if not RUN_ID_RE.match(run_id or ""):
        raise RunNotFound(f"Invalid run id '{run_id}'")
    d = config.runs_dir() / run_id
    if not d.is_dir():
        raise RunNotFound(f"Run '{run_id}' not found")
    return d


def load_resume(d: Path, name: str = "resume.json") -> Resume:
    return Resume.model_validate_json((d / name).read_text(encoding="utf-8"))


def load_jd(d: Path) -> JobDescription:
    return JobDescription.model_validate_json((d / "jd.json").read_text(encoding="utf-8"))


def original_format_flags(d: Path) -> set[str]:
    """Layout problems found in the uploaded PDF (e.g. two columns)."""
    p = d / "parse_warnings.json"
    if not p.exists():
        return set()
    return {w["code"] for w in json.loads(p.read_text(encoding="utf-8")) if w.get("source") == "resume"}


def load_pins(d: Path) -> dict | None:
    p = d / "pins.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def save_pins(d: Path, pins: dict) -> None:
    (d / "pins.json").write_text(json.dumps(pins, indent=2), encoding="utf-8")


def score_files(d: Path) -> list[Path]:
    """score_0.json, score_1.json, ... oldest first (each "Score again" adds one)."""
    return sorted(d.glob("score_*.json"), key=lambda p: int(p.stem.split("_")[1]))


def latest_score(d: Path) -> ScoreResult | None:
    """The run's current score: the most recent one. Every screen and step uses this one."""
    files = score_files(d)
    return ScoreResult.model_validate_json(files[-1].read_text(encoding="utf-8")) if files else None


def save_score(d: Path, result: ScoreResult) -> str:
    files = score_files(d)
    n = int(files[-1].stem.split("_")[1]) + 1 if files else 0
    name = f"score_{n}.json"
    (d / name).write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return name


def delete_run(run_id: str) -> None:
    """Deletes one of YOUR runs for good (its folder lives under your own user folder).
    Saved resumes made from it are separate copies and stay."""
    d = run_dir(run_id)
    # All or nothing: first move it out of the way (on Windows this fails while a file in it is
    # open, and then nothing has been touched), then remove it.
    gone = d.with_name(f".deleted-{d.name}")
    d.rename(gone)
    shutil.rmtree(gone, ignore_errors=True)
