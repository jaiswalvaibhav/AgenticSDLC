from pathlib import Path

from sdlc.adapters.fake import FakeObjectStore, FakeTicketSystem
from sdlc.jira_sync import TRACE_KEY, sync_issues
from sdlc.ports import Attachment

BASE_URL = "https://x.atlassian.net"


def _seed_tree(tickets: FakeTicketSystem):
    tickets.add_issue("DEMO-1", "Epic", "Delivery", updated="2026-01-01T00:00:00+0000")
    tickets.add_issue("DEMO-2", "Story", "Ingest Orders", parent_key="DEMO-1",
                       description="Do the thing.", updated="2026-01-01T00:00:00+0000")
    tickets.add_issue("DEMO-3", "Sub-task", "[Engineering] Ingest Orders", parent_key="DEMO-2",
                       updated="2026-01-01T00:00:00+0000")
    tickets.attachments["DEMO-2"] = [
        Attachment(attachment_id="a1", title="diagram.png", media_type="image/png",
                   download_url="fake://a1", file_size=100),
    ]
    tickets.attachment_bytes["a1"] = b"PNG-BYTES"


def test_first_sync_writes_epic_story_subtask_hierarchy(tmp_path: Path):
    tickets = FakeTicketSystem()
    _seed_tree(tickets)
    store = FakeObjectStore()

    result = sync_issues(tickets, store, epic_key="DEMO-1", base_url=BASE_URL,
                          data_dir=tmp_path, dry_run=False)

    assert set(result.fetched) == {"DEMO-1", "DEMO-2", "DEMO-3"}
    story_dir = tmp_path / "corpus_jira" / "DEMO-1" / "DEMO-2"
    subtask_dir = story_dir / "DEMO-3"
    assert (story_dir / "issue.md").exists()
    assert (subtask_dir / "issue.md").exists()
    assert (story_dir / "diagram.png").read_bytes() == b"PNG-BYTES"
    assert "Do the thing." in (story_dir / "issue.md").read_text()
    assert f"{BASE_URL}/browse/DEMO-2" in (story_dir / "issue.md").read_text()


def test_second_sync_with_no_changes_is_unchanged(tmp_path: Path):
    tickets = FakeTicketSystem()
    _seed_tree(tickets)
    store = FakeObjectStore()
    sync_issues(tickets, store, epic_key="DEMO-1", base_url=BASE_URL, data_dir=tmp_path, dry_run=False)

    result = sync_issues(tickets, store, epic_key="DEMO-1", base_url=BASE_URL, data_dir=tmp_path, dry_run=False)

    assert result.fetched == []
    assert set(result.unchanged) == {"DEMO-1", "DEMO-2", "DEMO-3"}


def test_updated_timestamp_change_triggers_refetch(tmp_path: Path):
    tickets = FakeTicketSystem()
    _seed_tree(tickets)
    store = FakeObjectStore()
    sync_issues(tickets, store, epic_key="DEMO-1", base_url=BASE_URL, data_dir=tmp_path, dry_run=False)

    tickets.issues["DEMO-2"].updated = "2026-02-01T00:00:00+0000"
    tickets.issues["DEMO-2"].description = "Updated body."
    result = sync_issues(tickets, store, epic_key="DEMO-1", base_url=BASE_URL, data_dir=tmp_path, dry_run=False)

    assert result.fetched == ["DEMO-2"]
    story_dir = tmp_path / "corpus_jira" / "DEMO-1" / "DEMO-2"
    assert "Updated body." in (story_dir / "issue.md").read_text()


def test_failed_fetch_is_retried_next_run(tmp_path: Path):
    tickets = FakeTicketSystem()
    _seed_tree(tickets)
    store = FakeObjectStore()

    # Simulate a transient failure (e.g. a Jira API error) on one issue, then recover.
    real_get_issue = tickets.get_issue
    calls = {"n": 0}

    def flaky_get_issue(key):
        if key == "DEMO-2" and calls["n"] == 0:
            calls["n"] += 1
            raise RuntimeError("transient Jira API error")
        return real_get_issue(key)

    tickets.get_issue = flaky_get_issue

    result = sync_issues(tickets, store, epic_key="DEMO-1", base_url=BASE_URL, data_dir=tmp_path, dry_run=False)
    assert result.failed == ["DEMO-2"]
    assert store.get_json(TRACE_KEY)["DEMO-2"]["status"] == "failed"

    result = sync_issues(tickets, store, epic_key="DEMO-1", base_url=BASE_URL, data_dir=tmp_path, dry_run=False)
    assert "DEMO-2" in result.fetched
    assert store.get_json(TRACE_KEY)["DEMO-2"]["status"] == "success"


def test_unsupported_attachment_type_is_skipped_with_a_note(tmp_path: Path):
    tickets = FakeTicketSystem()
    _seed_tree(tickets)
    tickets.attachments["DEMO-2"].append(
        Attachment(attachment_id="a2", title="archive.zip", media_type="application/zip",
                   download_url="fake://a2", file_size=10))
    tickets.attachment_bytes["a2"] = b"ZIP-BYTES"
    store = FakeObjectStore()

    sync_issues(tickets, store, epic_key="DEMO-1", base_url=BASE_URL, data_dir=tmp_path, dry_run=False)

    story_dir = tmp_path / "corpus_jira" / "DEMO-1" / "DEMO-2"
    assert not (story_dir / "archive.zip").exists()
    assert "archive.zip" in (story_dir / "issue.md").read_text()
    import json
    meta = json.loads((story_dir / "meta.json").read_text())
    zip_entry = next(a for a in meta["attachments"] if a["filename"] == "archive.zip")
    assert zip_entry["status"] == "skipped_unsupported_type"


def test_oversized_image_attachment_is_skipped_with_a_note(tmp_path: Path):
    tickets = FakeTicketSystem()
    _seed_tree(tickets)
    tickets.attachments["DEMO-2"][0].file_size = 10 * 1024 * 1024  # over the 3.75MB image limit
    store = FakeObjectStore()

    sync_issues(tickets, store, epic_key="DEMO-1", base_url=BASE_URL, data_dir=tmp_path, dry_run=False)

    story_dir = tmp_path / "corpus_jira" / "DEMO-1" / "DEMO-2"
    assert not (story_dir / "diagram.png").exists()
    assert "too large" in (story_dir / "issue.md").read_text() or \
        "exceeds" in (story_dir / "issue.md").read_text()


def test_dry_run_writes_nothing(tmp_path: Path):
    tickets = FakeTicketSystem()
    _seed_tree(tickets)
    store = FakeObjectStore()

    sync_issues(tickets, store, epic_key="DEMO-1", base_url=BASE_URL, data_dir=tmp_path, dry_run=True)

    assert not (tmp_path / "corpus_jira").exists()
    assert store.get_json(TRACE_KEY) is None
