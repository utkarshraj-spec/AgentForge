"""Test fixtures for the AgentForge pipeline itself."""

from __future__ import annotations

import pytest

from agentforge import config as cfg_mod
from agentforge import providers


@pytest.fixture(autouse=True)
def _force_mock_provider(monkeypatch):
    """All AgentForge tests use the mock provider — no real API calls."""
    monkeypatch.setenv("AGENTFORGE_PROVIDER", "mock")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg_mod.reset_config()
    providers.reset_provider()
    yield
    cfg_mod.reset_config()
    providers.reset_provider()


@pytest.fixture()
def tmp_chroma(tmp_path, monkeypatch):
    chroma = tmp_path / "chroma"
    monkeypatch.setenv("CHROMA_PERSIST_DIR", str(chroma))
    cfg_mod.reset_config()
    return chroma


@pytest.fixture()
def run_root(tmp_path):
    """Return an isolated directory for a single AgentForge run."""
    return tmp_path / "runs"
