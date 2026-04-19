"""Tests for the LangGraph-backed orchestration layer."""

from __future__ import annotations

import threading

import pytest

from agentforge.intake import parse_prompt
from agentforge.memory import SharedContext
from agentforge.models import EngineeringSpec, ModuleArtifact, ModuleSpec
from agentforge.orchestrator import SeniorAgent
from agentforge.orchestrator import graph as graph_mod


def test_run_graph_respects_dependencies():
    """A module whose deps are unresolved must not start before they finish."""

    spec = EngineeringSpec(raw_prompt="test", project_name="demo", summary="demo")
    modules = [
        ModuleSpec(name="database", kind="database", description="d", depends_on=[]),
        ModuleSpec(name="auth", kind="auth", description="a", depends_on=["database"]),
        ModuleSpec(name="api", kind="api", description="i", depends_on=["auth", "database"]),
    ]
    ctx = SharedContext(persist_dir=None)

    started: list[str] = []
    finished: list[str] = []
    lock = threading.Lock()

    def build(_spec: EngineeringSpec, module: ModuleSpec) -> list[ModuleArtifact]:
        with lock:
            started.append(module.name)
        finished.append(module.name)
        return [
            ModuleArtifact(
                module=module.name,
                agent="fake",
                files={f"{module.name}.py": f"# {module.name}\n"},
            )
        ]

    artifacts = graph_mod.run_graph(spec, modules, build, ctx)
    names = {a.module for a in artifacts}
    assert names == {"database", "auth", "api"}
    # database must start before auth, and auth before api.
    assert finished.index("database") < finished.index("auth")
    assert finished.index("auth") < finished.index("api")


def test_senior_agent_execute_uses_langgraph_by_default():
    ctx = SharedContext(persist_dir=None)
    spec = parse_prompt("Build a todo REST API with user authentication")
    senior = SeniorAgent(ctx)
    modules = senior.plan(spec)
    artifacts = senior.execute(spec, modules)

    trail = [e.event for e in ctx.audit_trail() if e.actor == "senior_agent"]
    assert "execute.start" in trail
    assert "execute.done" in trail

    # The langgraph node events must also appear in the audit trail.
    graph_events = [e for e in ctx.audit_trail() if e.actor == "orchestrator.graph"]
    assert any(e.event == "node_done" for e in graph_events)
    assert artifacts


def test_senior_agent_execute_waves_fallback(monkeypatch):
    monkeypatch.setenv("AGENTFORGE_ORCHESTRATOR", "waves")
    ctx = SharedContext(persist_dir=None)
    spec = parse_prompt("Build a todo REST API with user authentication")
    senior = SeniorAgent(ctx)
    modules = senior.plan(spec)
    artifacts = senior.execute(spec, modules)

    done_events = [
        e for e in ctx.audit_trail()
        if e.actor == "senior_agent" and e.event == "execute.done"
    ]
    assert done_events
    assert done_events[-1].payload.get("driver") == "waves"
    assert artifacts


def test_graph_failure_raises_runtime_error():
    spec = EngineeringSpec(raw_prompt="test", project_name="demo", summary="demo")
    modules = [
        ModuleSpec(name="a", kind="database", description="d", depends_on=[]),
        ModuleSpec(name="b", kind="api", description="i", depends_on=["a"]),
    ]
    ctx = SharedContext(persist_dir=None)

    def build(_spec: EngineeringSpec, module: ModuleSpec) -> list[ModuleArtifact]:
        if module.name == "b":
            raise RuntimeError("boom")
        return [ModuleArtifact(module=module.name, agent="fake", files={})]

    with pytest.raises(RuntimeError, match="failures in modules"):
        graph_mod.run_graph(spec, modules, build, ctx)
