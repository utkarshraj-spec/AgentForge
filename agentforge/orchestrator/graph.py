"""LangGraph-backed orchestration for the Senior Agent.

The Senior Agent's :meth:`execute` step used to drive a custom
``ThreadPoolExecutor``-over-topological-waves loop. This module routes the
same semantics through a LangGraph ``StateGraph`` so the orchestration
layer is framework-driven:

* one node per module, each running the module's assigned sub-agents;
* module dependencies become graph edges;
* LangGraph's pregel runtime fans out independent nodes in parallel and
  merges their ``artifacts`` contributions via an ``operator.add`` reducer.

The public surface is a single :func:`build_graph` factory and a
:func:`run_graph` driver. Both are wrapped by ``SeniorAgent.execute``.
"""

from __future__ import annotations

import operator
from collections.abc import Callable
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from agentforge.logging import get_logger, log_event
from agentforge.memory import SharedContext
from agentforge.models import EngineeringSpec, ModuleArtifact, ModuleSpec

logger = get_logger("orchestrator.graph")


def _merge_names(a: list[str], b: list[str]) -> list[str]:
    """Reducer that concatenates lists while de-duplicating."""
    merged = list(a)
    for name in b:
        if name not in merged:
            merged.append(name)
    return merged


class OrchestratorState(TypedDict, total=False):
    """Shared state that flows through the LangGraph execution."""

    # Concatenated via ``operator.add`` so parallel branches can merge
    # independently produced artifacts without clobbering each other.
    artifacts: Annotated[list[ModuleArtifact], operator.add]
    failed_modules: Annotated[list[str], _merge_names]
    completed_modules: Annotated[list[str], _merge_names]


BuildModule = Callable[[EngineeringSpec, ModuleSpec], list[ModuleArtifact]]


def build_graph(
    spec: EngineeringSpec,
    modules: list[ModuleSpec],
    build_module: BuildModule,
    context: SharedContext,
):
    """Compile a LangGraph ``StateGraph`` for *modules*.

    Parameters
    ----------
    spec:
        The engineering spec passed through to every node.
    modules:
        The module plan produced by :meth:`SeniorAgent.plan`.
    build_module:
        Callable that actually dispatches a module to its sub-agents and
        returns the artifacts it produced. The Senior Agent supplies
        :meth:`SeniorAgent._build_module` bound to itself.
    context:
        Shared context used for audit-trail events emitted per node.
    """

    graph: StateGraph = StateGraph(OrchestratorState)
    module_by_name = {m.name: m for m in modules}

    def _make_node(module: ModuleSpec):
        def _node(state: OrchestratorState) -> OrchestratorState:
            completed = set(state.get("completed_modules") or [])
            failed = set(state.get("failed_modules") or [])

            # LangGraph fires a node once per incoming edge that becomes
            # active. For DAG modules with multiple dependencies this
            # means the node may be invoked before all deps finish, or
            # more than once. Gate execution: run exactly once, only
            # when every dependency has completed.
            if module.name in completed or module.name in failed:
                return {}
            unmet = [d for d in module.depends_on if d not in completed]
            if unmet:
                context.audit(
                    "orchestrator.graph",
                    "node_deferred",
                    {"module": module.name, "waiting_on": unmet},
                )
                return {}

            log_event(
                logger,
                "graph.node_start",
                module=module.name,
                deps=module.depends_on,
            )
            context.audit(
                "orchestrator.graph",
                "node_start",
                {"module": module.name, "deps": module.depends_on},
            )
            try:
                produced = build_module(spec, module)
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("Module %s failed in graph: %s", module.name, exc)
                context.audit(
                    "orchestrator.graph",
                    "node_failed",
                    {"module": module.name, "error": str(exc)},
                )
                return {"failed_modules": [module.name]}

            context.audit(
                "orchestrator.graph",
                "node_done",
                {"module": module.name, "artifact_count": len(produced)},
            )
            return {
                "artifacts": list(produced),
                "completed_modules": [module.name],
            }

        return _node

    for module in modules:
        graph.add_node(module.name, _make_node(module))

    # Wire dependencies. Modules without deps come straight from START.
    for module in modules:
        if not module.depends_on:
            graph.add_edge(START, module.name)
        else:
            for dep in module.depends_on:
                if dep in module_by_name:
                    graph.add_edge(dep, module.name)
                else:  # pragma: no cover - defensive
                    logger.warning(
                        "Module %s depends on unknown module %s; skipping edge",
                        module.name,
                        dep,
                    )
                    graph.add_edge(START, module.name)

    # Every terminal module (no other module depends on it) feeds END so
    # the pregel runtime knows when execution is complete.
    depended_on = {dep for m in modules for dep in m.depends_on}
    terminals = [m.name for m in modules if m.name not in depended_on]
    for name in terminals or [m.name for m in modules]:
        graph.add_edge(name, END)

    return graph.compile()


def run_graph(
    spec: EngineeringSpec,
    modules: list[ModuleSpec],
    build_module: BuildModule,
    context: SharedContext,
) -> list[ModuleArtifact]:
    """Execute the LangGraph pipeline and return the merged artifacts."""

    compiled = build_graph(spec, modules, build_module, context)
    final_state: OrchestratorState = compiled.invoke({"artifacts": []})
    failed = final_state.get("failed_modules") or []
    if failed:
        raise RuntimeError(
            f"LangGraph execution reported failures in modules: {failed}"
        )
    return list(final_state.get("artifacts") or [])
