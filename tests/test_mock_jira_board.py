"""MockJiraClient's board operations for the UI: sprints, ranking, edits and read views."""
import pytest

from sdlc.adapters.local_store import LocalObjectStore
from sdlc.adapters.mock_jira import MockJiraClient

from tests.test_mock_jira import Clock


@pytest.fixture
def jira(tmp_path):
    return MockJiraClient(LocalObjectStore(tmp_path), now=Clock())


def make(jira, issue_type="Story", summary="s", **kw):
    return jira.create_issue(issue_type, summary, dry_run=False, **kw).key


def test_update_fields_edits_and_logs_each_changed_field(jira):
    key = make(jira)
    jira.update_fields(key, summary="New", priority="High", story_points=5, assignee="me")
    jira.update_fields(key, summary="New")  # unchanged: no new history entry
    doc = jira._doc(key)
    assert (doc["summary"], doc["priority"], doc["story_points"], doc["assignee"]) == ("New", "High", 5, "me")
    assert len(doc["changelog"]) == 1
    assert {i["field"] for i in doc["changelog"][0]["items"]} == {"summary", "priority", "story_points", "assignee"}


def test_update_fields_rejects_bad_input(jira):
    key = make(jira)
    for bad in ({"status": "Done"}, {"priority": "Urgent"}, {"parent": key}):
        with pytest.raises(ValueError):
            jira.update_fields(key, **bad)
    with pytest.raises(KeyError):
        jira.update_fields(key, parent="MOCK-99")
    with pytest.raises(KeyError):
        jira.update_fields("MOCK-99", summary="x")


def test_comment_link_and_attachment_removal(jira):
    a, b = make(jira), make(jira)
    jira.add_comment(a, "one", dry_run=False)
    jira.add_comment(a, "two", dry_run=False)
    jira.add_remote_link(a, "https://x/y", "t", dry_run=False)
    att = jira.add_attachment(a, "f.txt", b"x", dry_run=False)
    jira.link_issues(a, b, dry_run=False)

    jira.delete_comment(a, "1")
    jira.delete_remote_link(a, "1")
    jira.delete_attachment(a, att.attachment_id)
    jira.unlink_issues(a, b)

    doc = jira._doc(a)
    assert [c["body"] for c in doc["comments"]] == ["two"]
    assert doc["remote_links"] == [] and doc["attachments"] == [] and doc["issue_links"] == []
    assert jira._doc(b)["issue_links"] == []
    assert jira.store.get_bytes(f"mock_jira/MOCK/attachments/{att.attachment_id}") is None
    assert not jira.has_artifact(a)


def test_sprint_lifecycle_and_one_active_rule(jira):
    s1, s2 = jira.create_sprint(goal="ship it"), jira.create_sprint("Next")
    assert (s1["name"], s1["goal"], s2["name"]) == ("MOCK Sprint 1", "ship it", "Next")
    started = jira.start_sprint(s1["id"])
    assert started["state"] == "active" and started["start_date"] and started["end_date"]
    with pytest.raises(ValueError, match="already active"):
        jira.start_sprint(s2["id"])
    with pytest.raises(ValueError, match="future"):
        jira.start_sprint(s1["id"])
    assert jira.update_sprint(s2["id"], name="Renamed", goal="g")["name"] == "Renamed"
    with pytest.raises(ValueError):
        jira.update_sprint(s2["id"], state="closed")
    with pytest.raises(KeyError):
        jira.start_sprint(99)


def test_complete_sprint_moves_only_unfinished_issues(jira):
    s1, s2 = jira.create_sprint()["id"], jira.create_sprint()["id"]
    done, open_, doing = make(jira), make(jira), make(jira)
    jira.rank([done, open_, doing], s1)
    jira.transition_issue(done, "Done", dry_run=False)
    jira.transition_issue(doing, "In Progress", dry_run=False)
    with pytest.raises(ValueError, match="active"):
        jira.complete_sprint(s1)  # not started yet
    jira.start_sprint(s1)

    result = jira.complete_sprint(s1, move_to=s2)

    assert sorted(result["moved"]) == sorted([open_, doing])
    assert jira._doc(done)["sprint_id"] == s1
    assert jira._doc(open_)["sprint_id"] == s2 and jira._doc(doing)["sprint_id"] == s2
    assert next(s for s in jira._board()["sprints"] if s["id"] == s1)["state"] == "closed"
    with pytest.raises(ValueError):
        jira.update_sprint(s1, name="x")
    with pytest.raises(ValueError, match="completed"):
        jira.rank([open_], s1)


