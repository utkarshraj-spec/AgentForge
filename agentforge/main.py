"""AgentForge entry point.

Usage
-----

    $ agentforge "Build a REST API for a todo app with user authentication"

By default output is written to ``./.agentforge_runs/<timestamp>/``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import typer
from rich import print as rprint
from rich.panel import Panel
from rich.table import Table

from agentforge.config import get_config
from agentforge.intake import parse_prompt
from agentforge.logging import get_logger, setup_logging
from agentforge.memory import SharedContext
from agentforge.models import PipelineResult
from agentforge.orchestrator import SeniorAgent
from agentforge.output import assemble, write_audit_report
from agentforge.pipeline import ReviewLoop, Sandbox

logger = get_logger("main")

app = typer.Typer(
    help="AgentForge — autonomous multi-agent AI engineering system.",
    no_args_is_help=True,
    add_completion=False,
)


def run_pipeline(
    prompt: str,
    *,
    output_root: Path | None = None,
    skip_sandbox: bool = False,
) -> PipelineResult:
    """Execute every stage of the AgentForge pipeline for *prompt*."""

    setup_logging()
    cfg = get_config()
    logger.info("AgentForge starting (provider=%s)", cfg.resolved_provider())

    output_root = output_root or Path(".agentforge_runs")
    run_dir = output_root / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    context = SharedContext(persist_dir=cfg.chroma_persist_dir)
    context.audit("main", "run_start", {"prompt": prompt, "run_dir": str(run_dir)})

    # Stage 1 — intake
    spec = parse_prompt(prompt)
    context.put("spec", spec)

    # Stage 2/3 — orchestration + sub-agents
    senior = SeniorAgent(context)
    modules = senior.plan(spec)
    artifacts = senior.execute(spec, modules)
    artifacts = senior.reconcile(artifacts)

    # Stage 4 — review loop
    artifacts, rounds = ReviewLoop(context).run(artifacts)

    # Stage 5 — sandbox
    sandbox_report = None
    if not skip_sandbox:
        sandbox = Sandbox(context, run_dir / "sandbox")
        sandbox_report = sandbox.run(artifacts)

    # Stage 6 — delivery
    project_dir = assemble(run_dir, artifacts, context)
    report_paths = write_audit_report(run_dir, context, rounds, sandbox_report)

    return PipelineResult(
        spec=spec,
        modules=modules,
        artifacts=artifacts,
        rounds=rounds,
        sandbox=sandbox_report,
        audit_report_path=str(report_paths["audit"]),
        dependency_map_path=str(report_paths["dependencies"]),
        test_coverage_path=str(report_paths["coverage"]),
        output_dir=str(project_dir),
    )


@app.command()
def build(
    prompt: str = typer.Argument(..., help="Plain-English description of the project to build."),
    output: Path = typer.Option(Path(".agentforge_runs"), "--output", "-o", help="Run output root."),
    skip_sandbox: bool = typer.Option(False, "--skip-sandbox", help="Skip the sandbox test stage."),
) -> None:
    """Build a project end-to-end from the given prompt."""

    result = run_pipeline(prompt, output_root=output, skip_sandbox=skip_sandbox)

    table = Table(title="AgentForge run summary")
    table.add_column("Stage")
    table.add_column("Outcome")
    table.add_row("Intake", f"project_name={result.spec.project_name}")
    table.add_row("Modules", ", ".join(m.name for m in result.modules))
    table.add_row("Artifacts", str(sum(len(a.files) for a in result.artifacts)))
    table.add_row("Review rounds", str(len(result.rounds)))
    table.add_row(
        "Critical remaining",
        str(result.rounds[-1].critical_remaining if result.rounds else 0),
    )
    if result.sandbox is not None:
        table.add_row(
            "Sandbox",
            f"passed={result.sandbox.passed} total={result.sandbox.total} failed={result.sandbox.failed}",
        )
    else:
        table.add_row("Sandbox", "skipped")
    rprint(table)
    rprint(
        Panel.fit(
            f"[bold]Project written to:[/bold] {result.output_dir}\n"
            f"[bold]Audit report:[/bold] {result.audit_report_path}\n"
            f"[bold]Dependency map:[/bold] {result.dependency_map_path}\n"
            f"[bold]Test coverage:[/bold] {result.test_coverage_path}",
            title="Delivery",
        )
    )


@app.command()
def version() -> None:
    """Print the installed AgentForge version."""
    from agentforge.version import __version__

    rprint(__version__)


if __name__ == "__main__":
    app()
