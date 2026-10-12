"""/api/mockjira (mounted only in mock mode) and 'Start fresh' deleting the mock's tickets."""
import pytest
from fastapi.testclient import TestClient

from sdlc.adapters.fake import FakeDocumentSource, FakeObjectStore
from sdlc.adapters.mock_jira import MockJiraClient
from sdlc.workflow.registry import WorkflowRegistry
from sdlc_api.app import create_app

from tests.test_mock_jira import Clock

UC = "demo_order_fulfilment"
API = "/api/mockjira"


def _cfg(mode):
    return {"profile": "local", "use_case": UC,
            "atlassian": {"base_url": "https://x.atlassian.net", "email": "a@b.com", "api_token": "T"},
            "jira": {"mode": mode, "project_key": "REAL", "approval_label": "sdlc-approved",
                     "reject_label": "sdlc-rejected", "mock": {"project_key": "MOCK"},
                     "components": {"analyst": None, "engineer": "Engineering", "tester": "Testing"},
                     "assignees": {"analyst": None, "engineer": None, "tester": None}},
            "aws": {"agent_runtime_arn": ""}}


@pytest.fixture
def store():
    return FakeObjectStore()


@pytest.fixture
def jira(store):
    return MockJiraClient(store, now=Clock())


@pytest.fixture
def docs():
    return FakeDocumentSource()


@pytest.fixture
def app(jira, store, docs):
    return create_app(cfg=_cfg("mock"), registry=WorkflowRegistry.load(), tickets=jira, store=store, docs=docs)


@pytest.fixture
def api(app):
    return TestClient(app)


@pytest.fixture
def woken(app, monkeypatch):
    """Records poller wake-ups; pretends a poller is running."""
    calls = []
    app.state.poller.run = {"use_case": UC, "profile": "local"}
    monkeypatch.setattr(app.state.poller, "sync_now", lambda: calls.append(1) or True)
    return calls


def new_issue(api, **body):
    response = api.post(f"{API}/issues", json={"issue_type": "Story", "summary": "s", **body})
    assert response.status_code == 200, response.text
    return response.json()["key"]


def test_routes_exist_only_in_mock_mode(store):
    real = TestClient(create_app(cfg=_cfg("real"), registry=WorkflowRegistry.load(), store=store))
    assert real.get(f"{API}/board").status_code == 404


def test_create_view_edit_and_delete_an_issue(api):
    sprint = api.post(f"{API}/sprints", json={"goal": "g"}).json()["id"]
    key = new_issue(api, description="d", labels=["a"], priority="High", story_points=3, sprint_id=sprint)
    assert key == "MOCK-1"

    view = api.get(f"{API}/issues/{key}").json()
    assert (view["summary"], view["priority"], view["story_points"], view["labels"]) == ("s", "High", 3, ["a"])
    assert view["sprint"]["id"] == sprint and view["reporter"] == "You"

    assert api.patch(f"{API}/issues/{key}", json={"summary": "New", "assignee": "me"}).status_code == 200
    edited = api.get(f"{API}/issues/{key}").json()
    assert (edited["summary"], edited["assignee"], edited["priority"]) == ("New", "me", "High")

    assert api.delete(f"{API}/issues/{key}").json() == {"deleted": key}
    assert api.get(f"{API}/issues/{key}").status_code == 404
    assert api.delete(f"{API}/issues/{key}").status_code == 404
    assert api.get(f"{API}/board").json()["issues"] == []


@pytest.mark.parametrize("body", [{"issue_type": "Sub-task", "summary": "x"},
                                  {"issue_type": "Story", "summary": "x", "priority": "Urgent"}])
def test_invalid_create_is_a_400(api, body):
    assert api.post(f"{API}/issues", json=body).status_code == 400


def test_delete_removes_subtasks_too(api):
    story = new_issue(api)
    sub = new_issue(api, issue_type="Subtask", parent=story)
    api.delete(f"{API}/issues/{story}")
    assert api.get(f"{API}/issues/{sub}").status_code == 404


