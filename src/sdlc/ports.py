"""Ports: the interfaces adapters implement. See CLAUDE.md for the local/aws split."""
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class Page:
    page_id: str
    space_key: str
    title: str
    url: str
    version: int
    parent_path: str
    html: str = ""
    attachments: list[str] = field(default_factory=list)  # local paths to images/files


@dataclass
class Attachment:
    attachment_id: str
    title: str
    media_type: str
    download_url: str
    file_size: int = 0


@dataclass
class Issue:
    key: str
    issue_type: str  # Epic | Story | Sub-task
    status: str
    summary: str
    labels: list[str] = field(default_factory=list)
    parent_key: str | None = None
    assignee: str | None = None
    properties: dict = field(default_factory=dict)


@dataclass
class StatusEvent:
    issue_key: str
    from_status: str
    to_status: str


@dataclass
class Chunk:
    text: str
    score: float
    page_title: str
    page_url: str
    s3_uri: str
    metadata: dict = field(default_factory=dict)


class DocumentSource(Protocol):
    """Confluence (or any page-tree document source)."""

    def get_page(self, page_id: str) -> Page: ...
    def get_descendants(self, root_page_id: str) -> list[Page]: ...
    def find_page_by_title(self, title: str) -> Page | None: ...
    def get_attachments(self, page_id: str) -> list[Attachment]: ...
    def download_attachment(self, attachment: Attachment) -> bytes: ...
    def add_attachment(self, page_id: str, filename: str, data: bytes,
                        media_type: str = "image/png", dry_run: bool = True) -> Attachment: ...
    def create_page(self, parent_id: str | None, title: str, body_html: str,
                     dry_run: bool = True) -> Page: ...
    def append_to_page(self, page_id: str, body_html: str, dry_run: bool = True) -> Page: ...


class TransitionNotAvailable(Exception):
    """Raised by transition_issue when status_name isn't among the issue's currently
    available transitions — either it doesn't exist, or the workflow blocks it from the
    issue's current status. Callers use this to fall back to a comment-only action
    (e.g. the reopen-on-child-reopen rule, when the workflow forbids Done -> In Progress)."""


class TicketSystem(Protocol):
    """Jira (or any ticket system)."""

    def create_issue(self, issue_type: str, summary: str, *, parent_key: str | None = None,
                      description: str = "", labels: list[str] | None = None,
                      assignee: str | None = None, component: str | None = None,
                      dry_run: bool = True) -> Issue: ...
    def get_issue(self, key: str) -> Issue: ...
    def find_issue_by_label(self, label: str) -> Issue | None: ...
    def transition_issue(self, key: str, status_name: str, dry_run: bool = True) -> None: ...
    def add_comment(self, key: str, body: str, dry_run: bool = True) -> None: ...
    def add_label(self, key: str, label: str, dry_run: bool = True) -> None: ...
    def search(self, jql: str) -> list[Issue]: ...
    def link_issues(self, inward_key: str, outward_key: str, link_type: str = "Blocks",
                     dry_run: bool = True) -> None: ...
    def add_remote_link(self, key: str, url: str, title: str, dry_run: bool = True) -> None: ...
    def has_artifact(self, key: str) -> bool: ...
    def status_changes_since(self, key: str, since: str) -> list[StatusEvent]: ...


class StatusSource(Protocol):
    """Produces StatusEvents. Polling now; a WebhookStatusSource stub for later."""

    def poll(self) -> list[StatusEvent]: ...


class ObjectStore(Protocol):
    """State storage: local folder (profile=local) or S3 (profile=aws)."""

    def get_json(self, key: str) -> dict | None: ...
    def put_json(self, key: str, value: dict, dry_run: bool = True) -> None: ...
    def put_bytes(self, key: str, data: bytes, dry_run: bool = True) -> None: ...
    def get_bytes(self, key: str) -> bytes | None: ...
    def delete(self, key: str, dry_run: bool = True) -> None: ...


class KnowledgeIndex(Protocol):
    """Bedrock Managed Knowledge Base. Queries are always scoped by use_case."""

    def search(self, query: str, use_case: str, top_k: int = 10) -> list[Chunk]: ...


class LLM(Protocol):
    """Claude on Bedrock."""

    def complete(self, system: str, messages: list[dict], tools: list[dict] | None = None) -> dict: ...


class AgentRuntime(Protocol):
    """Where the analyst agent executes: a plain Python runner, or AgentCore."""

    def run(self, task_id: str, context: dict) -> dict: ...
