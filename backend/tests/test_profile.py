"""The saved resume description: strict levels, checked quotes, dates from the resume."""
from datetime import date

from app.library.profile import build_input, normalize
from app.schemas.models import Experience, Resume

R = Resume(
    summary="Backend engineer working with Python and Kafka.",
    skills=["Python", "Kafka", "Postgres", "Terraform"],
    experience=[
        Experience(title="SWE II", company="Finlytics", start="2021-04", end="present", bullets=[
            "Designed and owned a Kafka pipeline in Python that cut payout delay from 24 hours to under 1 minute",
            "Built REST APIs in FastAPI for the settlements service",
            "Worked on PostgreSQL query tuning",
        ]),
        Experience(title="SWE", company="CartNest", start="2019-07", end="2021-03", bullets=[
            "Built Django services in Python for order management",
        ]),
    ])
TODAY = date(2026, 11, 1)


def lvl(p, name):
    return next((s for s in p.skills if s.name.lower() == name.lower()), None)


def test_quotes_are_checked_and_levels_are_strict():
    raw = {"skills": [
        {"name": "Kafka", "level": 5, "quote": "Designed and owned a Kafka pipeline in Python that cut payout delay from 24 hours to under 1 minute"},
        {"name": "Python", "level": 5, "quote": "Built Django services in Python for order management"},   # no number -> 4, no "owned" -> 3
        {"name": "FastAPI", "level": 4, "quote": "Built REST APIs in FastAPI for the settlements service"},  # no "owned" -> 3
        {"name": "PostgreSQL", "level": 3, "quote": "Worked on PostgreSQL query tuning"},                    # AI said 3: kept (rubric is the AI's job)
        {"name": "Terraform", "level": 4, "quote": "Terraform"},                                             # only in the skills list -> 1
        {"name": "Go", "level": 4, "quote": "Built Go microservices for payments"},                           # not in the resume -> dropped
        {"name": "Redis", "level": 3, "quote": "Built REST APIs in FastAPI for the settlements service"},     # skill not in its quote -> dropped
        {"name": "Python", "level": 2, "quote": "Backend engineer working with Python and Kafka."},           # summary -> max 2 (merged, max kept)
    ], "capabilities": [
        {"id": "event_driven", "level": 5, "quote": "Designed and owned a Kafka pipeline in Python that cut payout delay from 24 hours to under 1 minute"},
        {"id": "backend_apis", "level": 5, "quote": "Built REST APIs in FastAPI for the settlements service"},  # no number, no owned -> 3
        {"id": "made_up_thing", "level": 3, "quote": "Built REST APIs in FastAPI for the settlements service"},  # not in the list -> dropped
    ]}
    p = normalize(raw, R, TODAY)
    assert lvl(p, "Kafka").level == 5 and lvl(p, "Python").level == 3
    assert lvl(p, "FastAPI").level == 3 and lvl(p, "Terraform").level == 1
    assert lvl(p, "Go") is None and lvl(p, "Redis") is None and p.dropped >= 3
    assert lvl(p, "PostgreSQL").level == 3                 # "Postgres" in the skills list merged into it
    caps = {c.id: c.level for c in p.capabilities}
    assert caps == {"event_driven": 5, "backend_apis": 3}


def test_last_used_and_years_come_from_job_dates():
    raw = {"skills": [{"name": "Python", "level": 3, "quote": "Built Django services in Python for order management"},
                      {"name": "Django", "level": 3, "quote": "Built Django services in Python for order management"}]}
    p = normalize(raw, R, TODAY)
    py, dj = lvl(p, "Python"), lvl(p, "Django")
    assert py.last_used == "present" and py.years > 7          # both jobs show Python
    assert dj.last_used == "2021-03" and 1.5 < dj.years < 2
    assert lvl(p, "Kafka").level == 1                          # never rated by the AI: listed only


def test_the_ai_sees_only_the_resume_and_the_capability_list():
    text = build_input(R)
    assert text.startswith("RESUME:") and "CAPABILITIES:" in text and "event_driven:" in text
    assert "JOB" not in text and "JD" not in text
