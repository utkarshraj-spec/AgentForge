"""Generate the audit / security / coverage reports."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from agentforge.memory import SharedContext
from agentforge.models import ReviewRoundResult, SandboxReport


def write_audit_report(
    output_dir: Path,
    context: SharedContext,
    rounds: list[ReviewRoundResult],
    sandbox: SandboxReport | None,
) -> dict[str, Path]:
    """Write three artefacts next to the generated project:

    * ``audit_report.json``   — security summary (OWASP-tagged findings).
    * ``dependency_map.json`` — module dependency graph.
    * ``test_coverage.json``  — sandbox test outcome + coverage heuristic.
    """

    output_dir.mkdir(parents=True, exist_ok=True)

    # Security summary
    all_findings = [f for r in rounds for f in r.findings]
    security_findings = [f for f in all_findings if f.kind == "security"]
    severities = Counter(f.severity for f in security_findings)
    by_owasp = Counter(f.owasp_category for f in security_findings if f.owasp_category)
    unresolved = [f for f in security_findings if not f.resolved]

    security_payload = {
        "total_findings": len(security_findings),
        "unresolved": len(unresolved),
        "by_severity": {str(k): v for k, v in severities.items()},
        "by_owasp_category": dict(by_owasp),
        "unresolved_findings": [f.model_dump() for f in unresolved],
        "rounds_run": len(rounds),
    }
    audit_path = output_dir / "audit_report.json"
    audit_path.write_text(json.dumps(security_payload, indent=2))

    # Dependency map
    plan = context.get("plan", [])
    dep_path = output_dir / "dependency_map.json"
    dep_path.write_text(
        json.dumps(
            {
                "modules": [
                    {
                        "name": m.name,
                        "kind": m.kind,
                        "depends_on": list(m.depends_on),
                        "assigned_agents": list(m.assigned_agents),
                    }
                    for m in plan
                ],
            },
            indent=2,
        )
    )

    # Coverage / test report
    coverage_path = output_dir / "test_coverage.json"
    coverage_path.write_text(
        json.dumps(
            {
                "sandbox": sandbox.model_dump() if sandbox else None,
                "findings_over_time": [
                    {
                        "round": r.round,
                        "total_findings": len(r.findings),
                        "critical_remaining": r.critical_remaining,
                        "fixed": len(r.fixed),
                    }
                    for r in rounds
                ],
            },
            indent=2,
        )
    )

    # Dump the full audit trail too.
    audit_trail_path = output_dir / "audit_trail.json"
    context.dump_audit(audit_trail_path)

    return {
        "audit": audit_path,
        "dependencies": dep_path,
        "coverage": coverage_path,
        "audit_trail": audit_trail_path,
    }


__all__ = ["write_audit_report"]
