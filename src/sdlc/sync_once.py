"""One sync-progress cycle: poll Jira, run rollup + step readiness, check approvals.
Shared by the CLI (sync-progress/watch-progress) and the scheduled-orchestrator
Lambda (profile=aws) — this is the one-shot command the brief's EventBridge schedule
runs, kept free of CLI/Typer so the Lambda can import it directly.
"""
from sdlc.adapters.jira_polling import PollingStatusSource
from sdlc.wiring import agent_runtime, jira_client, object_store
from sdlc.workflow import orchestrator
from sdlc.workflow.registry import WorkflowRegistry


def sync_once(cfg: dict, *, dry_run: bool) -> dict:
    tickets = jira_client(cfg)
    store = object_store(cfg)
    registry = WorkflowRegistry.load()
    agent = agent_runtime(cfg, tickets, store)
    status_source = PollingStatusSource(tickets, store, cfg["jira"]["project_key"])

    events = status_source.poll(dry_run=dry_run)
    for event in events:
        orchestrator.handle_status_change(event, tickets=tickets, store=store, cfg=cfg,
                                           registry=registry, agent=agent, dry_run=dry_run)
    acted = orchestrator.check_approvals(tickets=tickets, store=store, cfg=cfg,
                                         use_case=cfg["use_case"], dry_run=dry_run)
    return {"status_events": len(events), "approvals_acted_on": acted}
