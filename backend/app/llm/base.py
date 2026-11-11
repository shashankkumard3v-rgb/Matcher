"""What every LLM provider must offer to the pipeline."""
from __future__ import annotations

import json
import re
from typing import Protocol


class LLMError(RuntimeError):
    pass


class LLMClient(Protocol):
    mode: str  # "prompt" (OpenRouter) or "skills" (Claude)
    last_model: str | None  # model that produced the last answer

    async def run_skill(self, skill: str, user_input: str, model: str | None = None) -> dict:
        """Run one step: follow the named skill's instructions on user_input, return JSON.

        model: force this exact model (no fallbacks). Used to keep the judge identical
        across every pass of a run.
        """
        ...


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model reply (handles ```json fences)."""
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as e:
            raise LLMError(f"Model reply was not valid JSON: {e}") from e
    raise LLMError("Model reply contained no JSON object")
