from agentforge.agents import refactor_agent
from agentforge.memory import SharedContext
from agentforge.models import Finding, ModuleArtifact, Severity


def _mk_finding(rule: str, severity: Severity = Severity.HIGH, **kwargs) -> Finding:
    return Finding(
        kind="security",
        severity=severity,
        message=f"[{rule}] problem",
        **kwargs,
    )


def test_auto_fixes_hardcoded_secret():
    ctx = SharedContext(persist_dir=None)
    artifact = ModuleArtifact(
        module="auth",
        agent="code_writer",
        files={"app/auth.py": 'SECRET_KEY = "supersecretvalue12345"\n'},
    )
    finding = _mk_finding("SEC004", module="auth", file="app/auth.py")
    refactor_agent.apply([artifact], [finding], ctx)
    assert "os.environ.get" in artifact.files["app/auth.py"]
    assert finding.resolved


def test_auto_fixes_debug_true():
    ctx = SharedContext(persist_dir=None)
    artifact = ModuleArtifact(
        module="api",
        agent="code_writer",
        files={"app/main.py": "app.run(debug=True)\n"},
    )
    finding = _mk_finding("SEC005", module="api", file="app/main.py")
    refactor_agent.apply([artifact], [finding], ctx)
    assert "debug=False" in artifact.files["app/main.py"]
    assert finding.resolved
