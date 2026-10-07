from pathlib import Path

import pytest

from sdlc.agents.analyst.anchors import AnchorNotConfirmed, read_anchor
from sdlc.adapters.fake import FakeDocumentSource, FakeObjectStore

UC = "demo_order_fulfilment"  # uses the real usecases/demo_order_fulfilment/page_roles.yaml


def test_missing_page_id_raises_with_no_candidate():
    doc = FakeDocumentSource()
    store = FakeObjectStore()
    with pytest.raises(AnchorNotConfirmed) as exc:
        read_anchor("data_design_solution", use_case=UC, doc_source=doc, store=store, data_dir="/tmp")
    assert exc.value.role == "data_design_solution"
    assert exc.value.candidates == []


def test_missing_page_id_surfaces_a_title_candidate():
    # page_roles.yaml's title_pattern for this role is "Data Design Solution*"; the
    # fake's find_page_by_title is an exact match, so the title must match it exactly
    # once the trailing '*' is stripped.
    doc = FakeDocumentSource()
    doc.add_page("Data Design Solution", page_id="9")
    store = FakeObjectStore()
    with pytest.raises(AnchorNotConfirmed) as exc:
        read_anchor("data_design_solution", use_case=UC, doc_source=doc, store=store, data_dir="/tmp")
    assert exc.value.candidates == ["Data Design Solution"]


def test_reads_from_local_corpus_when_synced(tmp_path: Path, monkeypatch):
    doc = FakeDocumentSource()
    store = FakeObjectStore()
    monkeypatch.setattr(
        "sdlc.agents.analyst.anchors._page_roles",
        lambda use_case: {"data_design_solution": {"page_id": "42"}},
    )
    page_dir = tmp_path / "corpus" / "FAKE" / "data-design-solution__42"
    page_dir.mkdir(parents=True)
    (page_dir / "page.html").write_text(
        "<h2>Layers</h2><p>Raw, Curated, Presentation.</p>"
        "<h2>Refresh schedule</h2><p>Daily by 6am.</p>")
    (page_dir / "meta.json").write_text(
        '{"title": "Data Design Solution", "url": "fake://42", "attachments": ["diagram.png"]}')
    (page_dir / "_attachments").mkdir()
    (page_dir / "_attachments" / "diagram.png").write_bytes(b"PNGDATA")
    store.put_json("manifest.json", {"42": {"version": 1, "path": "FAKE/data-design-solution__42"}},
                    dry_run=False)

    result = read_anchor("data_design_solution", use_case=UC, doc_source=doc, store=store, data_dir=tmp_path)

    assert result.title == "Data Design Solution"
    assert result.sections == [
        ("Layers", "Raw, Curated, Presentation."),
        ("Refresh schedule", "Daily by 6am."),
    ]
    assert len(result.images) == 1
    assert result.images[0].format == "png"
    assert result.images[0].data == b"PNGDATA"


def test_falls_back_to_live_read_when_not_in_corpus(monkeypatch):
    doc = FakeDocumentSource()
    doc.add_page("Data Design Solution", page_id="42", html="<h1>Intro</h1><p>Hello.</p>")
    store = FakeObjectStore()  # empty manifest -> not in corpus
    monkeypatch.setattr(
        "sdlc.agents.analyst.anchors._page_roles",
        lambda use_case: {"data_design_solution": {"page_id": "42"}},
    )

    result = read_anchor("data_design_solution", use_case=UC, doc_source=doc, store=store, data_dir="/tmp")

    assert result.sections == [("Intro", "Hello.")]
