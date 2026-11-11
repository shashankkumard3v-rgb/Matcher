from fastapi.testclient import TestClient

from app.api.routes import get_llm
from app.loop.loop import apply_changes
from app.main import app
from app.schemas.models import Change, Experience, Resume
from tests.conftest import FakeLLM


def setup(tmp_workspace, resume_pdf, jd_docx, proceed=True):
    llm = FakeLLM()
    app.dependency_overrides[get_llm] = lambda: llm
    c = TestClient(app)
    run = c.post("/api/parse", files={
        "resume": ("cv.pdf", resume_pdf, "application/pdf"),
        "jd": ("jd.docx", jd_docx, "application/octet-stream"),
    }).json()["run_id"]
    c.post(f"/api/runs/{run}/gate")
    if proceed:
        c.post(f"/api/runs/{run}/gate/decide", json={"decision": "proceed", "reason": "testing"})
    return c, run, llm


def ch(state, cid):
    return next(x for x in state["rounds"][-1]["changes"] if x["id"] == cid)


def by_new(state, text):
    return next(x for x in state["rounds"][-1]["changes"] if x["new"] and text in x["new"])


def test_loop_needs_gate_proceed(tmp_workspace, resume_pdf, jd_docx):
    c, run, _ = setup(tmp_workspace, resume_pdf, jd_docx, proceed=False)
    r = c.post(f"/api/runs/{run}/loop/start")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "gate_not_passed"


