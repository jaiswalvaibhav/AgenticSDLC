"""Jira Cloud TicketSystem, using REST API v3.

Verified against developer.atlassian.com/cloud/jira (Oct 2026) and current migration
guidance, except where noted:
- Auth: Basic base64(email:api_token), same as Confluence.
- GET/POST /rest/api/3/search/jql  -> the current search endpoint. The old
  /rest/api/3/search (startAt-based) was fully removed; this one paginates with
  nextPageToken, defaults to returning only the `id` field (we always pass `fields`
  explicitly), and does NOT support expand=changelog.
- GET  /rest/api/3/issue/{key}/changelog -> per-issue status history (the only way to
  get changelog now that search can't expand it). NOT independently doc-verified this
  session (the docs page kept truncating) — the {"values": [{"created", "items": [
  {"field", "fromString", "toString"}]}]} shape below is the long-standing, widely used
  Jira Cloud changelog shape, but treat it as unconfirmed until checked against a real
  response from your instance.
- POST /rest/api/3/issue                      -> create (fields.description is ADF, not plain text)
- GET  /rest/api/3/issue/{key}/transitions    -> {"transitions": [{"id", "name", "to": {"name"}}]}
- POST /rest/api/3/issue/{key}/transitions    -> {"transition": {"id": ...}}
- POST /rest/api/3/issue/{key}/comment        -> {"body": <ADF>}
- PUT  /rest/api/3/issue/{key}                -> {"update": {"labels": [{"add": "..."}]}}
- POST /rest/api/3/issueLink                  -> {"type": {"name": "Blocks"}, "inwardIssue", "outwardIssue"}
- POST /rest/api/3/issue/{key}/remotelink     -> {"object": {"url": "...", "title": "..."}}
- GET  /rest/api/3/issue/{key}?fields=attachment -> fields.attachment is a list of
  {id, filename, mimeType, size, content}. **NOT independently doc-verified this
  session** (same caveat as the changelog shape below) — this is the long-standing
  documented Jira Cloud attachment object shape; check it against a real response
  from your instance before relying on it for jira_sync.py's downloader.
- fields.description / fields.comment.comments[].body are ADF documents, same shape
  _to_adf produces for writes; adf_to_text() below is the read-side counterpart
  (minimal — only the node types this instance's own content actually uses).

Sprint placement uses the separate Jira Software "Agile" REST root, /rest/agile/1.0
(not /rest/api/3) — verified live against a real Jira Cloud site (Oct 2026):
- GET  /rest/agile/1.0/board?projectKeyOrId={key}      -> boards for the project
- GET  /rest/agile/1.0/board/{boardId}/sprint?state=future -> not-yet-started sprints
- POST /rest/agile/1.0/sprint {name, originBoardId}    -> create one (state "future")
- POST /rest/agile/1.0/sprint/{sprintId}/issue {"issues": [...]} -> move from backlog

Confirmed by the user: rollup (_rollup in orchestrator.py) queries children with JQL
`parent = <key>`, which is how Jira's simplified issue hierarchy (team-managed
projects) relates Sub-task -> Story -> Epic uniformly — the "AgenticSDLC" Jira
project is team-managed, so this is correct as-is. A classic company-managed project
instead uses a separate "Epic Link" custom field for Story -> Epic, and `parent` only
for Sub-task -> Story; a different project type would need a project-type-aware JQL
clause here.
"""
import base64

import requests

from sdlc.ports import Attachment, Issue, StatusEvent, TransitionNotAvailable


def _to_adf(text: str) -> dict:
    paragraphs = text.split("\n\n") if text else [""]
    return {
        "type": "doc", "version": 1,
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": p}]}
                    for p in paragraphs if p] or [{"type": "paragraph", "content": []}],
    }


def adf_to_text(node: dict | None) -> str:
    """Minimal ADF -> plain text renderer for reading descriptions/comments back out
    (no reverse of _to_adf existed before jira_sync.py needed one). Handles only the
    node types Jira actually emits for this instance's issue content — not a general
    ADF renderer."""
    if not node:
        return ""
    node_type = node.get("type")
    content = node.get("content", [])
    if node_type == "text":
        return node.get("text", "")
    if node_type == "doc":
        return "\n\n".join(adf_to_text(c) for c in content).strip()
    if node_type == "paragraph":
        return "".join(adf_to_text(c) for c in content)
    if node_type == "heading":
        return "## " + "".join(adf_to_text(c) for c in content)
    if node_type in ("bulletList", "orderedList"):
        return "\n".join(adf_to_text(c) for c in content)
    if node_type == "listItem":
        return "- " + "".join(adf_to_text(c) for c in content)
    if node_type == "codeBlock":
        return "```\n" + "".join(adf_to_text(c) for c in content) + "\n```"
    return "".join(adf_to_text(c) for c in content)


def _attachment_from_json(item: dict) -> Attachment:
    return Attachment(attachment_id=item["id"], title=item["filename"],
                       media_type=item.get("mimeType", ""), download_url=item["content"],
                       file_size=item.get("size", 0))


