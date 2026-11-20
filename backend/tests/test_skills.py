import json
from types import SimpleNamespace

import pytest

from app import config
from app.llm import factory
from app.llm.base import LLMError
from app.llm.claude import CODE_EXECUTION_TOOL, ClaudeSkillsClient
from app.llm.openrouter import OpenRouterClient
from app.skills.loader import SKILL_STEP, SkillError, all_skills, load_skill, validate


# ---------- SKILL.md files ----------

def test_every_skill_is_valid_and_registered():
    names = {s.name for s in all_skills()}
    assert {"matcher-parse-resume", "matcher-parse-jd"} <= names
    for name in SKILL_STEP:
        load_skill(name)  # raises if missing or invalid


def test_skill_body_has_no_frontmatter():
    s = load_skill("matcher-parse-resume")
    assert s.body.startswith("# Parse resume")
    assert "name:" not in s.body.splitlines()[0]
    assert s.step == "parse"


@pytest.mark.parametrize("meta,err", [
    ({"name": "Bad_Name", "description": "x"}, "name must be"),
    ({"name": "my-claude-skill", "description": "x"}, "may not contain"),
    ({"name": "matcher-x", "description": ""}, "description"),
    ({"name": "matcher-x", "description": "x" * 1025}, "description"),
    ({"name": "other-name", "description": "x"}, "folder must be named"),
])
def test_validation_rules(tmp_path, meta, err):
    folder = tmp_path / "matcher-x"
    folder.mkdir()
    with pytest.raises(SkillError, match=err):
        validate(meta, folder)


# ---------- provider choice ----------

ALL_KEYS = ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY")


def only(monkeypatch, *keys):
    for k in ALL_KEYS:
        monkeypatch.delenv(k, raising=False)
    for k in keys:
        monkeypatch.setenv(k, "test-key")


def test_auto_prefers_claude_when_key_set(monkeypatch):
    only(monkeypatch, *ALL_KEYS)
    assert factory.provider_name() == "claude"


def test_auto_order_claude_gemini_openrouter(monkeypatch):
    only(monkeypatch, "GEMINI_API_KEY", "OPENROUTER_API_KEY")
    assert factory.provider_name() == "gemini"
    only(monkeypatch, "OPENROUTER_API_KEY")
    assert factory.provider_name() == "openrouter"


def test_openai_skipped_until_models_are_set(monkeypatch):
    only(monkeypatch, "OPENAI_API_KEY")       # models: [] in models.yaml by default
    with pytest.raises(LLMError, match="No usable AI provider"):
        factory.provider_name()


def test_no_keys_is_a_clear_error(monkeypatch):
    only(monkeypatch)
    with pytest.raises(LLMError, match="No usable AI provider"):
        factory.provider_name()


def test_make_llm_builds_the_right_client(monkeypatch):
    from app.llm.openai_compat import GeminiClient
    only(monkeypatch, "GEMINI_API_KEY")
    llm = factory.make_llm()
    assert isinstance(llm, GeminiClient) and llm.mode == "prompt" and llm.name == "gemini"
    assert llm.base_url == "https://generativelanguage.googleapis.com/v1beta/openai"


# ---------- OpenRouter: SKILL.md body becomes the system prompt ----------

async def test_openrouter_sends_skill_as_system_prompt(monkeypatch):
    sent = {}

    async def fake_call(self, client, model, messages, step=None):
        sent["model"], sent["messages"] = model, messages
        return '{"title": "X"}'

    monkeypatch.setattr(OpenRouterClient, "_call", fake_call)
    out = await OpenRouterClient(api_key="k").run_skill("matcher-parse-jd", "JD here")
    assert out == {"title": "X"}
    assert sent["messages"][0]["content"] == load_skill("matcher-parse-jd").body
    assert sent["messages"][1]["content"] == "JD here"
    assert sent["model"] == "deepseek/deepseek-v4-flash:free"


# ---------- Claude: real Agent Skills ----------