def test_transitions_labels_comments(api):
    key = new_issue(api)
    assert api.post(f"{API}/issues/{key}/transitions", json={"status": "In Progress"}).status_code == 200
    assert api.post(f"{API}/issues/{key}/transitions", json={"status": "Nope"}).status_code == 400
    api.post(f"{API}/issues/{key}/labels", json={"label": "x"})
    api.post(f"{API}/issues/{key}/labels", json={"label": "y"})
    api.delete(f"{API}/issues/{key}/labels/x")
    api.post(f"{API}/issues/{key}/comments", json={"body": "hello"})
    view = api.get(f"{API}/issues/{key}").json()
    assert view["status"] == "In Progress" and view["labels"] == ["y"]
    assert [(c["author"], c["body"]) for c in view["comments"]] == [("You", "hello")]
    assert api.delete(f"{API}/issues/{key}/comments/{view['comments'][0]['id']}").status_code == 200
    assert api.get(f"{API}/issues/{key}").json()["comments"] == []
    assert api.post(f"{API}/issues/MOCK-99/comments", json={"body": "x"}).status_code == 404


def test_attachment_upload_download_and_delete(api):
    key = new_issue(api)
    uploaded = api.post(f"{API}/issues/{key}/attachments",
                        files={"file": ("TDS v1.docx", b"bytes", "application/x-docx")})
    assert uploaded.status_code == 200
    [att] = api.get(f"{API}/issues/{key}").json()["attachments"]
    assert att["filename"] == "TDS v1.docx" and att["url"] == f"{API}/issues/{key}/attachments/{att['id']}"

    download = api.get(att["url"])
    assert download.content == b"bytes" and download.headers["content-type"] == "application/x-docx"
    assert "TDS%20v1.docx" in download.headers["content-disposition"]

    assert api.delete(att["url"]).status_code == 200
    assert api.get(att["url"]).status_code == 404
    assert api.get(f"{API}/issues/{key}").json()["attachments"] == []


def test_weblinks_and_issue_links(api):
    a, b = new_issue(api), new_issue(api)
    api.post(f"{API}/issues/{a}/weblinks", json={"url": "https://x/y", "title": "Plan"})
    link_id = api.get(f"{API}/issues/{a}").json()["remote_links"][0]["id"]
    assert api.delete(f"{API}/issues/{a}/weblinks/{link_id}").status_code == 200

    assert api.post(f"{API}/issues/{a}/links", json={"issue": b, "relation": "is depended by"}).status_code == 200
    assert api.post(f"{API}/issues/{a}/links", json={"issue": b, "relation": "blocks"}).status_code == 400
    assert api.post(f"{API}/issues/{a}/links", json={"issue": "MOCK-99"}).status_code == 404
    a_link = api.get(f"{API}/issues/{a}").json()["links"][0]
    b_link = api.get(f"{API}/issues/{b}").json()["links"][0]
    assert (a_link["direction"], a_link["key"]) == ("outward", b)  # b depends on a: a is depended by b
    assert (b_link["direction"], b_link["key"]) == ("inward", a)   # b depends on a
    api.delete(f"{API}/issues/{a}/links/{b}")
    assert api.get(f"{API}/issues/{b}").json()["links"] == []
    api.post(f"{API}/issues/{a}/links", json={"issue": b, "relation": "depends on"})  # the other way round
    assert api.get(f"{API}/issues/{a}").json()["links"][0]["direction"] == "inward"
    assert api.get(f"{API}/issues/{b}").json()["links"][0]["direction"] == "outward"


def test_sprint_lifecycle_rank_and_rules(api):
    s1, s2 = (api.post(f"{API}/sprints", json={"name": n}).json()["id"] for n in ("One", "Two"))
    a, b = new_issue(api), new_issue(api)
    api.post(f"{API}/rank", json={"keys": [a, b], "sprint_id": s1})
    api.post(f"{API}/rank", json={"keys": [b], "sprint_id": s1, "before_key": a})
    assert [i["key"] for i in api.get(f"{API}/board").json()["issues"]] == [b, a]

    assert api.post(f"{API}/sprints/{s1}/start").json()["state"] == "active"
    assert api.post(f"{API}/sprints/{s2}/start").status_code == 400  # one active sprint at a time
    assert api.patch(f"{API}/sprints/{s2}", json={"name": "Renamed", "goal": "g"}).json()["name"] == "Renamed"
    assert api.delete(f"{API}/sprints/{s1}").status_code == 400
    api.post(f"{API}/issues/{a}/transitions", json={"status": "Done"})

    done = api.post(f"{API}/sprints/{s1}/complete", json={"move_to": s2}).json()
    assert done == {"moved": [b], "move_to": s2}
    issues = {i["key"]: i for i in api.get(f"{API}/board").json()["issues"]}
    assert issues[a]["sprint_id"] == s1 and issues[b]["sprint_id"] == s2
    assert api.post(f"{API}/sprints/99/start").status_code == 404
    assert api.delete(f"{API}/sprints/{s2}").json() == {"deleted": s2}
    after = {i["key"]: i["sprint_id"] for i in api.get(f"{API}/board").json()["issues"]}
    assert after == {a: s1, b: None}  # deleting the future sprint sent its issue to the backlog


