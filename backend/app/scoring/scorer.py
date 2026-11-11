"""Step 2: the blind judge.

score_resume() takes ONLY a resume, a JD, layout flags from the original file and the
run's pins. It has no parameter for the pass number, earlier scores, the diff or anything
the improver said - so it cannot be influenced by them. Pins lock the skill version,
model, weights and synonym map for a whole run; if any of them change mid-run, scoring
refuses instead of silently judging differently.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date

from app.config import weights_config
from app.llm.base import LLMClient
from app.schemas.models import JobDescription, Resume, ScoreResult
from app.scoring.deterministic import score_deterministic
from app.scoring.llm_rubric import SKILL, score_llm
from app.scoring.render import render_resume
from app.scoring.synonyms import synonym_table
from app.skills.loader import load_skill

PINNED_KEYS = ("provider", "skill_hash", "weights_hash", "synonyms_version")


class ScoreError(RuntimeError):
    pass


def _scoring_hash(cfg: dict) -> str:
    """Hash of only the settings that change a score (weights + scoring rules)."""
    relevant = {"weights": cfg.get("weights"), "scoring": cfg.get("scoring")}
    return hashlib.sha256(json.dumps(relevant, sort_keys=True).encode()).hexdigest()[:16]


def current_settings(llm: LLMClient) -> dict:
    return {
        # provider NAME (claude / gemini / openrouter / openai), so a mid-run switch is caught
        "provider": getattr(llm, "name", None) or getattr(llm, "mode", "unknown"),
        "skill": SKILL,
        "skill_hash": load_skill(SKILL).content_hash,
        "weights_hash": _scoring_hash(weights_config()),
        "synonyms_version": synonym_table()[0],
    }


def check_weights(weights: dict) -> None:
    total = sum(weights["deterministic"].values()) + sum(weights["llm"].values())
    if abs(total - 100) > 1e-6:
        raise ScoreError(f"weights.yaml weights must add up to 100 (they add up to {total}).")


async def score_resume(
    llm: LLMClient,
    resume: Resume,
    jd: JobDescription,
    *,
    format_flags: set[str] | None = None,
    pins: dict | None = None,
    today: date | None = None,
    samples: int | None = None,
) -> ScoreResult:
    """samples: how many times the AI judge reads the resume (the median level of each part is
    used). None = the configured number (scoring.llm_samples)."""
    cfg = weights_config()
    weights = cfg["weights"]
    check_weights(weights)

    settings = current_settings(llm)
    if pins:
        changed = [k for k in PINNED_KEYS if pins.get(k) != settings.get(k)]
        if changed:
            raise ScoreError(
                "Scoring settings changed during this run (" + ", ".join(changed) + "). "
                "Start a new run so every version is judged the same way."
            )

    det_dims, must, nice, years, warnings = score_deterministic(
        resume, jd, weights, cfg.get("scoring", {}), format_flags, today)
    llm_dims, model, llm_warnings = await score_llm(
        llm, jd, render_resume(resume), weights,
        samples=samples or int(cfg.get("scoring", {}).get("llm_samples", 1)),
        model=(pins or {}).get("model"),
    )

    dims = det_dims + llm_dims
    det_pts = round(sum(d.points for d in det_dims), 2)
    llm_pts = round(sum(d.points for d in llm_dims), 2)
    top = sorted((d for d in dims if d.lost > 0), key=lambda d: (-d.lost, d.key))
    return ScoreResult(
        total=round(det_pts + llm_pts, 1),
        deterministic_points=det_pts,
        llm_points=llm_pts,
        dimensions=dims,
        must_have=must,
        nice_to_have=nice,
        years_found=years,
        top_losses=[d.key for d in top[:5]],
        warnings=warnings + llm_warnings,
        pins={**settings, "model": model},
    )
