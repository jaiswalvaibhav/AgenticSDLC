"""`workflow cleanup`: deletes every Jira issue of a use case (found by its `uc:<use_case>`
label) and the Confluence pages the workflow created (workflow/pages.py's record, plus any
plan page linked from the tickets by title), then forgets the saved run state and resets the
Jira checkpoint. Never touches the Knowledge Base."""
from sdlc.adapters.jira_polling import CHECKPOINT_KEY
from sdlc.agents.activity import clear_step
from sdlc.agents.analyst.ticket_anchors import _page_id
from sdlc.ports import DocumentSource, ObjectStore, TicketSystem
from sdlc.workflow.pages import delete_pages, recorded_pages


def _depth(issue, by_key: dict) -> int:
    depth = 0
    while issue.parent_key in by_key:
        issue = by_key[issue.parent_key]
        depth += 1
    return depth


def cleanup_run(*, tickets: TicketSystem, doc_source: DocumentSource | None, store: ObjectStore,
                use_case: str, plan_page_title: str | None = None, dry_run: bool = True) -> dict:
    """If any Jira delete fails (e.g. no Delete issues permission), stops there: the pages
    and saved state are left alone, so the run still matches what is left in Jira.
    With no doc_source (the UI's "Start fresh" without "also delete the pages") no Confluence
    page is looked up or deleted and the record of created pages is kept; plan_page_title
    (pages from before the record existed) is only used with a doc_source. A page that fails
    to delete is reported in "page_errors" but doesn't stop the saved state being cleared."""
    issues = tickets.search(f'labels = "uc:{use_case}"')
    by_key = {i.key: i for i in issues}
    page_ids = set(recorded_pages(store, use_case)) if doc_source else set()
    page_ids |= {pid for i in issues for url, title in tickets.get_remote_links(i.key)
                 if doc_source and title == plan_page_title and (pid := _page_id(url))}
    result = {"issues": [i.key for i in issues], "pages": sorted(page_ids), "failed": {},
              "page_errors": {}}

    for issue in sorted(issues, key=lambda i: _depth(i, by_key), reverse=True):  # children first
        try:
            tickets.delete_issue(issue.key, dry_run=dry_run)
        except Exception as exc:
            result["failed"][issue.key] = f"{type(exc).__name__}: {exc}"
            return result
    if doc_source:
        result["page_errors"] = delete_pages(doc_source, store, use_case, page_ids, dry_run=dry_run)
    for step_id in store.get_json(f"workflow/{use_case}/steps.json") or {}:
        clear_step(store, use_case, step_id, dry_run=dry_run)
    for name in ("steps", "run_state", "plan", "traceability", "llm_usage"):
        store.delete(f"workflow/{use_case}/{name}.json", dry_run=dry_run)
    store.delete(f"workflow/{use_case}/plan.md", dry_run=dry_run)
    store.delete(f"ui/{use_case}/selection.json", dry_run=dry_run)
    store.delete(CHECKPOINT_KEY, dry_run=dry_run)
    return result