def test_complete_sprint_to_backlog_and_target_must_be_future(jira):
    s1, s2 = jira.create_sprint()["id"], jira.create_sprint()["id"]
    key = make(jira)
    jira.rank([key], s1)
    jira.start_sprint(s1)
    with pytest.raises(ValueError, match="future"):
        jira.complete_sprint(s1, move_to=s1)
    jira.complete_sprint(s1)
    assert jira._doc(key)["sprint_id"] is None


def test_delete_sprint_sends_issues_to_backlog_and_only_future_sprints(jira):
    s1, s2 = jira.create_sprint()["id"], jira.create_sprint()["id"]
    key = make(jira)
    jira.rank([key], s2)
    jira.start_sprint(s1)
    with pytest.raises(ValueError):
        jira.delete_sprint(s1)
    jira.delete_sprint(s2)
    assert jira._doc(key)["sprint_id"] is None
    assert [s["id"] for s in jira._board()["sprints"]] == [s1]


def test_rank_moves_between_sprints_reorders_and_carries_subtasks(jira):
    s1 = jira.create_sprint()["id"]
    a, b, c = make(jira), make(jira), make(jira)
    sub = make(jira, "Subtask", parent_key=a)
    jira.rank([c], s1, before_key=a)
    assert jira._board()["rank"] == [c, a, b, sub]
    jira.rank([a], s1)  # no before_key: to the end
    assert jira._board()["rank"] == [c, b, sub, a]
    assert jira._doc(a)["sprint_id"] == s1 and jira._doc(sub)["sprint_id"] == s1  # subtask follows
    jira.rank([a, c], None, before_key="MOCK-404")  # unknown before_key: end; back to the backlog
    assert jira._board()["rank"][-2:] == [a, c]
    assert jira._doc(a)["sprint_id"] is None
    with pytest.raises(KeyError):
        jira.rank(["MOCK-99"], None)
    with pytest.raises(KeyError):
        jira.rank([a], 42)


def test_board_view_lists_sprints_and_issues_in_rank_order(jira):
    sprint = jira.create_sprint()["id"]
    first, second = make(jira, summary="first"), make(jira, summary="second")
    jira.rank([second], sprint, before_key=first)
    view = jira.board_view()
    assert [i["key"] for i in view["issues"]] == [second, first]
    assert view["issues"][0]["sprint_id"] == sprint and view["statuses"] == ["To Do", "In Progress", "Done"]
    assert view["project"]["key"] == "MOCK" and view["sprints"][0]["id"] == sprint
    assert "comments" not in view["issues"][0]  # summaries only


def test_issue_view_resolves_parent_children_links_and_sprint(jira):
    sprint = jira.create_sprint()["id"]
    epic = make(jira, "Epic", summary="the epic")
    story = make(jira, parent_key=epic)
    sub = make(jira, "Subtask", parent_key=story, summary="the sub")
    other = make(jira, summary="other")
    jira.link_issues(story, other, dry_run=False)
    jira.rank([story], sprint)

    view = jira.issue_view(story)

    assert view["parent_issue"]["summary"] == "the epic"
    assert [c["key"] for c in view["children"]] == [sub]
    assert view["links"][0]["issue"] == {"key": other, "summary": "other", "issuetype": "Story", "status": "To Do"}
    assert view["sprint"]["id"] == sprint
    assert jira.issue_view(epic)["parent_issue"] is None and jira.issue_view(other)["sprint"] is None
    with pytest.raises(KeyError):
        jira.issue_view("MOCK-99")


def test_acting_as_writes_under_another_author(jira):
    key = make(jira)
    jira.acting_as("You").add_comment(key, "hi", dry_run=False)
    jira.acting_as("You").update_fields(key, summary="x")
    doc = jira._doc(key)
    assert doc["comments"][0]["author"] == "You" and doc["changelog"][0]["author"] == "You"
