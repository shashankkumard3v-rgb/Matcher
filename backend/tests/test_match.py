"""Quick match: the right resume wins, and the score explains itself."""
from datetime import date

from app.library.match import match
from app.library.profile import CapabilityLevel, ResumeProfile, SkillLevel
from app.schemas.models import Experience, JobDescription, Resume

TODAY = date(2026, 11, 1)
JD = JobDescription(title="Senior Backend Engineer", years_required=5,
                    must_have=["Strong Python", "Kafka or a similar event streaming platform", "PostgreSQL", "AWS"],
                    nice_to_have=["Terraform"], capabilities=["event_driven", "backend_apis", "on_call_ops"])


def resume(title, years_start):
    return Resume(experience=[Experience(title=title, company="X", start=years_start, end="present")])


BACKEND = ResumeProfile(skills=[
    SkillLevel(name="Python", level=5, last_used="present"), SkillLevel(name="Kafka", level=5, last_used="present"),
    SkillLevel(name="PostgreSQL", level=4, last_used="present"), SkillLevel(name="AWS", level=3, last_used="present"),
    SkillLevel(name="Terraform", level=1)],
    capabilities=[CapabilityLevel(id="event_driven", label="", level=5), CapabilityLevel(id="backend_apis", label="", level=4)])
FRONTEND = ResumeProfile(skills=[
    SkillLevel(name="React", level=5, last_used="present"), SkillLevel(name="TypeScript", level=5, last_used="present"),
    SkillLevel(name="Python", level=1), SkillLevel(name="AWS", level=2, last_used="2020-06")],
    capabilities=[CapabilityLevel(id="frontend_ui", label="", level=5)])


def test_the_backend_resume_wins_by_a_clear_margin():
    b = match(BACKEND, resume("Backend Engineer", "2019-01"), JD, TODAY)
    f = match(FRONTEND, resume("Frontend Engineer", "2019-01"), JD, TODAY)
    assert b.score > 70 and f.score < 40 and b.score - f.score > 40, (b.score, f.score)
    kafka = next(x for x in b.required if x.jd_skill.startswith("Kafka"))
    assert kafka.matched == "Kafka" and kafka.level == 5            # "Kafka or similar" phrase matched
    assert "missing: Kafka or a similar event streaming platform, PostgreSQL" in f.summary
    assert "weak: Strong Python, AWS" in f.summary


def test_old_skills_count_less_and_empty_parts_move_their_points():
    old = ResumeProfile(skills=[SkillLevel(name="Python", level=5, last_used="2019-01")])
    new = ResumeProfile(skills=[SkillLevel(name="Python", level=5, last_used="present")])
    jd = JobDescription(title="Python Developer", must_have=["Python"])
    a, b = match(old, resume("Developer", "2015-01"), jd, TODAY), match(new, resume("Developer", "2015-01"), jd, TODAY)
    assert a.parts["required"] < b.parts["required"] and set(b.parts) == {"required", "years", "title"}
    assert b.parts["required"] == 85.0                                # 55 + 10 nice + 20 capabilities moved in


def test_same_input_same_score():
    r = resume("Backend Engineer", "2019-01")
    assert match(BACKEND, r, JD, TODAY).model_dump() == match(BACKEND, r, JD, TODAY).model_dump()
