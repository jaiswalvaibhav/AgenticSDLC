"""In-memory fakes for every port, for tests (no AWS/Jira/Confluence network needed)."""
import re

from sdlc.ports import Attachment, Chunk, Issue, Page, StatusEvent, TransitionNotAvailable


class FakeDocumentSource:
    """Tracks a real parent/child tree, so get_descendants and ancestor paths behave
    like Confluence: parent_path is the ancestor titles joined with '/', root-first."""

    def __init__(self, space_key: str = "FAKE"):
        self.space_key = space_key
        self.pages: dict[str, Page] = {}
        self.children: dict[str, list[str]] = {}  # page_id -> [child page_id, ...]
        self.attachments: dict[str, list[Attachment]] = {}
        self.attachment_bytes: dict[str, bytes] = {}
        self._next_id = 1

    def _new_id(self) -> str:
        page_id = str(self._next_id)
        self._next_id += 1
        return page_id

    def add_page(self, title: str, *, parent_id: str | None = None, html: str = "",
                 page_id: str | None = None, version: int = 1) -> Page:
        """Test helper: seed a page directly (bypassing create_page's dry-run default)."""
        page_id = page_id or self._new_id()
        parent_titles = []
        node = parent_id
        while node:
            parent_titles.insert(0, self.pages[node].title)
            node = self._parent_of(node)
        page = Page(page_id=page_id, space_key=self.space_key, title=title,
                    url=f"fake://{page_id}", version=version,
                    parent_path="/".join([self.space_key, *parent_titles]), html=html)
        self.pages[page_id] = page
        self.children.setdefault(parent_id or "", []).append(page_id)
        return page

    def _parent_of(self, page_id: str) -> str | None:
        for parent, kids in self.children.items():
            if page_id in kids and parent:
                return parent
        return None

    def get_page(self, page_id: str) -> Page:
        return self.pages[page_id]

    def find_page_by_title(self, title: str) -> Page | None:
        return next((p for p in self.pages.values() if p.title == title), None)

    def get_descendants(self, root_page_id: str) -> list[Page]:
        ids, stack = [], [root_page_id]
        while stack:
            current = stack.pop()
            ids.append(current)
            stack.extend(self.children.get(current, []))
        return [self.pages[i] for i in ids]

    def get_attachments(self, page_id: str) -> list[Attachment]:
        return self.attachments.get(page_id, [])

    def download_attachment(self, attachment: Attachment) -> bytes:
        return self.attachment_bytes.get(attachment.attachment_id, b"")

    def add_attachment(self, page_id: str, filename: str, data: bytes,
                        media_type: str = "image/png", dry_run: bool = True) -> Attachment:
        attachment = Attachment(attachment_id=f"att-{len(self.attachment_bytes) + 1}",
                                 title=filename, media_type=media_type, download_url=f"fake://{filename}")
        if not dry_run:
            self.attachments.setdefault(page_id, []).append(attachment)
            self.attachment_bytes[attachment.attachment_id] = data
        return attachment

    def create_page(self, parent_id: str | None, title: str, body_html: str,
                     dry_run: bool = True) -> Page:
        if dry_run:
            return Page(page_id="(dry-run)", space_key=self.space_key, title=title,
                        url="", version=1, parent_path=parent_id or "", html=body_html)
        return self.add_page(title, parent_id=parent_id, html=body_html)

    def append_to_page(self, page_id: str, body_html: str, dry_run: bool = True) -> Page:
        page = self.pages[page_id]
        if not dry_run:
            page.html += body_html
            page.version += 1
        return page


