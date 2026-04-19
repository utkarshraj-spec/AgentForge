"""Stage 1 — turn a plain-English prompt into a structured EngineeringSpec."""

from __future__ import annotations

import re

from agentforge.logging import get_logger
from agentforge.models import Constraint, EngineeringSpec
from agentforge.providers import Message, extract_json, get_provider

logger = get_logger("intake")

_SYSTEM_PROMPT = """You are an intake engineer for an autonomous coding system.
Read the user's plain-English prompt and extract a structured engineering
specification. Respond in strict JSON with the following shape:

{
  "project_name": "snake_case_name",
  "summary": "one paragraph summary of what to build",
  "functional_requirements": ["..."],
  "non_functional_requirements": ["..."],
  "constraints": [{"kind": "stack|security|perf|...", "description": "..."}],
  "target_stack": ["python", "fastapi", ...],
  "success_criteria": ["..."]
}

Do not include prose outside the JSON object.
"""


_KEYWORDS: dict[str, list[str]] = {
    "auth": ["auth", "login", "signup", "user account", "authentication", "jwt", "oauth"],
    "database": ["database", "persist", "store", "sql", "postgres", "sqlite", "mongo"],
    "api": ["api", "rest", "endpoint", "graphql", "http"],
    "frontend": ["frontend", "ui", "react", "svelte", "vue", "html", "dashboard"],
    "realtime": ["websocket", "real-time", "streaming", "pubsub"],
    "ml": ["model", "inference", "training", "ml", "ai"],
}


def _heuristic_spec(prompt: str) -> EngineeringSpec:
    """Extract a reasonable spec from the prompt without an LLM.

    This runs always and is also used as the baseline before merging the
    LLM's structured output (which may omit things).
    """

    lowered = prompt.lower()
    name_match = re.search(r"([a-z][a-z0-9_]{2,})\s+(app|api|service|system|tool|bot)", lowered)
    project_name = (
        name_match.group(1) if name_match else re.sub(r"[^a-z0-9]+", "_", lowered)[:32].strip("_") or "user_project"
    )

    functional: list[str] = []
    for sentence in re.split(r"[.!?]", prompt):
        s = sentence.strip()
        if not s:
            continue
        if any(kw in s.lower() for kw in ["build", "create", "implement", "add", "support", "allow"]):
            functional.append(s)
    if not functional:
        functional = [prompt.strip()]

    stack: list[str] = []
    for kw in ["python", "fastapi", "flask", "django", "react", "typescript", "node", "postgres", "sqlite", "redis"]:
        if kw in lowered:
            stack.append(kw)
    if not stack:
        stack = ["python", "fastapi", "sqlite"]

    constraints: list[Constraint] = []
    if any(w in lowered for w in ["secure", "auth", "password", "login"]):
        constraints.append(Constraint(kind="security", description="OWASP Top 10 compliance required"))
    if "test" in lowered or "tested" in lowered:
        constraints.append(Constraint(kind="quality", description="Comprehensive test coverage required"))

    return EngineeringSpec(
        project_name=project_name,
        summary=prompt.strip(),
        functional_requirements=functional,
        non_functional_requirements=[
            "Production ready",
            "Secure by default",
            "Fully tested",
        ],
        constraints=constraints,
        target_stack=stack,
        success_criteria=[
            "All integration tests pass",
            "No critical security findings",
            "Documentation is complete",
        ],
        raw_prompt=prompt,
    )


def parse_prompt(prompt: str) -> EngineeringSpec:
    """Parse a user prompt into an :class:`EngineeringSpec`."""

    baseline = _heuristic_spec(prompt)
    provider = get_provider()

    try:
        raw = provider.complete(
            [
                Message(role="system", content=_SYSTEM_PROMPT + "\n<task:intake>"),
                Message(role="user", content=prompt),
            ],
            temperature=0.1,
        )
        data = extract_json(raw)
    except Exception as exc:
        logger.warning("Intake LLM failed, using heuristic spec: %s", exc)
        return baseline

    # Merge LLM output on top of the heuristic baseline so missing fields
    # do not wipe out useful defaults.
    merged = baseline.model_dump()
    for key in (
        "project_name",
        "summary",
        "functional_requirements",
        "non_functional_requirements",
        "target_stack",
        "success_criteria",
    ):
        if data.get(key):
            merged[key] = data[key]
    if data.get("constraints"):
        merged["constraints"] = [
            Constraint(**c).model_dump() if not isinstance(c, Constraint) else c.model_dump()
            for c in data["constraints"]
        ]
    merged["raw_prompt"] = prompt
    return EngineeringSpec(**merged)
