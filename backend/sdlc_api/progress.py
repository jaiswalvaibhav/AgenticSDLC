"""Builds the per-step progress snapshot the UI renders: read-only, from Jira + the
ObjectStore. It reuses the orchestrator's own state (steps.json, run_state.json,
plan.json) and ticket_anchors' artifact check, so what the UI shows is exactly what the
orchestrator and the analyst agent will see. Costs get_issue + get_remote_links per step
(plus the anchor check for steps that feed an automated step) — hence the caller caches it.
"""
from datetime import datetime, timezone

from sdlc.agents.analyst.ticket_anchors import AnchorMissing, AttachmentSource, pick_anchor_source
from sdlc.ports import ObjectStore, TicketSystem
from sdlc.workflow import orchestrator
from sdlc.workflow.registry import WorkflowRegistry


def selection_key(use_case: str) -> str:
    return f"ui/{use_case}/selection.json"


def remember_selection(store: ObjectStore, use_case: str, generate: list[str], provide: list[str],
                        project_key: str) -> None:
    """Which steps the user ticked (generate) vs. were added as inputs (provide), and which
    Jira project the run lives in. Merged across triggers, since steps.json only ever grows;
    the project and start time are those of the run's first trigger."""
    old = store.get_json(selection_key(use_case)) or {}
    merged_generate = sorted(set(old.get("generate", [])) | set(generate))
    merged_provide = sorted((set(old.get("provide", [])) | set(provide)) - set(merged_generate))
    store.put_json(selection_key(use_case), {
        "generate": merged_generate, "provide": merged_provide,
        "project_key": old.get("project_key", project_key),
        "started_at": old.get("started_at", datetime.now(timezone.utc).isoformat())}, dry_run=False)


def run_info(store: ObjectStore, use_case: str, current_project_key: str) -> dict:
    """Which run the saved state describes — read from the store only (no Jira), so it works
    even when the saved tickets can't be fetched. `stale` = the run is in a different Jira
    project than the one configured now. A run saved before projects were recorded has its
    project inferred from its ticket keys."""
    steps_map = orchestrator._steps_map(store, use_case)
    if not steps_map:
        return {"exists": False, "current_project_key": current_project_key}
    saved = store.get_json(selection_key(use_case)) or {}
    project = saved.get("project_key") or next(iter(steps_map.values())).rsplit("-", 1)[0]
    return {"exists": True, "project_key": project, "started_at": saved.get("started_at"),
            "ticket_count": len(steps_map), "current_project_key": current_project_key,
            "stale": project != current_project_key}


def reset_run(store: ObjectStore, use_case: str) -> None:
    """Forgets the saved run (ticket map, run state, plan, traceability, selection). Jira
    tickets are not touched: a later trigger re-adopts same-project tickets by their marker
    labels (workflow_start), and creates new ones in a different project. Leaves the Jira
    status checkpoint alone — it only bounds how far back the poller looks."""
    for key in (f"workflow/{use_case}/steps.json", f"workflow/{use_case}/run_state.json",
                f"workflow/{use_case}/plan.json", f"workflow/{use_case}/plan.md",
                f"workflow/{use_case}/traceability.json", f"workflow/{use_case}/llm_usage.json",
                selection_key(use_case)):
        store.delete(key, dry_run=False)


def _anchor(step_id: str, key: str, tickets: TicketSystem) -> dict:
    try:
        source = pick_anchor_source(key, tickets)
    except AnchorMissing as exc:
        return {"ok": False, "reason": exc.reason}
    if isinstance(source, AttachmentSource):
        return {"ok": True, "kind": "file", "name": source.attachment.title}
    return {"ok": True, "kind": "link", "name": source.title or source.page_id, "via": source.via}


def build_progress(*, use_case: str, registry: WorkflowRegistry, tickets: TicketSystem,
                    store: ObjectStore, cfg: dict) -> dict:
    steps_map = orchestrator._steps_map(store, use_case)
    run_state = orchestrator._run_state(store, use_case)
    provide = set((store.get_json(selection_key(use_case)) or {}).get("provide", []))
    plan_ready = store.get_json(f"workflow/{use_case}/plan.json") is not None
    llm_usage = store.get_json(f"workflow/{use_case}/llm_usage.json") or {}
    done_status = orchestrator.load_terminology(use_case)["statuses"]["done"]
    base_url = cfg["atlassian"]["base_url"].rstrip("/")
    feeds_automation = {dep for s in registry.all_steps() if s.automation for dep in s.inputs}

    rows = []
    for step in registry.all_steps():
        key = steps_map.get(step.id)
        if not key:
            continue
        issue = tickets.get_issue(key)
        rows.append({
            "step_id": step.id, "artifact": step.artifact, "key": key,
            "jira_url": f"{base_url}/browse/{key}",
            "role": "automated" if step.automation else ("provide" if step.id in provide else "generate"),
            "status": issue.status, "done": issue.status == done_status,
            "files": [a.title for a in issue.attachments],
            "links": [{"url": url, "title": title} for url, title in tickets.get_remote_links(key)],
            "anchor": _anchor(step.id, key, tickets) if step.id in feeds_automation else None,
            "run_state": run_state.get(step.id),
            "plan_ready": plan_ready if step.automation else None,
            "llm_usage": llm_usage.get(step.id),
            "recent_comments": issue.comments[-3:],
        })
    return {"built_at": datetime.now(timezone.utc).isoformat(),
            "approval_label": cfg["jira"]["approval_label"], "steps": rows}
