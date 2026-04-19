from agentforge.agents import conflict_detector
from agentforge.memory import SharedContext
from agentforge.models import ModuleArtifact


def test_detects_duplicate_files():
    ctx = SharedContext(persist_dir=None)
    a = ModuleArtifact(module="auth", agent="x", files={"app/util.py": "a = 1\n"})
    b = ModuleArtifact(module="api", agent="y", files={"app/util.py": "a = 2\n"})
    findings = conflict_detector.scan([a, b], ctx)
    assert any(f.kind == "conflict" for f in findings)


def test_detects_route_collision():
    ctx = SharedContext(persist_dir=None)
    a = ModuleArtifact(
        module="auth",
        agent="x",
        files={"app/auth.py": '@app.get("/me")\ndef me(): ...\n'},
    )
    b = ModuleArtifact(
        module="api",
        agent="y",
        files={"app/api.py": '@app.get("/me")\ndef me(): ...\n'},
    )
    findings = conflict_detector.scan([a, b], ctx)
    assert any("Route collision" in f.message for f in findings)


def test_flags_syntax_error():
    ctx = SharedContext(persist_dir=None)
    broken = ModuleArtifact(
        module="api", agent="x", files={"app/broken.py": "def foo(:\n"}
    )
    findings = conflict_detector.scan([broken], ctx)
    assert any("Syntax error" in f.message for f in findings)
