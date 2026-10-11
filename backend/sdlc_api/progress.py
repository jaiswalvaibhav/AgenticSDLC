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


def remember_selection(store: ObjectStore, use_case: str, generate: list[str], provide: list[str]) -> None:
    """Which steps the user ticked (generate) vs. were added as inputs (provide). Merged
    across triggers, since steps.json only ever grows."""
    old = store.get_json(selection_key(use_case)) or {}
    merged_generate = sorted(set(old.get("generate", [])) | set(generate))
    merged_provide = sorted((set(old.get("provide", [])) | set(provide)) - set(merged_generate))
    store.put_json(selection_key(use_case), {"generate": merged_generate, "provide": merged_provide},
                   dry_run=False)


def _anchor(step_id: str, key: str, tickets: TicketSystem) -> dict:
    try:
        source = pick_anchor_source(key, tickets)
    except AnchorMissing as exc:
        return {"ok": False, "reason": exc.reason}
    if isinstance(source, AttachmentSource):
        return {"ok": True, "kind": "file", "name": source.attachment.title}
    return {"ok": True, "kind": "link", "name": source.title or source.page_id}


def build_progress(*, use_case: str, registry: WorkflowRegistry, tickets: TicketSystem,
                    store: ObjectStore, cfg: dict) -> dict:
    steps_map = orchestrator._steps_map(store, use_case)
    run_state = orchestrator._run_state(store, use_case)
    provide = set((store.get_json(selection_key(use_case)) or {}).get("provide", []))
    plan_ready = store.get_json(f"workflow/{use_case}/plan.json") is not None
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
            "recent_comments": issue.comments[-3:],
        })
    return {"built_at": datetime.now(timezone.utc).isoformat(),
            "approval_label": cfg["jira"]["approval_label"], "steps": rows}
