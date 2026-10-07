"""Anchor resolution + full-text reading for the analyst agent.

Per BRIEF.md: anchors (DDS/TDS) are resolved through page_roles.yaml (page_id
preferred, then label, then title_pattern) and read IN FULL — section by section,
never through top-k retrieval. If an anchor isn't configured (no page_id), the agent
must show the candidate it found and wait for confirmation, not silently proceed —
that's AnchorNotConfirmed below.

Reads from the local corpus (confluence_sync's canonical copy) when the page has been
synced; falls back to a live DocumentSource read otherwise. Diagrams come back as
image bytes, ready for a multi-modal message to Claude.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from bs4 import BeautifulSoup

from sdlc.ports import DocumentSource, ObjectStore

_IMAGE_FORMATS = {"image/png": "png", "image/jpeg": "jpeg", "image/gif": "gif", "image/webp": "webp"}
_EXT_FORMATS = {"png": "png", "jpg": "jpeg", "jpeg": "jpeg", "gif": "gif", "webp": "webp"}


class AnchorNotConfirmed(Exception):
    def __init__(self, role: str, candidate_title: str | None):
        self.role = role
        self.candidates = [candidate_title] if candidate_title else []
        msg = f"anchor {role!r} has no page_id set in page_roles.yaml"
        if candidate_title:
            msg += f"; found a candidate by title: {candidate_title!r} — confirm by setting page_id"
        super().__init__(msg)


@dataclass
class ImageRef:
    format: str
    data: bytes


@dataclass
class AnchorResult:
    page_id: str
    title: str
    url: str
    sections: list[tuple[str, str]]  # [(heading, text), ...]
    images: list[ImageRef] = field(default_factory=list)


def _page_roles(use_case: str) -> dict:
    return yaml.safe_load((Path("usecases") / use_case / "page_roles.yaml").read_text())["roles"]


def _split_sections(html: str) -> list[tuple[str, str]]:
    """Splits at h1-h3, matching how a human would read the page section by section."""
    soup = BeautifulSoup(html, "html.parser")
    sections: list[tuple[str, str]] = []
    heading, parts = "(untitled)", []
    for el in soup.find_all(True):
        if el.name in ("h1", "h2", "h3"):
            if parts:
                sections.append((heading, "\n".join(parts)))
            heading, parts = el.get_text(strip=True), []
        elif el.name in ("p", "li", "td", "th") and el.find(["h1", "h2", "h3"]) is None:
            text = el.get_text(" ", strip=True)
            if text:
                parts.append(text)
    if parts:
        sections.append((heading, "\n".join(parts)))
    if sections:
        return sections
    whole_text = soup.get_text(" ", strip=True)
    return [("(untitled)", whole_text)] if whole_text else []


def resolve_anchor_page_id(role: str, *, use_case: str, doc_source: DocumentSource) -> str:
    entry = _page_roles(use_case).get(role)
    if not entry:
        raise AnchorNotConfirmed(role, None)
    if entry.get("page_id"):
        return str(entry["page_id"])
    title_prefix = (entry.get("title_pattern") or "").rstrip("*")
    candidate = doc_source.find_page_by_title(title_prefix) if title_prefix else None
    raise AnchorNotConfirmed(role, candidate.title if candidate else None)


def _read_from_corpus(page_id: str, *, store: ObjectStore, data_dir: Path) -> AnchorResult | None:
    manifest = store.get_json("manifest.json") or {}
    entry = manifest.get(page_id)
    if not entry:
        return None
    page_dir = data_dir / "corpus" / entry["path"]
    html_path, meta_path = page_dir / "page.html", page_dir / "meta.json"
    if not html_path.exists() or not meta_path.exists():
        return None

    meta = json.loads(meta_path.read_text())
    images = []
    for filename in meta.get("attachments", []):
        fmt = _EXT_FORMATS.get(Path(filename).suffix.lower().lstrip("."))
        if fmt:
            images.append(ImageRef(format=fmt, data=(page_dir / "_attachments" / filename).read_bytes()))

    return AnchorResult(page_id=page_id, title=meta["title"], url=meta["url"],
                        sections=_split_sections(html_path.read_text()), images=images)


def read_anchor(role: str, *, use_case: str, doc_source: DocumentSource, store: ObjectStore,
                 data_dir: str | Path) -> AnchorResult:
    page_id = resolve_anchor_page_id(role, use_case=use_case, doc_source=doc_source)

    from_corpus = _read_from_corpus(page_id, store=store, data_dir=Path(data_dir))
    if from_corpus:
        return from_corpus

    page = doc_source.get_page(page_id)  # not yet synced locally — read live instead
    images = [
        ImageRef(format=_IMAGE_FORMATS[a.media_type], data=doc_source.download_attachment(a))
        for a in doc_source.get_attachments(page_id) if a.media_type in _IMAGE_FORMATS
    ]
    return AnchorResult(page_id=page.page_id, title=page.title, url=page.url,
                        sections=_split_sections(page.html), images=images)
