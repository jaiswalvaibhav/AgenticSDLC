"""sdlc CLI. `config show`, `workflow preview`, `sync` and `seed` work; the rest are stubs."""
from pathlib import Path

import typer

from sdlc.adapters.bedrock_kb import BedrockKnowledgeIndex
from sdlc.agents.analyst.tasks import TASKS
from sdlc.aws import agent_deploy as agent_deploy_mod
from sdlc.aws import deploy as aws_deploy_mod
from sdlc.aws import destroy as aws_destroy_mod
from sdlc.aws import jira_kb_deploy as jira_kb_deploy_mod
from sdlc.aws_sync import aws_sync as run_aws_sync
from sdlc.config import load_config, masked
from sdlc.confluence_sync import sync_tree
from sdlc.jira_aws_sync import jira_aws_sync as run_jira_aws_sync
from sdlc.jira_citations import jira_url_from_s3_uri
from sdlc.jira_sync import sync_issues as sync_jira_issues
from sdlc.seed import seed_usecase
from sdlc.sync_once import sync_once
from sdlc.wiring import agent_runtime, confluence_client, jira_client, object_store
from sdlc.workflow import orchestrator
from sdlc.workflow.registry import WorkflowRegistry

app = typer.Typer(no_args_is_help=True, add_completion=False)
workflow_app = typer.Typer(no_args_is_help=True)
app.add_typer(workflow_app, name="workflow")


def _stub(command: str, phase: int) -> None:
    typer.echo(f"`{command}` is not implemented yet — it lands in Phase {phase}. See docs/BRIEF.md.")
    raise typer.Exit(code=1)


@app.command()
def setup() -> None:
    _stub("setup", 3)  # one-time setup becomes meaningful once page_roles.yaml exists (Phase 3)


@app.command()
def seed(use_case: str = typer.Option(None, "--use-case", help="defaults to config use_case"),
          dry_run: bool = True) -> None:
    """Seed the demo Confluence page tree + diagrams for a use case (dry-run by default)."""
    cfg = load_config()
    use_case = use_case or cfg["use_case"]
    client = confluence_client(cfg)
    result = seed_usecase(client, Path("usecases") / use_case, dry_run=dry_run)
    typer.echo(f"created={len(result.created)} skipped(already existed)={len(result.skipped)}")


@app.command()
def sync(root_page_id: str = typer.Option(..., "--root-page-id", help="Confluence page id to sync"),
          dry_run: bool = True) -> None:
    """Download the Confluence tree under --root-page-id into the local corpus."""
    cfg = load_config()
    client = confluence_client(cfg)
    store = object_store(cfg)
    result = sync_tree(client, store, root_page_id=root_page_id, data_dir=cfg["data_dir"], dry_run=dry_run)
    typer.echo(f"created={len(result.created)} updated={len(result.updated)} "
               f"moved={len(result.moved)} deleted={len(result.deleted)} "
               f"unchanged={len(result.unchanged)} failed={len(result.failed)}")


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


@app.command(name="jira-sync")
def jira_sync_cmd(epic_key: str = typer.Option(..., "--epic-key", help="Jira epic key to download"),
                   dry_run: bool = True) -> None:
    """Download a Jira epic + its Stories/Sub-tasks into .data/corpus_jira/, mirroring
    Jira's own parent hierarchy. Separate from Confluence sync; never run by the
    orchestrator — always triggered by hand for a specific epic."""
    cfg = load_config()
    tickets = jira_client(cfg)
    store = object_store(cfg)
    result = sync_jira_issues(tickets, store, epic_key=epic_key, base_url=cfg["atlassian"]["base_url"],
                               data_dir=cfg["data_dir"], dry_run=dry_run)
    typer.echo(f"fetched={len(result.fetched)} unchanged={len(result.unchanged)} "
               f"failed={len(result.failed)}")
    if result.failed:
        typer.echo(f"  failed: {result.failed} (will retry on the next jira-sync run)")


@app.command(name="jira-kb-create")
def jira_kb_create(dry_run: bool = True) -> None:
    """One-time setup: create the separate Managed Knowledge Base + S3 data source
    for the Jira downloader, reusing the storage stack `aws-deploy` already created.
    Writes jira_knowledge_base_id/jira_data_source_id back into config.yaml."""
    jira_kb_deploy_mod.deploy_jira_kb(load_config(), dry_run=dry_run)


@app.command(name="jira-aws-sync")
def jira_aws_sync_cmd(dry_run: bool = True) -> None:
    """Upload .data/corpus_jira/ to S3 and run ingestion against the Jira Knowledge
    Base only. Never called by aws-sync or the orchestrator — explicit trigger only."""
    result = run_jira_aws_sync(load_config(), dry_run=dry_run)
    typer.echo(f"uploaded={len(result.uploaded)} unchanged={len(result.unchanged)} "
               f"failed={len(result.failed)} deleted={len(result.deleted)} "
               f"ingestion_job={result.ingestion_job_id} status={result.ingestion_status}")
    if result.failed:
        typer.echo(f"  failed: {result.failed} (will retry on the next jira-aws-sync run)")