class FakeAnthropic:
    """Records calls to the parts of the Anthropic SDK we use."""

    def __init__(self, existing=None, pauses=0):
        self.created, self.versions, self.requests = [], [], []
        self._existing = existing or []
        self._pauses = pauses
        outer = self

        class Versions:
            async def create(self, skill_id, files):
                outer.versions.append((skill_id, [f[0] for f in files]))
                return SimpleNamespace(id=f"v{len(outer.versions) + 1}")

        class Skills:
            versions = Versions()

            def list(self, source=None):
                async def gen():
                    for s in outer._existing:
                        yield s
                return gen()

            async def create(self, display_name, files):
                outer.created.append((display_name, [f[0] for f in files]))
                return SimpleNamespace(id="skill_new", latest_version_id="v1")

        class Messages:
            async def create(self, **kw):
                outer.requests.append(kw)
                if outer._pauses:
                    outer._pauses -= 1
                    return SimpleNamespace(stop_reason="pause_turn", content=[],
                                           container=SimpleNamespace(id="cnt_1"))
                return SimpleNamespace(
                    stop_reason="end_turn", container=SimpleNamespace(id="cnt_1"),
                    content=[SimpleNamespace(type="text", text='{"title": "Engineer"}')],
                )

        self.skills = Skills()
        self.messages = Messages()


@pytest.fixture
def skill_cache(tmp_path, monkeypatch):
    path = tmp_path / ".claude_skills.json"
    monkeypatch.setattr(config, "SKILL_CACHE_FILE", path)
    return path


async def test_claude_uploads_once_then_reuses(skill_cache):
    fake = FakeAnthropic()
    c = ClaudeSkillsClient(client=fake)
    out = await c.run_skill("matcher-parse-jd", "JD here")
    assert out == {"title": "Engineer"}
    assert fake.created == [("matcher-parse-jd", ["matcher-parse-jd/SKILL.md"])]

    req = fake.requests[0]
    assert req["tools"] == [CODE_EXECUTION_TOOL]
    assert req["container"]["skills"] == [
        {"type": "custom", "skill_id": "skill_new", "version": "v1"}]
    assert req["model"] == "claude-sonnet-5-5"
    assert "matcher-parse-jd" in req["messages"][0]["content"]
    assert "temperature" not in req

    await c.run_skill("matcher-parse-jd", "JD again")
    assert len(fake.created) == 1 and fake.versions == []  # cached, no re-upload
    assert json.loads(skill_cache.read_text())["matcher-parse-jd"]["skill_id"] == "skill_new"


async def test_claude_new_version_when_skill_changes(skill_cache):
    skill_cache.write_text(json.dumps({"matcher-parse-jd": {
        "skill_id": "skill_old", "version_id": "v1", "hash": "outdated"}}))
    fake = FakeAnthropic()
    await ClaudeSkillsClient(client=fake).run_skill("matcher-parse-jd", "JD")
    assert fake.created == []
    assert fake.versions[0][0] == "skill_old"
    assert fake.requests[0]["container"]["skills"][0]["version"] == "v2"


async def test_claude_reuses_skill_already_in_account(skill_cache):
    fake = FakeAnthropic(existing=[SimpleNamespace(id="skill_acc", display_name="matcher-parse-jd")])
    await ClaudeSkillsClient(client=fake).run_skill("matcher-parse-jd", "JD")
    assert fake.created == [] and fake.versions[0][0] == "skill_acc"


async def test_claude_continues_after_pause_turn(skill_cache):
    fake = FakeAnthropic(pauses=2)
    out = await ClaudeSkillsClient(client=fake).run_skill("matcher-parse-jd", "JD")
    assert out == {"title": "Engineer"} and len(fake.requests) == 3
    assert fake.requests[-1]["container"]["id"] == "cnt_1"


def test_every_skill_has_a_model_in_both_providers():
    from app.config import models_config
    cfg = models_config()
    for s in all_skills():
        assert cfg["openrouter"]["steps"][s.step]["models"], s.name
        assert cfg["claude"]["steps"][s.step]["model"], s.name


def test_every_skill_has_models_in_every_configured_provider():
    from app.config import models_config
    cfg = models_config()
    for s in all_skills():
        for prov in ("gemini", "openrouter"):
            assert cfg[prov]["steps"][s.step]["models"], (prov, s.name)


# ---------- prompt mode gets extra files from the skill folder ----------

def test_prompt_text_includes_reference_files(tmp_path, monkeypatch):
    folder = tmp_path / "matcher-parse-jd"
    folder.mkdir()
    (folder / "SKILL.md").write_text("---\nname: matcher-parse-jd\ndescription: d\n---\n# Body\nRules here.")
    (folder / "references").mkdir()
    (folder / "references" / "examples.md").write_text("Example A")
    (folder / "helper.py").write_text("print(1)")
    monkeypatch.setattr(config, "SKILLS_DIR", tmp_path)
    text = load_skill("matcher-parse-jd").prompt_text
    assert text.startswith("# Body")
    assert "## File: references/examples.md" in text and "Example A" in text
    assert "helper.py" in text and "can't run here" in text and "print(1)" not in text


