import io

from fastapi.testclient import TestClient

from app.api.routes import get_llm
from app.context import notes
from app.loop.diagnose import diagnose, template_text
from app.loop.truth import check_code
from app.main import app
from app.schemas.models import Change, ChangeSource, JobDescription, ProjectContext, Resume
from app.scoring.scorer import score_resume
from tests.conftest import FakeLLM


def test_templates_explain_themselves_and_comments_are_ignored(tmp_workspace):
    notes.ensure()
    for kind in notes.KINDS:
        raw = notes.read_raw(kind)
        assert raw.startswith("# ") and "Matcher ignores them" in raw
        assert notes.read_facts(kind) == []          # only hints and unfilled [placeholders]
    notes.save("skills", "# my note\nKafka - Company A - consumers\n\n  # indented comment\nGo - learning")
    assert notes.read_facts("skills") == ["Kafka - Company A - consumers", "Go - learning"]


def test_api_get_save_upload(tmp_workspace):
    c = TestClient(app)
    body = c.get("/api/context/notes").json()
    assert set(body) == {"experience", "skills"} and body["skills"]["facts"] == []
    r = c.put("/api/context/notes/experience", json={"text": "At Company A: mentored 2 engineers"})
    assert r.json()["facts"] == ["At Company A: mentored 2 engineers"]
    r = c.post("/api/context/notes/experience/upload", data={"mode": "append"},
               files={"file": ("more.txt", b"At Company B: led migration of 12 services", "text/plain")})
    assert r.json()["facts"] == ["At Company A: mentored 2 engineers", "At Company B: led migration of 12 services"]
    r = c.post("/api/context/notes/skills/upload", data={"mode": "replace"},
               files={"file": ("s.txt", "Café ops - Python".encode("cp1252"), "text/plain")})
    assert r.json()["facts"] == ["Café ops - Python"]                  # old Notepad encoding works
    assert c.post("/api/context/notes/skills/upload",
                  files={"file": ("x.pdf", b"%PDF", "application/pdf")}).status_code == 400
    assert c.put("/api/context/notes/other", json={"text": "x"}).status_code == 400


async def _parsed():
    llm = FakeLLM()
    r = Resume.model_validate(await llm.run_skill("matcher-parse-resume", "x"))
    jd = JobDescription.model_validate(await llm.run_skill("matcher-parse-jd", "x"))
    return r, jd


async def test_notes_count_toward_best_possible_score(tmp_workspace, resume_pdf, jd_docx):
    llm = FakeLLM()
    app.dependency_overrides[get_llm] = lambda: llm
    c = TestClient(app)
    run = c.post("/api/parse", files={"resume": ("cv.pdf", resume_pdf, "application/pdf"),
                                      "jd": ("jd.docx", jd_docx, "application/octet-stream")}).json()["run_id"]
    g = c.post(f"/api/runs/{run}/gate").json()
    assert [k["id"] for k in g["knockouts"]] == ["missing_must_haves"]   # Kafka + Kubernetes missing
    notes.save("skills", "Kafka - Company A - built consumer services for order events\n"
                         "Kubernetes - deployed services on EKS")      # 2nd names no company
    g2 = c.post(f"/api/runs/{run}/gate").json()
    assert g2["knockouts"] == [] and g2["ceiling_score"] > g["ceiling_score"]


async def test_notes_are_a_valid_source_and_diagnose_sees_them(tmp_workspace):
    r, jd = await _parsed()
    notes.save("experience", "At Company A: built a Kafka consumer service that cut lag by 40%")
    tpl = template_text(ProjectContext())
    assert "notes-experience" in tpl
    ch = Change(id="c1", kind="add_bullet", job=0, new="Built a Kafka consumer service that cut lag by 40%",
                sources=[ChangeSource(type="notes", project_id="experience",
                                      quote="built a Kafka consumer service that cut lag by 40%")])
    check_code(ch, ["resume text"], tpl, jd)
    assert ch.truth_ok and ch.sources[0].verified

    score = await score_resume(FakeLLM(), r, jd)
    probs = {p.id: p for p in diagnose(score, r, jd, ProjectContext())}
    assert probs["must:kafka"].fix_by == "ai" and "experience.txt" in probs["must:kafka"].detail
    assert probs["must:kubernetes"].fix_by == "you"
