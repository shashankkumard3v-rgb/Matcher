"""Deleting a run: only your own, all of it, and nothing else."""
from fastapi.testclient import TestClient

from app import config
from app.api.routes import get_llm
from app.main import app
from tests.conftest import FakeLLM


def _new_run(c, resume_pdf, jd_docx):
    return c.post("/api/parse", files={"resume": ("cv.pdf", resume_pdf, "application/pdf"),
                                      "jd": ("jd.docx", jd_docx, "application/octet-stream")}).json()["run_id"]


def test_delete_run(tmp_workspace, resume_pdf, jd_docx):
    app.dependency_overrides[get_llm] = lambda: FakeLLM()
    c = TestClient(app)
    keep, gone = _new_run(c, resume_pdf, jd_docx), _new_run(c, resume_pdf, jd_docx)
    c.post(f"/api/runs/{gone}/score")
    assert c.delete(f"/api/runs/{gone}").json() == {"deleted": gone}
    assert not (config.runs_dir() / gone).exists()
    assert not any(p.name.startswith(".deleted-") for p in config.runs_dir().iterdir())
    assert [r["run_id"] for r in c.get("/api/runs").json()] == [keep]      # the other run is untouched
    assert c.get(f"/api/runs/{gone}").status_code == 404
    assert c.delete(f"/api/runs/{gone}").status_code == 404                # twice: not found


def test_delete_refuses_odd_ids(tmp_workspace):
    c = TestClient(app)
    config.runs_dir().mkdir(parents=True, exist_ok=True)
    (config.runs_dir() / "keep-me").mkdir()
    for bad in ("..", ".deleted-x", "a.b"):
        assert c.delete(f"/api/runs/{bad}").status_code in (404, 405)
    assert (config.runs_dir() / "keep-me").is_dir()


def test_old_server_is_reported(tmp_workspace, monkeypatch):
    """A server that missed a code update says so (the app then asks you to restart it)."""
    from app.api import routes
    c = TestClient(app)
    assert c.get("/api/app-version").json()["server_outdated"] is False
    monkeypatch.setattr(routes, "LOADED_STAMP", routes.code_stamp() - 60)
    assert c.get("/api/app-version").json()["server_outdated"] is True
    r = c.request("PATCH", "/api/runs/abc/nothing-here")                     # an unknown command
    assert r.status_code == 404 and "older code" in r.json()["detail"]["message"]
