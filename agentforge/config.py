"""Runtime configuration for AgentForge."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


@dataclass
class Config:
    """Global configuration loaded from environment variables."""

    provider: str = field(default_factory=lambda: _env("AGENTFORGE_PROVIDER", "mock") or "mock")
    openai_api_key: str | None = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    openai_model: str = field(default_factory=lambda: _env("OPENAI_MODEL", "gpt-4o-mini") or "gpt-4o-mini")
    anthropic_api_key: str | None = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    anthropic_model: str = field(
        default_factory=lambda: _env("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")
        or "claude-3-5-sonnet-latest"
    )
    chroma_persist_dir: Path = field(
        default_factory=lambda: Path(_env("CHROMA_PERSIST_DIR", ".chroma") or ".chroma")
    )
    max_review_rounds: int = field(
        default_factory=lambda: int(_env("AGENTFORGE_MAX_REVIEW_ROUNDS", "50") or 50)
    )
    min_review_rounds: int = field(
        default_factory=lambda: int(_env("AGENTFORGE_MIN_REVIEW_ROUNDS", "3") or 3)
    )
    log_level: str = field(default_factory=lambda: _env("AGENTFORGE_LOG_LEVEL", "INFO") or "INFO")

    def resolved_provider(self) -> str:
        """Return the provider, falling back to mock if credentials are missing."""
        p = (self.provider or "mock").lower()
        if p == "openai" and not self.openai_api_key:
            return "mock"
        if p == "anthropic" and not self.anthropic_api_key:
            return "mock"
        return p


_CONFIG: Config | None = None


def get_config() -> Config:
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = Config()
    return _CONFIG


def reset_config() -> None:
    """Reset cached config. Primarily for tests."""
    global _CONFIG
    _CONFIG = None
