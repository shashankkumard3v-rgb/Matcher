"""The 'best possible' resume and the run's one current score.

Regression for a real run (dummy sample data): the old builder dropped the only bullet proving a
must-have, pasted skills-note lines and near-duplicates in as bullets (keyword stuffing), so the
best possible (76.6) came out BELOW the resume as it was (83).
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.routes import get_llm
from app.config import weights_config
from app.context import notes
from app.gate import ceiling as ceiling_mod
from app.gate.ceiling import build_ceiling_resume, near_duplicate
from app.loop.loop import _cfg as loop_cfg
from app.main import app
from app.schemas.models import JobDescription, ProjectContext, Resume
from app.scoring.deterministic import score_deterministic
from app.scoring.render import evidence_text, skills_text
from app.scoring.synonyms import count_mentions
from tests.conftest import FakeLLM

FIX = Path(__file__).parent / "fixtures" / "best_possible"


@pytest.fixture
def real_case(monkeypatch):
    texts = {k: (FIX / f"{k}.txt").read_text(encoding="utf-8") for k in ("experience", "skills")}
    monkeypatch.setattr(notes, "read_raw", lambda kind: texts.get(kind, ""))
    load = lambda name, model: model.model_validate(json.loads((FIX / name).read_text(encoding="utf-8")))
    return load("resume.json", Resume), load("jd.json", JobDescription), load("projects.json", ProjectContext)


def rules_points(r: Resume, jd: JobDescription) -> float:
    cfg = weights_config()
    dims, *_ = score_deterministic(r, jd, cfg["weights"], cfg.get("scoring", {}), set(), None)
    return round(sum(d.points for d in dims), 2)


def test_best_possible_never_loses_what_the_resume_proves(real_case):
    resume, jd, ctx = real_case
    best = build_ceiling_resume(resume, ctx, jd, loop_cfg())
    ev = evidence_text(best)
    for skill in jd.must_have:                      # every must-have the resume showed is still shown
        if count_mentions(skill, evidence_text(resume)):
            assert count_mentions(skill, ev), skill
    assert any("REST API" in b for b in best.experience[0].bullets)   # the only REST API design proof
    assert rules_points(best, jd) >= rules_points(resume, jd)        # was 41.64 vs 43.0 before the fix


def test_best_possible_has_no_skill_lines_duplicates_or_stuffing(real_case):
    resume, jd, ctx = real_case
    best = build_ceiling_resume(resume, ctx, jd, loop_cfg())
    bullets = [b for j in best.experience for b in j.bullets] + [b for p in best.projects for b in p.bullets]
    assert not any(ceiling_mod._SKILL_ENTRY.search(b) for b in bullets)   # skills-notes entries aren't bullets
    assert ceiling_mod._SKILL_ENTRY.search("Python: used at Finlytics Pvt Ltd for 6 years. Level: Strong")
    for i, a in enumerate(bullets):
        for b in bullets[i + 1:]:
            assert not near_duplicate(a, b), (a, b)
    max_m = int(weights_config()["scoring"]["stuffing_max_mentions"])
    for s in [*jd.must_have, *jd.nice_to_have]:
        assert count_mentions(s, evidence_text(best)) + count_mentions(s, skills_text(best)) <= max_m, s
    caps = loop_cfg()["max_bullets_per_job"]
    assert [len(j.bullets) for j in best.experience] <= [caps[min(i, len(caps) - 1)] for i in range(len(best.experience))]


def test_best_possible_keeps_proof_of_a_job_duty(real_case):
    resume, jd, ctx = real_case
    best = build_ceiling_resume(resume, ctx, jd, loop_cfg())
    assert any("entor" in b for b in best.experience[0].bullets)   # "Mentor engineers ..." duty


def test_near_duplicate():
    assert near_duplicate("Developed Django services for order management handling 20K orders per day",
                          "Built and maintained Django services for order management handling about 20,000 orders per day")
    assert not near_duplicate("Designed PostgreSQL schemas for settlement records",
                              "Built Grafana dashboards and Prometheus alerts")


# ---------- one current score ----------

def _run(tmp_workspace, resume_pdf, jd_docx):
    llm = FakeLLM()
    app.dependency_overrides[get_llm] = lambda: llm
    c = TestClient(app)
    run = c.post("/api/parse", files={"resume": ("cv.pdf", resume_pdf, "application/pdf"),
                                     "jd": ("jd.docx", jd_docx, "application/octet-stream")}).json()["run_id"]
    return c, run, llm


def test_best_possible_is_never_below_now(tmp_workspace, resume_pdf, jd_docx, monkeypatch):
    c, run, _ = _run(tmp_workspace, resume_pdf, jd_docx)
    now = c.post(f"/api/runs/{run}/score").json()["score"]["total"]

    def worse(resume, *a, **k):          # a builder that loses bullets
        r = resume.model_copy(deep=True)
        for j in r.experience:
            j.bullets = j.bullets[:1]
        return r
    monkeypatch.setattr("app.gate.gate.build_ceiling_resume", worse)
    g = c.post(f"/api/runs/{run}/gate").json()
    assert g["ceiling_score"] == g["current_score"] == now


def test_score_again_reads_three_times_and_becomes_current(tmp_workspace, resume_pdf, jd_docx):
    c, run, llm = _run(tmp_workspace, resume_pdf, jd_docx)
    c.post(f"/api/runs/{run}/score")
    g = c.post(f"/api/runs/{run}/gate").json()
    assert g["needs_recompute"] is False

    # pretend Step 1 was worked out from a different (older) score
    from app import runs as runs_mod
    d = runs_mod.run_dir(run)
    gs = json.loads((d / "gate.json").read_text())
    gs["current_score"] = 1.0
    (d / "gate.json").write_text(json.dumps(gs))

    before = llm.calls.count("matcher-score")
    res = c.post(f"/api/runs/{run}/score", json={"again": True}).json()
    assert llm.calls.count("matcher-score") - before == 3            # careful re-check: 3 reads
    assert [h["n"] for h in res["history"]] == [0, 1]
    got = c.get(f"/api/runs/{run}").json()
    assert got["score"]["total"] == res["score"]["total"] and len(got["score_history"]) == 2
    assert c.get(f"/api/runs/{run}/gate").json()["needs_recompute"] is True   # Step 1 must recalculate


def test_gate_and_loop_use_the_current_score(tmp_workspace, resume_pdf, jd_docx):
    c, run, llm = _run(tmp_workspace, resume_pdf, jd_docx)
    first = c.post(f"/api/runs/{run}/score").json()["score"]["total"]
    g = c.post(f"/api/runs/{run}/gate").json()
    assert g["current_score"] == first
    c.post(f"/api/runs/{run}/gate/decide", json={"decision": "proceed", "reason": "testing"})
    before = llm.calls.count("matcher-score")
    s = c.post(f"/api/runs/{run}/loop/start").json()
    assert llm.calls.count("matcher-score") == before                  # v0 isn't judged a second time
    assert s["versions"][0]["score"] == first
    r = c.post(f"/api/runs/{run}/score", json={"again": True})        # versions have their own scores now
    assert r.status_code == 409 and r.json()["detail"]["code"] == "improving"
