"""PollingStatusSource: JQL `project = X AND updated >= <checkpoint>` to find issues
that changed, then each one's changelog for the actual status transitions since the
checkpoint. The checkpoint is persisted via ObjectStore, so it survives between
`sync-progress` runs (and is shared with `watch-progress`'s loop).

A WebhookStatusSource isn't built (BRIEF.md says not to, yet) — this stays as the
reconciliation source even once a webhook exists.

Note: the checkpoint is kept as an ISO8601 string (to compare lexically against
changelog `created` timestamps in status_changes_since) and reformatted to JQL's
"yyyy/MM/dd HH:mm" date-literal syntax for the search clause.
"""
from datetime import datetime, timezone

from sdlc.ports import ObjectStore, StatusEvent, TicketSystem

CHECKPOINT_KEY = "jira_checkpoint.json"
_JQL_DATETIME = "%Y/%m/%d %H:%M"


class PollingStatusSource:
    def __init__(self, tickets: TicketSystem, store: ObjectStore, project_key: str):
        self.tickets = tickets
        self.store = store
        self.project_key = project_key

    def poll(self, *, dry_run: bool = False) -> list[StatusEvent]:
        checkpoint_iso = (self.store.get_json(CHECKPOINT_KEY) or {}).get("since")
        now_iso = datetime.now(timezone.utc).isoformat()

        if checkpoint_iso is None:
            self.store.put_json(CHECKPOINT_KEY, {"since": now_iso}, dry_run=dry_run)
            return []  # first run establishes the baseline only

        jql_date = datetime.fromisoformat(checkpoint_iso).strftime(_JQL_DATETIME)
        jql = f'project = "{self.project_key}" AND updated >= "{jql_date}"'
        events = [
            event
            for issue in self.tickets.search(jql)
            for event in self.tickets.status_changes_since(issue.key, since=checkpoint_iso)
        ]
        self.store.put_json(CHECKPOINT_KEY, {"since": now_iso}, dry_run=dry_run)
        return events
