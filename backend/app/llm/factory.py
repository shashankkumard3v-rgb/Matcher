"""Pick the AI provider.

provider: auto (config/models.yaml) tries `auto_order` and uses the first provider that
has an API key in backend/.env AND models configured. Default order:
  claude -> gemini -> openrouter -> openai
Claude runs steps as Agent Skills (falls back to prompts if Skills aren't enabled);
every other provider runs the same SKILL.md files as prompts.
"""
from __future__ import annotations

import os

from app.config import models_config
from app.llm.base import LLMClient, LLMError

KEYS = {
    "claude": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
}
DEFAULT_ORDER = ["claude", "gemini", "openrouter", "openai"]


def _configured(name: str) -> bool:
    steps = (models_config().get(name) or {}).get("steps") or {}
    key = "model" if name == "claude" else "models"
    return any((s or {}).get(key) for s in steps.values())


def available() -> list[str]:
    """Providers that have a key and models configured, in auto order."""
    order = models_config().get("auto_order") or DEFAULT_ORDER
    return [p for p in order if p in KEYS and os.getenv(KEYS[p]) and _configured(p)]


def provider_name() -> str:
    choice = (models_config().get("provider") or "auto").lower()
    if choice == "auto":
        found = available()
        if not found:
            raise LLMError(
                "No usable AI provider. Add one key to backend/.env: ANTHROPIC_API_KEY (Claude), "
                "GEMINI_API_KEY, OPENROUTER_API_KEY or OPENAI_API_KEY (+ OPENAI_BASE_URL for "
                "other OpenAI-compatible services).")
        return found[0]
    if choice not in KEYS:
        raise LLMError(f"models.yaml provider must be auto or one of {', '.join(KEYS)} (got '{choice}')")
    return choice


def make_llm() -> LLMClient:
    name = provider_name()
    if name == "claude":
        from app.llm.claude import ClaudeSkillsClient
        return ClaudeSkillsClient()
    from app.llm.openai_compat import GeminiClient, OpenAICompatClient, OpenRouterClient
    return {"gemini": GeminiClient, "openrouter": OpenRouterClient, "openai": OpenAICompatClient}[name]()
