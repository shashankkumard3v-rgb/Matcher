"""My resumes: upload, save from a run, rank for a JD, start a run from a saved resume."""
import pytest
from fastapi.testclient import TestClient

from app.api.routes import get_llm
from app.data import store as ds
from app.main import app
from tests.conftest import JD_PARAS, FakeLLM

JD_TEXT = "\n".join(JD_PARAS) + "\nRequirements: Python, FastAPI, Kafka, PostgreSQL, Kubernetes"


@pytest.fixture
def lib_client(tmp_workspace):
    llm = FakeLLM()
    app.dependency_overrides[get_llm] = lambda: llm
    ds.set_data_store(ds.MemoryDataStore())
    yield TestClient(app), llm
    ds.set_data_store(None)


def upload(c, resume_pdf, name="Backend"):
    r = c.post("/api/resumes", files={"file": ("cv.pdf", resume_pdf, "application/pdf")},
               data={"name": name, "notes": "for backend roles"})
    assert r.status_code == 200, r.text
    return r.json()


def test_first_time_nothing_is_saved(lib_client):
    c, _ = lib_client
    assert c.get("/api/resumes").json() == []
    r = c.post("/api/match", json={"jd_text": JD_TEXT}).json()
    assert r["results"] == [] and r["jd"]["title"]


def test_upload_profiles_strictly_and_lists_it(lib_client, resume_pdf):
    c, llm = lib_client
    saved = upload(c, resume_pdf)
    names = {s["name"]: s["level"] for s in saved["skills"]}
    assert names.get("FastAPI") == 3 and "Rust" not in names        # invented skill dropped
    assert {x["id"] for x in saved["capabilities"]} >= {"backend_apis"}
    assert saved["name"] == "Backend" and saved["notes"] == "for backend roles" and saved["has_file"]
    assert "matcher-profile-resume" in llm.calls
    profile_input = llm.inputs[llm.calls.index("matcher-profile-resume")]
    resume_part = profile_input.split("CAPABILITIES:")[0]
    assert "Acme Corp" not in resume_part and "Kafka" not in resume_part            # blind: no job description
    lst = c.get("/api/resumes").json()
    assert len(lst) == 1 and lst[0]["id"] == saved["id"]
    assert c.get(f"/api/resumes/{saved['id']}/file").content.startswith(b"%PDF")


def test_rename_delete_and_isolation(lib_client, resume_pdf):
    c, _ = lib_client
    rid = upload(c, resume_pdf)["id"]
    assert c.patch(f"/api/resumes/{rid}", json={"name": "Platform", "notes": "x"}).json()["name"] == "Platform"
    assert c.delete(f"/api/resumes/{rid}").json() == {"ok": True}
    assert c.get(f"/api/resumes/{rid}").status_code == 404 and c.get("/api/resumes").json() == []
    assert c.delete(f"/api/resumes/{rid}").status_code == 404


def test_rank_then_start_a_run_without_uploading(lib_client, resume_pdf):
    c, llm = lib_client
    a = upload(c, resume_pdf, "Backend")
    m = c.post("/api/match", json={"jd_text": JD_TEXT}).json()
    assert [r["resume_id"] for r in m["results"]] == [a["id"]] and m["results"][0]["score"] > 0
    assert "summary" in m["results"][0]
    n_parse = llm.calls.count("matcher-parse-resume")
    run = c.post("/api/runs/start", json={"resume_id": a["id"], "jd": m["jd"], "jd_text": m["jd_text"]})
    assert run.status_code == 200, run.text
    rid = run.json()["run_id"]
    assert llm.calls.count("matcher-parse-resume") == n_parse          # the saved resume isn't re-read
    got = c.get(f"/api/runs/{rid}").json()
    assert got["resume"]["experience"] and got["jd"]["title"] == m["jd"]["title"]
    # deleting the saved resume doesn't break the run
    c.delete(f"/api/resumes/{a['id']}")
    assert c.get(f"/api/runs/{rid}").status_code == 200
    assert c.post(f"/api/runs/{rid}/score").status_code == 200


def test_save_a_run_version_to_my_resumes(lib_client, resume_pdf):
    c, _ = lib_client
    a = upload(c, resume_pdf)
    m = c.post("/api/match", json={"jd_text": JD_TEXT}).json()
    rid = c.post("/api/runs/start", json={"resume_id": a["id"], "jd": m["jd"], "jd_text": m["jd_text"]}).json()["run_id"]
    assert c.post(f"/api/runs/{rid}/save-resume", json={}).status_code == 404     # nothing improved yet
    c.post(f"/api/runs/{rid}/gate")
    c.post(f"/api/runs/{rid}/gate/decide", json={"decision": "proceed", "reason": "t"})
    c.post(f"/api/runs/{rid}/loop/start")
    c.post(f"/api/runs/{rid}/loop/finish", json={})
    saved = c.post(f"/api/runs/{rid}/save-resume", json={"name": "Acme backend", "notes": "tailored"}).json()
    assert saved["name"] == "Acme backend" and saved["source"]["type"] == "run" and saved["skills"]
    assert len(c.get("/api/resumes").json()) == 2
