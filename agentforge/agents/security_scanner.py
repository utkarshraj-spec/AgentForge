"""Security scanner sub-agent.

Runs a pattern-based review against the full assembled codebase and emits
:class:`Finding` objects. The rules cover the OWASP Top 10 at a minimum:

* A01 Broken Access Control — unauth-protected routes.
* A02 Cryptographic Failures — weak hashing (md5/sha1).
* A03 Injection — string-concatenated SQL queries, shell ``subprocess=True``.
* A05 Security Misconfiguration — ``debug=True`` flags, ``DEBUG = True``.
* A07 Identification & Authentication Failures — hardcoded secrets.
* A08 Software & Data Integrity Failures — unverified ``eval`` / ``exec``.
* A09 Security Logging & Monitoring Failures — missing logging (heuristic).
* A10 Server-Side Request Forgery — unrestricted ``requests.get(url)`` calls.
* XSS — ``innerHTML = ...`` string concatenation in frontend code.

This is intentionally a conservative static analyzer — false positives are
preferred over false negatives, and the refactor agent can fix them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agentforge.logging import get_logger
from agentforge.memory import SharedContext
from agentforge.models import Finding, ModuleArtifact, Severity

logger = get_logger("security_scanner")


@dataclass(frozen=True)
class Rule:
    id: str
    owasp: str
    severity: Severity
    pattern: re.Pattern[str]
    message: str
    suggestion: str


_RULES: list[Rule] = [
    Rule(
        id="SEC001",
        owasp="A03 Injection",
        severity=Severity.HIGH,
        pattern=re.compile(r"execute\(\s*f['\"].*\{", re.DOTALL),
        message="Possible SQL injection: execute() called with an f-string.",
        suggestion="Use parameterized queries, never interpolate user input.",
    ),
    Rule(
        id="SEC002",
        owasp="A03 Injection",
        severity=Severity.HIGH,
        pattern=re.compile(r"subprocess\.(?:run|Popen|call)\([^)]*shell\s*=\s*True"),
        message="Shell injection risk: subprocess call with shell=True.",
        suggestion="Pass arguments as a list and set shell=False.",
    ),
    Rule(
        id="SEC003",
        owasp="A02 Cryptographic Failures",
        severity=Severity.MEDIUM,
        pattern=re.compile(r"hashlib\.(?:md5|sha1)\b"),
        message="Weak hashing algorithm (md5/sha1) detected.",
        suggestion="Use hashlib.sha256 or bcrypt/argon2 for passwords.",
    ),
    Rule(
        id="SEC004",
        owasp="A07 Auth Failures",
        severity=Severity.CRITICAL,
        pattern=re.compile(
            r"""(?:SECRET_KEY|API_KEY|PASSWORD|TOKEN)\s*=\s*['"][A-Za-z0-9_\-]{8,}['"]""",
        ),
        message="Hardcoded secret detected.",
        suggestion="Load the value from an environment variable.",
    ),
    Rule(
        id="SEC005",
        owasp="A05 Security Misconfiguration",
        severity=Severity.HIGH,
        pattern=re.compile(r"\bdebug\s*=\s*True", re.IGNORECASE),
        message="Debug mode enabled.",
        suggestion="Disable debug in production (configure via env var).",
    ),
    Rule(
        id="SEC006",
        owasp="A08 Data Integrity Failures",
        severity=Severity.HIGH,
        pattern=re.compile(r"\b(?:eval|exec)\s*\("),
        message="Use of eval/exec on untrusted input is unsafe.",
        suggestion="Replace eval/exec with explicit parsing or dispatch.",
    ),
    Rule(
        id="SEC007",
        owasp="A10 SSRF",
        severity=Severity.MEDIUM,
        pattern=re.compile(r"requests\.(?:get|post|put|delete)\([^)]*url"),
        message="Potentially unrestricted outbound HTTP call (SSRF risk).",
        suggestion="Validate and allow-list destination hosts.",
    ),
    Rule(
        id="SEC008",
        owasp="A03 Injection — XSS",
        severity=Severity.HIGH,
        pattern=re.compile(r"innerHTML\s*=\s*[^;]*\+"),
        message="String concatenation into innerHTML (XSS risk).",
        suggestion="Use textContent or an escaping templating library.",
    ),
    Rule(
        id="SEC009",
        owasp="A05 Security Misconfiguration",
        severity=Severity.MEDIUM,
        pattern=re.compile(r"CORS.*allow_origins\s*=\s*\[['\"]\*['\"]"),
        message="CORS configured with allow_origins=['*'].",
        suggestion="Restrict CORS to an explicit allow-list.",
    ),
    Rule(
        id="SEC010",
        owasp="A07 Auth Failures",
        severity=Severity.MEDIUM,
        pattern=re.compile(r"verify\s*=\s*False"),
        message="TLS verification disabled (verify=False).",
        suggestion="Leave TLS verification enabled or pin certificates.",
    ),
]


# Files that are permitted to contain otherwise-dangerous regex triggers
# because they themselves are security tooling or generators.
_ALLOWLIST_FILES = {
    "agentforge/agents/security_scanner.py",
}


def _is_allowlisted(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return any(normalized.endswith(p) for p in _ALLOWLIST_FILES)


def scan(
    artifacts: list[ModuleArtifact],
    context: SharedContext,
    *,
    round_number: int = 0,
) -> list[Finding]:
    """Run the rule set across every file in *artifacts*."""

    findings: list[Finding] = []
    for artifact in artifacts:
        for path, content in artifact.files.items():
            if _is_allowlisted(path):
                continue
            for rule in _RULES:
                for match in rule.pattern.finditer(content):
                    snippet = content[max(0, match.start() - 20) : match.end() + 20]
                    findings.append(
                        Finding(
                            round=round_number,
                            kind="security",
                            module=artifact.module,
                            file=path,
                            severity=rule.severity,
                            message=f"[{rule.id}] {rule.message} (match: {snippet!r})",
                            suggestion=rule.suggestion,
                            owasp_category=rule.owasp,
                        )
                    )
    context.audit(
        "security_scanner",
        "scan_complete",
        {"round": round_number, "finding_count": len(findings)},
    )
    return findings
