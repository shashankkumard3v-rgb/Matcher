"""Step 3 chat: tell the AI what to change on the finished resume; code checks every change."""
import pytest

from app.api.routes import get_llm
from app.export.edit import apply
from tests.render_check import WHY, can_render
from app.main import app
from app.schemas.models import Education, Experience, Resume
from tests.conftest import FakeLLM


def _r():
    return Resume(summary="Backend engineer building Go services.",
                  skills=["Go", "Kafka", "Jira", "Docker"],
                  experience=[Experience(title="SDE", company="Gruve AI", bullets=[
                      "Cut p99 latency from 850ms to 120ms with Redis caching", "Built Kafka pipelines in Go",
                      "Wrote runbooks for on-call"])],
                  education=[Education(degree="B.E. ECE", institution="UIET Chandigarh", details=None)])


def test_moving_a_skill_to_another_line_is_layout_only():
    r, done, refused, layout = apply(_r(), [{"type": "set_skill_kind", "skill": "jira", "kind": "Backend"}],
                                     "move jira to backend", "")
    assert r.skill_kinds == {"Jira": "Backend"} and layout and done == ["Moved Jira to Backend."]
    from app.export.builder import skill_groups, skills_line_text
    assert any("Backend: Kafka, Jira" in skills_line_text(p) for p in skill_groups(r.skills, overrides=r.skill_kinds))
    _, _, refused, _ = apply(_r(), [{"type": "set_skill_kind", "skill": "Jira", "kind": "Magic"}], "", "")
    assert "use one of" in refused[0]


def test_edits_cant_bring_in_new_facts():
    actions = [{"type": "edit_bullet", "id": "j0b1", "new": "Built Kafka pipelines in Go handling 5M events a day"},
               {"type": "edit_bullet", "id": "j0b0", "new": "Cut p99 latency from 850ms to 120ms by caching in Redis"},
               {"type": "add_skill", "skill": "Rust"},
               {"type": "edit_education", "id": "e0", "field": "details", "new": "CGPA: 7.6/10"}]
    r, done, refused, layout = apply(_r(), actions, "add my CGPA 7.6/10", "")
    assert r.experience[0].bullets[0] == "Cut p99 latency from 850ms to 120ms by caching in Redis"
    assert r.experience[0].bullets[1] == "Built Kafka pipelines in Go"                    # 5M: nobody said it
    assert "Rust" not in r.skills and r.education[0].details == "CGPA: 7.6/10" and not layout
    assert any("5" in x for x in refused) and any("Rust" in x for x in refused)


def test_a_job_keeps_two_bullets_and_bad_ids_are_refused():
    r, done, refused, _ = apply(_r(), [{"type": "remove_bullet", "id": "j0b2"}, {"type": "remove_bullet", "id": "j0b1"},
                                       {"type": "edit_bullet", "id": "j9b9", "new": "x"}], "", "")
    assert len(r.experience[0].bullets) == 2 and "keeps at least 2" in refused[0] and "no bullet j9b9" in refused[1]


class ChatLLM(FakeLLM):
    async def run_skill(self, skill, user_input, model=None):
        if skill == "matcher-edit-resume":
            self.calls.append(skill)
            self.inputs.append(user_input)
            return {"reply": "Moved it.", "actions": [{"type": "set_skill_kind", "skill": "Docker", "kind": "Engineering"}]}
        return await super().run_skill(skill, user_input, model)


@pytest.mark.skipif(not can_render(), reason=WHY)
def test_chat_makes_a_new_version_and_rebuilds_the_page(tmp_workspace, resume_pdf, jd_docx):
    from tests.test_fit_agent import finished_run
    c, run = finished_run(tmp_workspace, resume_pdf, jd_docx)
    first = c.post(f"/api/runs/{run}/export").json()
    llm = ChatLLM()
    app.dependency_overrides[get_llm] = lambda: llm
    out = c.post(f"/api/runs/{run}/export/chat", json={"text": "put Docker under Engineering"}).json()
    assert out["version"] == first["version"] + 1
    assert [t["role"] for t in out["chat"]] == ["you", "ai"] and out["chat"][1]["done"] == ["Moved Docker to Engineering."]
    assert "matcher-score" not in llm.calls                     # moving a skill doesn't need a new score
    assert "RESUME:" in llm.inputs[-1] and "put Docker under Engineering" in llm.inputs[-1]
    assert c.get(f"/api/runs/{run}/export").json()["chat"][1]["text"] == "Moved it."
    # Regenerate: same version rebuilt, chat and history kept
    again = c.post(f"/api/runs/{run}/export?regenerate=true").json()
    assert again["version"] == out["version"] and len(again["chat"]) == 2
    assert again["history"][-1].startswith("Regenerated the page")
