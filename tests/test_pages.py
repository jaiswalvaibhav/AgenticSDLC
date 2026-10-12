"""workflow/pages.py: the record of Confluence pages a run created, and deleting them."""
from sdlc.adapters.fake import FakeDocumentSource, FakeObjectStore
from sdlc.workflow.pages import delete_pages, pages_key, record_page, recorded_pages

UC = "uc1"


def _docs_and_store():
    docs, store = FakeDocumentSource(), FakeObjectStore()
    return docs, store, docs.add_page("Plan"), docs.add_page("Other output")


def test_record_page_keeps_every_page_once(tmp_path):
    docs, store, plan, other = _docs_and_store()
    record_page(store, UC, plan, "D-1", dry_run=False)
    record_page(store, UC, other, "D-2", dry_run=False)
    record_page(store, UC, plan, "D-1", dry_run=False)  # rewritten by a re-run: still one entry
    assert set(recorded_pages(store, UC)) == {plan.page_id, other.page_id}
    assert recorded_pages(store, UC)[plan.page_id]["issue_key"] == "D-1"
    assert recorded_pages(FakeObjectStore(), UC) == {}


def test_delete_pages_removes_them_and_the_record():
    docs, store, plan, other = _docs_and_store()
    unrelated = docs.add_page("A page a person linked")
    record_page(store, UC, plan, "D-1", dry_run=False)
    record_page(store, UC, other, "D-2", dry_run=False)

    errors = delete_pages(docs, store, UC, set(recorded_pages(store, UC)), dry_run=False)

    assert errors == {} and set(docs.pages) == {unrelated.page_id}
    assert store.get_json(pages_key(UC)) is None


def test_a_failing_page_is_reported_and_stays_recorded_for_a_retry():
    docs, store, plan, other = _docs_and_store()
    record_page(store, UC, plan, "D-1", dry_run=False)
    record_page(store, UC, other, "D-2", dry_run=False)
    real_delete = docs.delete_page

    def flaky(page_id, dry_run=True):
        if page_id == plan.page_id:
            raise PermissionError("no")
        real_delete(page_id, dry_run=dry_run)
    docs.delete_page = flaky

    errors = delete_pages(docs, store, UC, set(recorded_pages(store, UC)), dry_run=False)

    assert list(errors) == [plan.page_id] and "PermissionError" in errors[plan.page_id]
    assert other.page_id not in docs.pages and plan.page_id in docs.pages
    assert set(recorded_pages(store, UC)) == {plan.page_id}
