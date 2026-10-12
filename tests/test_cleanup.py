from sdlc.adapters.fake import FakeDocumentSource, FakeObjectStore, FakeTicketSystem
from sdlc.workflow.cleanup import cleanup_run

UC = "uc1"
TITLE = "5. Solution Requirements"


def _setup():
    tickets, docs, store = FakeTicketSystem(), FakeDocumentSource(), FakeObjectStore()
    tickets.add_issue("D-1", "Epic", "Epic", labels=[f"uc:{UC}"])
    tickets.add_issue("D-2", "Story", "Step", parent_key="D-1", labels=[f"uc:{UC}"])
    tickets.add_issue("D-3", "Subtask", "Sub", parent_key="D-2", labels=[f"uc:{UC}"])
    tickets.add_issue("D-9", "Story", "Other use case", labels=["uc:other"])
    page = docs.add_page(TITLE)
    docs.add_page("Unrelated")
    tickets.remote_links["D-2"] = [(f"https://x/wiki/spaces/S/pages/{page.page_id}/p", TITLE)]
    for key in ("jira_checkpoint.json", f"workflow/{UC}/steps.json", f"workflow/{UC}/plan.json",
                f"ui/{UC}/selection.json"):
        store.put_json(key, {"x": 1}, dry_run=False)
    return tickets, docs, store, page


def test_dry_run_deletes_nothing():
    tickets, docs, store, _ = _setup()
    result = cleanup_run(tickets=tickets, doc_source=docs, store=store, use_case=UC, plan_page_title=TITLE)
    assert len(result["issues"]) == 3 and len(result["pages"]) == 1
    assert len(tickets.issues) == 4 and len(docs.pages) == 2
    assert store.get_json("jira_checkpoint.json") == {"x": 1}


def test_cleanup_deletes_the_use_case_issues_plan_page_and_state():
    tickets, docs, store, page = _setup()
    cleanup_run(tickets=tickets, doc_source=docs, store=store, use_case=UC, plan_page_title=TITLE, dry_run=False)
    assert list(tickets.issues) == ["D-9"]
    assert page.page_id not in docs.pages and len(docs.pages) == 1
    for key in ("jira_checkpoint.json", f"workflow/{UC}/steps.json", f"workflow/{UC}/plan.json",
                f"ui/{UC}/selection.json"):
        assert store.get_json(key) is None


def test_a_failed_delete_stops_before_pages_and_state():
    tickets, docs, store, page = _setup()

    def forbidden(key, dry_run=True):
        raise PermissionError("403")
    tickets.delete_issue = forbidden
    result = cleanup_run(tickets=tickets, doc_source=docs, store=store, use_case=UC,
                         plan_page_title=TITLE, dry_run=False)
    assert "D-3" in result["failed"]
    assert page.page_id in docs.pages and store.get_json("jira_checkpoint.json") == {"x": 1}


def test_cleanup_without_a_doc_source_leaves_confluence_alone():
    tickets, docs, store, page = _setup()
    result = cleanup_run(tickets=tickets, doc_source=None, store=store, use_case=UC, dry_run=False)
    assert list(tickets.issues) == ["D-9"] and result["pages"] == []
    assert page.page_id in docs.pages  # never looked at
    assert store.get_json(f"workflow/{UC}/steps.json") is None


def test_cleanup_deletes_every_recorded_page_but_never_a_page_linked_as_an_input():
    from sdlc.workflow.pages import record_page, recorded_pages
    tickets, docs, store, plan = _setup()
    other_output = docs.add_page("7. Another generated page")
    anchor = docs.add_page("Data Design Solution")  # a person linked this on a step ticket
    tickets.remote_links["D-3"] = [(f"https://x/wiki/spaces/S/pages/{anchor.page_id}/d", anchor.title)]
    record_page(store, UC, plan, "D-2", dry_run=False)
    record_page(store, UC, other_output, "D-3", dry_run=False)

    result = cleanup_run(tickets=tickets, doc_source=docs, store=store, use_case=UC,
                         plan_page_title=TITLE, dry_run=False)

    assert result["page_errors"] == {}
    assert plan.page_id not in docs.pages and other_output.page_id not in docs.pages
    assert anchor.page_id in docs.pages
    assert recorded_pages(store, UC) == {}


def test_a_page_that_fails_to_delete_does_not_stop_the_saved_run_being_cleared():
    from sdlc.workflow.pages import record_page, recorded_pages
    tickets, docs, store, plan = _setup()
    record_page(store, UC, plan, "D-2", dry_run=False)
    docs.delete_page = lambda page_id, dry_run=True: (_ for _ in ()).throw(PermissionError("no"))

    result = cleanup_run(tickets=tickets, doc_source=docs, store=store, use_case=UC,
                         plan_page_title=TITLE, dry_run=False)

    assert list(result["page_errors"]) == [plan.page_id]
    assert store.get_json(f"workflow/{UC}/steps.json") is None  # run state cleared regardless
    assert set(recorded_pages(store, UC)) == {plan.page_id}  # a retry can still find the page


def test_without_a_doc_source_the_record_of_pages_is_kept():
    from sdlc.workflow.pages import record_page, recorded_pages
    tickets, docs, store, plan = _setup()
    record_page(store, UC, plan, "D-2", dry_run=False)
    cleanup_run(tickets=tickets, doc_source=None, store=store, use_case=UC, dry_run=False)
    assert set(recorded_pages(store, UC)) == {plan.page_id} and plan.page_id in docs.pages
