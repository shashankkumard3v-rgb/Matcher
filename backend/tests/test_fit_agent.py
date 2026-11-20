import io

import pytest
from docx import Document
from fastapi.testclient import TestClient

from app.api.routes import get_llm
from tests.render_check import WHY, can_render
from app.main import app
from tests.conftest import FakeLLM
from tests.test_export import big_resume

pytestmark = pytest.mark.skipif(not can_render(), reason=WHY)


def finished_run(tmp_workspace, resume_pdf, jd_docx):
    llm = FakeLLM()
    app.dependency_overrides[get_llm] = lambda: llm
    c = TestClient(app)
    run = c.post("/api/parse", files={"resume": ("cv.pdf", resume_pdf, "application/pdf"),
                                      "jd": ("jd.docx", jd_docx, "application/octet-stream")}).json()["run_id"]
    c.post(f"/api/runs/{run}/gate")
    c.post(f"/api/runs/{run}/gate/decide", json={"decision": "proceed", "reason": "test"})
    c.post(f"/api/runs/{run}/loop/start")
    c.post(f"/api/runs/{run}/loop/finish", json={})
    return c, run


def accept_all(c, run, state):
    for ch in state["rounds"][-1]["changes"]:
        if ch["status"] == "pending":
            c.post(f"/api/runs/{run}/loop/changes/{ch['id']}", json={"action": "accept"})


def test_fit_agent_overflow(tmp_workspace, resume_pdf, jd_docx):
    c, run = finished_run(tmp_workspace, resume_pdf, jd_docx)
    (tmp_workspace / "runs" / run / "versions" / "resume_v0.json").write_text(big_resume(4, 7).model_dump_json())
    e = c.post(f"/api/runs/{run}/export").json()
    assert e["status"] == "overflow"

    s = c.post(f"/api/runs/{run}/export/fit/start").json()
    r = s["rounds"][-1]
    assert r["purpose"] == "fit" and r["layout"]["status"] == "overflow"
    assert any(p["id"] == "fit:overflow" for p in r["problems"])
    assert r["layout"]["goal"].startswith("Save at least")

    s = c.post(f"/api/runs/{run}/loop/suggest").json()
    changes = s["rounds"][-1]["changes"]
    assert changes and all(ch["lines_delta"] is not None for ch in changes)
    assert sum(ch["lines_delta"] for ch in changes) < 0          # they save lines
    assert all(ch["status"] == "pending" for ch in changes)       # same facts -> pass truth check

    accept_all(c, run, s)
    out = c.post(f"/api/runs/{run}/export/fit/apply").json()
    assert out["loop"]["final_version"] == 1                      # new version, blind re-scored
    assert out["loop"]["versions"][-1]["score"] is not None
    assert out["export"]["overflow_lines"] < e["overflow_lines"] or out["export"]["status"] != "overflow"
    assert "Fit round" in out["export"]["history"][-1]


def test_fit_agent_underfill_asks_then_adds(tmp_workspace, resume_pdf, jd_docx):
    c, run = finished_run(tmp_workspace, resume_pdf, jd_docx)
    e = c.post(f"/api/runs/{run}/export").json()
    assert e["status"] == "underfilled"

    s = c.post(f"/api/runs/{run}/export/fit/start").json()
    qs = {q["id"]: q for q in s["rounds"][-1]["questions"]}
    assert "q:fit:achievements" in qs and "q:fit:more:j0" in qs and len(qs) <= 5
    assert "room for about" in qs["q:fit:more:j0"]["text"]

    s = c.post(f"/api/runs/{run}/loop/answer", json={
        "question_id": "q:fit:achievements", "answer": "Won the company hackathon 2023"}).json()
    c.post(f"/api/runs/{run}/loop/proposals/{s['proposals'][-1]['id']}", json={"accept": True})
    s = c.post(f"/api/runs/{run}/loop/suggest").json()
    ch = s["rounds"][-1]["changes"][0]
    assert ch["kind"] == "add_achievement" and ch["status"] == "pending" and ch["lines_delta"] == 1
    accept_all(c, run, s)
    out = c.post(f"/api/runs/{run}/export/fit/apply").json()
    docx = c.get(f"/api/runs/{run}/export/file/docx").content
    texts = [p.text for p in Document(io.BytesIO(docx)).paragraphs]
    assert "ACHIEVEMENTS" in texts and "Won the company hackathon 2023" in texts


def test_fit_start_refused_when_already_fits(tmp_workspace, resume_pdf, jd_docx):
    c, run = finished_run(tmp_workspace, resume_pdf, jd_docx)
    (tmp_workspace / "runs" / run / "versions" / "resume_v0.json").write_text(big_resume(3, 5).model_dump_json())
    assert c.post(f"/api/runs/{run}/export").json()["status"] == "fitted"
    r = c.post(f"/api/runs/{run}/export/fit/start")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "already_fits"


def test_profile_links_become_clickable_labels(tmp_workspace, resume_pdf, jd_docx):
    from app import config
    c, run = finished_run(tmp_workspace, resume_pdf, jd_docx)
    text = config.PROJECT_CONTEXT_FILE.read_text()
    config.PROJECT_CONTEXT_FILE.write_text(
        text.replace("- Links:\n", "- Links:\n  - https://leetcode.com/u/priya\n", 1))
    c.post(f"/api/runs/{run}/export")
    doc = Document(io.BytesIO(c.get(f"/api/runs/{run}/export/file/docx").content))
    assert "LeetCode" in doc.paragraphs[1].text
    assert "https://leetcode.com/u/priya" in [r.target_ref for r in doc.part.rels.values()
                                              if "hyperlink" in r.reltype]
