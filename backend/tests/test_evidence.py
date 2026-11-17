"""Requirements your notes show in other words: found by the AI, checked by code, and used by
the best possible score, the gate questions and the improve loop."""
import re

from app.gate import evidence
from app.gate.gate import best_of
from app.loop.truth import check_code
from app.schemas.models import (Change, ChangeSource, Experience, FoundEvidence, JobDescription, Resume,
                                ScoreDimension, ScoreResult)
from tests.conftest import FakeLLM
from tests.test_gate import setup

EKS = "Wrote Helm charts and pod autoscaling rules for our event consumers"


class EvidenceLLM(FakeLLM):
    def __init__(self, bullet="Deployed our event consumers on Kubernetes with Helm charts and pod autoscaling rules"):
        super().__init__()
        self.bullet = bullet

    async def run_skill(self, skill, user_input, model=None):
        if skill != "matcher-find-evidence":
            return await super().run_skill(skill, user_input, model)
        self.calls.append(skill)
        nid = re.search(r"(N\d+) \[[^\]]*\] " + re.escape(EKS), user_input).group(1)
        return {"found": [{"gap": "Kubernetes", "notes": [nid], "job": 1, "bullet": self.bullet},
                          {"gap": "Kafka", "notes": ["N999"], "job": 0, "bullet": "Built Kafka things"}]}


def _gate(tmp_workspace, resume_pdf, jd_docx, llm):
    from app.api.routes import get_llm
    from app.context import notes
    from app.main import app
    notes.save("experience", f"Company: Company A\n- {EKS}\n")
    c, run, _ = setup(tmp_workspace, resume_pdf, jd_docx)
    app.dependency_overrides[get_llm] = lambda: llm
    return c, run


def test_notes_that_show_a_missing_requirement_raise_the_best_and_skip_the_question(tmp_workspace, resume_pdf, jd_docx):
    llm = EvidenceLLM()
    c, run = _gate(tmp_workspace, resume_pdf, jd_docx, llm)
    g = c.post(f"/api/runs/{run}/gate").json()
    found = g["found_in_notes"]
    assert [f["skill"] for f in found] == ["Kubernetes"]           # Kafka's made-up note id was dropped
    assert found[0]["quotes"] == [EKS] and found[0]["job"] == 0    # job from the "Company:" heading, not the AI's 1
    ids = [q["id"] for q in g["questions"]]
    assert "skill:kubernetes" not in ids and "skill:kafka" in ids  # don't ask what your notes already say
    assert g["ceiling_score"] > g["current_score"]
    assert "Kubernetes" in g["explanation"]["how_reached"]
    assert g["explanation"]["missing_must_haves"] == ["Kafka"]
    assert not [k for k in g["knockouts"] if k["id"] == "missing_must_haves"]   # 1 missing is allowed

    # asked once: the same notes and gaps use the saved answer
    c.post(f"/api/runs/{run}/gate")
    assert llm.calls.count("matcher-find-evidence") == 1


def test_a_bullet_with_a_number_the_notes_dont_have_is_dropped(tmp_workspace, resume_pdf, jd_docx):
    llm = EvidenceLLM(bullet="Deployed 40 event consumers on Kubernetes with Helm charts")
    c, run = _gate(tmp_workspace, resume_pdf, jd_docx, llm)
    g = c.post(f"/api/runs/{run}/gate").json()
    assert g["found_in_notes"] == []
    assert "skill:kubernetes" in [q["id"] for q in g["questions"]]


def test_check_needs_the_bullet_to_name_the_requirement():
    resume = Resume(experience=[Experience(title="SDE", company="Gruve AI", bullets=["x"])])
    lines = [("N1", "Company: Gruve AI", "Drew the data flow for payouts and chose a queue over polling")]
    raw = {"found": [{"gap": "System Design", "notes": ["N1"], "job": 0,
                      "bullet": "Drew the data flow for payouts and chose a queue over polling"}]}
    assert evidence.check(raw, ["System Design"], lines, resume, 32) == []      # doesn't name it
    raw["found"][0]["bullet"] = "Designed the payouts system architecture, choosing a queue over polling"
    got = evidence.check(raw, ["System Design"], lines, resume, 32)
    assert got[0].skill == "System Design" and got[0].job_label == "SDE - Gruve AI"


def test_the_loop_may_name_a_skill_your_notes_were_shown_to_have():
    jd = JobDescription(must_have=["Kubernetes"])
    ch = lambda: Change(id="c1", kind="add_bullet", job=0, new="Deployed event consumers on Kubernetes with Helm charts",
                        sources=[ChangeSource(type="notes", project_id="experience", quote=EKS)])
    tpl = {"notes-experience": EKS}
    plain = ch()
    check_code(plain, [], tpl, jd)
    assert not plain.truth_ok and any("Kubernetes" in i for i in plain.truth_issues)
    ok = ch()
    check_code(ok, [], tpl, jd, confirmed=[FoundEvidence(skill="Kubernetes", job=0, bullet="b", quotes=[EKS])])
    assert ok.truth_ok, ok.truth_issues


def _score(**pts):
    dims = [ScoreDimension(key=k, label=k, method="llm" if k == "relevance" else "deterministic",
                           weight=20, points=v) for k, v in pts.items()]
    return ScoreResult(total=sum(pts.values()), deterministic_points=0, llm_points=0, dimensions=dims,
                       must_have=[], nice_to_have=[], years_found=None, top_losses=[], warnings=[], pins={})


def test_best_possible_keeps_each_part_at_its_better_value():
    now = _score(skills=10, relevance=20)            # 30
    notes_added = _score(skills=18, relevance=10)    # 28: skills up, the judge a notch down
    best = best_of(now, notes_added)
    assert best.total == 38                           # before: 30 -> 30, every gain thrown away
    assert {d.key: d.points for d in best.dimensions} == {"skills": 18, "relevance": 20}


def test_the_improve_loop_starts_with_those_notes_and_doesnt_ask_again(tmp_workspace):
    from app.context import notes
    from app.loop.bullets import candidates
    from app.loop.diagnose import diagnose
    from app.parsing.pipeline import load_project_context
    notes.save("experience", f"Company: Gruve AI\n- {EKS}\n- Wrote unit tests for the billing screens\n")
    resume = Resume(experience=[Experience(title="SDE", company="Gruve AI", bullets=["Built REST APIs in Go"])])
    jd = JobDescription(must_have=["Kubernetes", "Go"])
    found = [FoundEvidence(skill="Kubernetes", job=0, job_label="SDE - Gruve AI", bullet="b", quotes=[EKS])]
    first = candidates(resume, jd, found=found)[0]
    assert first.text == EKS and first.shows[0] == "Kubernetes"
    s = _score()
    from app.config import weights_config
    from app.scoring.deterministic import score_deterministic
    cfg = weights_config()
    dims, must, nice, years, _ = score_deterministic(resume, jd, cfg["weights"], cfg.get("scoring", {}), set(), None)
    s = ScoreResult(total=0, deterministic_points=0, llm_points=0, dimensions=dims, must_have=must,
                    nice_to_have=nice, years_found=years, top_losses=[], warnings=[], pins={})
    p = next(p for p in diagnose(s, resume, jd, load_project_context(), found=found) if p.subject == "Kubernetes")
    assert p.fix_by == "ai" and "Your notes show it in other words" in p.detail
