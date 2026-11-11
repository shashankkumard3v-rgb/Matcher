from fastapi.testclient import TestClient

from app import config
from app.api.routes import get_llm
from app.main import app
from tests.conftest import FakeLLM


def test_runs_list_and_detail(tmp_workspace, resume_pdf, jd_docx):
    app.dependency_overrides[get_llm] = lambda: FakeLLM()
    c = TestClient(app)
    assert c.get("/api/runs").json() == []
    run = c.post("/api/parse", files={"resume": ("cv.pdf", resume_pdf, "application/pdf"),
                                      "jd": ("jd.docx", jd_docx, "application/octet-stream")}).json()["run_id"]
    runs = c.get("/api/runs").json()
    assert runs[0]["run_id"] == run and runs[0]["stage"] == "parsed" and runs[0]["score"] is None
    assert runs[0]["job_title"] == "Senior Backend Engineer" and runs[0]["candidate"] == "Priya Sharma"
    c.post(f"/api/runs/{run}/score")
    detail = c.get(f"/api/runs/{run}").json()
    assert detail["stage"] == "scored" and detail["score"]["total"] > 0
    assert detail["jd"]["company"] == "Acme Corp" and detail["resume"]["experience"]
    assert c.get("/api/runs/nope").status_code == 404


def test_web_app_is_served_with_spa_fallback(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    monkeypatch.setattr(config, "FRONTEND_DIST", dist)
    c = TestClient(app)
    assert c.get("/").text == "<html>app</html>"
    assert c.get("/runs/anything").text == "<html>app</html>"          # deep links -> index.html
    r = c.get("/assets/app.js")
    assert r.text == "console.log(1)" and "immutable" in r.headers["cache-control"]
    assert c.get("/../config.py").status_code in (200, 404) and "app" in c.get("/../config.py").text
    assert c.get("/api/does-not-exist").status_code == 404           # API paths never fall back
    assert c.get("/docs").status_code == 200                          # API page still works


def test_no_build_gives_clear_404(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FRONTEND_DIST", tmp_path / "missing")
    r = TestClient(app).get("/")
    assert r.status_code == 404 and "isn't built" in r.json()["detail"]
