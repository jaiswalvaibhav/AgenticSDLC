from pathlib import Path

import pytest

from sdlc import diagrams
from sdlc.adapters.fake import FakeDocumentSource
from sdlc.seed import seed_usecase

USECASE_DIR = Path(__file__).parent.parent / "usecases" / "demo_order_fulfilment"


@pytest.fixture(autouse=True)
def fake_diagrams(monkeypatch):
    """seed_usecase's diagram rendering is exercised by tests/test_diagrams.py against the
    real `dot` binary when available; here we only care about the seed/idempotency logic,
    so stub it out rather than requiring graphviz to be installed to run these tests."""
    monkeypatch.setattr(diagrams, "RENDERERS", {k: (lambda: b"PNGDATA") for k in diagrams.RENDERERS})


def test_dry_run_creates_nothing_but_lists_every_page():
    doc = FakeDocumentSource()
    result = seed_usecase(doc, USECASE_DIR, dry_run=True)

    assert doc.pages == {}
    assert "Order Fulfilment Performance" in result.created
    assert "Data Design Solution" in result.created
    assert "Data Contract" in result.created
    assert result.skipped == []


def test_apply_creates_full_tree_and_fills_page_roles(tmp_path: Path):
    import shutil
    import yaml

    usecase_dir = tmp_path / "demo_order_fulfilment"
    shutil.copytree(USECASE_DIR, usecase_dir)
    doc = FakeDocumentSource()

    result = seed_usecase(doc, usecase_dir, dry_run=False)

    assert len(result.created) == 12
    assert result.skipped == []
    root = doc.find_page_by_title("Order Fulfilment Performance")
    assert root is not None
    dds = doc.find_page_by_title("Data Design Solution")
    assert dds is not None
    assert dds.parent_path.endswith("Solution Architecture")  # nested under the right parent

    page_roles = yaml.safe_load((usecase_dir / "page_roles.yaml").read_text())
    assert page_roles["roles"]["data_design_solution"]["page_id"] == dds.page_id
    assert page_roles["roles"]["usecase_root"]["page_id"] == root.page_id


def test_rerun_is_idempotent(tmp_path: Path):
    import shutil

    usecase_dir = tmp_path / "demo_order_fulfilment"
    shutil.copytree(USECASE_DIR, usecase_dir)
    doc = FakeDocumentSource()
    seed_usecase(doc, usecase_dir, dry_run=False)
    pages_after_first_run = dict(doc.pages)

    result = seed_usecase(doc, usecase_dir, dry_run=False)

    assert result.created == []
    assert len(result.skipped) == 12
    assert doc.pages == pages_after_first_run  # no duplicates
