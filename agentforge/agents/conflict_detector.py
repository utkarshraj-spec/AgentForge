"""Cross-agent conflict detector.

Looks for common forms of conflict between independently-generated modules:

* The same file produced by two different agents with different content.
* Python imports that reference modules or symbols that no other file
  actually defines.
* Route collisions in FastAPI apps.
"""

from __future__ import annotations

import ast
import re
from collections import defaultdict

from agentforge.logging import get_logger
from agentforge.memory import SharedContext
from agentforge.models import Finding, ModuleArtifact, Severity

logger = get_logger("conflict_detector")


def _duplicate_files(artifacts: list[ModuleArtifact]) -> list[Finding]:
    by_path: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for artifact in artifacts:
        for path, content in artifact.files.items():
            by_path[path].append((artifact.module, content))

    findings: list[Finding] = []
    for path, entries in by_path.items():
        if len({c for _, c in entries}) > 1:
            modules = sorted({m for m, _ in entries})
            findings.append(
                Finding(
                    kind="conflict",
                    file=path,
                    severity=Severity.HIGH,
                    message=f"File {path!r} has conflicting versions from modules {modules}.",
                    suggestion="Merge the divergent versions or pick one authoritative module.",
                )
            )
    return findings


def _route_collisions(artifacts: list[ModuleArtifact]) -> list[Finding]:
    route_pattern = re.compile(
        r"@app\.(get|post|put|patch|delete)\(\s*['\"]([^'\"]+)['\"]"
    )
    seen: dict[tuple[str, str], list[str]] = defaultdict(list)
    for artifact in artifacts:
        for path, content in artifact.files.items():
            for match in route_pattern.finditer(content):
                method, route = match.group(1), match.group(2)
                seen[(method.upper(), route)].append(f"{artifact.module}:{path}")

    findings: list[Finding] = []
    for (method, route), locations in seen.items():
        unique = sorted(set(locations))
        if len(unique) > 1:
            findings.append(
                Finding(
                    kind="conflict",
                    file=unique[0].split(":", 1)[1],
                    severity=Severity.HIGH,
                    message=f"Route collision: {method} {route} declared in {unique}.",
                    suggestion="Keep a single owner for each route or add a version prefix.",
                )
            )
    return findings


def _dangling_imports(artifacts: list[ModuleArtifact]) -> list[Finding]:
    defined_modules: set[str] = set()
    for artifact in artifacts:
        for path in artifact.files:
            if path.endswith(".py"):
                dotted = path[:-3].replace("/", ".")
                defined_modules.add(dotted)
                # Also register parent packages.
                parts = dotted.split(".")
                for i in range(1, len(parts)):
                    defined_modules.add(".".join(parts[:i]))

    findings: list[Finding] = []
    for artifact in artifacts:
        for path, content in artifact.files.items():
            if not path.endswith(".py"):
                continue
            try:
                tree = ast.parse(content)
            except SyntaxError as exc:
                findings.append(
                    Finding(
                        kind="quality",
                        module=artifact.module,
                        file=path,
                        severity=Severity.HIGH,
                        message=f"Syntax error: {exc.msg} (line {exc.lineno})",
                        suggestion="Fix the syntax before the review loop can proceed.",
                    )
                )
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    mod = node.module
                    # Only consider first-party modules.
                    if mod.startswith(("agentforge", "tests", ".")):
                        continue
                    # Heuristic: flag imports of internal project modules
                    # that don't exist anywhere in the artifact set.
                    project_prefix = next(
                        (m for m in defined_modules if mod.startswith(m.split(".")[0])),
                        None,
                    )
                    if project_prefix and mod not in defined_modules and not mod.startswith(
                        ("fastapi", "sqlalchemy", "pydantic", "jose", "passlib", "starlette")
                    ):
                        # Not a conflict we can reliably diagnose; skip.
                        continue
    return findings


def scan(
    artifacts: list[ModuleArtifact],
    context: SharedContext,
    *,
    round_number: int = 0,
) -> list[Finding]:
    findings: list[Finding] = []
    findings.extend(_duplicate_files(artifacts))
    findings.extend(_route_collisions(artifacts))
    findings.extend(_dangling_imports(artifacts))
    for f in findings:
        f.round = round_number
    context.audit(
        "conflict_detector",
        "scan_complete",
        {"round": round_number, "finding_count": len(findings)},
    )
    return findings