# ---------- Gemini / OpenAI-compatible over HTTP (mocked) ----------

def _mock_transport(replies):
    """httpx transport that returns queued (status, json) replies and records requests."""
    import httpx
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        status, body = replies.pop(0)
        return httpx.Response(status, json=body) if isinstance(body, dict) else httpx.Response(status, text=body)
    return httpx.MockTransport(handler), seen


async def test_gemini_runs_skill_as_prompt(monkeypatch):
    import httpx
    from app.llm import openai_compat
    from app.llm.openai_compat import GeminiClient
    transport, seen = _mock_transport([(200, {"choices": [{"finish_reason": "stop",
                                                           "message": {"content": '{"title": "X"}'}}]})])
    real = httpx.AsyncClient
    monkeypatch.setattr(openai_compat.httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))
    llm = GeminiClient(api_key="g")
    out = await llm.run_skill("matcher-parse-jd", "JD here")
    assert out == {"title": "X"} and llm.last_model == "gemini-3.5-flash-lite"   # parse step: lite first
    req = seen[0]
    assert req["model"] == "gemini-3.5-flash-lite" and req["response_format"] == {"type": "json_object"}
    assert req["messages"][0]["content"] == load_skill("matcher-parse-jd").prompt_text


async def test_json_mode_rejected_is_retried_without_it(monkeypatch):
    import httpx
    from app.llm import openai_compat
    from app.llm.openai_compat import GeminiClient
    transport, seen = _mock_transport([
        (400, "Invalid value for response_format"),
        (200, {"choices": [{"finish_reason": "stop", "message": {"content": '```json\n{"a": 1}\n```'}}]}),
    ])
    real = httpx.AsyncClient
    monkeypatch.setattr(openai_compat.httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))
    out = await GeminiClient(api_key="g").run_skill("matcher-parse-jd", "JD")
    assert out == {"a": 1} and "response_format" in seen[0] and "response_format" not in seen[1]


async def test_cut_off_reply_is_a_clear_error(monkeypatch):
    import httpx
    from app.llm import openai_compat
    from app.llm.openai_compat import GeminiClient
    reply = (200, {"choices": [{"finish_reason": "length", "message": {"content": '{"a": '}}]})
    transport, _ = _mock_transport([reply] * 50)
    real = httpx.AsyncClient
    monkeypatch.setattr(openai_compat.httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))
    monkeypatch.setattr(openai_compat.asyncio, "sleep", lambda *_: _noop())
    with pytest.raises(LLMError, match="cut off"):
        await GeminiClient(api_key="g").run_skill("matcher-score", "x")


async def _noop():
    return None


# ---------- Claude falls back to prompts when Agent Skills aren't enabled ----------

class _Forbidden(Exception):
    status_code = 403


async def test_claude_falls_back_to_prompt_when_skills_unavailable(skill_cache):
    fake = FakeAnthropic()

    async def denied(**kw):
        raise _Forbidden("Skills are not enabled for this organization")
    fake.skills.create = denied
    llm = ClaudeSkillsClient(client=fake)
    assert llm.mode == "skills"
    out = await llm.run_skill("matcher-parse-jd", "JD here")
    assert out == {"title": "Engineer"} and llm.mode == "prompt" and "not enabled" in llm.fallback_reason
    req = fake.requests[-1]
    assert "container" not in req and "tools" not in req
    assert req["system"] == load_skill("matcher-parse-jd").prompt_text


async def test_claude_agent_skills_off_uses_prompts(skill_cache, monkeypatch):
    from app.llm import claude as claude_mod
    cfg = {**claude_mod.models_config()}
    cfg["claude"] = {**cfg["claude"], "agent_skills": "off"}
    monkeypatch.setattr(claude_mod, "models_config", lambda: cfg)
    fake = FakeAnthropic()
    llm = ClaudeSkillsClient(client=fake)
    await llm.run_skill("matcher-parse-jd", "JD")
    assert llm.mode == "prompt" and fake.created == [] and "container" not in fake.requests[0]


