"""Real resumes were scored 0 for things they clearly showed. Each test is one root cause."""
from app.config import weights_config
from app.parsing.structure import _norm, found_in_source, normalize_one_of
from app.schemas.models import Experience, JobDescription, Resume
from app.scoring.deterministic import _title_tokens, score_deterministic, title_dimension
from app.scoring.llm_rubric import _snap, parse_reply


def _resume(*bullets, title="SDE2", skills=()):
    return Resume(experience=[Experience(title=title, company="Gruve AI", start="2022-01", end="present",
                                         bullets=list(bullets))], skills=list(skills))


# 1. "SDE2" and "Software Development Engineer 2" are the same job title.
def test_sde2_is_software_development_engineer_2():
    same = {"SDE2", "SDE 2", "SDE-II", "Software Development Engineer II", "Software Engineer 2", "SWE II"}
    assert {frozenset(_title_tokens(t)) for t in same} == {frozenset({"software", "engineer"})}
    d = title_dimension(_resume("x"), JobDescription(title="Software Development Engineer 2"), 8)
    assert d.points == 8


# 2. A list of different kinds of things is not "any one of all of them".
def test_mixed_list_is_one_language_and_kubernetes_and_one_cloud():
    want = ["Go or Python or Java", "Kubernetes", "AWS or Azure"]
    assert JobDescription(must_have=normalize_one_of(["Go, Python, Java, Kubernetes, AWS or Azure"])).must_have == want
    # JDs saved before the fix are corrected when they're read
    assert JobDescription(must_have=["Go or Python or Java or Kubernetes or AWS or Azure"]).must_have == want
    assert normalize_one_of(["AWS, GCP or Azure"]) == ["AWS or GCP or Azure"]          # one kind: any one
    assert JobDescription(must_have=["Docker or Kubernetes"]).must_have == ["Docker or Kubernetes"]


# 3. Concepts show through the work, not one keyword.
def test_system_design_and_async_show_through_the_bullets():
    cfg = weights_config()
    resume = _resume("Designed microservices architecture for payments handling 2M daily transactions",
                     "Built event-driven pipelines on Kafka with asyncio workers")
    jd = JobDescription(title="SDE 2", must_have=["System Design", "Asynchronous Programming", "Concurrency"])
    _, must, *_ = score_deterministic(resume, jd, cfg["weights"], cfg.get("scoring", {}), set(), None)
    hits = {h.skill: h for h in must}
    assert hits["System Design"].status == "evidenced"
    assert hits["Async Programming"].status == "evidenced"
    assert hits["Concurrency"].status == "evidenced"       # asyncio is concurrency
    nothing = _resume("Wrote unit tests for the billing UI")
    _, must, *_ = score_deterministic(nothing, jd, cfg["weights"], cfg.get("scoring", {}), set(), None)
    assert all(h.status == "missing" for h in must)       # still strict when it isn't there


# 4. A good verdict was turned into 0 because its quote didn't match the resume character for character.
RESUME = """SDE2 | Gruve AI | 2022-01 - present
- Designed and built a reconciliation service in Go and PostgreSQL that matched 2 million daily transactions, cutting manual review by 70%.
- Led migration of 14 microservices to Kubernetes with Helm charts, reducing deployment time from 40 minutes to 6 minutes."""


def test_real_quotes_count_even_when_shortened_or_lightly_reworded():
    src = _norm(RESUME)
    assert found_in_source("Designed and built a reconciliation service ... cutting manual review by 70%", src)
    assert found_in_source("Designed and built a reconciliation service in Go and PostgreSQL… by 70%", src)
    assert found_in_source("Led the migration of 14 microservices to Kubernetes using Helm charts", src, loose=True)
    joined = _norm("Designedandbuiltareconciliationservice in Go")         # a resume read before the PDF fix
    assert found_in_source("Designed and built a reconciliation service", joined)


def test_made_up_quotes_still_score_zero():
    src = _norm(RESUME)
    assert not found_in_source("Led migration of 25 microservices to Kubernetes", src, loose=True)   # changed number
    assert not found_in_source("Managed a team of 8 engineers across three time zones", src, loose=True)
    assert not found_in_source("Designed and built a reconciliation service ... saving $2M a year", src, loose=True)


def test_a_strong_verdict_keeps_its_points():
    reply = {"dimensions": [
        {"key": "experience_relevance", "level": 100, "reason": "Directly aligns.",
         "evidence": ["Designed and built a reconciliation service ... cutting manual review by 70%"]},
        {"key": "impact_quantification", "level": "100%", "reason": "Metrics throughout.",
         "evidence": ["reducing deployment time from 40 minutes to 6 minutes"]},
        {"key": "seniority_scope", "level": "Exceeds expectations", "reason": "Exceeds.",
         "evidence": ["Led the migration of 14 microservices to Kubernetes using Helm charts"]},
        {"key": "domain_fit", "level": 50, "reason": "Fintech.", "evidence": ["reconciliation service"]},
    ]}
    got = parse_reply(reply, RESUME)
    assert [got[k]["level"] for k in ("experience_relevance", "impact_quantification", "seniority_scope", "domain_fit")] \
        == [100, 100, 100, 50]


def test_levels_written_differently():
    assert [_snap(x) for x in (75, "75", "75%", "75/100", "Exceeds expectations", "none", None)] == \
        [75, 75, 75, 75, 100, 0, 0]


# 5. A different title isn't a weaker candidate when the AI judges the level and scope fit.
def test_title_points_follow_the_ai_scope_verdict():
    from app.schemas.models import ScoreDimension
    from app.scoring.scorer import title_follows_scope
    title = title_dimension(_resume("x", title="Backend Engineer"), JobDescription(title="SDE 2"), 8)
    assert title.points < 8
    scope = lambda lv: ScoreDimension(key="seniority_scope", label="", method="llm", weight=10, level=lv,
                                      points=lv / 10)
    lifted = title_follows_scope([title], [scope(100)])[0]
    assert lifted.points == 8 and "isn't held against you" in lifted.reason
    assert title_follows_scope([title], [scope(75)])[0].points == max(6, title.points)
    assert title_follows_scope([title], [scope(0)])[0].points == title.points       # never lowers it
    exact = title_dimension(_resume("x", title="SDE2"), JobDescription(title="SDE 2"), 8)
    assert title_follows_scope([exact], [scope(25)])[0].points == 8
