"""Mocked-call unit tests for JiraClient: no real network, pins the request/response
shapes documented (and flagged as unverified where applicable) in adapters/jira.py.
"""
import pytest

from sdlc.adapters.jira import JiraClient
from sdlc.ports import TransitionNotAvailable
from tests._mock_http import FakeResponse, FakeSession


@pytest.fixture
def client():
    c = JiraClient(base_url="https://x.atlassian.net", email="a@b.com",
                    api_token="tok", project_key="DEMO")
    c._session = FakeSession()
    return c


def test_create_issue_sends_adf_description_and_parent(client):
    client._session.queue("POST", "/issue", FakeResponse({"key": "DEMO-1"}))
    client._session.queue(
        "GET", "/issue/DEMO-1",
        FakeResponse({"fields": {"issuetype": {"name": "Story"}, "status": {"name": "To Do"},
                                  "summary": "Ingest Orders", "labels": [], "parent": None,
                                  "assignee": None}}),
    )

    client.create_issue("Story", "Ingest Orders", parent_key="DEMO-0",
                         description="line one\n\nline two", dry_run=False)

    method, url, body = client._session.calls[0]
    assert method == "POST" and url.endswith("/issue")
    assert body["fields"]["parent"] == {"key": "DEMO-0"}
    assert body["fields"]["description"]["type"] == "doc"
    paragraphs = [p["content"][0]["text"] for p in body["fields"]["description"]["content"]]
    assert paragraphs == ["line one", "line two"]


def test_create_issue_dry_run_makes_no_calls(client):
    issue = client.create_issue("Story", "x", dry_run=True)
    assert issue.key == "(dry-run)"
    assert client._session.calls == []


def test_transition_issue_finds_id_by_status_name_case_insensitive(client):
    client._session.queue("GET", "/transitions", FakeResponse(
        {"transitions": [{"id": "21", "to": {"name": "In Progress"}},
                          {"id": "31", "to": {"name": "Done"}}]}))
    client._session.queue("POST", "/transitions", FakeResponse({}))

    client.transition_issue("DEMO-1", "in progress", dry_run=False)

    _, _, body = client._session.calls[-1]
    assert body == {"transition": {"id": "21"}}


def test_transition_issue_raises_when_not_available(client):
    client._session.queue("GET", "/transitions", FakeResponse(
        {"transitions": [{"id": "31", "to": {"name": "Done"}}]}))

    with pytest.raises(TransitionNotAvailable, match="Done"):
        client.transition_issue("DEMO-1", "In Progress", dry_run=False)


def test_search_paginates_via_next_page_token(client):
    client._session.queue("POST", "/search/jql", FakeResponse({
        "issues": [{"key": "DEMO-1", "fields": {"issuetype": {"name": "Story"},
                                                  "status": {"name": "To Do"}, "summary": "a"}}],
        "isLast": False, "nextPageToken": "page2",
    }))
    client._session.queue("POST", "/search/jql", FakeResponse({
        "issues": [{"key": "DEMO-2", "fields": {"issuetype": {"name": "Story"},
                                                  "status": {"name": "Done"}, "summary": "b"}}],
        "isLast": True,
    }))

    results = client.search('project = "DEMO"')

    assert [i.key for i in results] == ["DEMO-1", "DEMO-2"]
    second_call_body = client._session.calls[1][2]
    assert second_call_body["nextPageToken"] == "page2"


def test_status_changes_since_filters_by_field_and_date(client):
    client._session.queue("GET", "/changelog", FakeResponse({
        "isLast": True,
        "values": [
            {"created": "2026-01-01T00:00:00.000+0000",
             "items": [{"field": "status", "fromString": "To Do", "toString": "In Progress"}]},
            {"created": "2026-06-01T00:00:00.000+0000",
             "items": [{"field": "status", "fromString": "In Progress", "toString": "Done"},
                       {"field": "assignee", "fromString": None, "toString": "someone"}]},
        ],
    }))

    events = client.status_changes_since("DEMO-1", since="2026-03-01T00:00:00.000+0000")

    assert len(events) == 1
    assert events[0].from_status == "In Progress" and events[0].to_status == "Done"


def test_set_property_puts_value_to_properties_endpoint(client):
    client._session.queue("PUT", "/properties/sdlc.trace", FakeResponse({}))

    client.set_property("DEMO-1", "sdlc.trace", {"requirement_id": "REQ-1"}, dry_run=False)

    method, url, body = client._session.calls[0]
    assert method == "PUT" and url.endswith("/issue/DEMO-1/properties/sdlc.trace")
    assert body == {"requirement_id": "REQ-1"}


def test_set_property_dry_run_makes_no_calls(client):
    client.set_property("DEMO-1", "sdlc.trace", {}, dry_run=True)
    assert client._session.calls == []


def test_get_or_create_future_sprint_uses_existing_future_sprint(client):
    client._session.queue("GET", "/board", FakeResponse({"values": [{"id": 1}]}))
    client._session.queue("GET", "/board/1/sprint", FakeResponse({"values": [{"id": 7, "state": "future"}]}))

    sprint_id = client.get_or_create_future_sprint(dry_run=False)

    assert sprint_id == "7"
    assert not any(method == "POST" for method, _, _ in client._session.calls)


def test_get_or_create_future_sprint_creates_one_when_none_exists(client):
    client._session.queue("GET", "/board", FakeResponse({"values": [{"id": 1}]}))
    client._session.queue("GET", "/board/1/sprint", FakeResponse({"values": []}))
    client._session.queue("POST", "/sprint", FakeResponse({"id": 9, "state": "future"}))

    sprint_id = client.get_or_create_future_sprint(dry_run=False)

    assert sprint_id == "9"
    method, url, body = client._session.calls[-1]
    assert method == "POST" and url.endswith("/sprint")
    assert body == {"name": "DEMO Sprint (auto)", "originBoardId": 1}


def test_get_or_create_future_sprint_dry_run_makes_no_calls(client):
    assert client.get_or_create_future_sprint(dry_run=True) == "(dry-run)"
    assert client._session.calls == []


def test_add_issues_to_sprint_posts_issue_keys(client):
    client._session.queue("POST", "/sprint/7/issue", FakeResponse({}))

    client.add_issues_to_sprint("7", ["DEMO-1", "DEMO-2"], dry_run=False)

    method, url, body = client._session.calls[0]
    assert method == "POST" and url.endswith("/sprint/7/issue")
    assert body == {"issues": ["DEMO-1", "DEMO-2"]}


def test_add_issues_to_sprint_dry_run_makes_no_calls(client):
    client.add_issues_to_sprint("7", ["DEMO-1"], dry_run=True)
    assert client._session.calls == []


def test_add_issues_to_sprint_skips_call_when_no_keys(client):
    client.add_issues_to_sprint("7", [], dry_run=False)
    assert client._session.calls == []
