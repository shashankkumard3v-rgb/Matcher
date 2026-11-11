import json

from fastapi.testclient import TestClient

from app.api.routes import get_llm
from app.main import app
from app.parsing.structure import _norm, found_in_source
from tests.conftest import FakeLLM, make_blank_pdf


def client_with(llm):
    app.dependency_overrides[get_llm] = lambda: llm
    return TestClient(app)


def test_found_in_source_handles_line_wraps():
    src = _norm("Built REST APIs in FastAPI serving 2M\nrequests per day")
    assert found_in_source("Built REST APIs in FastAPI serving 2M requests per day", src)
    assert not found_in_source("Led a team of 12 engineers", src)


def test_parse_happy_path(tmp_workspace, resume_pdf, jd_docx):
    c = client_with(FakeLLM())
    r = c.post("/api/parse", files={
        "resume": ("cv.pdf", resume_pdf, "application/pdf"),
        "jd": ("jd.docx", jd_docx, "application/octet-stream"),
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["jd"]["must_have"][1] == "Kafka"
    codes = {w["code"] for w in body["warnings"]}
    assert "unverified_bullet" not in codes
    assert "no_projects" in codes  # fresh context = template only
    run = tmp_workspace / "runs" / body["run_id"]
    for f in ("resume.json", "jd.json", "projects.json", "resume_text.txt", "inputs/cv.pdf"):
        assert (run / f).exists(), f
    assert json.loads((run / "jd.json").read_text())["company"] == "Acme Corp"


def test_invented_bullet_is_flagged(tmp_workspace, resume_pdf, jd_docx):
    c = client_with(FakeLLM(invent_bullet=True))
    r = c.post("/api/parse", files={
        "resume": ("cv.pdf", resume_pdf, "application/pdf"),
        "jd": ("jd.docx", jd_docx, "application/octet-stream"),
    })
    msgs = [w["message"] for w in r.json()["warnings"] if w["code"] == "unverified_bullet"]
    assert len(msgs) == 1 and "Led a team of 12" in msgs[0]


def test_scanned_resume_returns_422(tmp_workspace, jd_docx):
    c = client_with(FakeLLM())
    r = c.post("/api/parse", files={
        "resume": ("cv.pdf", make_blank_pdf(), "application/pdf"),
        "jd": ("jd.docx", jd_docx, "application/octet-stream"),
    })
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "scanned_pdf"


def test_context_roundtrip_and_employer_check(tmp_workspace, resume_pdf, jd_docx):
    c = client_with(FakeLLM())
    text = """## Project: Pipeline
- Employer: Company A
- Role: SWE
- Start: 2023-01
- End: 2023-06
- What I did:
  - Built it

## Project: Side thing
- Employer: Startup Z
- Role: Dev
- Start: 2022-01
- End: 2022-02
- What I did:
  - Did it
"""
    r = c.put("/api/context", json={"text": text})
    assert r.status_code == 200 and len(r.json()["parsed"]["projects"]) == 2
    assert c.get("/api/context").json()["text"] == text

    r = c.post("/api/parse", files={
        "resume": ("cv.pdf", resume_pdf, "application/pdf"),
        "jd": ("jd.docx", jd_docx, "application/octet-stream"),
    })
    w = [x for x in r.json()["warnings"] if x["code"] == "employer_not_on_resume"]
    assert len(w) == 1 and "Startup Z" in w[0]["message"]


def test_score_endpoint_pins_and_numbers_files(tmp_workspace, resume_pdf, jd_docx):
    llm = FakeLLM()
    c = client_with(llm)
    run_id = c.post("/api/parse", files={
        "resume": ("cv.pdf", resume_pdf, "application/pdf"),
        "jd": ("jd.docx", jd_docx, "application/octet-stream"),
    }).json()["run_id"]

    r1 = c.post(f"/api/runs/{run_id}/score")
    assert r1.status_code == 200, r1.text
    r2 = c.post(f"/api/runs/{run_id}/score")
    assert r1.json()["file"] == "score_0.json" and r2.json()["file"] == "score_1.json"
    assert r1.json()["score"]["total"] == r2.json()["score"]["total"]
    run = tmp_workspace / "runs" / run_id
    assert (run / "pins.json").exists()
    # the judge never saw the run id or which score this was
    assert all(run_id not in i for i in llm.inputs if "JOB DESCRIPTION" in i)

    pins = json.loads((run / "pins.json").read_text())
    pins["skill_hash"] = "edited-mid-run"
    (run / "pins.json").write_text(json.dumps(pins))
    r3 = c.post(f"/api/runs/{run_id}/score")
    assert r3.status_code == 409 and r3.json()["detail"]["code"] == "settings_changed"


def test_score_unknown_run_is_404(tmp_workspace):
    c = client_with(FakeLLM())
    assert c.post("/api/runs/nope/score").status_code == 404
    assert c.post("/api/runs/..%2F..%2Fetc/score").status_code == 404


JD_TEXT = ("Senior Backend Engineer - Acme Corp\n\nWe are looking for a backend engineer with 4+ years "
           "of experience.\nRequirements: Python, Kafka, PostgreSQL, Kubernetes\nNice to have: Go, Terraform")


def test_parse_with_pasted_jd_text(tmp_workspace, resume_pdf):
    c = client_with(FakeLLM())
    r = c.post("/api/parse", files={"resume": ("cv.pdf", resume_pdf, "application/pdf")},
               data={"jd_text": JD_TEXT})
    assert r.status_code == 200, r.text
    run = tmp_workspace / "runs" / r.json()["run_id"]
    assert (run / "inputs" / "jd.txt").read_text() == JD_TEXT
    assert "Kafka" in (run / "jd_text.txt").read_text()


def test_pasted_jd_too_short_or_missing(tmp_workspace, resume_pdf):
    c = client_with(FakeLLM())
    r = c.post("/api/parse", files={"resume": ("cv.pdf", resume_pdf, "application/pdf")},
               data={"jd_text": "Python dev"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "jd_too_short"
    r = c.post("/api/parse", files={"resume": ("cv.pdf", resume_pdf, "application/pdf")})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "no_jd"


def test_same_second_runs_get_unique_folders(tmp_workspace):
    from app.parsing.pipeline import new_run_dir
    a, b, c = (new_run_dir("Acme", "Engineer") for _ in range(3))
    assert len({a, b, c}) == 3 and all(p.is_dir() for p in (a, b, c))


def test_no_empty_warning_when_notes_have_facts(tmp_workspace, resume_pdf):
    from app.context import notes
    notes.save("experience", "Company: Nowhere Ltd\n- Built a thing\n")
    c = client_with(FakeLLM())
    r = c.post("/api/parse", files={"resume": ("cv.pdf", resume_pdf, "application/pdf")},
               data={"jd_text": JD_TEXT})
    w = r.json()["warnings"]
    assert "no_projects" not in {x["code"] for x in w}
    assert any(x["code"] == "employer_not_on_resume" and "Nowhere Ltd" in x["message"] for x in w)
