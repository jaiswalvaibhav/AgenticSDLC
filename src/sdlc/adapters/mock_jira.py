"""Mock Jira TicketSystem: tickets live in the ObjectStore (a local folder for profile=local,
S3 for profile=aws), so the whole workflow runs without a Jira site. Layout, under
`mock_jira/<PROJECT_KEY>/`:

  board.json            project, key/sprint counters, `rank` (every issue key in backlog
                        order) and the sprints. The ObjectStore can't list keys, so this
                        file is also the index of issues.
  issues/<KEY>.json     one readable file per ticket (fields, comments, links, changelog).
  attachments/<id>      raw attachment bytes.

Several processes write (the UI backend, the poller, the Lambda), so every write is a
read-modify-write that retries on VersionConflict (ObjectStore.put_bytes_if). Creating an
issue touches two files (the board, then the issue), so a crash in between can leave a key
in `rank` with no file: readers skip those.

JQL: only what this project's own code emits — AND-joined `field = "x"` for project, key,
parent, labels, status, issuetype, plus `updated >= "yyyy/MM/dd HH:mm"` (read as UTC). An
ORDER BY is ignored; anything else raises ValueError so a gap fails loudly.
"""
import json
import re
import uuid
from datetime import datetime, timedelta, timezone

from sdlc.adapters.jira import _URL_RE
from sdlc.ports import Attachment, Issue, StatusEvent, TransitionNotAvailable, VersionConflict

