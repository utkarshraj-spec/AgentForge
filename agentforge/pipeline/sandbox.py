"""Stage 5 — isolated sandbox test runner.

The sandbox:
  * Materialises the generated codebase under a dedicated directory
    (``<sandbox_root>/workspace``). Writes outside that directory are not
    performed by this module.
  * Executes the bundled test suite with ``pytest`` in a subprocess.
  * Blocks outbound network access by running the subprocess with
    ``unshare -n`` when available. On platforms where that is not available
    the tests still execute; the sandbox contract is documented so users can
    wire it into tighter isolation (Docker, Firejail, gVisor) externally.

The return value is a :class:`SandboxReport` the Senior Agent can route back
to the failing sub-agents for revision.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from agentforge.logging import get_logger
from agentforge.memory import SharedContext
from agentforge.models import ModuleArtifact, SandboxReport

logger = get_logger("sandbox")


def _materialize(artifacts: list[ModuleArtifact], workspace: Path) -> None:
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    for artifact in artifacts:
        for rel_path, content in artifact.files.items():
            target = workspace / rel_path
            # Guard against path traversal: never allow writes outside the
            # workspace.
            resolved = target.resolve()
            if workspace.resolve() not in resolved.parents and resolved != workspace.resolve():
                raise RuntimeError(
                    f"Refusing to write outside sandbox workspace: {rel_path}"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)


_FAILED_MODULE_RE = re.compile(r"FAILED\s+(?P<file>\S+)::")


def _extract_failing_modules(stdout: str) -> list[str]:
    modules: set[str] = set()
    for match in _FAILED_MODULE_RE.finditer(stdout):
        path = match.group("file")
        # tests/test_api.py -> "api", etc. Best-effort.
        base = os.path.basename(path)
        stem = os.path.splitext(base)[0]
        if stem.startswith("test_"):
            stem = stem[len("test_") :]
        modules.add(stem)
    return sorted(modules)


class Sandbox:
    """Isolated test runner."""

    def __init__(
        self,
        context: SharedContext,
        root: Path,
        *,
        disable_network: bool = True,
        timeout_seconds: int = 600,
    ) -> None:
        self.context = context
        self.root = root
        self.workspace = root / "workspace"
        self.disable_network = disable_network
        self.timeout_seconds = timeout_seconds

    def run(self, artifacts: list[ModuleArtifact]) -> SandboxReport:
        _materialize(artifacts, self.workspace)

        self.context.audit(
            "sandbox",
            "materialized",
            {"workspace": str(self.workspace), "files": sum(len(a.files) for a in artifacts)},
        )

        base_cmd = [sys.executable, "-m", "pytest", "-ra", "--color=no"]
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.workspace)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.pop("AGENTFORGE_PROVIDER", None)  # don't leak into generated tests.

        cmd = base_cmd
        if self.disable_network and shutil.which("unshare"):
            cmd = ["unshare", "--net", *base_cmd]

        try:
            result = subprocess.run(
                cmd,
                cwd=self.workspace,
                env=env,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            self.context.audit(
                "sandbox",
                "timeout",
                {"timeout_seconds": self.timeout_seconds},
            )
            return SandboxReport(
                passed=False,
                total=0,
                failed=0,
                skipped=0,
                logs=str(exc),
                failing_modules=[],
            )

        # Fall back to running without `unshare` if it is not permitted in
        # the host container (common in rootless Docker/Podman). We still
        # mark the run as "best-effort isolated" in the audit trail.
        combined = (result.stdout or "") + (result.stderr or "")
        if cmd is not base_cmd and (
            "Operation not permitted" in combined
            or "unshare failed" in combined
            or result.returncode in (1, 255)
            and "unshare" in combined.lower()
        ):
            logger.warning("unshare --net failed, retrying without network isolation")
            self.context.audit(
                "sandbox",
                "unshare_unavailable",
                {"stderr": result.stderr[:500]},
            )
            try:
                result = subprocess.run(
                    base_cmd,
                    cwd=self.workspace,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                return SandboxReport(
                    passed=False,
                    total=0,
                    failed=0,
                    skipped=0,
                    logs=str(exc),
                    failing_modules=[],
                )

        stdout = result.stdout + "\n" + result.stderr

        # Parse pytest's summary line, e.g. "5 passed, 1 failed, 0 skipped".
        def _count(label: str) -> int:
            m = re.search(rf"(\d+)\s+{label}", stdout)
            return int(m.group(1)) if m else 0

        passed = _count("passed")
        failed = _count("failed")
        errors = _count("error")
        skipped = _count("skipped")
        total = passed + failed + errors + skipped

        report = SandboxReport(
            passed=(failed + errors) == 0 and total > 0,
            total=total,
            failed=failed + errors,
            skipped=skipped,
            logs=stdout[-20_000:],  # keep the last 20KB for the report
            failing_modules=_extract_failing_modules(stdout),
        )
        self.context.audit(
            "sandbox",
            "run_complete",
            {
                "passed": report.passed,
                "total": report.total,
                "failed": report.failed,
                "skipped": report.skipped,
            },
        )
        return report


__all__ = ["Sandbox"]
