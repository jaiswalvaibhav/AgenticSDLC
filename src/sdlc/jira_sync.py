"""Downloads a Jira epic's ticket tree (Epic -> Story -> Sub-task) into a local
corpus mirroring Jira's own parent hierarchy, with each ticket's attachments
co-located in its own folder (no separate _attachments/ subfolder) — for Knowledge
Base ingestion via jira_aws_sync.py. A separate pipeline from Confluence's: never
invoked by workflow_start, sync-progress/watch-progress, or orchestrator.py — always
triggered by hand, scoped to one epic given explicitly via --epic-key.

Layout: <data_dir>/corpus_jira/<epicKey>/[<storyKey>/[<subtaskKey>/]]
          issue.md, meta.json, <attachment files>

Epic -> Story -> Sub-task is resolved via the real Jira `parent` links
workflow_start/apply_plan already create (orchestrator.py) — plain `search()`
calls, no new Jira capability needed.

A download trace (jira_trace.json in the ObjectStore, keyed by issue key) records
success/failure per ticket, so a partial failure can be retried on the next run
instead of either silently losing it or (as confluence_sync.py did before this
change — see its own docstring) aborting the whole batch on the first exception.
An issue is re-fetched when it's new, its `updated` timestamp changed since the
last *successful* fetch, or its last recorded status was "failed".

Markdown, not PDF: Bedrock's S3 data source ingests .md directly (verified live,
Oct 2026), and Jira issues have no embedded-diagram requirement the way Confluence
pages do (attachments are uploaded as their own separate S3 objects regardless), so
there's nothing to flatten into a PDF.
"""
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sdlc.ports import Issue, ObjectStore, TicketSystem

TRACE_KEY = "jira_trace.json"
FETCH_WORKERS = 8

# Bedrock S3 data source support + size limits (verified live, Oct 2026): images are
# capped tighter than every other supported format.
_IMAGE_EXTENSIONS = {".jpeg", ".jpg", ".png"}
_SUPPORTED_EXTENSIONS = _IMAGE_EXTENSIONS | {".txt", ".md", ".html", ".doc", ".docx",
                                              ".csv", ".xls", ".xlsx", ".pdf"}
_IMAGE_SIZE_LIMIT = int(3.75 * 1024 * 1024)
_OTHER_SIZE_LIMIT = 50 * 1024 * 1024


@dataclass
class SyncResult:
    fetched: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)


def _ticket_dir(data_dir: Path, path_parts: tuple[str, ...]) -> Path:
    return data_dir / "corpus_jira" / Path(*path_parts)


def _resolve_tree(tickets: TicketSystem, epic_key: str) -> list[tuple[str, tuple[str, ...]]]:
    """(issue_key, path_parts) for the epic + every Story under it + every Sub-task
    under each Story. Lightweight search() calls only (no full-detail fetch here),
    so a failure fetching one ticket's detail later doesn't block discovering the
    rest of the tree."""
    pairs: list[tuple[str, tuple[str, ...]]] = [(epic_key, (epic_key,))]
    for story in tickets.search(f'parent = "{epic_key}"'):
        story_path = (epic_key, story.key)
        pairs.append((story.key, story_path))
        for subtask in tickets.search(f'parent = "{story.key}"'):
            pairs.append((subtask.key, story_path + (subtask.key,)))
    return pairs