@app.command(name="jira-search")
def jira_search(query: str, top_k: int = 10) -> None:
    """Search the Jira Knowledge Base, scoped to the configured use case. Manual
    testing command; not wired into the analyst agent's search_knowledge tool."""
    cfg = load_config()
    index = BedrockKnowledgeIndex(knowledge_base_id=cfg["aws"]["jira_knowledge_base_id"],
                                   region=cfg["aws"]["region"])
    base_url = cfg["atlassian"]["base_url"]
    for chunk in index.search(query, use_case=cfg["use_case"], top_k=top_k):
        jira_url = jira_url_from_s3_uri(chunk.s3_uri, base_url) or chunk.s3_uri
        typer.echo(f"[{chunk.score:.3f}] {jira_url}")
        typer.echo(f"    {chunk.text[:300]}")
        typer.echo(f"    s3: {chunk.s3_uri}\n")


@app.command(name="analyst-plan")
def analyst_plan(use_case: str = typer.Option(None, "--use-case"),
                  step: str = typer.Option("solution_requirements", "--step"),
                  dry_run: bool = True) -> None:
    """Manually run an analyst task's plan generation for a step (bypasses the
    orchestrator's readiness gating — useful to test/regenerate without Jira events)."""
    cfg = load_config()
    use_case = use_case or cfg["use_case"]
    tickets = jira_client(cfg)
    store = object_store(cfg)
    task = TASKS.get(step)
    if not task:
        typer.echo(f"no analyst task registered for step {step!r}")
        raise typer.Exit(code=1)

    steps_map = store.get_json(f"workflow/{use_case}/steps.json") or {}
    issue_key = steps_map.get(step)
    if not issue_key:
        typer.echo(f"no ticket for step {step!r} yet — run `workflow start` first")
        raise typer.Exit(code=1)

    result = agent_runtime(cfg, tickets, store).run(
        task.id, {"use_case": use_case, "step_id": step, "issue_key": issue_key, "dry_run": dry_run})
    typer.echo(result)


@app.command(name="analyst-apply")
def analyst_apply(use_case: str = typer.Option(None, "--use-case"),
                   step: str = typer.Option("solution_requirements", "--step"),
                   dry_run: bool = True) -> None:
    """Manually apply a stored plan.json for a step (bypasses the sdlc-approved label
    check — the same real work orchestrator.check_approvals does automatically)."""
    cfg = load_config()
    use_case = use_case or cfg["use_case"]
    tickets = jira_client(cfg)
    store = object_store(cfg)
    statuses = orchestrator.load_terminology(use_case)["statuses"]
    steps_map = store.get_json(f"workflow/{use_case}/steps.json") or {}
    issue_key = steps_map.get(step)
    if not issue_key:
        typer.echo(f"no ticket for step {step!r} yet — run `workflow start` first")
        raise typer.Exit(code=1)

    orchestrator.apply_plan(step, issue_key, tickets=tickets, store=store, use_case=use_case,
                             cfg=cfg, done_status=statuses["done"], dry_run=dry_run)


@app.command(name="sync-progress")
def sync_progress(dry_run: bool = True) -> None:
    """One-shot: poll Jira for status changes, run rollup + step readiness, and check
    any steps awaiting the approval/reject label."""
    result = sync_once(load_config(), dry_run=dry_run)
    typer.echo(f"status events: {result['status_events']}, "
               f"approvals/rejections acted on: {result['approvals_acted_on']}")


@app.command(name="watch-progress")
def watch_progress(dry_run: bool = True) -> None:
    """Loop sync-progress forever, every jira.poll_interval_seconds. In the aws profile,
    prefer the scheduled Lambda (see infra/aws/templates/agent.yaml) over leaving this
    running — an always-on loop has a standing cost, which a scheduled one-shot avoids."""
    import time
    cfg = load_config()
    interval = cfg["jira"]["poll_interval_seconds"]
    while True:
        sync_once(cfg, dry_run=dry_run)
        time.sleep(interval)


@app.command(name="aws-deploy")
def aws_deploy(dry_run: bool = True) -> None:
    """Deploy the storage stack + KB (CloudFormation + CLI step), then the agent stack:
    AgentCore execution role, scheduled-orchestrator Lambda + EventBridge schedule
    (CloudFormation), and the AgentCore Runtime itself (agentcore CLI toolkit).
    Writes the resolved ids back into config.yaml."""
    aws_deploy_mod.deploy(load_config(), dry_run=dry_run)
    agent_deploy_mod.deploy(load_config(), dry_run=dry_run)  # reloaded: picks up kb/role ids just written


@app.command(name="aws-sync")
def aws_sync(dry_run: bool = True) -> None:
    """Render changed pages' PDFs, sync them to S3, and run a Knowledge Base ingestion."""
    result = run_aws_sync(load_config(), dry_run=dry_run)
    typer.echo(f"uploaded={len(result.uploaded)} deleted={len(result.deleted)} "
               f"ingestion_job={result.ingestion_job_id} status={result.ingestion_status}")


@app.command(name="aws-destroy")
def aws_destroy(dry_run: bool = True) -> None:
    """Delete exactly what aws-deploy created, in reverse order, from the resource ledger."""
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
    tickets = jira_client(cfg)
    store = object_store(cfg)
    step_list = [s.strip() for s in steps.split(",")] if steps else None

    steps_map = orchestrator.workflow_start(
        tickets=tickets, store=store, registry=registry, cfg=cfg, use_case=use_case,
        from_step=from_step, steps=step_list, dry_run=not apply,
    )
    for step_id, key in steps_map.items():
        typer.echo(f"  {step_id}: {key}")


if __name__ == "__main__":
    app()
