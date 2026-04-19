"""Assemble the final, delivered codebase on disk."""

from __future__ import annotations

from pathlib import Path

from agentforge.memory import SharedContext
from agentforge.models import ModuleArtifact


def assemble(
    output_dir: Path,
    artifacts: list[ModuleArtifact],
    context: SharedContext,
) -> Path:
    """Write all artifact files into *output_dir*/project and return that path."""

    project_dir = output_dir / "project"
    project_dir.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    for artifact in artifacts:
        for rel_path, content in artifact.files.items():
            target = project_dir / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
            written.append(rel_path)

    context.audit(
        "final_assembler",
        "assembled",
        {"output_dir": str(project_dir), "file_count": len(written)},
    )
    return project_dir


__all__ = ["assemble"]
