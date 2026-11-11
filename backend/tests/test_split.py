import json

from app.loop import bullets, split
from app.schemas.models import Experience, JobDescription, Resume
from tests.conftest import FakeLLM
from tests.test_loop import setup

JD = JobDescription(title="Backend Engineer", must_have=["Python", "Kafka"], nice_to_have=["Redis"],
                    responsibilities=["Build and maintain Kafka-based pipelines for payment events"])


def _rep(n=3, cap=4):
    r = Resume(experience=[Experience(company="Acme", title="SWE", bullets=[f"Did thing number {i}" for i in range(n)])])
    rep = bullets.analyze(r, JD, None, {"max_bullets_per_job": [cap]})
    facts = {"f0": bullets.Candidate(job=0, text="Built Kafka consumers in Python", value=7, shows=["Kafka", "Python"]),
             "f1": bullets.Candidate(job=0, text="Built Redis cache for configs", value=1.5, shows=["Redis"])}
    return rep, facts


def test_validate_plan_respects_limits_and_ignores_junk(tmp_workspace):
    rep, facts = _rep(n=3, cap=4)
    raw = {"bullets": [{"id": "j0b0", "action": "remove"}, {"id": "j9b9", "action": "remove"},
                       {"id": "j0b1", "action": "explode"},
                       {"id": "j0b1", "action": "merge", "merge_with": "j0b2", "focus": "same work"}],
           "add": [{"job": 0, "facts": ["f0"]}, {"job": 0, "facts": ["f1"]}, {"job": 0, "facts": ["nope"]}],
           "summary": "rewrite"}
    plan = split.validate_plan(raw, rep, facts, split.rule_plan(rep, facts))
    assert plan["bullets"]["j0b0"]["action"] == "remove" and "j9b9" not in plan["bullets"]
    assert plan["bullets"]["j0b2"] == {"action": "merged_into", "into": "j0b1"}
    # 3 bullets - 1 removed - 1 merged = 1 left, cap 4 -> room for both adds
    assert [a["facts"] for a in plan["add"]] == [["f0"], ["f1"]] and plan["summary"] == "rewrite"


def test_adds_stop_at_the_cap_and_a_job_is_never_emptied(tmp_workspace):
    rep, facts = _rep(n=3, cap=4)
    plan = split.validate_plan({"add": [{"job": 0, "facts": ["f0"]}, {"job": 0, "facts": ["f1"]}]}, rep, facts, {})
    assert len(plan["add"]) == 1                      # 3 bullets, cap 4 -> one slot
    rep1, _ = _rep(n=1, cap=4)
    plan = split.validate_plan({"bullets": [{"id": "j0b0", "action": "remove"}]}, rep1, facts, {})
    assert "j0b0" not in plan["bullets"]


class SplitLLM(FakeLLM):
    def __init__(self, plan=None, fail_plan=False):
        super().__init__()
        self.plan, self.fail_plan = plan, fail_plan

    async def run_skill(self, skill, user_input, model=None):
        if skill == "matcher-plan-edits":
            self.calls.append(skill)
            self.inputs.append(user_input)
            if self.fail_plan:
                raise RuntimeError("model busy")
            return self.plan
        if skill in ("matcher-reword", "matcher-write-bullets"):
            self.calls.append(skill)
            self.inputs.append(user_input)
            items = json.loads(user_input.split("ITEMS:\n", 1)[1])
            out = []
            for it in items:
                if it.get("type") == "summary":
                    out.append({"id": it["id"], "new": "Backend engineer building Python services with FastAPI"})
                elif it["id"].startswith("r"):
                    out.append({"id": it["id"], "new": "Migrated reporting jobs from cron scripts to Airflow DAGs in Python"})
                else:
                    out.append({"id": it["id"], "new": "Built a Kafka consumer pipeline in Python that cut consumer lag by 40%"})
            return {"items": out}
        return await super().run_skill(skill, user_input, model)


def _start(tmp_workspace, resume_pdf, jd_docx, llm, monkeypatch):
    from app.api.routes import get_llm
    from app.context import notes
    from app.main import app
    monkeypatch.setenv("MATCHER_SUGGEST_MODE", "split")
    notes.save("experience", "Company: Company A\n"
               "- Built a Kafka consumer pipeline in Python that cut consumer lag by 40%\n"
               "- Migrated the reporting jobs from cron scripts to Airflow DAGs written in Python\n")
    c, run, _ = setup(tmp_workspace, resume_pdf, jd_docx)
    app.dependency_overrides[get_llm] = lambda: llm
    c.post(f"/api/runs/{run}/loop/start")
    return c, run


def test_split_round_plans_then_rewords_and_writes(tmp_workspace, resume_pdf, jd_docx, monkeypatch):
    llm = SplitLLM(plan={"bullets": [{"id": "j0b1", "action": "reword", "focus": "bring out Python"}],
                         "add": [{"job": 0, "facts": ["f0"], "focus": "shows Kafka"}], "summary": "keep"})
    c, run = _start(tmp_workspace, resume_pdf, jd_docx, llm, monkeypatch)
    s = c.post(f"/api/runs/{run}/loop/suggest").json()
    r = s["rounds"][-1]
    assert [x for x in llm.calls if x.startswith("matcher-") and x not in ("matcher-score", "matcher-parse-resume",
            "matcher-parse-jd", "matcher-truth-check")] == ["matcher-plan-edits", "matcher-reword", "matcher-write-bullets"]
    kinds = {ch["kind"]: ch for ch in r["changes"]}
    rw, add = kinds["replace_bullet"], kinds["add_bullet"]
    assert rw["old"].startswith("Migrated reporting jobs") and "Python" in rw["new"]
    assert rw["status"] == "pending" and all(src["verified"] for src in rw["sources"])
    assert add["status"] == "pending" and "Kafka" in add["new"] and add["from_notes"]
    assert r["plan"]["bullets"]["j0b1"]["action"] == "reword" and not r["plan"]["used_rules"]
    # the reword call only saw the facts for its item, not the whole resume
    reword_input = llm.inputs[llm.calls.index("matcher-reword")]
    assert "Built REST APIs" not in reword_input


def test_plan_failure_falls_back_to_rules(tmp_workspace, resume_pdf, jd_docx, monkeypatch):
    llm = SplitLLM(fail_plan=True)
    c, run = _start(tmp_workspace, resume_pdf, jd_docx, llm, monkeypatch)
    s = c.post(f"/api/runs/{run}/loop/suggest").json()
    r = s["rounds"][-1]
    assert r["plan"]["used_rules"] and r["changes"]


def test_a_note_feeding_a_reword_is_not_added_again(tmp_workspace):
    r = Resume(experience=[Experience(company="Acme", title="SWE", bullets=[
        "Wrote the consumer daemon for settlement transactions"])])
    from app.context import notes  # noqa: F401  (analyze reads notes; none here)
    rep = bullets.analyze(r, JD, None, {"max_bullets_per_job": [4]})
    rep.bullets[0].related_notes = ["Built the consumer daemon for settlement transactions in Python on Kafka"]
    facts = {"f0": bullets.Candidate(job=0, text="Built the consumer daemon for settlement transactions in Python on Kafka",
                                     value=6, shows=["Kafka"])}
    plan = split.validate_plan({"bullets": [{"id": "j0b0", "action": "reword"}],
                                "add": [{"job": 0, "facts": ["f0"]}]}, rep, facts, {})
    assert plan["bullets"]["j0b0"]["action"] == "reword" and plan["add"] == []
