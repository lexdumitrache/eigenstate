"""Provider-agnostic LLM adapter (spec section 8: OpenAI or Claude).

Both adapters expose one method: `complete_json(system, user) -> dict`.
The parser never talks to a provider SDK directly. A DeterministicStub is
included so the whole pipeline is testable offline.
"""
from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod

import httpx


class LLMAdapter(ABC):
    @abstractmethod
    def complete_json(self, system: str, user: str) -> dict:
        """Return a parsed JSON object. Raises LLMError on failure."""


class LLMError(RuntimeError):
    pass


def _extract_json(text: str) -> dict:
    """Tolerate markdown fences or prose around the JSON object."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start:end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMError(f"LLM did not return valid JSON: {e}") from e


class AnthropicAdapter(LLMAdapter):
    def __init__(self, model: str = "claude-sonnet-4-5", api_key: str | None = None):
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")

    def complete_json(self, system: str, user: str) -> dict:
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": 4096,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=120,
        )
        if resp.status_code != 200:
            raise LLMError(f"Anthropic API error {resp.status_code}: {resp.text[:300]}")
        text = "".join(b.get("text", "") for b in resp.json().get("content", []))
        return _extract_json(text)


class OpenAIAdapter(LLMAdapter):
    def __init__(self, model: str = "gpt-4o", api_key: str | None = None):
        self.model = model
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")

    def complete_json(self, system: str, user: str) -> dict:
        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=120,
        )
        if resp.status_code != 200:
            raise LLMError(f"OpenAI API error {resp.status_code}: {resp.text[:300]}")
        return _extract_json(resp.json()["choices"][0]["message"]["content"])


class DeterministicStub(LLMAdapter):
    """Offline adapter for tests/demo: returns a canned response per call."""

    def __init__(self, responses: list[dict]):
        self._responses = list(responses)

    def complete_json(self, system: str, user: str) -> dict:
        if not self._responses:
            raise LLMError("Stub exhausted")
        return self._responses.pop(0)


def get_adapter(provider: str | None = None) -> LLMAdapter:
    provider = (provider or os.environ.get("EIGENSTATE_LLM", "anthropic")).lower()
    if provider == "anthropic":
        return AnthropicAdapter()
    if provider == "openai":
        return OpenAIAdapter()
    raise ValueError(f"Unknown LLM provider: {provider}")
