import pytest

from app.llm.base import LLMError, extract_json
from app.llm.openrouter import OpenRouterClient


def test_extract_json_variants():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('Here you go:\n```json\n{"a": 2}\n```') == {"a": 2}
    assert extract_json('Sure! {"a": 3} hope that helps') == {"a": 3}
    with pytest.raises(LLMError):
        extract_json("no json here")


def test_missing_key_is_clear(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(LLMError, match="OPENROUTER_API_KEY"):
        OpenRouterClient()
