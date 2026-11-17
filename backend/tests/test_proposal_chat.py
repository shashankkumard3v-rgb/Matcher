"""Chat with a draft before saving it: tell the AI what to change until you're happy."""
import json

from app.api.routes import get_llm
from app.main import app
from tests.conftest import FakeLLM
from tests.test_gate import setup


class ChatLLM(FakeLLM):
    def __init__(self):
        super().__init__()
        self.notes_inputs = []

    async def run_skill(self, skill, user_input, model=None):
        if skill != "matcher-update-context" or "PREVIOUS DRAFT" not in user_input:
            return await super().run_skill(skill, user_input, model)
        self.notes_inputs.append(user_input)
        said = user_input.split("USER'S CHANGES", 1)[1]
        return {"action": "add_project", "summary": "Kafka pipeline at Yanolja.",
                "project": {"name": "Kafka work", "employer": "Yanolja", "role": "Backend Engineer",
                            "start": "2023-01", "end": "2024-01",
                            "what_i_did": ["Built a Kafka consumer pipeline at Yanolja that cut lag by 40%",
                                           "at Yanolja"],
                            "results": ["Handled 5M events per day"] if "5M" in said else ["Handled 9M events per day"]}}


def test_tell_the_ai_what_to_change_until_happy(tmp_workspace, resume_pdf, jd_docx):
    c, run, _ = setup(tmp_workspace, resume_pdf, jd_docx)
    llm = ChatLLM()
    app.dependency_overrides[get_llm] = lambda: llm
    g = c.post(f"/api/runs/{run}/gate").json()
    g = c.post(f"/api/runs/{run}/gate/answer", json={
        "question_id": "skill:kafka", "answer": "Yes, I built a Kafka consumer pipeline that cut lag by 40%"}).json()
    pid = g["proposals"][0]["id"]

    g = c.post(f"/api/runs/{run}/gate/proposals/{pid}/revise",
               json={"text": "It was at Yanolja, backend engineer, 2023 to 2024. It handled 5M events per day"}).json()
    p = next(x for x in g["proposals"] if x["id"] == pid)        # same draft, redone
    assert p["status"] == "pending" and p["project"]["employer"] == "Yanolja"
    assert p["chat"] == ["It was at Yanolja, backend engineer, 2023 to 2024. It handled 5M events per day"]
    assert "Handled 5M events per day" in p["project"]["results"]          # 5M: you said it
    assert "at Yanolja" not in p["project"]["what_i_did"]                  # fragments are left out
    sent = llm.notes_inputs[-1]
    assert "PREVIOUS DRAFT" in sent and "cut lag by 40%" in sent and "5M events" in sent

    # a second message keeps the whole conversation; numbers you never said are still removed
    g = c.post(f"/api/runs/{run}/gate/proposals/{pid}/revise", json={"text": "make it shorter"}).json()
    p = next(x for x in g["proposals"] if x["id"] == pid)
    assert len(p["chat"]) == 2 and "Handled 5M events per day" in p["project"]["results"]

    g = c.post(f"/api/runs/{run}/gate/proposals/{pid}", json={"accept": True}).json()
    assert next(x for x in g["proposals"] if x["id"] == pid)["status"] == "accepted"
    r = c.post(f"/api/runs/{run}/gate/proposals/{pid}/revise", json={"text": "more"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_pending"


def test_numbers_only_from_what_you_wrote(tmp_workspace, resume_pdf, jd_docx):
    c, run, _ = setup(tmp_workspace, resume_pdf, jd_docx)
    app.dependency_overrides[get_llm] = lambda: ChatLLM()
    c.post(f"/api/runs/{run}/gate")
    g = c.post(f"/api/runs/{run}/gate/answer", json={
        "question_id": "skill:kafka", "answer": "Yes, I built a Kafka consumer pipeline that cut lag by 40%"}).json()
    pid = g["proposals"][0]["id"]
    g = c.post(f"/api/runs/{run}/gate/proposals/{pid}/revise", json={"text": "It was at Yanolja"}).json()
    p = next(x for x in g["proposals"] if x["id"] == pid)
    assert "Handled 9M events per day" not in p["project"]["results"]        # 9M: nobody said it
    assert any("9" in w for w in p["warnings"])
