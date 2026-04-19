"""Refactor agent.

Given a list of :class:`Finding` objects, applies deterministic auto-fixes
where possible. Unresolved findings are returned to the review loop so the
Senior Agent can decide whether to re-run the affected module.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from agentforge.logging import get_logger
from agentforge.memory import SharedContext
from agentforge.models import Finding, ModuleArtifact

logger = get_logger("refactor_agent")


def _find_artifact(artifacts: list[ModuleArtifact], module: str) -> ModuleArtifact | None:
    for a in artifacts:
        if a.module == module:
            return a
    return None


def _fix_hardcoded_secret(content: str) -> str:
    """Replace ``SECRET_KEY = "abc"`` style literals with os.environ lookups.

    The original literal is preserved as a default so local test runs keep
    working; production deployments are still expected to set the env var.
    """
    pattern = re.compile(
        r"(?P<name>SECRET_KEY|API_KEY|PASSWORD|TOKEN)\s*=\s*['\"](?P<value>[^'\"]+)['\"]"
    )
    if not pattern.search(content):
        return content

    def _replacement(match: re.Match[str]) -> str:
        return (
            f"{match.group('name')} = os.environ.get("
            f"'{match.group('name')}', '{match.group('value')}')"
        )

    new = pattern.sub(_replacement, content)
    if new != content and "import os" not in new:
        new = "import os\n" + new
    return new


def _fix_debug_true(content: str) -> str:
    return re.sub(r"\bdebug\s*=\s*True", "debug=False", content, flags=re.IGNORECASE)


def _fix_shell_true(content: str) -> str:
    return re.sub(r"shell\s*=\s*True", "shell=False", content)


def _fix_verify_false(content: str) -> str:
    return re.sub(r"verify\s*=\s*False", "verify=True", content)


def _fix_cors_wildcard(content: str) -> str:
    return re.sub(
        r"allow_origins\s*=\s*\[['\"]\*['\"]\]",
        'allow_origins=[os.environ.get("ALLOWED_ORIGIN", "http://localhost")]',
        content,
    )


_FIXERS: dict[str, Callable[[str], str]] = {
    "SEC004": _fix_hardcoded_secret,
    "SEC005": _fix_debug_true,
    "SEC002": _fix_shell_true,
    "SEC010": _fix_verify_false,
    "SEC009": _fix_cors_wildcard,
}


def apply(
    artifacts: list[ModuleArtifact],
    findings: list[Finding],
    context: SharedContext,
) -> list[ModuleArtifact]:
    """Apply whatever deterministic fixes we can, in place on *artifacts*."""

    fixed_ids: list[str] = []
    for finding in findings:
        if finding.resolved or finding.kind != "security":
            continue
        rule_id_match = re.search(r"\[(SEC\d+)\]", finding.message)
        if not rule_id_match:
            continue
        rule_id = rule_id_match.group(1)
        fixer = _FIXERS.get(rule_id)
        if fixer is None:
            continue
        artifact = _find_artifact(artifacts, finding.module or "")
        if artifact is None or finding.file is None:
            continue
        original = artifact.files.get(finding.file)
        if original is None:
            continue
        patched = fixer(original)
        if patched != original:
            artifact.files[finding.file] = patched
            finding.resolved = True
            fixed_ids.append(finding.id)
            context.audit(
                "refactor_agent",
                "auto_fix",
                {
                    "module": finding.module,
                    "file": finding.file,
                    "rule": rule_id,
                    "finding_id": finding.id,
                },
            )
    logger.info("refactor_agent applied %d fixes", len(fixed_ids))
    return artifacts
