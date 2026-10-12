"""MockJiraClient against the TicketSystem port semantics (no Jira, no network)."""
from datetime import datetime, timedelta, timezone

import pytest

from sdlc.adapters.fake import FakeObjectStore
from sdlc.adapters.local_store import LocalObjectStore
from sdlc.adapters.mock_jira import MockJiraClient
from sdlc.ports import TransitionNotAvailable, VersionConflict

T0 = datetime(2026, 10, 12, 5, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        self.t += timedelta(seconds=1)
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def jira(tmp_path, clock):
    return MockJiraClient(LocalObjectStore(tmp_path), project_key="MOCK", now=clock)


def make(jira, issue_type="Story", summary="s", **kw):
    return jira.create_issue(issue_type, summary, dry_run=False, **kw)


def test_create_assigns_sequential_keys_and_starts_in_first_status(jira):
    a, b = make(jira, "Epic"), make(jira)
    assert (a.key, b.key) == ("MOCK-1", "MOCK-2")
    assert a.status == "To Do" and jira.get_issue("MOCK-2").issue_type == "Story"


def test_create_rejects_unknown_issue_type(jira):
    with pytest.raises(ValueError):
        make(jira, "Sub-task")


def test_dry_run_writes_nothing(jira):
    stub = jira.create_issue("Story", "s")
    assert stub.key == "(dry-run)"
    assert jira.search('project = "MOCK"') == []
    key = make(jira).key
    jira.add_comment(key, "x")
    jira.add_label(key, "l")
    jira.delete_issue(key)
    issue = jira.get_issue(key)
    assert issue.comments == [] and issue.labels == []


def test_get_issue_missing_raises_keyerror(jira):
    with pytest.raises(KeyError):
        jira.get_issue("MOCK-99")


def test_transition_records_changelog_and_rejects_unknown_status(jira):
    key = make(jira).key
    jira.transition_issue(key, "in progress", dry_run=False)
    assert jira.get_issue(key).status == "In Progress"
    with pytest.raises(TransitionNotAvailable):
        jira.transition_issue(key, "Nope", dry_run=False)
    with pytest.raises(TransitionNotAvailable):  # even in dry-run, like the real client
        jira.transition_issue(key, "Nope")


def test_status_changes_since_compares_datetimes(jira, clock):
    key = make(jira).key
    jira.transition_issue(key, "In Progress", dry_run=False)
    cut_at = clock.t
    cut = cut_at.isoformat()
    jira.transition_issue(key, "Done", dry_run=False)
    events = jira.status_changes_since(key, cut)
    assert [(e.from_status, e.to_status) for e in events] == [("In Progress", "Done")]
    # the same instant written with another UTC offset still bounds the window correctly
    ist = cut_at.astimezone(timezone(timedelta(hours=5, minutes=30))).isoformat()
    assert len(jira.status_changes_since(key, ist)) == 1
    assert len(jira.status_changes_since(key, T0.isoformat())) == 2


def test_labels_comments_properties(jira):
    key = make(jira, labels=["a"]).key
    jira.add_label(key, "b", dry_run=False)
    jira.add_label(key, "b", dry_run=False)
    jira.remove_label(key, "a", dry_run=False)
    jira.add_comment(key, "one", dry_run=False)
    jira.add_comment(key, "two", dry_run=False)
    jira.set_property(key, "sdlc.trace", {"x": 1}, dry_run=False)
    issue = jira.get_issue(key)
    assert issue.labels == ["b"] and issue.comments == ["one", "two"]
    assert issue.properties == {"sdlc.trace": {"x": 1}}


def test_search_jql_subset(jira, clock):
    epic = make(jira, "Epic", labels=["uc:demo"])
    story = make(jira, parent_key=epic.key, labels=["uc:demo", "step:x"])
    sub = make(jira, "Subtask", parent_key=story.key)
    keys = lambda jql: sorted(i.key for i in jira.search(jql))  # noqa: E731
    assert keys('labels = "uc:demo"') == [epic.key, story.key]
    assert keys(f'project = "MOCK" AND parent = "{story.key}"') == [sub.key]
    assert keys('project = "OTHER"') == []
    assert keys('status = "To Do" AND issuetype = "Epic" ORDER BY created DESC') == [epic.key]
    assert jira.find_issue_by_label("step:x").key == story.key
    assert jira.find_issue_by_label("missing") is None
    jira.transition_issue(story.key, "Done", dry_run=False)
    since = (clock.t - timedelta(seconds=1)).strftime("%Y/%m/%d %H:%M")
    assert story.key in keys(f'project = "MOCK" AND updated >= "{since}"')
    late = (clock.t + timedelta(minutes=5)).strftime("%Y/%m/%d %H:%M")
    assert keys(f'updated >= "{late}"') == []


@pytest.mark.parametrize("jql", ['summary ~ "x"', 'labels in ("a")', 'project = "MOCK" OR labels = "a"',
                                  'assignee = "x"'])
def test_search_unsupported_jql_raises(jira, jql):
    with pytest.raises(ValueError):
        jira.search(jql)


def test_links_are_stored_on_both_issues_and_removed_on_delete(jira):
    a, b = make(jira), make(jira)
    jira.link_issues(a.key, b.key, "Blocks", dry_run=False)
    assert jira._doc(a.key)["issue_links"] == [{"type": "Blocks", "direction": "inward", "key": b.key}]
    assert jira._doc(b.key)["issue_links"] == [{"type": "Blocks", "direction": "outward", "key": a.key}]
    jira.delete_issue(a.key, dry_run=False)
    assert jira._doc(b.key)["issue_links"] == []


def test_remote_links_text_links_and_artifact(jira):
    key = make(jira, description="See https://x.atlassian.net/wiki/spaces/S/pages/1/T.").key
    assert not jira.has_artifact(key)
    jira.add_comment(key, "also https://x.atlassian.net/wiki/spaces/S/pages/2/U, thanks", dry_run=False)
    assert jira.get_text_links(key) == ["https://x.atlassian.net/wiki/spaces/S/pages/1/T",
                                        "https://x.atlassian.net/wiki/spaces/S/pages/2/U"]
    jira.add_remote_link(key, "https://x/y", "Plan", dry_run=False)
    assert jira.get_remote_links(key) == [("https://x/y", "Plan")] and jira.has_artifact(key)


def test_attachment_roundtrip_counts_as_artifact(jira):
    key = make(jira).key
    att = jira.add_attachment(key, "TDS.docx", b"bytes", "application/x", dry_run=False)
    listed = jira.get_attachments(key)
    assert [(a.title, a.file_size) for a in listed] == [("TDS.docx", 5)]
    assert jira.download_attachment(listed[0]) == b"bytes" and att.attachment_id == listed[0].attachment_id
    assert jira.has_artifact(key)


def test_future_sprint_is_reused_and_issues_join_it(jira):
    first = jira.get_or_create_future_sprint(dry_run=False)
    assert jira.get_or_create_future_sprint(dry_run=False) == first
    assert jira.get_or_create_future_sprint() == first  # existing sprint: no dry-run placeholder
    key = make(jira).key
    jira.add_issues_to_sprint(first, [key], dry_run=False)
    assert jira._doc(key)["sprint_id"] == int(first)
    assert jira._board()["sprints"][0]["name"] == "MOCK Sprint 1"


def test_future_sprint_dry_run_creates_nothing(jira):
    assert jira.get_or_create_future_sprint() == "(dry-run)"
    assert jira._board()["sprints"] == []


def test_delete_removes_subtasks_attachments_and_detaches_other_children(jira):
    epic = make(jira, "Epic")
    story = make(jira, parent_key=epic.key)
    sub = make(jira, "Subtask", parent_key=story.key)
    att = jira.add_attachment(story.key, "f.txt", b"x", dry_run=False)
    jira.delete_issue(story.key, dry_run=False)
    assert [i.key for i in jira.search('project = "MOCK"')] == [epic.key]
    with pytest.raises(KeyError):
        jira.get_issue(sub.key)
    assert jira.store.get_bytes(f"mock_jira/MOCK/attachments/{att.attachment_id}") is None
    jira.delete_issue("MOCK-404", dry_run=False)  # missing: no-op

    story2 = make(jira, parent_key=epic.key)
    jira.delete_issue(epic.key, dry_run=False)  # an epic's stories are detached, not deleted
    assert jira.get_issue(story2.key).parent_key is None


def test_modify_retries_on_version_conflict(clock):
    class Racy(FakeObjectStore):
        raced = False

        def put_bytes_if(self, key, data, version, dry_run=True):
            if not self.raced and key.endswith("board.json") and version is not None:
                self.raced = True  # another writer allocates MOCK-2 between our read and write
                board = self.get_json(key)
                board["next_issue_num"] = 3
                board["rank"].append("MOCK-2")
                self.put_json(key, board, dry_run=False)
            super().put_bytes_if(key, data, version, dry_run)

    jira = MockJiraClient(Racy(), now=clock)
    make(jira)
    assert make(jira).key == "MOCK-3"  # re-read the board, didn't reuse MOCK-2 or lose it
    assert jira._board()["rank"] == ["MOCK-1", "MOCK-2", "MOCK-3"]


def test_modify_gives_up_after_repeated_conflicts(clock):
    class Always(FakeObjectStore):
        def put_bytes_if(self, key, data, version, dry_run=True):
            raise VersionConflict(key)

    with pytest.raises(VersionConflict):
        MockJiraClient(Always(), now=clock).create_issue("Story", "s", dry_run=False)


def test_two_clients_on_one_store_share_state(tmp_path, clock):
    a = MockJiraClient(LocalObjectStore(tmp_path), now=clock)
    b = MockJiraClient(LocalObjectStore(tmp_path), now=clock)
    key = make(a).key
    b.add_comment(key, "from b", dry_run=False)
    assert a.get_issue(key).comments == ["from b"]
