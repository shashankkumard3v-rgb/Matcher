import inspect
from datetime import date

import pytest

from app.config import weights_config
from app.llm.base import LLMError
from app.schemas.models import JobDescription, Resume
from app.scoring import scorer as scorer_mod
from app.scoring.deterministic import score_deterministic, skill_hits
from app.scoring.llm_rubric import build_input, parse_reply
from app.scoring.render import render_resume
from app.scoring.scorer import ScoreError, score_resume
from tests.conftest import FakeLLM

TODAY = date(2026, 11, 3)


async def parsed():
    llm = FakeLLM()
    r = Resume.model_validate(await llm.run_skill("matcher-parse-resume", "x"))
    jd = JobDescription.model_validate(await llm.run_skill("matcher-parse-jd", "x"))
    return r, jd


def cfg():
    c = weights_config()
    return c["weights"], c["scoring"]


# ---------- deterministic ----------

async def test_deterministic_points_are_exact():
    r, jd = await parsed()
    w, sc = cfg()
    dims, must, nice, years, warns = score_deterministic(r, jd, w, sc, set(), TODAY)
    d = {x.key: x.points for x in dims}
    # Python only in summary/skills (0.5: a summary isn't proof), PostgreSQL only listed
    # (0.5), Kafka + Kubernetes missing
    assert [(h.skill, h.status) for h in must] == [
        ("Python", "listed_only"), ("Kafka", "missing"),
        ("PostgreSQL", "listed_only"), ("Kubernetes", "missing")]
    assert d["must_have_coverage"] == 6.25          # 25 * 1.0/4
    assert d["nice_to_have_coverage"] == 0          # Go, Terraform missing
    assert d["title_alignment"] == 4                # "Software Engineer" covers engineer of backend+engineer
    assert years == 7.5 and d["years_fit"] == 7     # 2019-06..2021-03 + 2021-04..2026-11
    assert d["ats_format"] == 5
    assert warns == []


async def test_two_column_flag_costs_format_points():
    r, jd = await parsed()
    w, sc = cfg()
    dims, *_ = score_deterministic(r, jd, w, sc, {"two_column_layout"}, TODAY)
    assert next(x for x in dims if x.key == "ats_format").points == 4


async def test_synonyms_and_stuffing():
    r, jd = await parsed()
    _, sc = cfg()
    r.experience[0].bullets.append("Deployed services on k8s with Helm")
    hits = {h.skill: h for h in skill_hits(["Kubernetes"], r, sc)}
    assert hits["Kubernetes"].status == "evidenced"

    r.experience[0].bullets += ["Kafka Kafka Kafka", "More Kafka", "Kafka again"]
    h = skill_hits(["Kafka"], r, sc)[0]
    assert h.mentions == 5 and h.credit == 0.75 and "stuffing" in h.note


async def test_summary_stuffing_does_not_count_as_evidence():
    r, jd = await parsed()
    _, sc = cfg()
    r.summary = "Kafka Kubernetes expert"
    hits = {h.skill: h.status for h in skill_hits(["Kafka", "Kubernetes"], r, sc)}
    assert hits == {"Kafka": "listed_only", "Kubernetes": "listed_only"}


async def test_no_jd_nice_to_have_gives_full_points():
    r, jd = await parsed()
    jd.nice_to_have = []
    w, sc = cfg()
    dims, *_ = score_deterministic(r, jd, w, sc, set(), TODAY)
    assert next(x for x in dims if x.key == "nice_to_have_coverage").points == 10


# ---------- LLM rubric evidence rules ----------

async def test_invented_quote_scores_zero():
    r, _ = await parsed()
    text = render_resume(r)
    reply = {"dimensions": [
        {"key": "experience_relevance", "level": 100, "reason": "x",
         "evidence": ["Led a team of 50 engineers at Google"]},
        {"key": "impact_quantification", "level": 62, "reason": "x",
         "evidence": ["serving 2M requests per day"]},
        {"key": "seniority_scope", "level": 0, "reason": "x", "evidence": []},
        {"key": "domain_fit", "level": 25, "reason": "x", "evidence": ["Django admin tools"]},
    ]}
    out = parse_reply(reply, text)
    assert out["experience_relevance"]["level"] == 0
    assert "no evidence quote" in out["experience_relevance"]["reason"]
    assert out["impact_quantification"]["level"] == 50   # 62 snaps to nearest level
    assert out["domain_fit"]["level"] == 25


async def test_missing_dimension_is_an_error():
    r, _ = await parsed()
    with pytest.raises(LLMError, match="missing dimensions"):
        parse_reply({"dimensions": []}, render_resume(r))


# ---------- blind judge ----------

def test_scorer_has_no_way_to_know_history():
    params = set(inspect.signature(score_resume).parameters)
    # "samples" is only how many times the judge reads the same resume, never earlier results
    assert params == {"llm", "resume", "jd", "format_flags", "pins", "today", "samples"}


async def test_judge_input_is_identical_for_identical_resumes():
    r, jd = await parsed()
    copy = Resume.model_validate_json(r.model_dump_json())  # e.g. a rewrite that changed nothing
    a, b = build_input(jd, render_resume(r)), build_input(jd, render_resume(copy))
    assert a == b
    for word in ("iteration", "previous", "revision", "rewrite", "improved", "score_"):
        assert word not in a.lower()


async def test_same_resume_same_score_every_time():
    r, jd = await parsed()
    llm = FakeLLM()
    first = await score_resume(llm, r, jd, today=TODAY)
    again = await score_resume(llm, r, jd, pins=first.pins, today=TODAY)
    assert first.total == again.total == 48.5  # 22.25 deterministic + 26.25 judgment
    assert first.llm_points == 26.25
    assert first.top_losses[0] == "must_have_coverage"
    assert again.pins == first.pins


async def test_pinned_model_is_forced():
    r, jd = await parsed()
    llm = FakeLLM()
    first = await score_resume(llm, r, jd, today=TODAY)
    await score_resume(llm, r, jd, pins={**first.pins, "model": "pinned/model"}, today=TODAY)
    assert llm.models[-1] == "pinned/model"


async def test_settings_change_mid_run_is_refused():
    r, jd = await parsed()
    llm = FakeLLM()
    first = await score_resume(llm, r, jd, today=TODAY)
    with pytest.raises(ScoreError, match="skill_hash"):
        await score_resume(llm, r, jd, pins={**first.pins, "skill_hash": "old"}, today=TODAY)


async def test_weights_must_sum_to_100(monkeypatch):
    r, jd = await parsed()
    bad = {**weights_config(), "weights": {"deterministic": {"must_have_coverage": 10}, "llm": {}}}
    monkeypatch.setattr(scorer_mod, "weights_config", lambda: bad)
    with pytest.raises(ScoreError, match="add up to 100"):
        await score_resume(FakeLLM(), r, jd, today=TODAY)
