"""Core pydantic data models shared across the pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return uuid4().hex[:12]


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Constraint(BaseModel):
    """A non-functional constraint extracted from the user's prompt."""

    kind: str
    description: str


class EngineeringSpec(BaseModel):
    """Structured specification produced by Stage 1 (prompt intake)."""

    project_name: str
    summary: str
    functional_requirements: list[str] = Field(default_factory=list)
    non_functional_requirements: list[str] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)
    target_stack: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    raw_prompt: str = ""


class ModuleSpec(BaseModel):
    """A single module the Senior Agent plans and assigns."""

    name: str
    kind: str  # auth, database, api, frontend, security, tests, docs, ...
    description: str
    depends_on: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    assigned_agents: list[str] = Field(default_factory=list)


class ModuleArtifact(BaseModel):
    """Output of a sub-agent working on a module."""

    module: str
    agent: str
    files: dict[str, str] = Field(default_factory=dict)  # relative path -> contents
    notes: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class Finding(BaseModel):
    """A review finding raised by an agent."""

    id: str = Field(default_factory=_uid)
    round: int = 0
    kind: str  # conflict | security | quality | coverage
    module: str | None = None
    file: str | None = None
    severity: Severity = Severity.MEDIUM
    message: str
    suggestion: str | None = None
    owasp_category: str | None = None
    resolved: bool = False


class ReviewRoundResult(BaseModel):
    round: int
    findings: list[Finding] = Field(default_factory=list)
    fixed: list[str] = Field(default_factory=list)  # finding ids auto-fixed this round
    critical_remaining: int = 0


class SandboxReport(BaseModel):
    passed: bool
    total: int
    failed: int
    skipped: int
    logs: str
    failing_modules: list[str] = Field(default_factory=list)


class AuditEntry(BaseModel):
    id: str = Field(default_factory=_uid)
    timestamp: str = Field(default_factory=_now)
    actor: str
    event: str
    payload: dict[str, Any] = Field(default_factory=dict)


class PipelineResult(BaseModel):
    """Final envelope returned to the user."""

    spec: EngineeringSpec
    modules: list[ModuleSpec]
    artifacts: list[ModuleArtifact]
    rounds: list[ReviewRoundResult]
    sandbox: SandboxReport | None = None
    audit_report_path: str | None = None
    dependency_map_path: str | None = None
    test_coverage_path: str | None = None
    output_dir: str
