"""The Senior Software Agent — the master planner.

Responsibilities
----------------
* Read the :class:`EngineeringSpec` produced by Stage 1.
* Perform explicit chain-of-thought reasoning before assigning tasks.
* Break the project into modules (auth, database, api, frontend, security,
  tests, docs, ...).
* Assign each module to a set of sub-agents, respecting dependency order.
* Coordinate parallel execution and reconcile conflicts.

The Senior Agent is itself a stateless function: it takes shared context +
spec in, and returns a plan + artifacts out. Any state (plan, artifacts,
round results, audit trail) lives in :class:`SharedContext`.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from graphlib import CycleError, TopologicalSorter

from agentforge.agents import (
    code_writer,
    conflict_detector,
    refactor_agent,
    test_writer,
)
from agentforge.logging import get_logger, log_event
from agentforge.memory import SharedContext
from agentforge.models import EngineeringSpec, ModuleArtifact, ModuleSpec
from agentforge.providers import Message, extract_json, get_provider

logger = get_logger("senior_agent")


_PLAN_SYSTEM_PROMPT = """You are the Senior Software Architect of an autonomous
engineering system. You have just received a structured specification. Think
step-by-step about the minimal set of modules required to deliver it. For
each module, list its kind (auth, database, api, frontend, security, tests,
docs, infra), a 1-sentence description, and its dependencies (module names
declared earlier). Then respond in strict JSON:

