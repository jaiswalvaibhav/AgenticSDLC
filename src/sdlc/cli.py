"""sdlc CLI. Phase 1: `config show` and `workflow preview` work; the rest are stubs."""
import typer

from sdlc.config import load_config, masked
from sdlc.workflow.registry import WorkflowRegistry

app = typer.Typer(no_args_is_help=True, add_completion=False)
workflow_app = typer.Typer(no_args_is_help=True)
app.add_typer(workflow_app, name="workflow")


def _stub(command: str, phase: int) -> None:
    typer.echo(f"`{command}` is not implemented yet — it lands in Phase {phase}. See docs/BRIEF.md.")
    raise typer.Exit(code=1)


@app.command()
def setup() -> None:
    _stub("setup", 2)


@app.command()
def seed(dry_run: bool = True) -> None:
    _stub("seed", 3)


@app.command()
def sync(dry_run: bool = True) -> None:
    _stub("sync", 2)


@app.command()
def search(query: str) -> None:
    _stub("search", 4)


@app.command(name="analyst-plan")
def analyst_plan() -> None:
    _stub("analyst-plan", 6)


@app.command(name="analyst-apply")
def analyst_apply() -> None:
    _stub("analyst-apply", 6)


@app.command(name="sync-progress")
def sync_progress(dry_run: bool = True) -> None:
    _stub("sync-progress", 5)


@app.command(name="watch-progress")
def watch_progress() -> None:
    _stub("watch-progress", 5)


@app.command(name="aws-deploy")
def aws_deploy(dry_run: bool = True) -> None:
    _stub("aws-deploy", 4)


@app.command(name="aws-sync")
def aws_sync(dry_run: bool = True) -> None:
    _stub("aws-sync", 4)


@app.command(name="aws-destroy")
def aws_destroy(dry_run: bool = True) -> None:
    _stub("aws-destroy", 7)


config_app = typer.Typer(no_args_is_help=True)
app.add_typer(config_app, name="config")


@config_app.command("show")
def config_show() -> None:
    """Print the merged config (config.yaml + .env/env overrides), secrets masked."""
    import json
    typer.echo(json.dumps(masked(load_config()), indent=2))


@workflow_app.command("preview")
def workflow_preview(
    use_case: str = typer.Option(..., "--use-case"),
    from_step: str | None = typer.Option(None, "--from"),
    steps: str | None = typer.Option(None, "--steps", help="comma-separated step ids"),
) -> None:
    """Preview the epic + tickets `workflow start` would create for this selection."""
    registry = WorkflowRegistry.load()
    step_list = [s.strip() for s in steps.split(",")] if steps else None
    selection = registry.resolve_selection(from_step=from_step, steps=step_list)

    typer.echo(f"Epic: [{use_case}] Delivery")
    typer.echo("\nStories (worked steps):")
    for step in selection.selected:
        auto = f" (automated: {step.automation})" if step.automation else ""
        typer.echo(f"  - {step.artifact} [{step.id}] owner={step.owner}{auto}")

    if selection.artifact_only:
        typer.echo("\nArtifact-only stories (attach artifact, mark Done):")
        for step in selection.artifact_only:
            typer.echo(f"  - {step.artifact} [{step.id}]")


@workflow_app.command("start")
def workflow_start(
    use_case: str = typer.Option(..., "--use-case"),
    from_step: str | None = typer.Option(None, "--from"),
    steps: str | None = typer.Option(None, "--steps"),
    apply: bool = typer.Option(False, "--apply"),
) -> None:
    _stub("workflow start", 5)


if __name__ == "__main__":
    app()