class JiraClient:
    def __init__(self, base_url: str, email: str, api_token: str, project_key: str):
        self.base_url = base_url.rstrip("/")
        self.project_key = project_key
        token = base64.b64encode(f"{email}:{api_token}".encode()).decode()
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Basic {token}"
        self._board_id: int | None = None

    # -- low-level -------------------------------------------------------
    def _get(self, path: str, params: dict | None = None) -> dict:
        resp = self._session.get(f"{self.base_url}/rest/api/3{path}", params=params)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, json_body: dict) -> dict:
        resp = self._session.post(f"{self.base_url}/rest/api/3{path}", json=json_body)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    # -- low-level (Jira Software "Agile" API, a separate REST root from
    # /rest/api/3 — used only for sprint discovery/creation/assignment) -----
    def _agile_get(self, path: str, params: dict | None = None) -> dict:
        resp = self._session.get(f"{self.base_url}/rest/agile/1.0{path}", params=params)
        resp.raise_for_status()
        return resp.json()

    def _agile_post(self, path: str, json_body: dict) -> dict:
        resp = self._session.post(f"{self.base_url}/rest/agile/1.0{path}", json=json_body)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    def _get_all_changelog(self, key: str) -> list[dict]:
        values, next_start = [], 0
        while True:
            page = self._get(f"/issue/{key}/changelog", params={"startAt": next_start, "maxResults": 100})
            values.extend(page.get("values", []))
            if page.get("isLast", True) or not page.get("values"):
                return values
            next_start += len(page["values"])

    def _issue_from_fields(self, key: str, fields: dict) -> Issue:
        parent = fields.get("parent")
        assignee = fields.get("assignee")
        comments = [adf_to_text(c["body"]) for c in fields.get("comment", {}).get("comments", [])] \
            if fields.get("comment") else []
        attachments = [_attachment_from_json(a) for a in fields.get("attachment", []) or []]
        return Issue(
            key=key, issue_type=fields["issuetype"]["name"], status=fields["status"]["name"],
            summary=fields.get("summary", ""), labels=fields.get("labels", []) or [],
            parent_key=parent["key"] if parent else None,
            assignee=assignee["accountId"] if assignee else None,
            description=adf_to_text(fields.get("description")),
            comments=comments, attachments=attachments, updated=fields.get("updated", ""),
        )

    # -- TicketSystem ------------------------------------------------------
    def create_issue(self, issue_type: str, summary: str, *, parent_key: str | None = None,
                      description: str = "", labels: list[str] | None = None,
                      assignee: str | None = None, component: str | None = None,
                      dry_run: bool = True) -> Issue:
        fields: dict = {
            "project": {"key": self.project_key},
            "issuetype": {"name": issue_type},
            "summary": summary,
        }
        if description:
            fields["description"] = _to_adf(description)
        if parent_key:
            fields["parent"] = {"key": parent_key}
        if labels:
            fields["labels"] = labels
        if assignee:
            fields["assignee"] = {"accountId": assignee}
        if component:
            fields["components"] = [{"name": component}]

        if dry_run:
            print(f"[dry-run] would create {issue_type} {summary!r}"
                  f"{f' under {parent_key}' if parent_key else ''}")
            return Issue(key="(dry-run)", issue_type=issue_type, status="To Do", summary=summary,
                         labels=labels or [], parent_key=parent_key, assignee=assignee)
        data = self._post("/issue", {"fields": fields})
        return self.get_issue(data["key"])

    def get_issue(self, key: str) -> Issue:
        data = self._get(f"/issue/{key}", params={
            "fields": "summary,status,labels,parent,assignee,issuetype,description,comment,attachment,updated"})
        return self._issue_from_fields(key, data["fields"])

    def get_attachments(self, key: str) -> list[Attachment]:
        data = self._get(f"/issue/{key}", params={"fields": "attachment"})
        return [_attachment_from_json(a) for a in data["fields"].get("attachment", []) or []]

    def download_attachment(self, attachment: Attachment) -> bytes:
        resp = self._session.get(attachment.download_url)
        resp.raise_for_status()
        return resp.content

    def find_issue_by_label(self, label: str) -> Issue | None:
        results = self.search(f'project = "{self.project_key}" AND labels = "{label}"')
        return results[0] if results else None

    def transition_issue(self, key: str, status_name: str, dry_run: bool = True) -> None:
        transitions = self._get(f"/issue/{key}/transitions")["transitions"]
        match = next((t for t in transitions if t["to"]["name"].lower() == status_name.lower()), None)
        if match is None:
            available = [t["to"]["name"] for t in transitions]
            raise TransitionNotAvailable(
                f"{key}: no transition to {status_name!r} (available: {available})")
        if dry_run:
            print(f"[dry-run] would transition {key} -> {status_name}")
            return
        self._post(f"/issue/{key}/transitions", {"transition": {"id": match["id"]}})

    def add_comment(self, key: str, body: str, dry_run: bool = True) -> None:
        if dry_run:
            print(f"[dry-run] would comment on {key}: {body!r}")
            return
        self._post(f"/issue/{key}/comment", {"body": _to_adf(body)})

    def add_label(self, key: str, label: str, dry_run: bool = True) -> None:
        if dry_run:
            print(f"[dry-run] would add label {label!r} to {key}")
            return
        resp = self._session.put(f"{self.base_url}/rest/api/3/issue/{key}",
                                  json={"update": {"labels": [{"add": label}]}})
        resp.raise_for_status()

    def search(self, jql: str) -> list[Issue]:
        issues, next_token = [], None
        while True:
            body = {"jql": jql, "maxResults": 100,
                     "fields": ["summary", "status", "labels", "parent", "assignee", "issuetype"]}
            if next_token:
                body["nextPageToken"] = next_token
            page = self._post("/search/jql", body)
            issues.extend(self._issue_from_fields(i["key"], i["fields"]) for i in page.get("issues", []))
            next_token = page.get("nextPageToken")
            if page.get("isLast", True) or not next_token:
                return issues

    def link_issues(self, inward_key: str, outward_key: str, link_type: str = "Blocks",
                     dry_run: bool = True) -> None:
        if dry_run:
            print(f"[dry-run] would link {inward_key} {link_type} {outward_key}")
            return
        self._post("/issueLink", {
            "type": {"name": link_type},
            "inwardIssue": {"key": inward_key},
            "outwardIssue": {"key": outward_key},
        })

    def add_remote_link(self, key: str, url: str, title: str, dry_run: bool = True) -> None:
        if dry_run:
            print(f"[dry-run] would add remote link to {key}: {title} ({url})")
            return
        self._post(f"/issue/{key}/remotelink", {"object": {"url": url, "title": title}})

    def _board_id_for_project(self) -> int:
        if self._board_id is None:
            boards = self._agile_get("/board", params={"projectKeyOrId": self.project_key})["values"]
            if not boards:
                raise RuntimeError(f"no Jira Software board found for project {self.project_key!r}")
            self._board_id = boards[0]["id"]
        return self._board_id

    def get_or_create_future_sprint(self, dry_run: bool = True) -> str:
        """Returns the id of a not-yet-started ("future") sprint on this project's
        board, creating one if none exists. Verified live (Oct 2026) against a real
        Jira Cloud site: GET /rest/agile/1.0/board?projectKeyOrId=... for the board,
        GET /rest/agile/1.0/board/{id}/sprint?state=future for existing future sprints,
        and POST /rest/agile/1.0/sprint {name, originBoardId} to create one (it comes
        back in state "future" with no dates, same as a sprint created from the UI and
        not yet started)."""
        if dry_run:
            print("[dry-run] would find or create a future sprint")
            return "(dry-run)"
        board_id = self._board_id_for_project()
        future_sprints = self._agile_get(f"/board/{board_id}/sprint", params={"state": "future"})["values"]
        if future_sprints:
            return str(future_sprints[0]["id"])
        created = self._agile_post("/sprint", {"name": f"{self.project_key} Sprint (auto)",
                                                "originBoardId": board_id})
        return str(created["id"])

    def add_issues_to_sprint(self, sprint_id: str, keys: list[str], dry_run: bool = True) -> None:
        """POST /rest/agile/1.0/sprint/{sprintId}/issue {"issues": [...]} — verified
        live, moves issues straight from the backlog into the given sprint."""
        if not keys:
            return
        if dry_run:
            print(f"[dry-run] would add {keys} to sprint {sprint_id}")
            return
        self._agile_post(f"/sprint/{sprint_id}/issue", {"issues": keys})

    def has_artifact(self, key: str) -> bool:
        data = self._get(f"/issue/{key}", params={"fields": "attachment"})
        if data["fields"].get("attachment"):
            return True
        remote_links = self._get(f"/issue/{key}/remotelink")
        return bool(remote_links)

    def status_changes_since(self, key: str, since: str) -> list[StatusEvent]:
        events = []
        for entry in self._get_all_changelog(key):
            if entry.get("created", "") < since:
                continue
            for item in entry.get("items", []):
                if item.get("field") == "status":
                    events.append(StatusEvent(issue_key=key, from_status=item.get("fromString", ""),
                                               to_status=item.get("toString", "")))
        return events

    def set_property(self, key: str, property_key: str, value: dict, dry_run: bool = True) -> None:
        """Jira entity property — arbitrary JSON attached to the issue itself (separate
        from fields/comments), readable via GET /issue/{key}/properties/{propertyKey}
        without needing our ObjectStore. Used for traceability (see orchestrator.apply_plan)."""
        if dry_run:
            print(f"[dry-run] would set property {property_key!r} on {key}: {value}")
            return
        resp = self._session.put(f"{self.base_url}/rest/api/3/issue/{key}/properties/{property_key}",
                                  json=value)
        resp.raise_for_status()
