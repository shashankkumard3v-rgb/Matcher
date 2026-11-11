from app.loop import bullets
from app.loop.loop import CAP_HINT, _cap_check
from app.schemas.models import Change, Experience, JobDescription, Resume
from tests.test_loop import setup

JD = JobDescription(title="Backend Engineer", must_have=["Python", "Kafka"], nice_to_have=["Redis"],
                    responsibilities=["Build and maintain Kafka-based pipelines for payment events"])


def test_values_and_no_value_bullets(tmp_workspace):
    r = Resume(experience=[Experience(company="Acme", title="SWE", bullets=[
        "Built Kafka consumers in Python for payment events, 2M events per day",
        "Organized the office football league for 40 people",
    ])])
    rep = bullets.analyze(r, JD, None, {"max_bullets_per_job": [6, 5, 3]})
    good, dead = rep.bullets
    assert good.status == "strong" and "Kafka" in good.shows and good.duties
    assert dead.status == "no_value" and dead.has_number      # a number alone isn't relevance
    assert rep.jobs[0].cap == 6 and rep.jobs[0].no_value == 1


def test_caps_by_recency():
    assert bullets.caps_for(4, {"max_bullets_per_job": [6, 5, 3]}) == [6, 5, 3, 3]
    assert bullets.caps_for(2, {"max_bullets_per_job": 7}) == [7, 7]


def test_adds_past_the_cap_are_blocked_until_room_is_made():
    r = Resume(experience=[Experience(company="Acme", title="SWE", bullets=[f"b{i}" for i in range(5)])])
    a1 = Change(id="a1", kind="add_bullet", job=0, new="x", job_label="SWE - Acme")
    a2 = Change(id="a2", kind="add_bullet", job=0, new="y", job_label="SWE - Acme")
    cfg = {"max_bullets_per_job": [6]}
    _cap_check(r, [a1, a2], cfg)
    assert a1.status == "pending" and a2.status == "blocked" and CAP_HINT in a2.truth_issues[0]
    a1.status = "rejected"
    _cap_check(r, [a1, a2], cfg)
    assert a2.status == "pending" and not a2.truth_issues


def test_bullets_endpoint(tmp_workspace, resume_pdf, jd_docx):
    c, run, _ = setup(tmp_workspace, resume_pdf, jd_docx)
    c.post(f"/api/runs/{run}/loop/start")
    rep = c.get(f"/api/runs/{run}/loop/bullets").json()
    assert rep["version"] == 0 and rep["jobs"] and all(b["origin"] == "resume" for b in rep["bullets"])
    assert c.get(f"/api/runs/{run}/loop/bullets?version=9").status_code == 404


def test_added_bullet_shows_its_round_and_source(tmp_workspace, resume_pdf, jd_docx):
    from app.context import notes
    notes.save("experience", "Company: Company A\n- Built a Kafka consumer pipeline that cut lag by 40%\n")
    c, run, _ = setup(tmp_workspace, resume_pdf, jd_docx)
    s = c.post(f"/api/runs/{run}/loop/start").json()
    s = c.post(f"/api/runs/{run}/loop/suggest").json()
    for ch in s["rounds"][-1]["changes"]:
        act = "accept" if ch["status"] == "pending" and ch["kind"] == "replace_bullet" else "reject"
        s = c.post(f"/api/runs/{run}/loop/changes/{ch['id']}", json={"action": act}).json()
    s = c.post(f"/api/runs/{run}/loop/apply").json()
    assert s["current_version"] == 1, s
    rep = c.get(f"/api/runs/{run}/loop/bullets").json()
    added = [b for b in rep["bullets"] if b["origin"] == "added"]
    assert added and all(b["origin_label"].startswith("Rewritten in round 1") for b in added)


def test_merge_bullets_applies_and_frees_a_slot():
    from app.loop.loop import _to_change, apply_changes
    r = Resume(experience=[Experience(company="Acme", title="SWE", bullets=["A one", "B two", "C three"])])
    c = _to_change({"kind": "merge_bullets", "job": 0, "bullet": 0, "bullet2": 2,
                    "new": "A one and C three", "sources": []}, "m1", r)
    assert c.old == "A one" and c.old2 == "C three"
    c.status = "accepted"
    assert apply_changes(r, [c]).experience[0].bullets == ["A one and C three", "B two"]
    assert _to_change({"kind": "merge_bullets", "job": 0, "bullet": 0, "bullet2": 0, "new": "x"}, "m2", r) is None
    # a merge counts as one bullet fewer for the cap
    r6 = Resume(experience=[Experience(company="Acme", title="SWE", bullets=[f"b{i}" for i in range(6)])])
    m = Change(id="m", kind="merge_bullets", job=0, bullet=0, bullet2=1, new="b0 b1", old="b0", old2="b1")
    a = Change(id="a", kind="add_bullet", job=0, new="new", job_label="SWE - Acme")
    _cap_check(r6, [m, a], {"max_bullets_per_job": [6]})
    assert a.status == "pending"


def test_too_long_ai_bullets_are_blocked_but_your_edits_only_warned():
    from app.loop.loop import _length_check
    long_text = " ".join(["word"] * 40)
    ai = Change(id="a", kind="add_bullet", job=0, new=long_text)
    mine = Change(id="b", kind="replace_bullet", job=0, new=long_text, edited_by_you=True, status="accepted")
    _length_check([ai, mine], {"max_words_per_bullet": 32})
    assert ai.status == "blocked" and "40 words" in ai.truth_issues[0]
    assert mine.status == "accepted" and "40 words" in mine.truth_issues[0]


def test_candidates_come_from_the_jobs_notes(tmp_workspace):
    from app.context import notes
    notes.save("experience", "Company: Acme\nRole: SWE\n- Built Kafka consumers for payment events in Python\n"
                             "- Planned the office party for the whole team\nCompany: Other Co\n- Built Kafka things there too\n")
    r = Resume(experience=[Experience(company="Acme", title="SWE", bullets=["Wrote docs"])])
    cands = bullets.candidates(r, JD)
    assert [c.text for c in cands] == ["Built Kafka consumers for payment events in Python",
                                       "Planned the office party for the whole team"]
    assert cands[0].value > cands[1].value


def test_weak_wording_is_rewordable_when_notes_show_the_skill(tmp_workspace):
    from app.context import notes
    from app.loop.loop import bullet_problems
    notes.save("experience", "Company: Acme\n"
               "- Built the consumer daemon for settlement transactions in Python on Kafka, 2M a day\n")
    r = Resume(experience=[Experience(company="Acme", title="SWE", bullets=[
        "Wrote the consumer daemon for settlement transactions",   # same work, no JD skill named
        "Organized the office football league for 40 people",     # unrelated
    ])])
    rep = bullets.analyze(r, JD, None, {"max_bullets_per_job": [6]})
    weak, dead = rep.bullets
    assert weak.status == "rewordable" and set(weak.could_show) >= {"Python", "Kafka"}
    assert weak.related_notes and dead.status == "no_value"
    assert rep.jobs[0].rewordable == 1 and rep.jobs[0].no_value == 1
    ids = {p.id for p in bullet_problems(rep)}
    assert {"bullets:reword", "bullets:no_value"} <= ids
