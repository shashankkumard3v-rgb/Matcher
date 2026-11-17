"""Every writing step knows the goal; a rewrite keeps the bullet's numbers and tools; nothing repeats."""
from app.loop import goals, guard
from app.schemas.models import Change, Experience, JobDescription, Problem, Resume, Round

B = "Cut payout p99 latency from 2.1s to 450ms by caching merchant config in Redis"


def test_must_keep_finds_numbers_and_tools():
    keep = goals.must_keep(B)
    assert "2.1s" in keep and "450ms" in keep and "Redis" in keep


def test_a_rewrite_that_drops_a_number_or_tool_is_blocked():
    r = Resume(experience=[Experience(title="SDE", company="Acme", bullets=[B, "Wrote Python services"])])
    jd = JobDescription(must_have=["Python"])
    c = Change(id="c", kind="replace_bullet", job=0, bullet=0, old=B, new="Cut payout latency by caching config")
    guard.review(r, jd, [c])
    assert c.status == "blocked" and any("2.1s" in i and "450ms" in i and "Redis" in i for i in c.truth_issues)
    ok = Change(id="o", kind="replace_bullet", job=0, bullet=0, old=B,
                new="Reduced payout p99 latency from 2.1s to 450ms by caching merchant config in Redis")
    guard.review(r, jd, [ok])
    assert ok.status == "pending"


def test_a_new_bullet_that_repeats_one_on_the_resume_is_blocked():
    r = Resume(experience=[Experience(title="SDE", company="Acme", bullets=[B, "Wrote Python services"])])
    c = Change(id="a", kind="add_bullet", job=0,
               new="Cut payout p99 latency from 2.1s to 450ms by caching the merchant config in Redis")
    guard.review(r, JobDescription(), [c])
    assert c.status == "blocked" and "Says the same as" in c.truth_issues[-1]


def test_every_writing_step_gets_the_goal():
    r = Round(n=1, base_version=0, score_before=70, notes=["keep it short"], problems=[
        Problem(id="must:kafka", dimension="must_have_coverage", label="Must-haves", points_lost=6.2,
                cause="Kafka is required and doesn't appear in your resume.", fix_by="ai", detail="")])
    text = goals.goal_block(JobDescription(title="SDE 2", company="Acme"), r)
    assert "SDE 2 at Acme" in text and "Kafka is required" in text and "keep it short" in text
    assert "Keep every number and every tool" in text
