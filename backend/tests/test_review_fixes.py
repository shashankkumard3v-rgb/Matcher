"""Regression tests for problems found in the code review."""
import asyncio

import httpx
from fastapi.testclient import TestClient

from app.api.routes import get_llm
from app.context import notes
from app.main import app
from app.schemas.models import Experience, JobDescription, ProjectContext, Resume
from tests.conftest import FakeLLM
from tests.test_loop import setup


def test_company_wide_is_a_fact_not_a_heading():
    e = notes.entries("Company: Acme\nCompany-wide rollout of Kafka to 40 services\nCompany - Beta\n- Did X\n")
    assert ("Acme", "Company-wide rollout of Kafka to 40 services") in e
    assert ("Beta", "Did X") in e


def test_ai_numbers_in_text_fields_dont_crash_a_proposal(tmp_workspace):
    from app.context import updater

    class LLM:
        async def run_skill(self, skill, text, model=None):
            return {"action": "add_project", "project": {
                "name": "Kafka work", "employer": "Acme", "start": 2023, "end": None, "team_size": 4,
                "what_i_did": ["Built Kafka consumers with a team of 4 in 2023"], "tech": ["Kafka", 2],
                "results": []}, "summary": "x", "missing_info": []}

    prop = asyncio.run(updater.propose(LLM(), "At Acme in 2023, a team of 4, I built Kafka consumers",
                                       Resume(experience=[Experience(company="Acme", title="SWE")])))
    assert prop.project.team_size == "4" and prop.project.start == "2023" and prop.project.tech == ["Kafka", "2"]


def test_updating_a_project_copies_only_the_new_facts(tmp_workspace):
    from app.context import updater
    from app.schemas.models import ContextProposal, Project
    first = ContextProposal(id="a", source="x", user_text="x", action="add_project",
                            project=Project(id="p", name="P", employer="Acme", what_i_did=["Built A thing"]))
    updater.apply(first)
    notes.save("experience", "Company: Acme\n- Something else entirely\n")   # you rewrote your notes
    second = ContextProposal(id="b", source="x", user_text="x", action="update_project",
                             project=Project(id="p", name="P", employer="Acme",
                                             what_i_did=["Built A thing", "Built B thing"]))
    updater.apply(second)
    raw = notes.read_raw("experience")
    assert "Built B thing" in raw and "Built A thing" not in raw   # old template fact not re-copied


def test_best_possible_respects_one_page_limits(tmp_workspace):
    from app.gate.ceiling import build_ceiling_resume
    notes.save("experience", "Company: Acme\nRole: SWE\n"
               + "".join(f"- Wrote internal wiki page number {i}\n" for i in range(10))
               + "- Built Kafka consumers in Python for payment events\n")
    jd = JobDescription(title="Backend", must_have=["Python", "Kafka"])
    r = build_ceiling_resume(Resume(experience=[Experience(company="Acme", title="SWE", bullets=["Wrote docs"])]),
                             ProjectContext(), jd, {"max_bullets_per_job": [3]})
    b = r.experience[0].bullets
    assert len(b) == 3 and "Built Kafka consumers in Python for payment events" in b
    assert not any(x.startswith("Role:") for x in b)


def test_download_mid_review_then_keep_improving_resumes_the_round(tmp_workspace, resume_pdf, jd_docx):
    c, run, _ = setup(tmp_workspace, resume_pdf, jd_docx)
    c.post(f"/api/runs/{run}/loop/start")
    s = c.post(f"/api/runs/{run}/loop/suggest").json()
    cid = s["rounds"][-1]["changes"][0]["id"]
    c.post(f"/api/runs/{run}/loop/changes/{cid}", json={"action": "accept"})
    s = c.post(f"/api/runs/{run}/loop/finish", json={}).json()
    assert s["finished"] and s["rounds"][-1]["stage"] == "closed"
    from app.loop import loop as loop_mod
    s = loop_mod.reopen(tmp_workspace / "runs" / run, "").model_dump()
    r = s["rounds"][-1]
    assert len(s["rounds"]) == 1 and r["stage"] == "review"
    assert next(x for x in r["changes"] if x["id"] == cid)["status"] == "accepted"


def test_two_changes_at_once_are_not_lost(tmp_workspace, resume_pdf, jd_docx):
    c, run, llm = setup(tmp_workspace, resume_pdf, jd_docx)
    s = c.post(f"/api/runs/{run}/loop/start").json()
    q1, q2 = [q["id"] for q in s["rounds"][-1]["questions"][:2]]

    class SlowLLM(FakeLLM):
        async def run_skill(self, skill, text, model=None):
            if skill == "matcher-update-context":
                await asyncio.sleep(0.3)   # the AI is reading the answer...
            return await super().run_skill(skill, text, model)

    app.dependency_overrides[get_llm] = lambda: SlowLLM()

    async def both():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t",
                                     cookies={"matcher_session": "test"}) as ac:
            async def skip_later():
                await asyncio.sleep(0.1)    # ...while you skip another question
                return await ac.post(f"/api/runs/{run}/loop/skip/{q2}")
            await asyncio.gather(
                ac.post(f"/api/runs/{run}/loop/answer", json={"question_id": q1, "answer": "At Company A I used it"}),
                skip_later())

    asyncio.run(both())
    st = {q["id"]: q["status"] for q in c.get(f"/api/runs/{run}/loop").json()["rounds"][-1]["questions"]}
    assert st[q1] == "answered" and st[q2] == "skipped"


def test_other_websites_cannot_read_the_api(tmp_workspace):
    c = TestClient(app)
    r = c.get("/api/runs", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers
    r = c.get("/api/runs", headers={"Origin": "http://localhost:5173"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"
