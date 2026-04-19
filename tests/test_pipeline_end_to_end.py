"""End-to-end pipeline test.

Runs the full AgentForge pipeline against the hallmark demo prompt. We skip
the sandbox stage here because it depends on the ambient Python environment
having FastAPI installed; the sandbox is covered separately.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentforge.main import run_pipeline


def test_demo_prompt_produces_full_project(tmp_path: Path):
    result = run_pipeline(
        "Build a REST API for a todo app with user authentication",
        output_root=tmp_path,
        skip_sandbox=True,
    )

    # The pipeline produced a project directory.
    output = Path(result.output_dir)
    assert output.exists()
    # FastAPI app entrypoint must exist.
    assert any(output.rglob("main.py"))
    # Integration tests must be present.
    assert (output / "tests" / "test_api.py").exists()

    # All artefact files are on disk.
    all_paths = {str(p.relative_to(output)) for p in output.rglob("*") if p.is_file()}
    assert any("pyproject.toml" in p for p in all_paths)
    assert any("auth.py" in p for p in all_paths)

    # Review loop ran at least min_rounds and left no critical findings.
    assert result.rounds
    assert result.rounds[-1].critical_remaining == 0

    # Audit + dependency map are valid JSON.
    audit = json.loads(Path(result.audit_report_path).read_text())
    assert "total_findings" in audit
    deps = json.loads(Path(result.dependency_map_path).read_text())
    assert deps["modules"]
    cov = json.loads(Path(result.test_coverage_path).read_text())
    assert "findings_over_time" in cov
