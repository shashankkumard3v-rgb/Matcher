"""Code decides and checks what can be counted; the AI only writes wording."""
from app.loop import guard
from app.loop.loop import _flags
from app.schemas.models import Change, Experience, JobDescription, LoopState, Resume, Round

JD = JobDescription(title="Backend Engineer", must_have=["Kafka", "PostgreSQL", "Python"])


def _resume():
    return Resume(experience=[Experience(title="Backend Engineer", company="Acme", start="2021-01", end="present", bullets=[
        "Built a Kafka consumer pipeline in Python that cut lag by 40%",       # only proof of Kafka
        "Wrote Python services for billing",                                  # Python shown elsewhere too
        "Tuned PostgreSQL queries, cutting report time from 9s to 2s",        # only proof of PostgreSQL
    ])], skills=["Python"])


def test_labels_find_the_only_proof_and_repeats():
    marks = guard.labels(_resume(), JD)
    assert marks[(0, 0)]["protected"] == ["Kafka"]
    assert marks[(0, 2)]["protected"] == ["PostgreSQL"]
    assert marks[(0, 1)]["protected"] == [] and marks[(0, 1)]["redundant"]


def _ch(cid, kind, bullet=None, new=None, old=None, old2=None):
    return Change(id=cid, kind=kind, job=0, bullet=bullet, new=new, old=old, old2=old2)


def test_review_gives_exact_points_and_blocks_losing_a_skill():
    r = _resume()
    drop = _ch("a", "remove_bullet", 0, old=r.experience[0].bullets[0])
    harmless = _ch("b", "remove_bullet", 1, old=r.experience[0].bullets[1])
    reword = _ch("c", "replace_bullet", 2, old=r.experience[0].bullets[2],
                 new="Tuned slow report queries, cutting report time from 9s to 2s")   # PostgreSQL gone
    guard.review(r, JD, [drop, harmless, reword])
    assert drop.status == "blocked" and "only proof of Kafka" in drop.truth_issues[0] and drop.points_delta < 0
    assert harmless.status == "pending" and harmless.points_delta == 0
    assert reword.status == "blocked" and "PostgreSQL" in reword.truth_issues[0]


def test_a_merge_must_keep_every_number():
    r = _resume()
    m = _ch("m", "merge_bullets", 0, old=r.experience[0].bullets[0], old2=r.experience[0].bullets[2],
            new="Built a Python Kafka pipeline and tuned PostgreSQL queries, cutting report time to 2s")
    m.bullet2 = 2
    guard.review(r, JD, [m])
    assert m.status == "blocked" and any("40, 9" in i for i in m.truth_issues)


def test_two_edits_on_one_bullet_keep_the_one_worth_more():
    r = _resume()
    weak = _ch("w", "replace_bullet", 1, old=r.experience[0].bullets[1], new="Wrote services for billing")
    strong = _ch("s", "replace_bullet", 1, old=r.experience[0].bullets[1], new="Wrote Python services for billing")
    weak.points_delta, strong.points_delta = 0, 1.5
    guard.clashes([weak, strong])
    assert strong.status == "pending" and weak.status == "blocked"


def test_skills_your_notes_show_are_added_to_the_skills_list_by_code(tmp_workspace):
    from app.context import notes
    notes.save("experience", "Company: Acme\n- Moved order history to Cassandra for faster reads\n")
    jd = JobDescription(must_have=["Cassandra", "Rust"])
    r = Resume(experience=[Experience(title="SDE", company="Acme", bullets=["Built Go services"])])
    edits = guard.skill_edits(r, jd, [], 1, "c")
    assert [(e.kind, e.new) for e in edits] == [("add_skill", "Cassandra")]     # nothing shows Rust: not added
    assert guard.code_points(__import__("app.loop.loop", fromlist=["x"]).apply_changes(r, edits), jd) > guard.code_points(r, jd)
    planned = [Change(id="x", kind="add_bullet", job=0, new="Moved order history to Cassandra")]
    assert guard.skill_edits(r, jd, [], 1, "c", planned=planned) == []          # a bullet already brings it in


def test_the_plan_never_removes_the_only_proof():
    from app.loop.bullets import analyze
    from app.loop.split import validate_plan
    rep = analyze(_resume(), JD, None, {"max_bullets_per_job": [6]})
    plan = validate_plan({"bullets": [{"id": "j0b0", "action": "remove"}, {"id": "j0b1", "action": "remove"}]},
                         rep, {}, {"bullets": [], "add": []})
    assert "j0b0" not in plan["bullets"] and plan["bullets"]["j0b1"]["action"] == "remove"


def test_small_score_changes_are_noise():
    s = LoopState(run_id="r", target=90, rounds=[Round(n=1, base_version=0, score_before=80.0, score_after=79.4,
                                                        result_version=1)])
    _flags(s, {"score_noise": 1.0})
    assert "no_real_change" in s.flags and "score_dropped" not in s.flags
    s.rounds[0].score_after = 77.0
    _flags(s, {"score_noise": 1.0})
    assert "score_dropped" in s.flags
