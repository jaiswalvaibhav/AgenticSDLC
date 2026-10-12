"""mock-jira-rename: copy a board to a new project key without losing or touching anything."""
import pytest

from sdlc.adapters.fake import FakeObjectStore
from sdlc.adapters.mock_jira import MockJiraClient
from sdlc.mock_jira_migrate import rename_project

from tests.test_mock_jira import Clock

UC = "uc1"


@pytest.fixture
def populated():
    store = FakeObjectStore()
    jira = MockJiraClient(store, project_key="MOCK", project_name="Mock SDLC", now=Clock())
    make = lambda t, **kw: jira.create_issue(t, "s " + t, dry_run=False, **kw).key  # noqa: E731
    epic = make("Epic")
    story = make("Story", parent_key=epic, labels=["uc:uc1"])
    sub = make("Subtask", parent_key=story)
    other = make("Story", parent_key=epic)
    jira.link_issues(story, other, "Blocks", dry_run=False)
    sprint = jira.create_sprint()["id"]  # generated name: "MOCK Sprint 1"
    jira.create_sprint("Hand named")
    jira.rank([story], sprint)
    jira.add_comment(story, "see MOCK-4 for context", dry_run=False)
    att = jira.add_attachment(story, "TDS.txt", b"bytes", "text/plain", dry_run=False)
    jira.update_fields(sub, parent=other)  # moved away and back: old keys end up in its history
    jira.update_fields(sub, parent=story)
    jira.delete_issue(other, dry_run=False)  # a deleted ticket must not come along
    store.put_json(f"workflow/{UC}/steps.json", {"a": story, "b": epic}, dry_run=False)
    store.put_json(f"ui/{UC}/selection.json", {"generate": ["a"], "project_key": "MOCK"}, dry_run=False)
    return store, dict(epic=epic, story=story, sub=sub, att=att)


def test_dry_run_reports_and_writes_nothing(populated):
    store, _ = populated
    before = dict(store._data)
    result = rename_project(store, "MOCK", "SDLC", "SDLC Delivery", [UC])
    assert (result["issues"], result["attachments"], result["sprints"]) == (3, 1, 2)
    assert store._data == before


def test_everything_is_copied_and_rekeyed(populated):
    store, ids = populated
    rename_project(store, "MOCK", "SDLC", "SDLC Delivery", [UC], dry_run=False)
    new = MockJiraClient(store, project_key="SDLC", project_name="SDLC Delivery")

    board = new.board_view()
    assert board["project"] == {"key": "SDLC", "name": "SDLC Delivery"}
    assert [i["key"] for i in board["issues"]] == ["SDLC-1", "SDLC-3", "SDLC-2"]  # ranking kept (the story was moved last); deleted SDLC-4 is gone
    story = new.issue_view("SDLC-2")
    assert story["parent"] == "SDLC-1" and story["labels"] == ["uc:uc1"]
    assert [c["key"] for c in story["children"]] == ["SDLC-3"]
    assert story["links"] == [] and story["comments"][0]["body"] == "see MOCK-4 for context"  # free text is left alone
    assert [s["name"] for s in board["sprints"]] == ["SDLC Sprint 1", "Hand named"]
    assert story["sprint"]["id"] == 1
    [att] = new.get_attachments("SDLC-2")
    assert new.download_attachment(att) == b"bytes"
    history = [i for e in new.issue_view("SDLC-3")["changelog"] for i in e["items"] if i["field"] == "parent"]
    assert [(i["from"], i["to"]) for i in history] == [("SDLC-2", "SDLC-4"), ("SDLC-4", "SDLC-2")]
    assert new.create_issue("Task", "next", dry_run=False).key == "SDLC-5"  # the counter carried over
    assert store.get_json(f"workflow/{UC}/steps.json") == {"a": "SDLC-2", "b": "SDLC-1"}
    assert store.get_json(f"ui/{UC}/selection.json")["project_key"] == "SDLC"


def test_the_old_board_is_left_untouched_as_the_backup(populated):
    store, ids = populated
    old_files = {k: v for k, v in store._data.items() if k.startswith("mock_jira/MOCK/")}
    rename_project(store, "MOCK", "SDLC", "SDLC Delivery", [UC], dry_run=False)
    assert {k: store._data[k] for k in old_files} == old_files
    assert MockJiraClient(store, project_key="MOCK").get_issue(ids["story"]).key == "MOCK-2"


def test_refuses_a_missing_source_or_an_existing_target(populated):
    store, _ = populated
    with pytest.raises(ValueError, match="no mock Jira board"):
        rename_project(store, "NOPE", "SDLC", "x", [], dry_run=False)
    rename_project(store, "MOCK", "SDLC", "x", [], dry_run=False)
    with pytest.raises(ValueError, match="refusing to overwrite"):
        rename_project(store, "MOCK", "SDLC", "x", [], dry_run=False)