def test_full_interactive_round(tmp_workspace, resume_pdf, jd_docx):
    c, run, llm = setup(tmp_workspace, resume_pdf, jd_docx)
    s = c.post(f"/api/runs/{run}/loop/start").json()
    r1 = s["rounds"][0]
    assert s["versions"][0]["n"] == 0 and r1["stage"] == "questions"
    assert len(r1["questions"]) <= 5
    assert "q:must:kafka" in [q["id"] for q in r1["questions"]]
    fix_by = {p["id"]: p["fix_by"] for p in r1["problems"]}
    assert fix_by["must:kafka"] == "you" and fix_by.get("title") == "none"

    # 1) answer -> template proposal -> accept (Kafka now in template)
    s = c.post(f"/api/runs/{run}/loop/answer", json={
        "question_id": "q:must:kafka",
        "answer": "Yes, at Company A I built a Kafka consumer pipeline that cut lag by 40%"}).json()
    pid = s["proposals"][-1]["id"]
    s = c.post(f"/api/runs/{run}/loop/proposals/{pid}", json={"accept": True}).json()
    s = c.post(f"/api/runs/{run}/loop/notes", json={"text": "Keep bullets short"}).json()
    assert s["rounds"][-1]["notes"] == ["Keep bullets short"]

    # 2) suggestions, each truth-checked
    s = c.post(f"/api/runs/{run}/loop/suggest").json()
    assert s["rounds"][-1]["stage"] == "review"
    good = by_new(s, "for the order platform")
    kafka = by_new(s, "Kafka consumer pipeline")
    lie = by_new(s, "team of 12")
    puffed = by_new(s, "Spearheaded")
    summary = by_new(s, "Backend engineer building")
    assert good["status"] == "pending" and good["truth_ok"]
    assert kafka["status"] == "pending" and kafka["sources"][0]["type"] == "template"
    assert kafka["sources"][0]["verified"] and kafka["job_label"] == "Software Engineer - Company A"
    assert lie["status"] == "blocked"
    assert any("12" in i for i in lie["truth_issues"]) and any("Kubernetes" in i for i in lie["truth_issues"])
    assert puffed["status"] == "pending" and not puffed["truth_ok"]        # AI flag = warning only
    assert any("overstates" in i for i in puffed["truth_issues"])

    # 3) review: blocked can't be accepted; you can edit it in your own words
    r = c.post(f"/api/runs/{run}/loop/changes/{lie['id']}", json={"action": "accept"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "blocked"
    s = c.post(f"/api/runs/{run}/loop/changes/{lie['id']}",
               json={"action": "edit", "text": "Moved reporting jobs onto Airflow"}).json()
    assert ch(s, lie["id"])["status"] == "accepted" and ch(s, lie["id"])["edited_by_you"]

    # revise with feedback -> AI redoes just that one, checks rerun
    s = c.post(f"/api/runs/{run}/loop/changes/{good['id']}",
               json={"action": "revise", "feedback": "make it shorter"}).json()
    redo = ch(s, good["id"])
    assert redo["new"] == "Built FastAPI REST APIs serving 2M requests per day"
    assert redo["feedback_history"] == ["make it shorter"] and redo["status"] == "pending"

    r = c.post(f"/api/runs/{run}/loop/apply")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "undecided"
    for cid, act in ((good["id"], "accept"), (kafka["id"], "accept"),
                     (puffed["id"], "reject"), (summary["id"], "reject")):
        c.post(f"/api/runs/{run}/loop/changes/{cid}", json={"action": act})

    # 4) apply -> v1 -> blind re-score
    s = c.post(f"/api/runs/{run}/loop/apply").json()
    rd = s["rounds"][-1]
    assert rd["stage"] == "applied" and rd["result_version"] == 1 and s["current_version"] == 1
    assert rd["score_after"] > rd["score_before"]          # Kafka is now shown in a bullet
    v1 = c.get(f"/api/runs/{run}/loop/versions/1").json()
    assert "Built a Kafka consumer pipeline that cut lag by 40%" in v1["text"]
    assert "Moved reporting jobs onto Airflow" in v1["text"]
    assert "Spearheaded" not in v1["text"] and "team of 12" not in v1["text"]
    assert s["recommendation"]

    # The judge never saw rounds, problems, notes or versions
    for inp in [i for i, k in zip(llm.inputs, llm.calls) if k == "matcher-score"]:
        for leak in ("PROBLEMS", "Round", "USER NOTES", "Keep bullets short", "version"):
            assert leak not in inp

    # 5) next round doesn't re-ask answered questions; rollback + finish
    r = c.post(f"/api/runs/{run}/loop/rollback", json={"version": 0})
    assert r.status_code == 200 and r.json()["current_version"] == 0
    s = c.post(f"/api/runs/{run}/loop/next").json()
    assert s["rounds"][-1]["n"] == 2 and s["rounds"][-1]["base_version"] == 0
    assert "q:must:kafka" not in [q["id"] for q in s["rounds"][-1]["questions"]]
    r = c.post(f"/api/runs/{run}/loop/rollback", json={"version": 1})
    assert r.json()["detail"]["code"] == "round_open"
    s = c.post(f"/api/runs/{run}/loop/finish", json={"version": 1}).json()
    assert s["finished"] and s["final_version"] == 1


def test_no_accepted_changes_counts_toward_plateau(tmp_workspace, resume_pdf, jd_docx):
    c, run, _ = setup(tmp_workspace, resume_pdf, jd_docx)
    c.post(f"/api/runs/{run}/loop/start")
    for _ in range(2):
        s = c.post(f"/api/runs/{run}/loop/suggest").json()
        for x in s["rounds"][-1]["changes"]:
            if x["status"] == "pending":
                c.post(f"/api/runs/{run}/loop/changes/{x['id']}", json={"action": "reject"})
        s = c.post(f"/api/runs/{run}/loop/apply").json()
        if "plateau" not in s["flags"]:
            s = c.post(f"/api/runs/{run}/loop/next").json()
    assert "plateau" in s["flags"] and s["current_version"] == 0


def test_apply_changes_by_text_not_index():
    r = Resume(experience=[Experience(company="A", bullets=["one", "two", "three"])])
    out = apply_changes(r, [
        Change(id="a", kind="remove_bullet", job=0, bullet=0, old="one"),
        Change(id="b", kind="replace_bullet", job=0, bullet=2, old="three", new="THREE"),
        Change(id="c", kind="add_bullet", job=0, new="four"),
        Change(id="d", kind="set_summary", new="Summary"),
    ])
    assert out.experience[0].bullets == ["two", "THREE", "four"] and out.summary == "Summary"
    assert r.experience[0].bullets == ["one", "two", "three"]   # original untouched


def test_choose_and_change_target(tmp_workspace, resume_pdf, jd_docx):
    c, run, _ = setup(tmp_workspace, resume_pdf, jd_docx)
    s = c.post(f"/api/runs/{run}/loop/start", json={"target": 90}).json()
    assert s["target"] == 90
    assert c.post(f"/api/runs/{run}/loop/target", json={"target": 95}).json()["target"] == 95
    assert c.post(f"/api/runs/{run}/loop/start", json={"target": 80}).json()["target"] == 80
    assert c.post(f"/api/runs/{run}/loop/target", json={"target": 120}).status_code == 409
    assert c.get(f"/api/runs/{run}/loop").json()["target"] == 80