class FakeTicketSystem:
    def __init__(self):
        self.issues: dict[str, Issue] = {}
        self._next_num = 1
        self.events: list[StatusEvent] = []
        self.comments: dict[str, list[str]] = {}
        self.links: list[tuple[str, str, str]] = []  # (inward, outward, type)
        self.remote_links: dict[str, list[tuple[str, str]]] = {}  # key -> [(url, title)]
        self.artifacts: set[str] = set()  # issue keys treated as having an artifact
        self.blocked_transitions: set[tuple[str, str, str]] = set()  # (key, from_status, to_status)
        self.properties: dict[str, dict] = {}  # key -> {property_key: value}

    def create_issue(self, issue_type, summary, *, parent_key=None, description="",
                      labels=None, assignee=None, component=None, dry_run=True) -> Issue:
        key = f"FAKE-{self._next_num}"
        issue = Issue(key=key, issue_type=issue_type, status="To Do", summary=summary,
                      labels=labels or [], parent_key=parent_key, assignee=assignee)
        if not dry_run:
            self._next_num += 1
            self.issues[key] = issue
        return issue

    def get_issue(self, key: str) -> Issue:
        return self.issues[key]

    def find_issue_by_label(self, label: str) -> Issue | None:
        return next((i for i in self.issues.values() if label in i.labels), None)

    def transition_issue(self, key: str, status_name: str, dry_run: bool = True) -> None:
        issue = self.issues[key]
        if (key, issue.status, status_name) in self.blocked_transitions:
            raise TransitionNotAvailable(f"{key}: {issue.status} -> {status_name} is blocked")
        if dry_run:
            return
        self.events.append(StatusEvent(key, issue.status, status_name))
        issue.status = status_name

    def add_comment(self, key: str, body: str, dry_run: bool = True) -> None:
        if not dry_run:
            self.comments.setdefault(key, []).append(body)

    def add_label(self, key: str, label: str, dry_run: bool = True) -> None:
        if not dry_run:
            self.issues[key].labels.append(label)

    def search(self, jql: str) -> list[Issue]:
        """Minimal JQL matcher covering only the clauses this project's own code emits
        (parent =, labels =) — not a general JQL parser."""
        issues = list(self.issues.values())
        if (m := re.search(r'parent\s*=\s*"([^"]+)"', jql)):
            issues = [i for i in issues if i.parent_key == m.group(1)]
        if (m := re.search(r'labels\s*=\s*"([^"]+)"', jql)):
            issues = [i for i in issues if m.group(1) in i.labels]
        return issues

    def link_issues(self, inward_key: str, outward_key: str, link_type: str = "Blocks",
                     dry_run: bool = True) -> None:
        if not dry_run:
            self.links.append((inward_key, outward_key, link_type))

    def add_remote_link(self, key: str, url: str, title: str, dry_run: bool = True) -> None:
        if not dry_run:
            self.remote_links.setdefault(key, []).append((url, title))

    def has_artifact(self, key: str) -> bool:
        return key in self.artifacts

    def status_changes_since(self, key: str, since: str) -> list[StatusEvent]:
        return [e for e in self.events if e.issue_key == key]

    def set_property(self, key: str, property_key: str, value: dict, dry_run: bool = True) -> None:
        if not dry_run:
            self.properties.setdefault(key, {})[property_key] = value


class FakeObjectStore:
    def __init__(self):
        self._data: dict[str, bytes] = {}

    def get_json(self, key: str) -> dict | None:
        import json
        data = self._data.get(key)
        return json.loads(data) if data is not None else None

    def put_json(self, key: str, value: dict, dry_run: bool = True) -> None:
        import json
        self.put_bytes(key, json.dumps(value).encode(), dry_run=dry_run)

    def get_bytes(self, key: str) -> bytes | None:
        return self._data.get(key)

    def put_bytes(self, key: str, data: bytes, dry_run: bool = True) -> None:
        if not dry_run:
            self._data[key] = data

    def delete(self, key: str, dry_run: bool = True) -> None:
        if not dry_run:
            self._data.pop(key, None)


class FakeKnowledgeIndex:
    """Returns canned chunks, so analyst-task tests need no AWS."""

    def __init__(self, canned: list[Chunk] | None = None):
        self.canned = canned or []

    def search(self, query: str, use_case: str, top_k: int = 10) -> list[Chunk]:
        return [c for c in self.canned if c.metadata.get("use_case", use_case) == use_case][:top_k]


class FakeLLM:
    def __init__(self, canned_response: dict | None = None):
        self.canned_response = canned_response or {"content": "fake response"}

    def complete(self, system: str, messages: list[dict], tools: list[dict] | None = None) -> dict:
        return self.canned_response


class FakeAgentRuntime:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def run(self, task_id: str, context: dict) -> dict:
        self.calls.append((task_id, context))
        return {"status": "ok", "task_id": task_id}
