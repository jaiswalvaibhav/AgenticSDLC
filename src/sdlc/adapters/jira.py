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
- GET  /rest/api/3/issue/{key}?fields=attachment -> fields.attachment is a list

ASSUMPTION flagged for the user to confirm: rollup (_rollup in orchestrator.py) queries
children with JQL `parent = <key>`, which is how Jira's simplified issue hierarchy
(team-managed projects) relates Sub-task -> Story -> Epic uniformly. A classic
company-managed project instead uses a separate "Epic Link" custom field for
Story -> Epic, and `parent` only for Sub-task -> Story. If your Jira project is
company-managed, tell me and this needs a project-type-aware JQL clause.
"""
import base64

import requests

from sdlc.ports import Issue, StatusEvent, TransitionNotAvailable


def _to_adf(text: str) -> dict:
    paragraphs = text.split("\n\n") if text else [""]
    return {
        "type": "doc", "version": 1,
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": p}]}
                    for p in paragraphs if p] or [{"type": "paragraph", "content": []}],
    }


class JiraClient:
    def __init__(self, base_url: str, email: str, api_token: str, project_key: str):
        self.base_url = base_url.rstrip("/")
        self.project_key = project_key
        token = base64.b64encode(f"{email}:{api_token}".encode()).decode()
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Basic {token}"

    # -- low-level -------------------------------------------------------
    def _get(self, path: str, params: dict | None = None) -> dict:
        resp = self._session.get(f"{self.base_url}/rest/api/3{path}", params=params)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, json_body: dict) -> dict:
        resp = self._session.post(f"{self.base_url}/rest/api/3{path}", json=json_body)
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
        return Issue(
            key=key, issue_type=fields["issuetype"]["name"], status=fields["status"]["name"],
            summary=fields.get("summary", ""), labels=fields.get("labels", []) or [],
            parent_key=parent["key"] if parent else None,
            assignee=assignee["accountId"] if assignee else None,
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
        data = self._get(f"/issue/{key}", params={"fields": "summary,status,labels,parent,assignee,issuetype"})
        return self._issue_from_fields(key, data["fields"])

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
