"""Brief description + the three checklists (acceptance criteria, definition of done, test coverage)."""
import pytest

from sdlc.adapters.local_store import LocalObjectStore
from sdlc.adapters.mock_jira import MockJiraClient
from sdlc.sections import render_sections

from tests.test_mock_jira import Clock


@pytest.fixture
def jira(tmp_path):
    return MockJiraClient(LocalObjectStore(tmp_path), now=Clock())


def test_a_story_is_created_with_its_sections(jira):
    key = jira.create_issue("Story", "s", dry_run=False, sections={
        "brief_description": "  Land orders daily.  ", "acceptance_criteria": ["Lands by 3am", "  ", {"text": "No dupes", "done": True}],
        "definition_of_done": ["Peer reviewed"]}).key
    doc = jira._doc(key)
    assert doc["brief_description"] == "Land orders daily."
    assert doc["acceptance_criteria"] == [{"text": "Lands by 3am", "done": False}, {"text": "No dupes", "done": True}]
    assert doc["definition_of_done"] == [{"text": "Peer reviewed", "done": False}] and doc["test_coverage"] == []


def test_only_stories_tasks_and_bugs_have_them_and_unknown_sections_are_refused(jira):
    for issue_type in ("Task", "Bug"):
        assert jira.create_issue(issue_type, "s", dry_run=False, sections={"test_coverage": ["t"]})
    for issue_type in ("Epic", "Subtask"):
        with pytest.raises(ValueError, match="has no brief description"):
            jira.create_issue(issue_type, "s", dry_run=False, sections={"test_coverage": ["t"]})
    with pytest.raises(ValueError, match="unknown sections"):
        jira.create_issue("Story", "s", dry_run=False, sections={"notes": "x"})
    with pytest.raises(ValueError, match="string or"):
        jira.create_issue("Story", "s", dry_run=False, sections={"acceptance_criteria": [42]})


def test_ticking_and_editing_items_is_saved_and_shows_in_the_history(jira):
    key = jira.create_issue("Story", "s", dry_run=False, sections={"acceptance_criteria": ["a", "b", "c"]}).key
    jira.update_fields(key, acceptance_criteria=[{"text": "a", "done": True}, {"text": "b", "done": False},
                                                 {"text": "c", "done": False}, "d"])
    jira.update_fields(key, brief_description="Short.")
    jira.update_fields(key, brief_description="Short.")  # unchanged: nothing new in the history
    doc = jira._doc(key)
    assert [i["done"] for i in doc["acceptance_criteria"]] == [True, False, False, False]
    assert doc["brief_description"] == "Short."
    items = [i for e in doc["changelog"] for i in e["items"]]
    assert items == [{"field": "acceptance_criteria", "from": "3 items, 0 done", "to": "4 items, 1 done"},
                     {"field": "brief_description", "from": "", "to": "Short."}]


def test_editing_the_sections_of_an_epic_or_with_a_bad_shape_is_refused(jira):
    epic = jira.create_issue("Epic", "e", dry_run=False).key
    story = jira.create_issue("Story", "s", dry_run=False).key
    with pytest.raises(ValueError, match="has no brief description"):
        jira.update_fields(epic, definition_of_done=["x"])
    with pytest.raises(ValueError, match="must be text"):
        jira.update_fields(story, brief_description=["x"])
    with pytest.raises(ValueError, match="string or"):
        jira.update_fields(story, test_coverage=[{"done": True}])


def test_a_ticket_saved_before_the_sections_existed_still_opens(jira):
    key = jira.create_issue("Story", "s", dry_run=False).key
    doc = jira._doc(key)
    for name in ("brief_description", "acceptance_criteria", "definition_of_done", "test_coverage"):
        del doc[name]
    jira.store.put_json(f"mock_jira/MOCK/issues/{key}.json", doc, dry_run=False)
    view = jira.issue_view(key)
    assert view["brief_description"] == "" and view["acceptance_criteria"] == []
    jira.update_fields(key, test_coverage=["t"])  # and can be edited
    assert jira._doc(key)["test_coverage"] == [{"text": "t", "done": False}]


def test_render_sections_gives_real_jira_a_text_description():
    text = render_sections({"brief_description": "Short.", "acceptance_criteria": ["a", "b"],
                            "definition_of_done": [], "test_coverage": [{"text": "t", "done": True}]})
    assert text == "Brief description:\nShort.\n\nAcceptance criteria:\n- a\n- b\n\nTest coverage:\n- t"
    assert render_sections({}) == ""
