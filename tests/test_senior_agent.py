from agentforge.intake import parse_prompt
from agentforge.memory import SharedContext
from agentforge.orchestrator import SeniorAgent


def test_plan_includes_tests_and_docs():
    ctx = SharedContext(persist_dir=None)
    spec = parse_prompt("Build a todo REST API with user authentication")
    senior = SeniorAgent(ctx)
    modules = senior.plan(spec)
    names = {m.name for m in modules}
    assert "tests" in names
    assert "docs" in names


def test_dependency_order_no_cycles():
    from agentforge.orchestrator.senior_agent import _DEFAULT_MODULES, SeniorAgent

    waves = SeniorAgent._dependency_order(_DEFAULT_MODULES)
    # database has no deps and must appear in the first wave.
    assert "database" in waves[0]
    flat = [name for wave in waves for name in wave]
    assert sorted(flat) == sorted(m.name for m in _DEFAULT_MODULES)


def test_execute_produces_artifacts():
    ctx = SharedContext(persist_dir=None)
    spec = parse_prompt("Build a todo REST API with user authentication")
    senior = SeniorAgent(ctx)
    modules = senior.plan(spec)
    artifacts = senior.execute(spec, modules)
    assert artifacts
    # The api module should include a FastAPI app entrypoint.
    api_files = [
        path for a in artifacts if a.module == "api" for path in a.files
    ]
    assert any(p.endswith("main.py") for p in api_files)