def test_writes_wake_the_poller_and_reads_do_not(api, woken):
    key = new_issue(api)
    assert len(woken) == 1
    api.get(f"{API}/board")
    api.get(f"{API}/issues/{key}")
    assert len(woken) == 1
    api.post(f"{API}/issues/{key}/transitions", json={"status": "Done"})
    assert len(woken) == 2
    api.post(f"{API}/issues/{key}/transitions", json={"status": "Nope"})  # rejected: nothing changed
    assert len(woken) == 2


def test_start_fresh_deletes_the_use_cases_mock_tickets_and_saved_run(api, jira, store):
    epic = jira.create_issue("Epic", "e", labels=[f"uc:{UC}"], dry_run=False).key
    step = jira.create_issue("Story", "s", parent_key=epic, labels=[f"uc:{UC}"], dry_run=False).key
    sub = jira.create_issue("Subtask", "x", parent_key=step, labels=[f"uc:{UC}"], dry_run=False).key
    other = jira.create_issue("Story", "other use case", labels=["uc:other"], dry_run=False).key
    for key in (f"workflow/{UC}/steps.json", f"workflow/{UC}/run_state.json", f"ui/{UC}/selection.json",
                "jira_checkpoint.json"):
        store.put_json(key, {"x": 1}, dry_run=False)

    response = api.post(f"/api/runs/{UC}/reset").json()

    assert response["reset"] is True and sorted(response["deleted"]) == sorted([epic, step, sub])
    assert [i["key"] for i in api.get(f"{API}/board").json()["issues"]] == [other]
    assert store.get_json(f"workflow/{UC}/steps.json") is None and store.get_json("jira_checkpoint.json") is None


def test_start_fresh_stops_and_reports_when_a_delete_fails(api, jira, store, monkeypatch):
    jira.create_issue("Story", "s", labels=[f"uc:{UC}"], dry_run=False)
    store.put_json(f"workflow/{UC}/steps.json", {"x": 1}, dry_run=False)

    def boom(key, dry_run=True):
        raise PermissionError("no")
    monkeypatch.setattr(jira, "delete_issue", boom)

    response = api.post(f"/api/runs/{UC}/reset")

    assert response.status_code == 409 and "MOCK-1" in response.json()["detail"]
    assert store.get_json(f"workflow/{UC}/steps.json") == {"x": 1}  # the run still matches the board


def _run_with_pages(jira, store, docs):
    from sdlc.workflow.pages import record_page
    jira.create_issue("Story", "s", labels=[f"uc:{UC}"], dry_run=False)
    plan, other = docs.add_page("5. Solution Requirements"), docs.add_page("7. Another generated page")
    anchor = docs.add_page("Data Design Solution")  # linked by a person: never deleted
    record_page(store, UC, plan, "MOCK-1", dry_run=False)
    record_page(store, UC, other, "MOCK-1", dry_run=False)
    return plan, other, anchor


def test_start_fresh_leaves_confluence_pages_alone_by_default(api, jira, store, docs):
    plan, other, anchor = _run_with_pages(jira, store, docs)
    response = api.post(f"/api/runs/{UC}/reset").json()
    assert response["page_errors"] == {} and len(docs.pages) == 3
    # the record survives, so a later "also delete the pages" can still find them
    from sdlc.workflow.pages import recorded_pages
    assert set(recorded_pages(store, UC)) == {plan.page_id, other.page_id}


def test_start_fresh_can_also_delete_every_page_the_run_created(api, jira, store, docs):
    plan, other, anchor = _run_with_pages(jira, store, docs)
    response = api.post(f"/api/runs/{UC}/reset", params={"delete_pages_too": True}).json()
    assert response["deleted"] == ["MOCK-1"] and response["page_errors"] == {}
    assert set(docs.pages) == {anchor.page_id}


def test_start_fresh_reports_pages_that_could_not_be_deleted(api, jira, store, docs):
    plan, other, anchor = _run_with_pages(jira, store, docs)
    real_delete = docs.delete_page
    docs.delete_page = lambda page_id, dry_run=True: (
        (_ for _ in ()).throw(PermissionError("no")) if page_id == plan.page_id else real_delete(page_id, dry_run))
    response = api.post(f"/api/runs/{UC}/reset", params={"delete_pages_too": True})
    assert response.status_code == 200 and list(response.json()["page_errors"]) == [plan.page_id]
    assert api.get(f"{API}/board").json()["issues"] == []  # the run itself was still reset


