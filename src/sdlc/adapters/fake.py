"""In-memory fakes for every port, for tests (no AWS/Jira/Confluence network needed)."""
from sdlc.ports import Attachment, Chunk, Issue, Page, StatusEvent


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

    def create_page(self, parent_id: str, title: str, body_html: str, dry_run: bool = True) -> Page:
        if dry_run:
            return Page(page_id="(dry-run)", space_key=self.space_key, title=title,
                        url="", version=1, parent_path=parent_id, html=body_html)
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
        if dry_run:
            return
        issue = self.issues[key]
        self.events.append(StatusEvent(key, issue.status, status_name))
        issue.status = status_name

    def add_comment(self, key: str, body: str, dry_run: bool = True) -> None:
        pass

    def add_label(self, key: str, label: str, dry_run: bool = True) -> None:
        if not dry_run:
            self.issues[key].labels.append(label)

    def search(self, jql: str) -> list[Issue]:
        return list(self.issues.values())


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
