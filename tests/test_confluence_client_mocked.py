"""Mocked-call unit tests for ConfluenceClient: no real network, pins the request/
response shapes documented in adapters/confluence.py.
"""
from sdlc.adapters.confluence import ConfluenceClient
from tests._mock_http import FakeResponse, FakeSession


def _client():
    c = ConfluenceClient(base_url="https://x.atlassian.net", email="a@b.com",
                          api_token="tok", space_key="DEMO")
    c._session = FakeSession()
    return c


def _queue_empty_ancestors(client, page_id: str):
    client._session.queue("GET", f"/pages/{page_id}/ancestors", FakeResponse({"results": []}))


def test_get_page_builds_page_from_export_view_response():
    client = _client()
    _queue_empty_ancestors(client, "42")
    client._session.queue("GET", "/pages/42", FakeResponse({
        "id": "42", "title": "Data Design Solution", "version": {"number": 3},
        "_links": {"webui": "/spaces/DEMO/pages/42"},
        "body": {"export_view": {"value": "<h1>hi</h1>"}},
    }))

    page = client.get_page("42")

    assert page.page_id == "42" and page.version == 3
    assert page.url == "https://x.atlassian.net/wiki/spaces/DEMO/pages/42"
    assert page.html == "<h1>hi</h1>"
    assert page.parent_path == "DEMO"


def test_create_page_omits_parent_id_when_none():
    client = _client()
    client._session.queue("GET", "/spaces", FakeResponse({"results": [{"id": "999"}]}))
    client._session.queue("POST", "/wiki/api/v2/pages", FakeResponse({
        "id": "1", "title": "Root", "version": {"number": 1},
        "_links": {"webui": "/spaces/DEMO/pages/1"},
    }))
    _queue_empty_ancestors(client, "1")

    client.create_page(None, "Root", "<p>x</p>", dry_run=False)

    _, _, body = client._session.calls[-2]  # the POST .../pages call
    assert "parentId" not in body
    assert body["spaceId"] == "999"


def test_create_page_includes_parent_id_when_given():
    client = _client()
    client._session.queue("GET", "/spaces", FakeResponse({"results": [{"id": "999"}]}))
    client._session.queue("POST", "/wiki/api/v2/pages", FakeResponse({
        "id": "2", "title": "Child", "version": {"number": 1},
        "_links": {"webui": "/spaces/DEMO/pages/2"},
    }))
    _queue_empty_ancestors(client, "2")

    client.create_page("1", "Child", "<p>x</p>", dry_run=False)

    _, _, body = client._session.calls[-2]
    assert body["parentId"] == "1"


def test_create_page_dry_run_makes_no_calls():
    client = _client()
    page = client.create_page("1", "Child", "<p>x</p>", dry_run=True)
    assert page.page_id == "(dry-run)"
    assert client._session.calls == []


def test_find_page_by_title_returns_none_when_no_results():
    client = _client()
    client._session.queue("GET", "/spaces", FakeResponse({"results": [{"id": "999"}]}))
    client._session.queue("GET", "/wiki/api/v2/pages", FakeResponse({"results": []}))

    assert client.find_page_by_title("Nonexistent") is None


def test_get_descendants_is_metadata_only_no_body_fetched():
    """The whole point of get_descendants (see CLAUDE.md "Enterprise scale"): no
    per-page body fetch, just the descendants listing + one batched version call."""
    client = _client()
    client._session.queue("GET", "/pages/1/descendants", FakeResponse({"results": [
        {"id": "2", "title": "Child A", "parentId": "1"},
        {"id": "3", "title": "Child B", "parentId": "1"},
    ]}))
    client._session.queue("GET", "/pages/1", FakeResponse({"id": "1", "title": "Root"}))
    client._session.queue("GET", "/wiki/api/v2/pages", FakeResponse({"results": [
        {"id": "1", "version": {"number": 5}},
        {"id": "2", "version": {"number": 1}},
        {"id": "3", "version": {"number": 2}},
    ]}))

    pages = client.get_descendants("1")

    by_id = {p.page_id: p for p in pages}
    assert by_id["1"].version == 5 and by_id["1"].html == ""
    assert by_id["2"].parent_path == "DEMO/Root"
    assert by_id["3"].version == 2
    # no GET to /pages/2 or /pages/3 individually (no body fetch)
    fetched_urls = [url for _, url, _ in client._session.calls]
    assert not any(url.rstrip("/").endswith(("/pages/2", "/pages/3")) for url in fetched_urls)