async def test_claude_other_errors_do_not_fall_back(skill_cache):
    fake = FakeAnthropic()

    class _RateLimited(Exception):
        status_code = 429

    async def limited(**kw):
        raise _RateLimited("slow down")
    fake.skills.create = limited
    with pytest.raises(LLMError, match="slow down"):
        await ClaudeSkillsClient(client=fake).run_skill("matcher-parse-jd", "JD")


# ---------- the run lock knows the provider by name ----------

async def test_switching_provider_mid_run_is_refused():
    from app.schemas.models import JobDescription, Resume
    from app.scoring.scorer import ScoreError, current_settings, score_resume
    from tests.conftest import FakeLLM

    class GeminiFake(FakeLLM):
        name = "gemini"

    class OpenRouterFake(FakeLLM):
        name = "openrouter"

    r = Resume.model_validate(await FakeLLM().run_skill("matcher-parse-resume", "x"))
    jd = JobDescription.model_validate(await FakeLLM().run_skill("matcher-parse-jd", "x"))
    first = await score_resume(GeminiFake(), r, jd)
    assert first.pins["provider"] == "gemini" == current_settings(GeminiFake())["provider"]
    with pytest.raises(ScoreError, match="provider"):
        await score_resume(OpenRouterFake(), r, jd, pins=first.pins)


# ---------- retries: busy models, rate limits, missing models, bad keys ----------

def _routed_transport(by_model):
    """Reply per model: by_model[model] is a list of (status, body[, headers]) used in order."""
    import httpx
    calls = []

    def handler(request):
        model = json.loads(request.content)["model"]
        calls.append(model)
        queue = by_model.get(model) or [(503, {"error": {"message": "high demand"}})]
        status, body, *hdr = queue.pop(0) if len(queue) > 1 else queue[0]
        headers = hdr[0] if hdr else {}
        return httpx.Response(status, json=body, headers=headers)
    return httpx.MockTransport(handler), calls


def _client(monkeypatch, transport, sleeps):
    import httpx
    from app.llm import openai_compat
    from app.llm.openai_compat import GeminiClient
    real = httpx.AsyncClient
    monkeypatch.setattr(openai_compat.httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))
    llm = GeminiClient(api_key="g")

    async def fake_sleep(sec):
        sleeps.append(sec)
    llm._sleep = fake_sleep
    return llm


OK = (200, {"choices": [{"finish_reason": "stop", "message": {"content": '{"ok": true}'}}]})
BUSY = (503, {"error": {"code": 503, "message": "This model is currently experiencing high demand."}})


async def test_busy_model_moves_to_next_model_without_waiting(monkeypatch):
    transport, calls = _routed_transport({"gemini-3.8-flash": [BUSY], "gemini-3.6-flash": [OK]})
    sleeps = []
    llm = _client(monkeypatch, transport, sleeps)
    out = await llm.run_skill("matcher-score", "x")
    assert out == {"ok": True} and calls == ["gemini-3.8-flash", "gemini-3.6-flash"]
    assert sleeps == [] and llm.last_model == "gemini-3.6-flash"


async def test_all_busy_retries_in_passes_then_explains(monkeypatch):
    transport, calls = _routed_transport({})          # every model always 503
    sleeps = []
    llm = _client(monkeypatch, transport, sleeps)
    with pytest.raises(LLMError, match="overloaded right now") as e:
        await llm.run_skill("matcher-score", "x")
    n_models = len(llm.steps["score"]["models"])
    assert len(calls) == 3 * n_models and len(sleeps) == 2      # 3 passes, 2 waits between
    assert "HTTP 503" not in str(e.value) or "Tried:" in str(e.value)


async def test_busy_then_recovers_in_second_pass(monkeypatch):
    models = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite",
              "gemini-2.5-flash"]
    plan = {m: [BUSY] for m in models}
    plan["gemini-3.8-flash"] = [BUSY, OK]
    transport, calls = _routed_transport(plan)
    sleeps = []
    out = await _client(monkeypatch, transport, sleeps).run_skill("matcher-score", "x")
    assert out == {"ok": True} and calls[-1] == "gemini-3.8-flash" and len(sleeps) == 1


