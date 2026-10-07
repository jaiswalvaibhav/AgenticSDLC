"""Downloads a Confluence page tree into the canonical local corpus and keeps a
manifest for incremental sync (moves, renames, deletes). See CLAUDE.md / BRIEF.md
"Confluence download and write-back".

Layout: <data_dir>/corpus/<ancestor titles>/<slug(title)>__<pageId>/
          page.html, meta.json, _attachments/<file>
Deleted pages move to <data_dir>/corpus/_deleted/... and drop out of the manifest.

The manifest (pageId -> version/path/s3_key/pdf_hash) lives in the ObjectStore under
"manifest.json", so it's available to both profiles (local folder | S3).
"""
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from sdlc.adapters.confluence import slugify
from sdlc.ports import DocumentSource, ObjectStore, Page

MANIFEST_KEY = "manifest.json"


@dataclass
class SyncResult:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    moved: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)


def _corpus_dir(data_dir: Path) -> Path:
    return data_dir / "corpus"


def _page_dir(data_dir: Path, page: Page) -> Path:
    return _corpus_dir(data_dir) / page.parent_path / f"{slugify(page.title)}__{page.page_id}"


def sync_tree(doc_source: DocumentSource, store: ObjectStore, *, root_page_id: str,
              data_dir: str | Path, dry_run: bool = True) -> SyncResult:
    data_dir = Path(data_dir)
    manifest = store.get_json(MANIFEST_KEY) or {}
    pages = doc_source.get_descendants(root_page_id)
    result = SyncResult()
    seen_ids = set()

    for page in pages:
        seen_ids.add(page.page_id)
        entry = manifest.get(page.page_id)
        new_dir = _page_dir(data_dir, page)
        new_path = str(new_dir.relative_to(_corpus_dir(data_dir)))

        if entry is None:
            _write_page(doc_source, page, new_dir, dry_run=dry_run)
            manifest[page.page_id] = {"version": page.version, "path": new_path}
            result.created.append(page.page_id)
            continue

        old_dir = _corpus_dir(data_dir) / entry["path"]
        if entry["path"] != new_path:
            _move(old_dir, new_dir, dry_run=dry_run)
            entry["path"] = new_path
            result.moved.append(page.page_id)

        if entry["version"] != page.version:
            _write_page(doc_source, page, new_dir, dry_run=dry_run)
            entry["version"] = page.version
            result.updated.append(page.page_id)
        elif page.page_id not in result.moved:
            result.unchanged.append(page.page_id)

    for page_id in list(manifest):
        if page_id in seen_ids:
            continue
        entry = manifest.pop(page_id)
        old_dir = _corpus_dir(data_dir) / entry["path"]
        deleted_dir = _corpus_dir(data_dir) / "_deleted" / entry["path"]
        _move(old_dir, deleted_dir, dry_run=dry_run)
        result.deleted.append(page_id)

    store.put_json(MANIFEST_KEY, manifest, dry_run=dry_run)
    return result


def _write_page(doc_source: DocumentSource, page: Page, page_dir: Path, *, dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] would write {page_dir}/page.html + meta.json")
        return
    page_dir.mkdir(parents=True, exist_ok=True)
    (page_dir / "page.html").write_text(page.html)
    (page_dir / "meta.json").write_text(json.dumps({
        "pageId": page.page_id, "spaceKey": page.space_key, "title": page.title,
        "parentPath": page.parent_path, "url": page.url, "version": page.version,
    }, indent=2))

    attachments_dir = page_dir / "_attachments"
    for attachment in doc_source.get_attachments(page.page_id):
        attachments_dir.mkdir(parents=True, exist_ok=True)
        data = doc_source.download_attachment(attachment)
        (attachments_dir / attachment.title).write_bytes(data)


def _move(src: Path, dest: Path, *, dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] would move {src} -> {dest}")
        return
    if not src.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
