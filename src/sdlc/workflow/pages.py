"""The Confluence pages a workflow run created or rewrote, recorded per use case so cleanup
deletes exactly those. A page a person linked on a ticket as an input (a DDS/TDS anchor) is
never recorded, so it is never deleted. Agents call record_page after publishing a page."""
from sdlc.ports import DocumentSource, ObjectStore, Page


def pages_key(use_case: str) -> str:
    return f"workflow/{use_case}/pages.json"


def recorded_pages(store: ObjectStore, use_case: str) -> dict[str, dict]:
    """page_id -> {title, url, issue_key}."""
    return store.get_json(pages_key(use_case)) or {}


def record_page(store: ObjectStore, use_case: str, page: Page, issue_key: str,
                dry_run: bool = True) -> None:
    pages = recorded_pages(store, use_case)
    pages[page.page_id] = {"title": page.title, "url": page.url, "issue_key": issue_key}
    store.put_json(pages_key(use_case), pages, dry_run=dry_run)


def delete_pages(doc_source: DocumentSource, store: ObjectStore, use_case: str,
                 page_ids: set[str], dry_run: bool = True) -> dict[str, str]:
    """Deletes the pages (Confluence moves them to the space trash). One failing page doesn't
    stop the rest. Deleted pages leave the record; failed ones stay in it so a retry finds
    them. Returns {page_id: reason} for the failures."""
    errors: dict[str, str] = {}
    for page_id in sorted(page_ids):
        try:
            doc_source.delete_page(page_id, dry_run=dry_run)
        except Exception as exc:
            errors[page_id] = f"{type(exc).__name__}: {exc}"
    remaining = {pid: info for pid, info in recorded_pages(store, use_case).items()
                 if pid not in page_ids or pid in errors}
    if remaining:
        store.put_json(pages_key(use_case), remaining, dry_run=dry_run)
    else:
        store.delete(pages_key(use_case), dry_run=dry_run)
    return errors