def sync_issues(tickets: TicketSystem, store: ObjectStore, *, epic_key: str, base_url: str,
                 data_dir: str | Path, dry_run: bool = True) -> SyncResult:
    data_dir = Path(data_dir)
    trace = store.get_json(TRACE_KEY) or {}
    candidates = _resolve_tree(tickets, epic_key)
    result = SyncResult()

    if dry_run:
        for key, parts in candidates:
            print(f"[dry-run] would check {key} and write "
                  f"{_ticket_dir(data_dir, parts)}/issue.md if new/changed/previously failed")
        return result

    def process(item: tuple[str, tuple[str, ...]]) -> tuple[str, str, str | None, str | None]:
        key, parts = item
        try:
            issue = tickets.get_issue(key)
            entry = trace.get(key)
            if entry and entry.get("status") == "success" and entry.get("last_success_updated") == issue.updated:
                return key, "unchanged", None, issue.updated
            _write_ticket(tickets, issue, _ticket_dir(data_dir, parts), base_url)
            return key, "fetched", None, issue.updated
        except Exception as exc:  # noqa: BLE001 -- isolate per-ticket, see module docstring
            print(f"[error] failed to sync Jira issue {key}: {exc}")
            return key, "failed", str(exc), None

    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        outcomes = list(pool.map(process, candidates))

    now = datetime.now(timezone.utc).isoformat()
    for key, outcome, error, updated in outcomes:
        if outcome == "fetched":
            trace[key] = {"status": "success", "error": None, "last_attempt": now,
                          "last_success_updated": updated}
            result.fetched.append(key)
        elif outcome == "unchanged":
            result.unchanged.append(key)
        else:
            previous = trace.get(key, {})
            trace[key] = {"status": "failed", "error": error, "last_attempt": now,
                          "last_success_updated": previous.get("last_success_updated")}
            result.failed.append(key)

    store.put_json(TRACE_KEY, trace, dry_run=dry_run)
    return result


def _check_attachment(file_size: int, ext: str) -> tuple[str, str | None]:
    if ext not in _SUPPORTED_EXTENSIONS:
        return "skipped_unsupported_type", f"{ext or 'no extension'} isn't ingestible by the Knowledge Base"
    limit = _IMAGE_SIZE_LIMIT if ext in _IMAGE_EXTENSIONS else _OTHER_SIZE_LIMIT
    if file_size > limit:
        return "skipped_too_large", f"{file_size} bytes exceeds the {limit}-byte limit"
    return "downloaded", None


def _write_ticket(tickets: TicketSystem, issue: Issue, ticket_dir: Path, base_url: str) -> None:
    ticket_dir.mkdir(parents=True, exist_ok=True)

    attachment_meta = []
    notes: list[str] = []
    for attachment in issue.attachments:
        ext = Path(attachment.title).suffix.lower()
        status, reason = _check_attachment(attachment.file_size, ext)
        if status == "downloaded":
            try:
                data = tickets.download_attachment(attachment)
            except Exception as exc:  # noqa: BLE001 -- log + note, don't abort the whole ticket
                status, reason = "failed_download", str(exc)
                print(f"[error] failed to download attachment {attachment.title!r} on {issue.key}: {exc}")
            else:
                (ticket_dir / attachment.title).write_bytes(data)
        if status != "downloaded":
            notes.append(f"> Attachment not downloaded: {attachment.title} ({reason})")
        attachment_meta.append({"filename": attachment.title, "mediaType": attachment.media_type,
                                 "size": attachment.file_size, "status": status, "reason": reason})

    (ticket_dir / "issue.md").write_text(_render_markdown(issue, base_url, notes))
    (ticket_dir / "meta.json").write_text(json.dumps({
        "issueKey": issue.key, "issueType": issue.issue_type, "status": issue.status,
        "updated": issue.updated, "summary": issue.summary, "attachments": attachment_meta,
    }, indent=2))


def _render_markdown(issue: Issue, base_url: str, notes: list[str]) -> str:
    lines = [
        f"# {issue.summary}",
        "",
        f"Jira: {issue.key} — {base_url.rstrip('/')}/browse/{issue.key}",
        "",
        f"Type: {issue.issue_type}  \nStatus: {issue.status}",
        "",
    ]
    if issue.description:
        lines += ["## Description", "", issue.description, ""]
    if issue.comments:
        lines += ["## Comments", ""]
        for comment in issue.comments:
            lines += [comment, ""]
    if notes:
        lines += ["## Attachments", "", *notes, ""]
    return "\n".join(lines)
