"""Prompt-mode client for any OpenAI-compatible chat API: Gemini, OpenRouter, OpenAI,
Groq, a local Ollama, ...

Each step's skill (SKILL.md + any extra text files in its folder) is sent as the system
prompt. Which models each step uses, and their fallbacks, come from that provider's
section in config/models.yaml.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from typing import Any

import httpx

from app.config import models_config
from app.llm.base import LLMError, extract_json
from app.skills.loader import load_skill

logger = logging.getLogger("matcher.llm")


class ProviderHTTPError(LLMError):
    """An HTTP error from the provider, with what we need to decide how to retry."""

    def __init__(self, provider: str, model: str, status: int, body: str, retry_after: float | None):
        self.provider, self.model, self.status, self.retry_after = provider, model, status, retry_after
        self.short = _short_reason(status, body)
        super().__init__(f"{provider}/{model}: HTTP {status} - {self.short}")

    @property
    def kind(self) -> str:
        if self.status in (401, 403):
            return "auth"          # bad/missing key or no access: retrying won't help
        if self.status == 404:
            return "missing"       # this model doesn't exist for your key: skip it
        if self.status == 429:
            return "rate_limit"    # too many requests: wait as asked
        if self.status in (500, 502, 503, 504):
            return "busy"          # provider overloaded: try another model / later
        return "other"


def _short_reason(status: int, body: str) -> str:
    try:
        data = json.loads(body)
        if isinstance(data, list) and data:
            data = data[0]
        msg = (data.get("error") or {}).get("message") if isinstance(data, dict) else None
        if msg:
            return msg.strip()[:200]
    except (ValueError, AttributeError):
        pass
    return (body or "").strip()[:200]


def _retry_after(resp: httpx.Response) -> float | None:
    v = resp.headers.get("retry-after")
    try:
        return min(float(v), 60.0) if v else None
    except ValueError:
        return None


class OpenAICompatClient:
    mode = "prompt"
    name = "openai"               # provider key in models.yaml
    default_base_url = "https://api.openai.com/v1"
    key_env = "OPENAI_API_KEY"
    base_url_env: str | None = "OPENAI_BASE_URL"
    last_model: str | None = None

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key or os.getenv(self.key_env) or None
        if not self.api_key:
            raise LLMError(f"{self.key_env} is not set. Add it to backend/.env.")
        cfg = models_config().get(self.name, {}) or {}
        self.base_url = (base_url or (os.getenv(self.base_url_env) if self.base_url_env else None)
                         or cfg.get("base_url") or self.default_base_url).rstrip("/")
        self.defaults: dict[str, Any] = cfg.get("defaults", {}) or {}
        self.steps: dict[str, Any] = cfg.get("steps", {}) or {}
        self._json_mode = bool(self.defaults.get("json_mode", True))
        self._reasoning_ok = True   # turned off if the API rejects "reasoning_effort"

    # ----- per-provider hooks -----

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _models_for(self, step: str) -> list[str]:
        models = (self.steps.get(step) or {}).get("models")
        if not models:
            raise LLMError(f"No {self.name} models configured for step '{step}' in models.yaml")
        return models

    def _effort_for(self, step: str | None) -> str | None:
        """How hard the model should "think" for this step (models.yaml reasoning_effort).
        Lower = faster replies. None = the model's default."""
        if not step:
            return self.defaults.get("reasoning_effort")
        return (self.steps.get(step) or {}).get("reasoning_effort", self.defaults.get("reasoning_effort"))

    async def _call(self, client: httpx.AsyncClient, model: str, messages: list[dict],
                    step: str | None = None) -> str:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": int(self.defaults.get("max_tokens", 4000)),
        }
        effort = self._effort_for(step) if self._reasoning_ok else None
        if effort:
            payload["reasoning_effort"] = effort
        if self.defaults.get("temperature") is not None:
            payload["temperature"] = self.defaults["temperature"]
        if self._json_mode:
            payload["response_format"] = {"type": "json_object"}
        resp = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=self.headers())
        if resp.status_code == 400 and "reasoning" in resp.text.lower() and "reasoning_effort" in payload:
            logger.info("%s: reasoning_effort not supported for %s; retrying without it", self.name, model)
            self._reasoning_ok = False
            payload.pop("reasoning_effort", None)
            resp = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=self.headers())
        if resp.status_code == 400 and self._json_mode and "response_format" in resp.text:
            # Some models/APIs don't support JSON mode: the prompt already demands JSON.
            logger.info("%s: JSON mode not supported for %s; retrying without it", self.name, model)
            self._json_mode = False
            payload.pop("response_format", None)
            resp = await client.post(f"{self.base_url}/chat/completions", json=payload,
                                     headers=self.headers())
        if resp.status_code != 200:
            raise ProviderHTTPError(self.name, model, resp.status_code, resp.text, _retry_after(resp))
        data = resp.json()
        try:
            choice = data["choices"][0]
            if choice.get("finish_reason") == "length":
                raise LLMError(f"{self.name}/{model}: reply cut off (raise max_tokens in models.yaml)")
            return choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"{self.name}/{model}: unexpected response {str(data)[:300]}") from e

    async def _sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    async def run_skill(self, skill: str, user_input: str, model: str | None = None) -> dict:
        """Try every configured model in turn; repeat in a few passes with growing waits.

        - busy (5xx)       -> move on to the next model now; retry it in the next pass
        - rate limit (429) -> wait as long as the provider asks, then continue
        - missing (404)    -> drop that model for this request
        - auth (401/403)   -> stop: the key is wrong or has no access
        A pinned `model` (used by the blind scorer) is never swapped for another one.
        """
        s = load_skill(skill)
        messages = [
            {"role": "system", "content": s.prompt_text},
            {"role": "user", "content": user_input},
        ]
        passes = max(int(self.defaults.get("retries_per_model", 3)), 1)
        waits = [float(x) for x in self.defaults.get("backoff_seconds", [2, 8, 20])]
        timeout = float(self.defaults.get("timeout_seconds", 120))
        models = [model] if model else list(self._models_for(s.step))
        last: dict[str, str] = {}
        async with httpx.AsyncClient(timeout=timeout) as client:
            for n in range(passes):
                if not models:
                    break
                for m in list(models):
                    try:
                        logger.info("%s skill=%s model=%s pass=%d", self.name, skill, m, n + 1)
                        t0 = time.monotonic()
                        out = extract_json(await self._call(client, m, messages, s.step))
                        logger.info("%s skill=%s model=%s answered in %.1fs", self.name, skill, m,
                                    time.monotonic() - t0)
                        self.last_model = m
                        return out
                    except ProviderHTTPError as e:
                        last[m] = e.short
                        logger.warning("%s skill=%s model=%s: HTTP %s %s", self.name, skill, m, e.status, e.short)
                        if e.kind == "auth":
                            raise LLMError(
                                f"{self.name} rejected the API key (HTTP {e.status}: {e.short}). "
                                f"Check {self.key_env} in backend/.env.") from e
                        if e.kind == "missing":
                            models.remove(m)
                        elif e.kind == "rate_limit":
                            await self._sleep(e.retry_after or waits[min(n, len(waits) - 1)])
                    except (LLMError, httpx.HTTPError) as e:
                        last[m] = str(e)[:200]
                        logger.warning("%s skill=%s model=%s failed: %s", self.name, skill, m, e)
                if n < passes - 1 and models:
                    wait = waits[min(n, len(waits) - 1)]
                    await self._sleep(wait + random.uniform(0, wait / 4))
        tried = ", ".join(f"{m} ({r[:80]})" for m, r in last.items())
        busy = any("demand" in r.lower() or "overload" in r.lower() or "unavailable" in r.lower()
                   for r in last.values())
        lead = (f"{self.name} is overloaded right now (all models busy). Try again in a minute or two."
                if busy else f"All {self.name} models failed for this step.")
        raise LLMError(f"{lead} Tried: {tried}")

    async def list_models(self) -> list[str]:
        """Models your key can use (from the provider's /models endpoint)."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{self.base_url}/models", headers=self.headers())
        if resp.status_code != 200:
            raise ProviderHTTPError(self.name, "-", resp.status_code, resp.text, None)
        data = resp.json().get("data", [])
        return sorted(str(m.get("id", "")).removeprefix("models/") for m in data if m.get("id"))


class GeminiClient(OpenAICompatClient):
    """Google Gemini through its OpenAI-compatible endpoint."""
    name = "gemini"
    default_base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
    key_env = "GEMINI_API_KEY"
    base_url_env = None


class OpenRouterClient(OpenAICompatClient):
    name = "openrouter"
    default_base_url = "https://openrouter.ai/api/v1"
    key_env = "OPENROUTER_API_KEY"
    base_url_env = None

    def headers(self) -> dict[str, str]:
        return {**super().headers(),
                "HTTP-Referer": os.getenv("OPENROUTER_APP_URL", "http://localhost:8000"),
                "X-Title": os.getenv("OPENROUTER_APP_NAME", "Matcher")}