async def test_missing_model_is_dropped(monkeypatch):
    missing = (404, {"error": {"message": "models/gemini-2.5-flash is not found"}})
    plan = {"gemini-3.8-flash": [BUSY], "gemini-3.6-flash": [BUSY], "gemini-3.5-flash-lite": [BUSY],
            "gemini-3.1-flash-lite": [BUSY], "gemini-2.5-flash": [missing]}
    transport, calls = _routed_transport(plan)
    with pytest.raises(LLMError):
        await _client(monkeypatch, transport, []).run_skill("matcher-score", "x")
    assert calls.count("gemini-2.5-flash") == 1 and calls.count("gemini-3.8-flash") == 3


async def test_bad_key_stops_immediately(monkeypatch):
    transport, calls = _routed_transport({"gemini-3.8-flash": [(401, {"error": {"message": "API key not valid"}})]})
    with pytest.raises(LLMError, match="rejected the API key.*GEMINI_API_KEY"):
        await _client(monkeypatch, transport, []).run_skill("matcher-score", "x")
    assert calls == ["gemini-3.8-flash"]


async def test_rate_limit_waits_as_asked(monkeypatch):
    limited = (429, {"error": {"message": "quota"}}, {"retry-after": "7"})
    transport, calls = _routed_transport({"gemini-3.8-flash": [limited, OK]})
    sleeps = []
    out = await _client(monkeypatch, transport, sleeps).run_skill("matcher-score", "x", model="gemini-3.8-flash")
    assert out == {"ok": True} and sleeps[0] == 7.0 and calls == ["gemini-3.8-flash", "gemini-3.8-flash"]


async def test_a_busy_pinned_model_falls_back_to_the_others(monkeypatch):
    transport, calls = _routed_transport({"gemini-3.6-flash": [OK]})   # only another model works
    llm = _client(monkeypatch, transport, [])
    assert await llm.run_skill("matcher-score", "x", model="gemini-3.8-flash") == {"ok": True}
    assert calls[0] == "gemini-3.8-flash"            # the run's judge always gets the first try
    assert llm.last_model == "gemini-3.6-flash"      # the scorer reports who stood in


def test_models_endpoint_marks_available(monkeypatch):
    import httpx
    from fastapi.testclient import TestClient
    from app.llm import openai_compat
    from app.main import app

    def handler(request):
        return httpx.Response(200, json={"data": [{"id": "models/gemini-3.8-flash"},
                                                  {"id": "models/gemini-3.6-flash"}]})
    real = httpx.AsyncClient
    monkeypatch.setattr(openai_compat.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    body = TestClient(app).get("/api/providers/gemini/models").json()
    avail = {x["model"]: x["available"] for x in body["configured"]}
    assert avail["gemini-3.8-flash"] and not avail["gemini-2.5-flash"]


def test_reasoning_effort_is_sent_and_dropped_if_rejected(monkeypatch):
    import asyncio
    import httpx
    from app.llm.openai_compat import GeminiClient
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    c = GeminiClient()
    sent = []

    async def post(url, json=None, headers=None):
        sent.append(dict(json))
        if "reasoning_effort" in json:
            return httpx.Response(400, text='{"error": "Unknown name reasoning_effort"}')
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]})

    class FakeClient:
        pass
    fc = FakeClient()
    fc.post = post
    out = asyncio.run(c._call(fc, "gemini-x", [{"role": "user", "content": "hi"}], "context"))
    assert out == "{}" and sent[0]["reasoning_effort"] == "low" and "reasoning_effort" not in sent[1]
    assert c._effort_for("score") is None


async def test_another_provider_answers_when_the_first_cant(monkeypatch):
    from app.llm.base import LLMError
    from app.llm.factory import FallbackClient

    class Busy:
        name, last_model = "gemini", None
        async def run_skill(self, skill, user_input, model=None):
            raise LLMError("gemini is overloaded right now (all models busy).")

    class Works:
        name, last_model = "openrouter", None
        async def run_skill(self, skill, user_input, model=None):
            assert model is None                         # the pinned Gemini model isn't sent elsewhere
            self.last_model = "deepseek/deepseek-v4-flash:free"
            return {"ok": True}

    fb = FallbackClient.__new__(FallbackClient)
    fb.clients = [Busy(), Works()]
    fb.primary = fb.clients[0]
    assert await fb.run_skill("matcher-score", "x", model="gemini-3.8-flash") == {"ok": True}
    assert fb.last_model == "deepseek/deepseek-v4-flash:free" and fb.name == "gemini"