DEFAULT_ISSUE_TYPES = ["Epic", "Story", "Task", "Bug", "Subtask"]
DEFAULT_STATUSES = ["To Do", "In Progress", "Done"]
_SUBTASK_TYPES = {"subtask", "sub-task"}
PRIORITIES = ["Highest", "High", "Medium", "Low", "Lowest"]
_EDITABLE = {"summary", "description", "assignee", "priority", "story_points", "components", "parent"}
_SPRINT_EDITABLE = {"name", "goal", "start_date", "end_date"}
_RETRIES = 5
_CLAUSE = re.compile(r'(\w+)\s*(>=|=)\s*"([^"]*)"')
_JQL_DATETIME = "%Y/%m/%d %H:%M"
# JQL field -> issue-document key, for the plain `field = "x"` clauses
_FIELDS = {"labels": "labels", "project": "key", "key": "key", "parent": "parent",
           "status": "status", "issuetype": "issuetype"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MockJiraClient:
    def __init__(self, store, project_key: str = "MOCK", project_name: str = "Mock SDLC",
                 issue_types: list[str] | None = None, statuses: list[str] | None = None,
                 author: str = "SDLC bot", now=_now):
        self.store = store
        self.project_key = project_key
        self.project_name = project_name
        self.issue_types = issue_types or DEFAULT_ISSUE_TYPES
        self.statuses = statuses or DEFAULT_STATUSES
        self.author = author
        self._now = now
        self._prefix = f"mock_jira/{project_key}/"

    # -- storage -----------------------------------------------------------
    def _stamp(self) -> str:
        return self._now().isoformat()

    def _issue_key(self, key: str) -> str:
        return f"{self._prefix}issues/{key}.json"

    def _default_board(self) -> dict:
        return {"project": {"key": self.project_key, "name": self.project_name},
                "next_issue_num": 1, "next_sprint_id": 1, "rank": [], "sprints": []}

    def _read(self, path: str, default: dict | None) -> dict | None:
        data, _ = self.store.get_bytes_versioned(path)
        return json.loads(data) if data is not None else default

    def _modify(self, path: str, mutate, default: dict | None = None):
        """Read-modify-write one JSON file, retrying when another writer got in first.
        `mutate` edits the document in place and may return a value."""
        for _ in range(_RETRIES):
            data, version = self.store.get_bytes_versioned(path)
            doc = json.loads(data) if data is not None else default
            if doc is None:
                raise KeyError(path)
            result = mutate(doc)
            try:
                self.store.put_bytes_if(path, json.dumps(doc, indent=2).encode(), version,
                                         dry_run=False)
            except VersionConflict:
                continue
            return result
        raise VersionConflict(path)

    def _board(self) -> dict:
        return self._read(f"{self._prefix}board.json", self._default_board())

    def _modify_board(self, mutate):
        return self._modify(f"{self._prefix}board.json", mutate, self._default_board())

    def _doc(self, key: str) -> dict:
        doc = self._read(self._issue_key(key), None)
        if doc is None:
            raise KeyError(f"issue {key} not found")
        return doc

    def _modify_issue(self, key: str, mutate):
        """mutate(doc) runs first; then `updated` is bumped and the change is saved."""
        def wrapped(doc):
            result = mutate(doc)
            doc["updated"] = self._stamp()
            return result
        try:
            return self._modify(self._issue_key(key), wrapped)
        except KeyError:
            raise KeyError(f"issue {key} not found") from None

    def _log(self, doc: dict, **change) -> None:
        """Appends a changelog entry: change maps field -> (from, to)."""
        doc["changelog"].append({
            "id": str(len(doc["changelog"]) + 1), "created": self._stamp(), "author": self.author,
            "items": [{"field": f, "from": a, "to": b} for f, (a, b) in change.items()]})

    def _all_docs(self) -> list[dict]:
        docs = (self._read(self._issue_key(k), None) for k in self._board()["rank"])
        return [d for d in docs if d is not None]

    @staticmethod
    def _to_issue(doc: dict) -> Issue:
        return Issue(
            key=doc["key"], issue_type=doc["issuetype"], status=doc["status"],
            summary=doc["summary"], labels=list(doc["labels"]), parent_key=doc["parent"],
            assignee=doc["assignee"], properties=dict(doc["properties"]),
            description=doc["description"], comments=[c["body"] for c in doc["comments"]],
            attachments=[Attachment(attachment_id=a["id"], title=a["filename"],
                                    media_type=a.get("mimeType", ""),
                                    download_url=f"mockjira:attachments/{a['id']}",
                                    file_size=a.get("size", 0)) for a in doc["attachments"]],
            updated=doc["updated"])

    # -- TicketSystem ------------------------------------------------------
    def create_issue(self, issue_type: str, summary: str, *, parent_key: str | None = None,
                      description: str = "", labels: list[str] | None = None,
                      assignee: str | None = None, component: str | None = None,
                      dry_run: bool = True) -> Issue:
        if issue_type not in self.issue_types:
            raise ValueError(f"issue type {issue_type!r} is not one of {self.issue_types}")
        if dry_run:
            print(f"[dry-run] would create {issue_type} in {self.project_key}: {summary}")
            return Issue(key="(dry-run)", issue_type=issue_type, status=self.statuses[0],
                         summary=summary, labels=labels or [], parent_key=parent_key)
        if parent_key:
            self._check_parent(issue_type, self._doc(parent_key))
        elif issue_type.lower() in _SUBTASK_TYPES:
            raise ValueError("a subtask needs a parent")

        def allocate(board):
            num = board["next_issue_num"]
            board["next_issue_num"] = num + 1
            board["rank"].append(f"{self.project_key}-{num}")
            return num
        num = self._modify_board(allocate)
        stamp = self._stamp()
        doc = {"key": f"{self.project_key}-{num}", "id": str(10000 + num),
               "issuetype": issue_type, "summary": summary, "description": description,
               "status": self.statuses[0], "labels": list(labels or []), "parent": parent_key,
               "assignee": assignee, "reporter": self.author, "priority": "Medium",
               "story_points": None, "components": [component] if component else [],
               "sprint_id": None, "created": stamp, "updated": stamp, "comments": [],
               "attachments": [], "remote_links": [], "issue_links": [], "properties": {},
               "changelog": []}
        self.store.put_bytes_if(self._issue_key(doc["key"]), json.dumps(doc, indent=2).encode(),
                                 None, dry_run=False)
        return self._to_issue(doc)

    @staticmethod
    def _check_parent(issue_type: str, parent: dict) -> None:
        """Jira's hierarchy: an Epic has no parent; a Story/Task/Bug sits under an Epic; a
        Subtask sits under a Story/Task/Bug (never an Epic or another Subtask)."""
        child, above = issue_type.lower(), parent["issuetype"].lower()
        if child == "epic":
            raise ValueError("an epic cannot have a parent")
        if child in _SUBTASK_TYPES:
            if above == "epic" or above in _SUBTASK_TYPES:
                raise ValueError("a subtask's parent must be a story, task or bug")
        elif above != "epic":
            raise ValueError(f"a {issue_type.lower()} can only be placed under an epic")

    def get_issue(self, key: str) -> Issue:
        return self._to_issue(self._doc(key))

    def find_issue_by_label(self, label: str) -> Issue | None:
        found = self.search(f'labels = "{label}"')
        return found[0] if found else None

    def transition_issue(self, key: str, status_name: str, dry_run: bool = True) -> None:
        target = next((s for s in self.statuses if s.lower() == status_name.lower()), None)
        if target is None:
            raise TransitionNotAvailable(f"{key}: no transition to {status_name!r}")
        if dry_run:
            print(f"[dry-run] would transition {key} to {target}")
            return

        def move(doc):
            if doc["status"] != target:
                self._log(doc, status=(doc["status"], target))
                doc["status"] = target
        self._modify_issue(key, move)

    def add_comment(self, key: str, body: str, dry_run: bool = True) -> None:
        if dry_run:
            print(f"[dry-run] would comment on {key}: {body[:80]}")
            return

        def add(doc):
            next_id = max((int(c["id"]) for c in doc["comments"]), default=0) + 1
            doc["comments"].append({"id": str(next_id), "author": self.author, "body": body,
                                    "created": self._stamp()})
        self._modify_issue(key, add)

    def add_label(self, key: str, label: str, dry_run: bool = True) -> None:
        if dry_run:
            print(f"[dry-run] would add label {label!r} to {key}")
            return

        def add(doc):
            if label not in doc["labels"]:
                before = list(doc["labels"])
                doc["labels"].append(label)
                self._log(doc, labels=(before, list(doc["labels"])))
        self._modify_issue(key, add)

    def remove_label(self, key: str, label: str, dry_run: bool = True) -> None:
        if dry_run:
            print(f"[dry-run] would remove label {label!r} from {key}")
            return

        def remove(doc):
            if label in doc["labels"]:
                before = list(doc["labels"])
                doc["labels"].remove(label)
                self._log(doc, labels=(before, list(doc["labels"])))
        self._modify_issue(key, remove)

    def delete_issue(self, key: str, dry_run: bool = True) -> None:
        """Like Jira's deleteSubtasks=true: the issue and its Subtasks go; other children
        (an epic's stories) are detached, not deleted. Missing key: nothing to do."""
        if dry_run:
            print(f"[dry-run] would delete {key}")
            return
        root = self._read(self._issue_key(key), None)
        if root is None:
            return
        children = [d for d in self._all_docs() if d["parent"] == key]
        doomed = [root] + [d for d in children if d["issuetype"].lower() in _SUBTASK_TYPES]
        doomed_keys = {d["key"] for d in doomed}

        self._modify_board(lambda b: b.update(rank=[k for k in b["rank"] if k not in doomed_keys]))
        for doc in doomed:
            for link in doc["issue_links"]:
                if link["key"] not in doomed_keys:
                    self._drop_links(link["key"], doc["key"])
            for att in doc["attachments"]:
                self.store.delete(f"{self._prefix}attachments/{att['id']}", dry_run=False)
            self.store.delete(self._issue_key(doc["key"]), dry_run=False)
        for child in children:
            if child["key"] not in doomed_keys:
                self._modify_issue(child["key"], lambda d: d.update(parent=None))

    def _drop_links(self, key: str, other: str) -> None:
        try:
            self._modify_issue(key, lambda d: d.update(
                issue_links=[lk for lk in d["issue_links"] if lk["key"] != other]))
        except KeyError:
            pass

    def search(self, jql: str) -> list[Issue]:
        jql = re.split(r"\border\s+by\b", jql, flags=re.I)[0]
        clauses = _CLAUSE.findall(jql)
        if re.sub(r"\band\b", "", _CLAUSE.sub("", jql), flags=re.I).strip():
            raise ValueError(f"unsupported JQL: {jql!r}")
        for field, op, _ in clauses:  # reject gaps even when there is nothing to filter
            if not (op == "=" and field.lower() in _FIELDS or (field.lower(), op) == ("updated", ">=")):
                raise ValueError(f"unsupported JQL clause {field} {op} in {jql!r}")
        docs = self._all_docs()
        for field, op, value in clauses:
            docs = [d for d in docs if self._matches(d, field, op, value)]
        return [self._to_issue(d) for d in docs]

    def _matches(self, doc: dict, field: str, op: str, value: str) -> bool:
        field = field.lower()
        if field == "updated":
            since = datetime.strptime(value, _JQL_DATETIME).replace(tzinfo=timezone.utc)
            return datetime.fromisoformat(doc["updated"]) >= since
        if field == "labels":
            return value in doc["labels"]
        if field == "project":
            return doc["key"].rsplit("-", 1)[0] == value
        return doc[_FIELDS[field]] == value

    def link_issues(self, inward_key: str, outward_key: str, link_type: str = "Blocks",
                     dry_run: bool = True) -> None:
        """Stored on both issues, from each one's point of view (as Jira's issuelinks are)."""
        if dry_run:
            print(f"[dry-run] would link {outward_key} -> {inward_key} ({link_type})")
            return
        for key, other, direction in ((inward_key, outward_key, "inward"),
                                      (outward_key, inward_key, "outward")):
            self._modify_issue(key, lambda d, o=other, di=direction: d["issue_links"].append(
                {"type": link_type, "direction": di, "key": o}))

    def add_remote_link(self, key: str, url: str, title: str, dry_run: bool = True) -> None:
        if dry_run:
            print(f"[dry-run] would add web link {url} to {key}")
            return

        def add(doc):
            next_id = max((int(r["id"]) for r in doc["remote_links"]), default=0) + 1
            doc["remote_links"].append({"id": str(next_id), "url": url, "title": title})
        self._modify_issue(key, add)

    def get_remote_links(self, key: str) -> list[tuple[str, str]]:
        return [(r["url"], r["title"]) for r in self._doc(key)["remote_links"]]

    def get_text_links(self, key: str) -> list[str]:
        doc = self._doc(key)
        bodies = [doc["description"]] + [c["body"] for c in doc["comments"]]
        return [m.rstrip(".,;:") for body in bodies for m in _URL_RE.findall(body)]

    def get_or_create_future_sprint(self, dry_run: bool = True) -> str:
        future = next((s for s in self._board()["sprints"] if s["state"] == "future"), None)
        if future:
            return str(future["id"])
        if dry_run:
            print(f"[dry-run] would create a future sprint in {self.project_key}")
            return "(dry-run)"

        return str(self._modify_board(self._new_sprint)["id"])

    def _new_sprint(self, board: dict, name: str | None = None, goal: str = "") -> dict:
        sprint_id = board["next_sprint_id"]
        board["next_sprint_id"] = sprint_id + 1
        sprint = {"id": sprint_id, "name": name or f"{self.project_key} Sprint {sprint_id}",
                  "state": "future", "goal": goal, "start_date": None, "end_date": None,
                  "complete_date": None}
        board["sprints"].append(sprint)
        return dict(sprint)

    def add_issues_to_sprint(self, sprint_id: str, keys: list[str], dry_run: bool = True) -> None:
        if dry_run:
            print(f"[dry-run] would add {', '.join(keys)} to sprint {sprint_id}")
            return
        for key in keys:
            self._modify_issue(key, lambda d: d.update(sprint_id=int(sprint_id)))

    def has_artifact(self, key: str) -> bool:
        doc = self._doc(key)
        return bool(doc["attachments"] or doc["remote_links"])

    def status_changes_since(self, key: str, since: str) -> list[StatusEvent]:
        since_at = datetime.fromisoformat(since)
        return [StatusEvent(issue_key=key, from_status=item["from"], to_status=item["to"])
                for entry in self._doc(key)["changelog"]
                if datetime.fromisoformat(entry["created"]) >= since_at
                for item in entry["items"] if item["field"] == "status"]

    def set_property(self, key: str, property_key: str, value: dict, dry_run: bool = True) -> None:
        if dry_run:
            print(f"[dry-run] would set property {property_key!r} on {key}")
            return
        self._modify_issue(key, lambda d: d["properties"].__setitem__(property_key, value))

    def get_attachments(self, key: str) -> list[Attachment]:
        return self.get_issue(key).attachments

    def download_attachment(self, attachment: Attachment) -> bytes:
        data = self.store.get_bytes(f"{self._prefix}attachments/{attachment.attachment_id}")
        if data is None:
            raise KeyError(f"attachment {attachment.attachment_id} not found")
        return data

    def add_attachment(self, key: str, filename: str, data: bytes,
                        media_type: str = "application/octet-stream",
                        dry_run: bool = True) -> Attachment:
        """Not on the port: real Jira attachments are uploaded by people, and in the mock the
        UI (and tests) do that."""
        att_id = uuid.uuid4().hex
        if dry_run:
            print(f"[dry-run] would attach {filename} to {key}")
            return Attachment(att_id, filename, media_type, f"mockjira:attachments/{att_id}", len(data))
        self._doc(key)  # fail before writing bytes for a missing issue
        self.store.put_bytes(f"{self._prefix}attachments/{att_id}", data, dry_run=False)
        self._modify_issue(key, lambda d: d["attachments"].append({
            "id": att_id, "filename": filename, "mimeType": media_type, "size": len(data),
            "author": self.author, "created": self._stamp()}))
        return Attachment(att_id, filename, media_type, f"mockjira:attachments/{att_id}", len(data))

    # -- Board operations for the UI (not on the TicketSystem port) -----------
    # ValueError = the action breaks a Jira rule (the API answers 400/409);
    # KeyError = no such issue/sprint (404).
    def acting_as(self, author: str) -> "MockJiraClient":
        """The same project, with `author` on the comments/changelog it writes (the UI's user)."""
        return MockJiraClient(self.store, self.project_key, self.project_name, self.issue_types,
                               self.statuses, author=author, now=self._now)

    def update_fields(self, key: str, **fields) -> None:
        unknown = set(fields) - _EDITABLE
        if unknown:
            raise ValueError(f"cannot edit {sorted(unknown)}")
        if "priority" in fields and fields["priority"] not in PRIORITIES:
            raise ValueError(f"priority must be one of {PRIORITIES}")
        if "parent" in fields:
            doc = self._doc(key)
            if fields["parent"] == key:
                raise ValueError("an issue cannot be its own parent")
            if fields["parent"]:
                self._check_parent(doc["issuetype"], self._doc(fields["parent"]))
            elif doc["issuetype"].lower() in _SUBTASK_TYPES:
                raise ValueError("a subtask needs a parent")

        def apply(doc):
            change = {f: (doc[f], v) for f, v in fields.items() if doc[f] != v}
            doc.update({f: v for f, (_, v) in change.items()})
            if change:
                self._log(doc, **change)
        self._modify_issue(key, apply)

    def delete_comment(self, key: str, comment_id: str) -> None:
        self._modify_issue(key, lambda d: d.update(
            comments=[c for c in d["comments"] if c["id"] != comment_id]))

    def delete_remote_link(self, key: str, link_id: str) -> None:
        self._modify_issue(key, lambda d: d.update(
            remote_links=[r for r in d["remote_links"] if r["id"] != link_id]))

    def delete_attachment(self, key: str, attachment_id: str) -> None:
        self._modify_issue(key, lambda d: d.update(
            attachments=[a for a in d["attachments"] if a["id"] != attachment_id]))
        self.store.delete(f"{self._prefix}attachments/{attachment_id}", dry_run=False)

    def unlink_issues(self, key: str, other_key: str) -> None:
        self._drop_links(key, other_key)
        self._drop_links(other_key, key)

    def _sprint_in(self, board: dict, sprint_id: int) -> dict:
        found = next((s for s in board["sprints"] if s["id"] == sprint_id), None)
        if found is None:
            raise KeyError(f"sprint {sprint_id} not found")
        return found

    def create_sprint(self, name: str | None = None, goal: str = "") -> dict:
        return self._modify_board(lambda b: self._new_sprint(b, name, goal))

    def update_sprint(self, sprint_id: int, **fields) -> dict:
        unknown = set(fields) - _SPRINT_EDITABLE
        if unknown:
            raise ValueError(f"cannot edit {sorted(unknown)}")

        def apply(board):
            sprint = self._sprint_in(board, sprint_id)
            if sprint["state"] == "closed":
                raise ValueError("a completed sprint cannot be edited")
            sprint.update(fields)
            return dict(sprint)
        return self._modify_board(apply)

    def start_sprint(self, sprint_id: int, start_date: str | None = None,
                      end_date: str | None = None) -> dict:
        def start(board):
            sprint = self._sprint_in(board, sprint_id)
            if sprint["state"] != "future":
                raise ValueError("only a future sprint can be started")
            if any(x["state"] == "active" for x in board["sprints"]):
                raise ValueError("another sprint is already active")
            sprint.update(state="active", start_date=start_date or self._stamp(),
                          end_date=end_date or (self._now() + timedelta(days=14)).isoformat())
            return dict(sprint)
        return self._modify_board(start)

    def complete_sprint(self, sprint_id: int, move_to: int | None = None) -> dict:
        """Closes the active sprint. Issues not in the last status go to sprint `move_to`
        (a future one) or back to the backlog; finished ones stay with the closed sprint."""
        board = self._board()
        sprint = self._sprint_in(board, sprint_id)
        if sprint["state"] != "active":
            raise ValueError("only the active sprint can be completed")
        if move_to is not None and self._sprint_in(board, move_to)["state"] != "future":
            raise ValueError("unfinished issues can only move to a future sprint")
        moved = []
        for doc in self._all_docs():
            if doc["sprint_id"] == sprint_id and doc["status"] != self.statuses[-1]:
                self._set_sprint(doc["key"], move_to)
                moved.append(doc["key"])

        def close(b):
            self._sprint_in(b, sprint_id).update(state="closed", complete_date=self._stamp())
        self._modify_board(close)
        return {"moved": moved, "move_to": move_to}

    def delete_sprint(self, sprint_id: int) -> None:
        """Future sprints only; their issues go back to the backlog."""
        if self._sprint_in(self._board(), sprint_id)["state"] != "future":
            raise ValueError("only a future sprint can be deleted")
        for doc in self._all_docs():
            if doc["sprint_id"] == sprint_id:
                self._set_sprint(doc["key"], None)
        self._modify_board(lambda b: b.update(
            sprints=[s for s in b["sprints"] if s["id"] != sprint_id]))

    def _set_sprint(self, key: str, sprint_id: int | None) -> None:
        def move(doc):
            if doc["sprint_id"] != sprint_id:
                self._log(doc, sprint=(doc["sprint_id"], sprint_id))
                doc["sprint_id"] = sprint_id
        self._modify_issue(key, move)

    def rank(self, keys: list[str], sprint_id: int | None, before_key: str | None = None) -> None:
        """Moves `keys` (with their Subtasks) into a sprint (None = backlog) and puts them
        before `before_key` in the global order (the end if it is None or not found)."""
        board = self._board()
        if sprint_id is not None and self._sprint_in(board, sprint_id)["state"] == "closed":
            raise ValueError("issues cannot be moved into a completed sprint")
        for key in keys:
            self._doc(key)
        subtasks = [d["key"] for d in self._all_docs()
                    if d["parent"] in keys and d["issuetype"].lower() in _SUBTASK_TYPES]
        for key in keys + [k for k in subtasks if k not in keys]:
            self._set_sprint(key, sprint_id)

        def reorder(b):
            rest = [k for k in b["rank"] if k not in keys]
            at = rest.index(before_key) if before_key in rest else len(rest)
            b["rank"] = rest[:at] + [k for k in keys if k in b["rank"]] + rest[at:]
        self._modify_board(reorder)

    @staticmethod
    def _summary(doc: dict) -> dict:
        return {f: doc[f] for f in ("key", "summary", "issuetype", "status", "priority", "assignee",
                                    "story_points", "labels", "parent", "sprint_id", "components",
                                    "updated")}

    def board_view(self) -> dict:
        """Everything the backlog/board pages draw: sprints plus every issue, in rank order."""
        board = self._board()
        return {"project": board["project"], "statuses": self.statuses,
                "issue_types": self.issue_types, "priorities": PRIORITIES,
                "sprints": board["sprints"], "issues": [self._summary(d) for d in self._all_docs()]}

    def issue_view(self, key: str) -> dict:
        """One issue in full, with its parent, children, links and sprint resolved."""
        doc = self._doc(key)
        by_key = {d["key"]: d for d in self._all_docs()}

        def ref(k):
            d = by_key.get(k)
            return ({f: d[f] for f in ("key", "summary", "issuetype", "status")} if d
                    else {"key": k, "summary": "(deleted)", "issuetype": "", "status": ""})
        sprint = next((x for x in self._board()["sprints"] if x["id"] == doc["sprint_id"]), None)
        return {**doc, "parent_issue": ref(doc["parent"]) if doc["parent"] else None,
                "children": [ref(k) for k, d in by_key.items() if d["parent"] == key],
                "links": [{**lk, "issue": ref(lk["key"])} for lk in doc["issue_links"]],
                "sprint": sprint}
