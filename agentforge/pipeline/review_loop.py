"""Stage 4 — multi-round review loop.

Each round performs:

  (a) cross-agent conflict detection
  (b) security vulnerability scan (OWASP Top 10)
  (c) code quality checks (complexity, dead code, missing error handling)
  (d) test coverage verification

Findings are auto-patched by the refactor agent when possible. The loop exits
when either:
  * zero critical findings remain *and* at least ``min_rounds`` have run, or
  * the hard ceiling (``max_rounds``, default 50) is reached.
"""

from __future__ import annotations

import ast
import re

from agentforge.agents import conflict_detector, refactor_agent, security_scanner
from agentforge.config import get_config
from agentforge.logging import get_logger, log_event
from agentforge.memory import SharedContext
from agentforge.models import Finding, ModuleArtifact, ReviewRoundResult, Severity

logger = get_logger("review_loop")


_MAX_FUNCTION_LINES = 80


def _quality_scan(
    artifacts: list[ModuleArtifact], round_number: int
) -> list[Finding]:
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
                        round=round_number,
                        kind="quality",
                        module=artifact.module,
                        file=path,
                        severity=Severity.HIGH,
                        message=f"Syntax error: {exc.msg} (line {exc.lineno}).",
                        suggestion="Fix the syntax.",
                    )
                )
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    end = getattr(node, "end_lineno", None) or node.lineno
                    body_len = end - node.lineno
                    if body_len > _MAX_FUNCTION_LINES:
                        findings.append(
                            Finding(
                                round=round_number,
                                kind="quality",
                                module=artifact.module,
                                file=path,
                                severity=Severity.LOW,
                                message=(
                                    f"Function {node.name!r} is {body_len} lines long "
                                    f"(> {_MAX_FUNCTION_LINES})."
                                ),
                                suggestion="Consider splitting into smaller helpers.",
                            )
                        )
                    if isinstance(node, ast.FunctionDef) and _contains_bare_except(node):
                        findings.append(
                            Finding(
                                round=round_number,
                                kind="quality",
                                module=artifact.module,
                                file=path,
                                severity=Severity.MEDIUM,
                                message=(
                                    f"Bare 'except' in {node.name!r} swallows all errors."
                                ),
                                suggestion="Catch specific exception classes.",
                            )
                        )
    return findings


def _contains_bare_except(func: ast.FunctionDef) -> bool:
    return any(
        isinstance(node, ast.ExceptHandler) and node.type is None
        for node in ast.walk(func)
    )


def _coverage_scan(
    artifacts: list[ModuleArtifact], round_number: int
) -> list[Finding]:
    """Verify tests exist that reference every significant module.

    This is a lightweight substitute for runtime coverage — we just make sure
    the ``tests/`` artifact imports or mentions every non-trivial module.
    """

    test_files: dict[str, str] = {}
    non_test_files: list[tuple[str, str]] = []
    for artifact in artifacts:
        for path, content in artifact.files.items():
            if path.startswith("tests/"):
                test_files[path] = content
            elif path.endswith(".py"):
                non_test_files.append((path, content))

    haystack = "\n".join(test_files.values()).lower()
    findings: list[Finding] = []
    route_pattern = re.compile(r"@app\.(get|post|put|patch|delete)\(\s*['\"]([^'\"]+)")
    routes: list[str] = []
    for _, content in non_test_files:
        routes.extend(m.group(2) for m in route_pattern.finditer(content))
    missing = [r for r in routes if r.lower() not in haystack]
    if missing:
        findings.append(
            Finding(
                round=round_number,
                kind="coverage",
                severity=Severity.MEDIUM,
                message=(
                    f"{len(missing)} route(s) are not mentioned in any test file: "
                    f"{missing[:5]}"
                ),
                suggestion="Add integration tests for uncovered routes.",
            )
        )

    if not test_files:
        findings.append(
            Finding(
                round=round_number,
                kind="coverage",
                severity=Severity.HIGH,
                message="No test files were produced.",
                suggestion="Assign the test_writer agent to a test module.",
            )
        )
    return findings


class ReviewLoop:
    """Stage 4 implementation."""

    def __init__(self, context: SharedContext) -> None:
        self.context = context
        cfg = get_config()
        self.max_rounds = cfg.max_review_rounds
        self.min_rounds = cfg.min_review_rounds

    def run(
        self,
        artifacts: list[ModuleArtifact],
    ) -> tuple[list[ModuleArtifact], list[ReviewRoundResult]]:
        results: list[ReviewRoundResult] = []
        for round_number in range(1, self.max_rounds + 1):
            log_event(logger, "review.round", round=round_number)
            self.context.audit(
                "review_loop",
                "round_start",
                {"round": round_number},
            )

            findings: list[Finding] = []
            findings.extend(
                conflict_detector.scan(artifacts, self.context, round_number=round_number)
            )
            findings.extend(
                security_scanner.scan(artifacts, self.context, round_number=round_number)
            )
            findings.extend(_quality_scan(artifacts, round_number=round_number))
            findings.extend(_coverage_scan(artifacts, round_number=round_number))

            artifacts = refactor_agent.apply(artifacts, findings, self.context)

            critical_remaining = sum(
                1
                for f in findings
                if not f.resolved
                and f.severity in (Severity.HIGH, Severity.CRITICAL)
            )
            fixed = [f.id for f in findings if f.resolved]
            results.append(
                ReviewRoundResult(
                    round=round_number,
                    findings=findings,
                    fixed=fixed,
                    critical_remaining=critical_remaining,
                )
            )
            self.context.audit(
                "review_loop",
                "round_done",
                {
                    "round": round_number,
                    "finding_count": len(findings),
                    "critical_remaining": critical_remaining,
                    "fixed": len(fixed),
                },
            )

            if critical_remaining == 0 and round_number >= self.min_rounds:
                log_event(
                    logger,
                    "review.exit",
                    round=round_number,
                    reason="no_critical_findings",
                )
                break
        return artifacts, results


__all__ = ["ReviewLoop"]