def test_real_jira_start_fresh_can_delete_the_pages_without_touching_tickets(store, docs):
    from sdlc.adapters.fake import FakeTicketSystem
    from sdlc.workflow.pages import record_page
    tickets = FakeTicketSystem()
    tickets.add_issue("REAL-1", "Story", "s", labels=[f"uc:{UC}"])
    plan = docs.add_page("5. Solution Requirements")
    record_page(store, UC, plan, "REAL-1", dry_run=False)
    client = TestClient(create_app(cfg=_cfg("real"), registry=WorkflowRegistry.load(), tickets=tickets,
                                   store=store, docs=docs))

    response = client.post(f"/api/runs/{UC}/reset", params={"delete_pages_too": True}).json()

    assert response["deleted"] == [] and response["page_errors"] == {}
    assert "REAL-1" in tickets.issues and plan.page_id not in docs.pages


def test_the_parent_rules_are_enforced_over_the_api(api):
    epic = new_issue(api, issue_type="Epic")
    story = new_issue(api, parent=epic)
    refused = api.post(f"{API}/issues", json={"issue_type": "Story", "summary": "x", "parent": story})
    assert refused.status_code == 400 and "epic" in refused.json()["detail"]
    assert api.post(f"{API}/issues", json={"issue_type": "Subtask", "summary": "x"}).status_code == 400
    assert api.patch(f"{API}/issues/{epic}", json={"parent": story}).status_code == 400
    assert api.patch(f"{API}/issues/{story}", json={"parent": None}).status_code == 200


def test_comments_can_be_edited_but_not_the_bots(api, jira):
    key = new_issue(api)
    jira.add_comment(key, "from the bot", dry_run=False)  # author "SDLC bot"
    api.post(f"{API}/issues/{key}/comments", json={"body": "mine"})
    ids = {c["author"]: c["id"] for c in api.get(f"{API}/issues/{key}").json()["comments"]}

    assert api.patch(f"{API}/issues/{key}/comments/{ids['You']}", json={"body": "mine v2"}).status_code == 200
    assert api.patch(f"{API}/issues/{key}/comments/{ids['SDLC bot']}", json={"body": "x"}).status_code == 400
    assert api.patch(f"{API}/issues/{key}/comments/99", json={"body": "x"}).status_code == 404
    bodies = [c["body"] for c in api.get(f"{API}/issues/{key}").json()["comments"]]
    assert bodies == ["from the bot", "mine v2"]


def test_images_are_served_inline_and_sandboxed_other_files_download(api):
    key = new_issue(api)
    api.post(f"{API}/issues/{key}/attachments", files={"file": ("shot.png", b"\x89PNG", "image/png")})
    api.post(f"{API}/issues/{key}/attachments", files={"file": ("notes.docx", b"d", "application/x-docx")})
    by_name = {a["filename"]: a["url"] for a in api.get(f"{API}/issues/{key}").json()["attachments"]}

    image = api.get(by_name["shot.png"])
    assert image.headers["content-disposition"].startswith("inline")
    assert image.headers["content-security-policy"] == "sandbox" and image.headers["x-content-type-options"] == "nosniff"
    assert api.get(by_name["notes.docx"]).headers["content-disposition"].startswith("attachment")


def test_linking_an_issue_to_itself_is_refused(api):
    key = new_issue(api)
    response = api.post(f"{API}/issues/{key}/links", json={"issue": key, "relation": "depends on"})
    assert response.status_code == 400 and "itself" in response.json()["detail"]


def test_the_sections_are_edited_through_the_api(api):
    story = new_issue(api)
    items = [{"text": "Lands by 3am", "done": True}, {"text": "No duplicates"}]
    assert api.patch(f"{API}/issues/{story}", json={"brief_description": "Short.", "acceptance_criteria": items}).status_code == 200
    view = api.get(f"{API}/issues/{story}").json()
    assert view["brief_description"] == "Short." and view["definition_of_done"] == []
    assert view["acceptance_criteria"] == [{"text": "Lands by 3am", "done": True}, {"text": "No duplicates", "done": False}]
    epic = new_issue(api, issue_type="Epic")
    refused = api.patch(f"{API}/issues/{epic}", json={"definition_of_done": items})
    assert refused.status_code == 400 and "no brief description" in refused.json()["detail"]
