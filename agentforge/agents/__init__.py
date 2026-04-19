"""Sub-agent pool.

Each agent is a *stateless* module that exposes either a :func:`run` function
(for build-time agents) or a scan/apply function (for review-time agents).
The Senior Agent dispatches work to these agents based on the module kind.
"""

from agentforge.agents import (
    code_writer,
    conflict_detector,
    refactor_agent,
    security_scanner,
    test_writer,
)

__all__ = [
    "code_writer",
    "conflict_detector",
    "refactor_agent",
    "security_scanner",
    "test_writer",
]
