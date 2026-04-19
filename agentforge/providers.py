"""LLM provider abstraction.

AgentForge supports OpenAI, Anthropic, and a deterministic mock provider used
when no API keys are configured (or in tests). All agents call the provider
through :func:`complete`, which returns plain text. JSON extraction is handled
by the caller so that provider backends stay simple.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from agentforge.config import get_config
from agentforge.logging import get_logger

logger = get_logger("providers")


@dataclass
class Message:
    role: str  # "system" | "user" | "assistant"
    content: str


class LLMProvider(Protocol):
    name: str

    def complete(self, messages: list[Message], *, temperature: float = 0.2) -> str: ...


class MockProvider:
    """Deterministic provider used for offline tests and the working demo.

    It inspects the incoming messages for role-specific markers and produces
    structured JSON that the pipeline can parse. This lets the full end-to-end
    pipeline run without any real API keys, which is essential for CI.
    """

    name = "mock"

    def complete(self, messages: list[Message], *, temperature: float = 0.2) -> str:
        joined = "\n".join(m.content for m in messages)
        lowered = joined.lower()

        if "<task:intake>" in lowered:
            return self._intake(joined)
        if "<task:plan>" in lowered:
            return self._plan(joined)
        if "<task:code>" in lowered:
            return self._code(joined)
        if "<task:tests>" in lowered:
            return self._tests(joined)
        if "<task:security>" in lowered:
            return self._security(joined)
        if "<task:conflicts>" in lowered:
            return self._conflicts(joined)
        if "<task:refactor>" in lowered:
            return self._refactor(joined)
        return json.dumps({"ok": True, "echo": joined[-200:]})

    # --- Mock task outputs -------------------------------------------------

    def _intake(self, prompt: str) -> str:
        payload = {
            "project_name": "user_project",
            "summary": "Build the system described by the user.",
            "functional_requirements": [
                "Accept user input",
                "Persist data",
                "Expose a clean interface",
            ],
            "non_functional_requirements": [
                "Production ready",
                "Secure by default",
            ],
            "constraints": [
                {"kind": "stack", "description": "Python 3.10+"},
                {"kind": "security", "description": "OWASP Top 10 compliance"},
            ],
            "target_stack": ["python", "fastapi", "sqlite"],
            "success_criteria": [
                "All integration tests pass",
                "No critical security findings",
            ],
        }
        return json.dumps(payload)

    def _plan(self, prompt: str) -> str:
        payload = {
            "modules": [
                {
                    "name": "auth",
                    "kind": "auth",
                    "description": "User authentication module",
                    "depends_on": [],
                    "acceptance_criteria": ["Password hashing", "Token issuance"],
                },
                {
                    "name": "database",
                    "kind": "database",
                    "description": "Persistence layer",
                    "depends_on": [],
                    "acceptance_criteria": ["Schema defined", "Migrations"],
                },
                {
                    "name": "api",
                    "kind": "api",
                    "description": "REST API surface",
                    "depends_on": ["auth", "database"],
                    "acceptance_criteria": ["CRUD endpoints", "Auth-protected"],
                },
                {
                    "name": "tests",
                    "kind": "tests",
                    "description": "Integration tests",
                    "depends_on": ["api"],
                    "acceptance_criteria": ["Covers happy and error paths"],
                },
            ]
        }
        return json.dumps(payload)

    def _code(self, prompt: str) -> str:
        # Mock code writer never used directly; the real code_writer has
        # deterministic templates that do not require LLM output.
        return json.dumps({"files": {}})

    def _tests(self, prompt: str) -> str:
        return json.dumps({"files": {}})

    def _security(self, prompt: str) -> str:
        return json.dumps({"findings": []})

    def _conflicts(self, prompt: str) -> str:
        return json.dumps({"findings": []})

    def _refactor(self, prompt: str) -> str:
        return json.dumps({"patches": []})


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, model: str) -> None:
        from openai import OpenAI  # type: ignore

        self._client = OpenAI(api_key=api_key)
        self._model = model

    def complete(self, messages: list[Message], *, temperature: float = 0.2) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            temperature=temperature,
            messages=[{"role": m.role, "content": m.content} for m in messages],
        )
        content = response.choices[0].message.content or ""
        return content


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str) -> None:
        import anthropic  # type: ignore

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def complete(self, messages: list[Message], *, temperature: float = 0.2) -> str:
        system_parts = [m.content for m in messages if m.role == "system"]
        convo = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role != "system"
        ]
        response = self._client.messages.create(
            model=self._model,
            max_tokens=4096,
            temperature=temperature,
            system="\n".join(system_parts) if system_parts else None,
            messages=convo,
        )
        # Anthropic returns a list of content blocks.
        chunks: list[str] = []
        for block in response.content:
            text = getattr(block, "text", None)
            if text:
                chunks.append(text)
        return "".join(chunks)


_PROVIDER: LLMProvider | None = None


def get_provider() -> LLMProvider:
    """Return the configured LLM provider (singleton)."""
    global _PROVIDER
    if _PROVIDER is not None:
        return _PROVIDER
    cfg = get_config()
    resolved = cfg.resolved_provider()
    logger.info("Using LLM provider: %s", resolved)
    if resolved == "openai":
        assert cfg.openai_api_key is not None
        _PROVIDER = OpenAIProvider(cfg.openai_api_key, cfg.openai_model)
    elif resolved == "anthropic":
        assert cfg.anthropic_api_key is not None
        _PROVIDER = AnthropicProvider(cfg.anthropic_api_key, cfg.anthropic_model)
    else:
        _PROVIDER = MockProvider()
    return _PROVIDER


def reset_provider() -> None:
    global _PROVIDER
    _PROVIDER = None


def extract_json(text: str) -> dict:
    """Best-effort JSON extraction from an LLM response."""
    text = text.strip()
    if text.startswith("```"):
        # Strip triple-backtick fences.
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    # If the model wrapped JSON in prose, find the first { and last }.
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidate = text[start : end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Provider returned non-JSON: {text[:200]}") from exc


__all__ = [
    "Message",
    "LLMProvider",
    "MockProvider",
    "OpenAIProvider",
    "AnthropicProvider",
    "get_provider",
    "reset_provider",
    "extract_json",
]
