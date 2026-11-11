import json

from fastapi.testclient import TestClient

from app import config
from app.api.routes import get_llm
from app.main import app
from tests.conftest import FakeLLM


def setup(tmp_workspace, resume_pdf, jd_docx, **kw):
    llm = FakeLLM(**kw)
    app.dependency_overrides[get_llm] = lambda: llm
    c = TestClient(app)
    run_id = c.post("/api/parse", files={
        "resume": ("cv.pdf", resume_pdf, "application/pdf"),
        "jd": ("jd.docx", jd_docx, "application/octet-stream"),
    }).json()["run_id"]
    return c, run_id, llm


def q(state, qid):
    return next(x for x in state["questions"] if x["id"] == qid)


def test_full_interactive_flow(tmp_workspace, resume_pdf, jd_docx):
    c, run, _ = setup(tmp_workspace, resume_pdf, jd_docx)
    g = c.post(f"/api/runs/{run}/gate").json()

    # Kafka + Kubernetes missing everywhere -> knockout, below floor -> DON'T APPLY
    assert g["verdict"] == "DONT_APPLY"
    assert [k["id"] for k in g["knockouts"]] == ["missing_must_haves"]
    ids = [x["id"] for x in g["questions"]]
    assert ids[:2] == ["skill:kafka", "skill:kubernetes"]           # knockout questions first
    assert {"listed:python", "listed:postgresql"} <= set(ids)
    assert g["ceiling_score"] == g["current_score"] == 48.5           # empty template
    assert g["explanation"]["points_below_floor"] == 16.5
    assert g["explanation"]["missing_must_haves"] == ["Kafka", "Kubernetes"]
    assert g["explanation"]["top_losses"][0]["to_gain"]

    # Answer -> proposal only; template untouched until accepted
    before = config.PROJECT_CONTEXT_FILE.read_text()
    g = c.post(f"/api/runs/{run}/gate/answer", json={
        "question_id": "skill:kafka",
        "answer": "Yes, at Company A I built a Kafka consumer pipeline that cut lag by 40%"}).json()
    prop = g["proposals"][0]
    assert prop["status"] == "pending" and prop["action"] == "add_project"
    assert "## Project: Kafka work" in prop["after"] and "Cut consumer lag by 40%" in prop["after"]
    assert q(g, "skill:kafka")["status"] == "answered"
    assert config.PROJECT_CONTEXT_FILE.read_text() == before

    g = c.post(f"/api/runs/{run}/gate/proposals/{prop['id']}", json={"accept": True}).json()
    assert g["needs_recompute"] is True
    assert "## Project: Kafka work" in config.PROJECT_CONTEXT_FILE.read_text()

    r = c.post(f"/api/runs/{run}/gate/decide", json={"decision": "proceed"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "recompute_first"

    # Recompute: Kafka now counts, knockout clears, question resolved, ceiling rises
    g = c.post(f"/api/runs/{run}/gate").json()
    assert g["knockouts"] == []
    assert q(g, "skill:kafka")["status"] == "resolved"
    assert g["ceiling_score"] > g["current_score"]
    assert g["verdict"] == "DONT_APPLY"                               # still below 65

    # Proceeding past DON'T APPLY needs a reason, which is logged as an override
    r = c.post(f"/api/runs/{run}/gate/decide", json={"decision": "proceed"})
    assert r.json()["detail"]["code"] == "needs_override"
    g = c.post(f"/api/runs/{run}/gate/decide",
               json={"decision": "proceed", "reason": "Dream company, trying anyway"}).json()
    assert g["decision"] == "proceed" and g["verdict"] == "OVERRIDDEN"
    assert g["overrides"][0]["target"] == "verdict"

    # The original resume's score file was never touched by any of this
    run_dir = tmp_workspace / "runs" / run
    assert json.loads((run_dir / "score_0.json").read_text())["total"] == 48.5


def test_language_question_and_knockout(tmp_workspace, resume_pdf, jd_docx):
    c, run, _ = setup(tmp_workspace, resume_pdf, jd_docx, jd_languages=["German"])
    g = c.post(f"/api/runs/{run}/gate").json()
    assert "language:german" in [k["id"] for k in g["knockouts"]]
    lq = q(g, "language:german")
    assert lq["text"].startswith("This is required and your resume doesn't mention it. Do you want to add German?")

    g = c.post(f"/api/runs/{run}/gate/answer",
               json={"question_id": "language:german", "answer": "yes fluent"}).json()
    prop = g["proposals"][-1]
    assert prop["action"] == "add_profile" and "German (fluent)" in prop["after"]
    c.post(f"/api/runs/{run}/gate/proposals/{prop['id']}", json={"accept": True})
    g = c.post(f"/api/runs/{run}/gate").json()
    assert "language:german" not in [k["id"] for k in g["knockouts"]]


def test_override_knockout_survives_recompute(tmp_workspace, resume_pdf, jd_docx):
    c, run, _ = setup(tmp_workspace, resume_pdf, jd_docx)
    c.post(f"/api/runs/{run}/gate")
    g = c.post(f"/api/runs/{run}/gate/override",
               json={"target": "missing_must_haves", "reason": "I'll learn them"}).json()
    assert g["knockouts"][0]["status"] == "overridden"
    g = c.post(f"/api/runs/{run}/gate").json()
    assert g["knockouts"][0]["status"] == "overridden"
    assert g["verdict"] == "DONT_APPLY"   # still below the 65 floor; that's a separate override

    r = c.post(f"/api/runs/{run}/gate/override", json={"target": "nonsense"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "bad_target"


def test_no_answer_and_skip(tmp_workspace, resume_pdf, jd_docx):
    c, run, _ = setup(tmp_workspace, resume_pdf, jd_docx)
    c.post(f"/api/runs/{run}/gate")
    g = c.post(f"/api/runs/{run}/gate/answer",
               json={"question_id": "skill:kubernetes", "answer": "No, never used it"}).json()
    assert q(g, "skill:kubernetes")["status"] == "answered" and g["proposals"] == []
    g = c.post(f"/api/runs/{run}/gate/skip/listed:python").json()
    assert q(g, "listed:python")["status"] == "skipped"
    g = c.post(f"/api/runs/{run}/gate").json()          # answers survive a recompute
    assert q(g, "listed:python")["status"] == "skipped"


def test_unsaid_numbers_are_removed(tmp_workspace, resume_pdf, jd_docx):
    c, run, _ = setup(tmp_workspace, resume_pdf, jd_docx, invent_number=True)
    c.post(f"/api/runs/{run}/gate")
    g = c.post(f"/api/runs/{run}/gate/context",
               json={"text": "At Company A I ran Kafka consumers"}).json()
    prop = g["proposals"][0]
    assert "1000000" not in prop["after"]
    assert any("didn't say" in w for w in prop["warnings"])


def test_reject_and_stop(tmp_workspace, resume_pdf, jd_docx):
    c, run, _ = setup(tmp_workspace, resume_pdf, jd_docx)
    c.post(f"/api/runs/{run}/gate")
    g = c.post(f"/api/runs/{run}/gate/context", json={"text": "I used Kubernetes at Company A"}).json()
    pid = g["proposals"][0]["id"]
    g = c.post(f"/api/runs/{run}/gate/proposals/{pid}", json={"accept": False}).json()
    assert g["proposals"][0]["status"] == "rejected" and g["needs_recompute"] is False
    g = c.post(f"/api/runs/{run}/gate/decide", json={"decision": "stop"}).json()
    assert g["decision"] == "stop"


def test_gate_before_compute_is_404(tmp_workspace, resume_pdf, jd_docx):
    c, run, _ = setup(tmp_workspace, resume_pdf, jd_docx)
    assert c.get(f"/api/runs/{run}/gate").status_code == 404
    r = c.post(f"/api/runs/{run}/gate/override", json={"target": "verdict"})
    assert r.status_code == 404
