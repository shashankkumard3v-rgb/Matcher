"""Claude API client.

Skills mode (default): each backend/skills/<name>/ folder is uploaded to your Anthropic
account as a custom Agent Skill - automatically the first time it's used, and again as a
new version whenever its files change. Requests attach exactly one skill, pinned to that
version, plus the code execution tool that skills require.

Prompt mode (fallback): if your organization hasn't enabled Agent Skills or code
execution, the same SKILL.md is sent as the system prompt instead - exactly what the
Gemini/OpenRouter providers do. Set `claude.agent_skills` in models.yaml:
  auto (default) - try skills, fall back to prompts if they're not available
  on             - skills only (fail loudly)
  off            - prompts only
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app import config
from app.config import anthropic_api_key, models_config
from app.llm.base import LLMError, extract_json
from app.skills.loader import Skill, all_skills, load_skill

logger = logging.getLogger("matcher.llm.claude")

CODE_EXECUTION_TOOL = {"type": "code_execution_20250825", "name": "code_execution"}
MAX_CONTINUATIONS = 5

SYSTEM = (
    "You run one step of the Matcher resume pipeline. Follow the attached skill named in "
    "the user's message exactly. Your final message must be ONLY the JSON object the "
    "skill asks for, with no other text."
)


def _load_cache() -> dict[str, dict]:
    try:
        return json.loads(config.SKILL_CACHE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict[str, dict]) -> None:
    config.SKILL_CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")


class ClaudeSkillsClient:
    name = "claude"
    last_model: str | None = None

    def __init__(self, api_key: str | None = None, client: Any = None):
        key = api_key or anthropic_api_key()
        if client is None:
            if not key:
                raise LLMError("ANTHROPIC_API_KEY is not set in backend/.env")
            import anthropic  # imported lazily so OpenRouter-only installs still work

            cfg = models_config().get("claude", {}).get("defaults", {})
            client = anthropic.AsyncAnthropic(
                api_key=key, timeout=float(cfg.get("timeout_seconds", 300))
            )
        self.client = client
        cfg = models_config().get("claude", {})
        self.defaults: dict[str, Any] = cfg.get("defaults", {})
        self.steps: dict[str, Any] = cfg.get("steps", {})
        self.agent_skills = str(cfg.get("agent_skills", "auto")).lower()
        self._skills_ok = self.agent_skills != "off"
        self.fallback_reason: str | None = None
        self._lock = asyncio.Lock()

    @property
    def mode(self) -> str:
        return "skills" if self._skills_ok else "prompt"

    # ---------- skill upload / versioning ----------

    async def _find_remote(self, name: str):
        async for s in self.client.skills.list(source="custom"):
            if s.display_name == name:
                return s
        return None

    async def ensure_skill(self, skill: Skill) -> tuple[str, str]:
        """Return (skill_id, version_id) for this local skill, uploading if needed."""
        from anthropic.lib import files_from_dir

        async with self._lock:
            cache = _load_cache()
            entry = cache.get(skill.name)
            if entry and entry.get("hash") == skill.content_hash:
                return entry["skill_id"], entry["version_id"]

            files = files_from_dir(skill.folder)
            skill_id = (entry or {}).get("skill_id")
            if not skill_id:
                remote = await self._find_remote(skill.name)
                skill_id = remote.id if remote else None

            if skill_id:
                logger.info("Uploading new version of skill %s", skill.name)
                v = await self.client.skills.versions.create(skill_id, files=files)
                version_id = v.id
            else:
                logger.info("Creating skill %s", skill.name)
                created = await self.client.skills.create(display_name=skill.name, files=files)
                skill_id, version_id = created.id, created.latest_version_id

            cache[skill.name] = {
                "skill_id": skill_id, "version_id": version_id, "hash": skill.content_hash,
            }
            _save_cache(cache)
            return skill_id, version_id

    async def sync_all(self) -> dict[str, dict]:
        out = {}
        for s in all_skills():
            sid, vid = await self.ensure_skill(s)
            out[s.name] = {"skill_id": sid, "version_id": vid, "hash": s.content_hash}
        return out

    # ---------- running a step ----------

    def _model_for(self, step: str) -> str:
        model = self.steps.get(step, {}).get("model")
        if not model:
            raise LLMError(f"No Claude model configured for step '{step}' in models.yaml")
        return model

    def _params(self, model: str) -> dict[str, Any]:
        params: dict[str, Any] = {"model": model,
                                  "max_tokens": int(self.defaults.get("max_tokens", 8000))}
        if self.defaults.get("temperature") is not None:
            params["temperature"] = self.defaults["temperature"]
        return params

    @staticmethod
    def _feature_unavailable(e: Exception) -> bool:
        """400/403/404 on the skills path = Skills or code execution not enabled for you."""
        status = getattr(e, "status_code", None)
        return status in (400, 403, 404)

    async def run_skill(self, skill: str, user_input: str, model: str | None = None) -> dict:
        s = load_skill(skill)
        model = model or self._model_for(s.step)
        if self._skills_ok:
            try:
                return await self._run_with_skill(s, user_input, model)
            except LLMError as e:
                cause = e.__cause__
                if self.agent_skills == "auto" and cause is not None and self._feature_unavailable(cause):
                    self._skills_ok = False
                    self.fallback_reason = str(cause)[:300]
                    logger.warning("Agent Skills unavailable (%s); using SKILL.md as a prompt instead",
                                   self.fallback_reason)
                else:
                    raise
        return await self._run_as_prompt(s, user_input, model)

    async def _run_as_prompt(self, s: Skill, user_input: str, model: str) -> dict:
        try:
            resp = await self.client.messages.create(
                system=s.prompt_text, messages=[{"role": "user", "content": user_input}],
                **self._params(model))
        except Exception as e:
            raise LLMError(f"Claude request for '{s.name}' failed: {e}") from e
        if resp.stop_reason == "max_tokens":
            raise LLMError(f"{s.name}: reply was cut off (raise claude.defaults.max_tokens)")
        out = extract_json("\n".join(b.text for b in resp.content if getattr(b, "type", "") == "text"))
        self.last_model = model
        return out

    async def _run_with_skill(self, s: Skill, user_input: str, model: str) -> dict:
        skill = s.name
        try:
            skill_id, version_id = await self.ensure_skill(s)
        except Exception as e:
            raise LLMError(f"Couldn't upload skill '{skill}': {e}") from e
        params = {**self._params(model), "system": SYSTEM, "tools": [CODE_EXECUTION_TOOL]}
        container: dict[str, Any] = {
            "skills": [{"type": "custom", "skill_id": skill_id, "version": version_id}]
        }
        messages: list[dict] = [{
            "role": "user",
            "content": f"Use the {s.name} skill on this input.\n\n{user_input}",
        }]

        try:
            for _ in range(MAX_CONTINUATIONS + 1):
                resp = await self.client.messages.create(
                    container=container, messages=messages, **params
                )
                if resp.stop_reason != "pause_turn":
                    break
                # Long-running turn: hand the partial turn back so Claude continues
                messages = messages + [{"role": "assistant", "content": resp.content}]
                if getattr(resp, "container", None) is not None:
                    container = {"id": resp.container.id, "skills": container["skills"]}
            else:
                raise LLMError(f"{skill}: still paused after {MAX_CONTINUATIONS} continuations")
        except LLMError:
            raise
        except Exception as e:  # anthropic.APIError and friends
            raise LLMError(f"Claude request for '{skill}' failed: {e}") from e

        if resp.stop_reason == "max_tokens":
            raise LLMError(f"{skill}: reply was cut off (raise claude.defaults.max_tokens)")
        text = "\n".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        out = extract_json(text)
        self.last_model = model
        return out
