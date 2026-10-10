"""Downloads a Confluence page tree into the canonical local corpus and keeps a
manifest for incremental sync (moves, renames, deletes). See CLAUDE.md / BRIEF.md
"Confluence download and write-back".

Layout: <data_dir>/corpus/<ancestor titles>/<slug(title)>__<pageId>/
          page.html, meta.json, _attachments/<file>
Deleted pages move to <data_dir>/corpus/_deleted/... and drop out of the manifest.

The manifest (pageId -> version/path/s3_key/pdf_hash) lives in the ObjectStore under
"manifest.json", so it's available to both profiles (local folder | S3).

A separate download trace ("confluence_trace.json" in the ObjectStore, keyed by page
id) records success/failure per page. Each new/changed page's fetch runs in its own
try/except, so one page's exception no longer aborts the whole batch (as it used to
via list(pool.map(...)) surfacing the first exception raised) — the rest of the
batch still completes, and a page whose last recorded status was "failed" is
retried on the next sync even if its version hasn't changed since. See
jira_sync.py's TRACE_KEY for the equivalent in the Jira downloader.

Scaling to an enterprise space (~3000 pages, see CLAUDE.md "Enterprise scale: Confluence sync"):
doc_source.get_descendants() is metadata-only (no page body), so the diff loop below
decides what's new/changed/moved/deleted using only that cheap metadata. Only the
new/changed pages get a full-body get_page() call, and those run concurrently in
_fetch_and_write — the expensive work scales with how much changed, not the space size.
"""
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sdlc.adapters.confluence import slugify
from sdlc.ports import DocumentSource, ObjectStore, Page

MANIFEST_KEY = "manifest.json"
TRACE_KEY = "confluence_trace.json"
FETCH_WORKERS = 8  # concurrent get_page() + attachment fetches for new/changed pages


@dataclass
class SyncResult:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    moved: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def _corpus_dir(data_dir: Path) -> Path:
    return data_dir / "corpus"


def _page_dir(data_dir: Path, page: Page) -> Path:
    return _corpus_dir(data_dir) / page.parent_path / f"{slugify(page.title)}__{page.page_id}"


def sync_tree(doc_source: DocumentSource, store: ObjectStore, *, root_page_id: str,
              data_dir: str | Path, dry_run: bool = True) -> SyncResult:
    data_dir = Path(data_dir)
    manifest = store.get_json(MANIFEST_KEY) or {}
    trace = store.get_json(TRACE_KEY) or {}
    pages = doc_source.get_descendants(root_page_id)  # metadata-only; no page body yet
    result = SyncResult()
    seen_ids = set()
    to_fetch: list[tuple[Page, Path]] = []  # (metadata page, target dir) needing a body write

    for page in pages:
        seen_ids.add(page.page_id)
        entry = manifest.get(page.page_id)
        new_dir = _page_dir(data_dir, page)
        new_path = str(new_dir.relative_to(_corpus_dir(data_dir)))
        previously_failed = trace.get(page.page_id, {}).get("status") == "failed"

        if entry is None:
            manifest[page.page_id] = {"version": page.version, "path": new_path}
            to_fetch.append((page, new_dir))
            result.created.append(page.page_id)
            continue

        old_dir = _corpus_dir(data_dir) / entry["path"]
        if entry["path"] != new_path:
            _move(old_dir, new_dir, dry_run=dry_run)
            entry["path"] = new_path
            result.moved.append(page.page_id)

        if entry["version"] != page.version or previously_failed:
            entry["version"] = page.version
            to_fetch.append((page, new_dir))
            result.updated.append(page.page_id)
        elif page.page_id not in result.moved:
            result.unchanged.append(page.page_id)

    _fetch_and_write(doc_source, to_fetch, trace, dry_run=dry_run, result=result)

    for page_id in list(manifest):
        if page_id in seen_ids:
            continue
        entry = manifest.pop(page_id)
        old_dir = _corpus_dir(data_dir) / entry["path"]
        deleted_dir = _corpus_dir(data_dir) / "_deleted" / entry["path"]
        _move(old_dir, deleted_dir, dry_run=dry_run)
        trace.pop(page_id, None)
        result.deleted.append(page_id)

    store.put_json(MANIFEST_KEY, manifest, dry_run=dry_run)
    store.put_json(TRACE_KEY, trace, dry_run=dry_run)
    return result


def _fetch_and_write(doc_source: DocumentSource, to_fetch: list[tuple[Page, Path]],
                      trace: dict, *, dry_run: bool, result: SyncResult) -> None:
    if not to_fetch:
        return
    if dry_run:
        for _, page_dir in to_fetch:
            print(f"[dry-run] would write {page_dir}/page.html + meta.json")
        return

    def fetch_one(item: tuple[Page, Path]) -> tuple[str, bool, str | None]:
        meta, page_dir = item
        try:
            full = doc_source.get_page(meta.page_id)  # the one full-body fetch, only here
            page_dir.mkdir(parents=True, exist_ok=True)
            (page_dir / "page.html").write_text(full.html)

            attachments_dir = page_dir / "_attachments"
            attachment_names = []
            for attachment in doc_source.get_attachments(meta.page_id):
                attachments_dir.mkdir(parents=True, exist_ok=True)
                data = doc_source.download_attachment(attachment)
                (attachments_dir / attachment.title).write_bytes(data)
                attachment_names.append(attachment.title)

            (page_dir / "meta.json").write_text(json.dumps({
                "pageId": full.page_id, "spaceKey": full.space_key, "title": meta.title,
                "parentPath": meta.parent_path, "url": full.url, "version": meta.version,
                "attachments": attachment_names,  # lets pdf.py resolve <img> src locally
            }, indent=2))
            return meta.page_id, True, None
        except Exception as exc:  # noqa: BLE001 -- isolate per-page; see module docstring
            print(f"[error] failed to sync Confluence page {meta.page_id}: {exc}")
            return meta.page_id, False, str(exc)

    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        outcomes = list(pool.map(fetch_one, to_fetch))

    now = datetime.now(timezone.utc).isoformat()
    for page_id, ok, error in outcomes:
        if ok:
            trace[page_id] = {"status": "success", "error": None, "last_attempt": now}
        else:
            trace[page_id] = {"status": "failed", "error": error, "last_attempt": now}
            if page_id in result.created:
                result.created.remove(page_id)
            if page_id in result.updated:
                result.updated.remove(page_id)
            result.failed.append(page_id)


def _move(src: Path, dest: Path, *, dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] would move {src} -> {dest}")
        return
    if not src.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
