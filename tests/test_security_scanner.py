from agentforge.agents import security_scanner
from agentforge.memory import SharedContext
from agentforge.models import ModuleArtifact, Severity


def test_detects_hardcoded_secret():
    ctx = SharedContext(persist_dir=None)
    bad = ModuleArtifact(
        module="auth",
        agent="code_writer",
        files={"app/auth.py": 'SECRET_KEY = "supersecretvalue12345"\n'},
    )
    findings = security_scanner.scan([bad], ctx)
    assert any(f.owasp_category and f.owasp_category.startswith("A07") for f in findings)
    assert any(f.severity is Severity.CRITICAL for f in findings)


def test_detects_debug_true_and_eval():
    ctx = SharedContext(persist_dir=None)
    bad = ModuleArtifact(
        module="api",
        agent="code_writer",
        files={
            "app/main.py": (
                "app.run(debug=True)\n"
                "eval(user_input)\n"
                "hashlib.md5(x).hexdigest()\n"
            )
        },
    )
    findings = security_scanner.scan([bad], ctx)
    kinds = {f.message.split()[0] for f in findings}
    assert any("SEC005" in k for k in kinds)  # debug=True
    assert any("SEC006" in k for k in kinds)  # eval
    assert any("SEC003" in k for k in kinds)  # md5


def test_clean_code_has_no_findings():
    ctx = SharedContext(persist_dir=None)
    clean = ModuleArtifact(
        module="api",
        agent="code_writer",
        files={"app/routes.py": "def healthz():\n    return {'ok': True}\n"},
    )
    findings = security_scanner.scan([clean], ctx)
    assert findings == []