{
  "chain_of_thought": "your reasoning",
  "modules": [
    {
      "name": "...",
      "kind": "...",
      "description": "...",
      "depends_on": ["..."],
      "acceptance_criteria": ["..."]
    }
  ]
}
<task:plan>
"""


_DEFAULT_MODULES = [
    ModuleSpec(
        name="database",
        kind="database",
        description="Persistence layer with SQLAlchemy models and a SQLite default.",
        depends_on=[],
        acceptance_criteria=["Declarative schema", "Session factory"],
    ),
    ModuleSpec(
        name="auth",
        kind="auth",
        description="User authentication with bcrypt-hashed passwords and JWT tokens.",
        depends_on=["database"],
        acceptance_criteria=["Signup", "Login", "JWT issuance"],
    ),
    ModuleSpec(
        name="api",
        kind="api",
        description="REST API surface implementing the spec's functional requirements.",
        depends_on=["auth", "database"],
        acceptance_criteria=["CRUD endpoints", "Auth-protected routes"],
    ),
    ModuleSpec(
        name="security",
        kind="security",
        description="Security hardening (input validation, rate limits, headers).",
        depends_on=["api"],
        acceptance_criteria=["Rate limiting", "Security headers"],
    ),
    ModuleSpec(
        name="tests",
        kind="tests",
        description="Integration and unit tests exercising the full API surface.",
        depends_on=["api", "auth", "database"],
        acceptance_criteria=["Happy path", "Auth failure path"],
    ),
    ModuleSpec(
        name="docs",
        kind="docs",
        description="README and architecture documentation.",
        depends_on=[],
        acceptance_criteria=["Setup instructions", "API reference"],
    ),
]


_AGENT_ASSIGNMENT: dict[str, list[str]] = {
    "auth": ["code_writer", "security_scanner", "test_writer"],
    "database": ["code_writer", "test_writer"],
    "api": ["code_writer", "security_scanner", "test_writer"],
    "frontend": ["code_writer", "test_writer"],
    "security": ["security_scanner", "refactor_agent"],
    "tests": ["test_writer"],
    "docs": ["code_writer"],
    "infra": ["code_writer"],
}


class SeniorAgent:
    """Master planner and coordinator."""

    def __init__(self, context: SharedContext) -> None:
        self.context = context

    # ------------------------------------------------------------------
    # Planning
    # ------------------------------------------------------------------

    def plan(self, spec: EngineeringSpec) -> list[ModuleSpec]:
        """Run chain-of-thought planning and produce module assignments."""

        log_event(logger, "plan.start", project=spec.project_name)
        self.context.audit("senior_agent", "plan.start", {"project": spec.project_name})

        provider = get_provider()
        try:
            raw = provider.complete(
                [
                    Message(role="system", content=_PLAN_SYSTEM_PROMPT),
                    Message(role="user", content=spec.model_dump_json()),
                ],
                temperature=0.2,
            )
            data = extract_json(raw)
            cot = data.get("chain_of_thought", "")
            if cot:
                self.context.remember(cot, metadata={"kind": "chain_of_thought", "stage": "plan"})
            modules = [ModuleSpec(**m) for m in data.get("modules") or []]
        except Exception as exc:
            logger.warning("Plan LLM failed, using default module set: %s", exc)
            modules = []

        if not modules:
            modules = [m.model_copy(deep=True) for m in _DEFAULT_MODULES]

        # Always ensure tests + docs are in the plan.
        have = {m.name for m in modules}
        for required in ("tests", "docs"):
            if required not in have:
                modules.append(next(m for m in _DEFAULT_MODULES if m.name == required).model_copy(deep=True))

        # Assign sub-agents.
        for module in modules:
            module.assigned_agents = _AGENT_ASSIGNMENT.get(module.kind, ["code_writer", "test_writer"])

        self.context.put("spec", spec)
        self.context.put("plan", modules)
        self.context.audit(
            "senior_agent",
            "plan.done",
            {"modules": [m.name for m in modules]},
        )
        log_event(logger, "plan.done", modules=[m.name for m in modules])
        return modules

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    @staticmethod
    def _dependency_order(modules: list[ModuleSpec]) -> list[list[str]]:
        """Return waves of module names that can be built in parallel."""
        graph: dict[str, set[str]] = {m.name: set(m.depends_on) for m in modules}
        ts = TopologicalSorter(graph)
        try:
            ts.prepare()
        except CycleError as exc:
            raise RuntimeError(f"Module dependency cycle: {exc}") from exc
        waves: list[list[str]] = []
        while True:
            ready = list(ts.get_ready())
            if not ready:
                break
            waves.append(ready)
            for node in ready:
                ts.done(node)
        return waves

    def execute(
        self,
        spec: EngineeringSpec,
        modules: list[ModuleSpec],
        *,
        max_workers: int = 8,
    ) -> list[ModuleArtifact]:
        """Run sub-agents for every module respecting dependency order."""

        waves = self._dependency_order(modules)
        module_by_name = {m.name: m for m in modules}
        artifacts: list[ModuleArtifact] = []

        for wave_index, wave in enumerate(waves):
            log_event(logger, "execute.wave", wave=wave_index, modules=wave)
            self.context.audit(
                "senior_agent",
                "execute.wave",
                {"wave": wave_index, "modules": wave},
            )
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                future_to_module = {
                    pool.submit(self._build_module, spec, module_by_name[name]): name
                    for name in wave
                }
                for fut in as_completed(future_to_module):
                    name = future_to_module[fut]
                    try:
                        produced = fut.result()
                    except Exception as exc:
                        logger.exception("Module %s failed: %s", name, exc)
                        self.context.audit(
                            "senior_agent",
                            "execute.module_failed",
                            {"module": name, "error": str(exc)},
                        )
                        raise
                    artifacts.extend(produced)

        self.context.put("artifacts", artifacts)
        self.context.audit(
            "senior_agent",
            "execute.done",
            {"artifact_count": len(artifacts)},
        )
        return artifacts

    def _build_module(
        self, spec: EngineeringSpec, module: ModuleSpec
    ) -> list[ModuleArtifact]:
        """Dispatch to the agents assigned to this module."""

        artifacts: list[ModuleArtifact] = []
        for agent_name in module.assigned_agents:
            self.context.audit(
                "senior_agent",
                "dispatch",
                {"module": module.name, "agent": agent_name},
            )
            if agent_name == "code_writer":
                artifacts.append(code_writer.run(spec, module, self.context))
            elif agent_name == "test_writer":
                artifacts.append(test_writer.run(spec, module, self.context))
            elif agent_name == "security_scanner":
                # Security scanner runs in the review loop, not on initial build.
                continue
            elif agent_name in ("conflict_detector", "refactor_agent"):
                continue
            else:
                logger.warning("Unknown agent %s for module %s", agent_name, module.name)
        return artifacts

    # ------------------------------------------------------------------
    # Conflict resolution surface (used by the review loop)
    # ------------------------------------------------------------------

    def reconcile(
        self,
        artifacts: list[ModuleArtifact],
    ) -> list[ModuleArtifact]:
        """Detect cross-agent conflicts and delegate patches to refactor_agent."""

        findings = conflict_detector.scan(artifacts, self.context)
        if findings:
            self.context.audit(
                "senior_agent",
                "reconcile.findings",
                {"count": len(findings)},
            )
            artifacts = refactor_agent.apply(artifacts, findings, self.context)
        return artifacts


__all__ = ["SeniorAgent"]
