"""sdlc CLI. `config show`, `workflow preview`, `sync` and `seed` work; the rest are stubs."""
from pathlib import Path

import typer

from sdlc.adapters.bedrock_kb import BedrockKnowledgeIndex
from sdlc.adapters.confluence import ConfluenceClient
from sdlc.adapters.fake import FakeAgentRuntime
from sdlc.adapters.jira import JiraClient
from sdlc.adapters.jira_polling import PollingStatusSource
from sdlc.adapters.local_store import LocalObjectStore
from sdlc.aws import deploy as aws_deploy_mod
from sdlc.aws import destroy as aws_destroy_mod
from sdlc.aws_sync import aws_sync as run_aws_sync
from sdlc.config import load_config, masked
from sdlc.confluence_sync import sync_tree
from sdlc.seed import seed_usecase
from sdlc.workflow import orchestrator
from sdlc.workflow.registry import WorkflowRegistry

app = typer.Typer(no_args_is_help=True, add_completion=False)
workflow_app = typer.Typer(no_args_is_help=True)
app.add_typer(workflow_app, name="workflow")


def _stub(command: str, phase: int) -> None:
    typer.echo(f"`{command}` is not implemented yet — it lands in Phase {phase}. See docs/BRIEF.md.")
    raise typer.Exit(code=1)


def _confluence_client(cfg: dict) -> ConfluenceClient:
    atlassian = cfg["atlassian"]
    return ConfluenceClient(
        base_url=atlassian["base_url"], email=atlassian["email"],
        api_token=atlassian["api_token"], space_key=cfg["confluence"]["space_key"],
    )


def _jira_client(cfg: dict) -> JiraClient:
    atlassian = cfg["atlassian"]
    return JiraClient(
        base_url=atlassian["base_url"], email=atlassian["email"],
        api_token=atlassian["api_token"], project_key=cfg["jira"]["project_key"],
    )


@app.command()
def setup() -> None:
    _stub("setup", 3)  # one-time setup becomes meaningful once page_roles.yaml exists (Phase 3)


@app.command()
def seed(use_case: str = typer.Option(None, "--use-case", help="defaults to config use_case"),
          dry_run: bool = True) -> None:
    """Seed the demo Confluence page tree + diagrams for a use case (dry-run by default)."""
    cfg = load_config()
    use_case = use_case or cfg["use_case"]
    client = _confluence_client(cfg)
    result = seed_usecase(client, Path("usecases") / use_case, dry_run=dry_run)
    typer.echo(f"created={len(result.created)} skipped(already existed)={len(result.skipped)}")


@app.command()
def sync(root_page_id: str = typer.Option(..., "--root-page-id", help="Confluence page id to sync"),
          dry_run: bool = True) -> None:
    """Download the Confluence tree under --root-page-id into the local corpus."""
    cfg = load_config()
    client = _confluence_client(cfg)
    store = LocalObjectStore(cfg["state_dir"])
    result = sync_tree(client, store, root_page_id=root_page_id, data_dir=cfg["data_dir"], dry_run=dry_run)
    typer.echo(f"created={len(result.created)} updated={len(result.updated)} "
               f"moved={len(result.moved)} deleted={len(result.deleted)} "
               f"unchanged={len(result.unchanged)}")


@app.command()
def search(query: str, top_k: int = 10) -> None:
    """Search the Managed Knowledge Base, scoped to the configured use case."""
    cfg = load_config()
    index = BedrockKnowledgeIndex(knowledge_base_id=cfg["aws"]["knowledge_base_id"],
                                   region=cfg["aws"]["region"])
    for chunk in index.search(query, use_case=cfg["use_case"], top_k=top_k):
        typer.echo(f"[{chunk.score:.3f}] {chunk.page_title}  ({chunk.page_url})")
        typer.echo(f"    {chunk.text[:300]}")
        typer.echo(f"    s3: {chunk.s3_uri}\n")


@app.command(name="analyst-plan")
def analyst_plan() -> None:
    _stub("analyst-plan", 6)


@app.command(name="analyst-apply")
def analyst_apply() -> None:
    _stub("analyst-apply", 6)


def _sync_once(cfg: dict, *, dry_run: bool) -> None:
    tickets = _jira_client(cfg)
    store = LocalObjectStore(cfg["state_dir"])
    registry = WorkflowRegistry.load()
    agent = FakeAgentRuntime()  # real AgentRuntime wiring lands in Phase 6/7
    status_source = PollingStatusSource(tickets, store, cfg["jira"]["project_key"])

    events = status_source.poll(dry_run=dry_run)
    for event in events:
        orchestrator.handle_status_change(event, tickets=tickets, store=store, cfg=cfg,
                                           registry=registry, agent=agent, dry_run=dry_run)
    acted = orchestrator.check_approvals(tickets=tickets, store=store, cfg=cfg,
                                         use_case=cfg["use_case"], dry_run=dry_run)
    typer.echo(f"status events: {len(events)}, approvals/rejections acted on: {acted}")


@app.command(name="sync-progress")
def sync_progress(dry_run: bool = True) -> None:
    """One-shot: poll Jira for status changes, run rollup + step readiness, and check
    any steps awaiting the approval/reject label."""
    _sync_once(load_config(), dry_run=dry_run)


@app.command(name="watch-progress")
def watch_progress(dry_run: bool = True) -> None:
    """Loop sync-progress forever, every jira.poll_interval_seconds."""
    import time
    cfg = load_config()
    interval = cfg["jira"]["poll_interval_seconds"]
    while True:
        _sync_once(cfg, dry_run=dry_run)
        time.sleep(interval)


@app.command(name="aws-deploy")
def aws_deploy(dry_run: bool = True) -> None:
    """Deploy the storage stack (CloudFormation) + Managed Knowledge Base + data
    source (CLI step), and write the resolved ids back into config.yaml."""
    aws_deploy_mod.deploy(load_config(), dry_run=dry_run)


@app.command(name="aws-sync")
def aws_sync(dry_run: bool = True) -> None:
    """Render changed pages' PDFs, sync them to S3, and run a Knowledge Base ingestion."""
    result = run_aws_sync(load_config(), dry_run=dry_run)
    typer.echo(f"uploaded={len(result.uploaded)} deleted={len(result.deleted)} "
               f"ingestion_job={result.ingestion_job_id} status={result.ingestion_status}")


@app.command(name="aws-destroy")
def aws_destroy(dry_run: bool = True) -> None:
    """Delete exactly what aws-deploy created (and, from Phase 7, AgentCore), in
    reverse order, from the resource ledger."""
    aws_destroy_mod.destroy(load_config(), dry_run=dry_run)


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
    """Create the use-case epic + step tickets (dry-run unless --apply)."""
    cfg = load_config()
    registry = WorkflowRegistry.load()
    tickets = _jira_client(cfg)
    store = LocalObjectStore(cfg["state_dir"])
    step_list = [s.strip() for s in steps.split(",")] if steps else None

    steps_map = orchestrator.workflow_start(
        tickets=tickets, store=store, registry=registry, cfg=cfg, use_case=use_case,
        from_step=from_step, steps=step_list, dry_run=not apply,
    )
    for step_id, key in steps_map.items():
        typer.echo(f"  {step_id}: {key}")


if __name__ == "__main__":
    app()
