from pathlib import Path

from sdlc.adapters.fake import FakeDocumentSource, FakeObjectStore
from sdlc.confluence_sync import MANIFEST_KEY, TRACE_KEY, sync_tree
from sdlc.ports import Attachment


def _seed_tree(doc: FakeDocumentSource):
    root = doc.add_page("Order Fulfilment", page_id="1")
    dds = doc.add_page("Data Design Solution", parent_id="1", page_id="2", html="<p>v1</p>")
    doc.attachments["2"] = [Attachment(attachment_id="a1", title="diagram.png",
                                        media_type="image/png", download_url="fake://a1")]
    doc.attachment_bytes["a1"] = b"PNG-BYTES"
    return root, dds


def test_first_sync_creates_pages_and_attachments(tmp_path: Path):
    doc = FakeDocumentSource()
    _seed_tree(doc)
    store = FakeObjectStore()

    result = sync_tree(doc, store, root_page_id="1", data_dir=tmp_path, dry_run=False)

    assert set(result.created) == {"1", "2"}
    dds_dir = tmp_path / "corpus" / "FAKE" / "Order Fulfilment" / "data-design-solution__2"
    assert (dds_dir / "page.html").read_text() == "<p>v1</p>"
    assert (dds_dir / "_attachments" / "diagram.png").read_bytes() == b"PNG-BYTES"
    manifest = store.get_json(MANIFEST_KEY)
    assert manifest["2"]["version"] == 1


def test_second_sync_with_no_changes_is_a_noop(tmp_path: Path):
    doc = FakeDocumentSource()
    _seed_tree(doc)
    store = FakeObjectStore()
    sync_tree(doc, store, root_page_id="1", data_dir=tmp_path, dry_run=False)

    result = sync_tree(doc, store, root_page_id="1", data_dir=tmp_path, dry_run=False)

    assert result.created == []
    assert result.updated == []
    assert set(result.unchanged) == {"1", "2"}


def test_version_bump_triggers_update(tmp_path: Path):
    doc = FakeDocumentSource()
    _seed_tree(doc)
    store = FakeObjectStore()
    sync_tree(doc, store, root_page_id="1", data_dir=tmp_path, dry_run=False)

    doc.pages["2"].html = "<p>v2</p>"
    doc.pages["2"].version = 2
    result = sync_tree(doc, store, root_page_id="1", data_dir=tmp_path, dry_run=False)

    assert result.updated == ["2"]
    dds_dir = tmp_path / "corpus" / "FAKE" / "Order Fulfilment" / "data-design-solution__2"
    assert (dds_dir / "page.html").read_text() == "<p>v2</p>"


def test_rename_moves_the_folder_without_changing_the_page_id(tmp_path: Path):
    doc = FakeDocumentSource()
    _seed_tree(doc)
    store = FakeObjectStore()
    sync_tree(doc, store, root_page_id="1", data_dir=tmp_path, dry_run=False)

    doc.pages["2"].title = "Data Design Solution v2"
    result = sync_tree(doc, store, root_page_id="1", data_dir=tmp_path, dry_run=False)

    assert result.moved == ["2"]
    old_dir = tmp_path / "corpus" / "FAKE" / "Order Fulfilment" / "data-design-solution__2"
    new_dir = tmp_path / "corpus" / "FAKE" / "Order Fulfilment" / "data-design-solution-v2__2"
    assert not old_dir.exists()
    assert (new_dir / "page.html").exists()
    assert store.get_json(MANIFEST_KEY)["2"]["path"].endswith("data-design-solution-v2__2")


def test_deleted_page_moves_to_deleted_folder_and_leaves_manifest(tmp_path: Path):
    doc = FakeDocumentSource()
    _seed_tree(doc)
    store = FakeObjectStore()
    sync_tree(doc, store, root_page_id="1", data_dir=tmp_path, dry_run=False)

    doc.children["1"].remove("2")
    del doc.pages["2"]
    result = sync_tree(doc, store, root_page_id="1", data_dir=tmp_path, dry_run=False)

    assert result.deleted == ["2"]
    deleted_dir = (tmp_path / "corpus" / "_deleted" / "FAKE" / "Order Fulfilment"
                   / "data-design-solution__2")
    assert (deleted_dir / "page.html").exists()
    assert "2" not in (store.get_json(MANIFEST_KEY) or {})


def test_dry_run_writes_nothing(tmp_path: Path):
    doc = FakeDocumentSource()
    _seed_tree(doc)
    store = FakeObjectStore()

    sync_tree(doc, store, root_page_id="1", data_dir=tmp_path, dry_run=True)

    assert not (tmp_path / "corpus").exists()
    assert store.get_json(MANIFEST_KEY) is None


def test_failed_page_fetch_does_not_abort_the_rest_of_the_batch(tmp_path: Path):
    doc = FakeDocumentSource()
    _seed_tree(doc)
    store = FakeObjectStore()

    real_get_page = doc.get_page

    def flaky_get_page(page_id):
        if page_id == "2":
            raise RuntimeError("Confluence API error")
        return real_get_page(page_id)

    doc.get_page = flaky_get_page

    result = sync_tree(doc, store, root_page_id="1", data_dir=tmp_path, dry_run=False)

    assert result.failed == ["2"]
    assert "1" in result.created
    assert store.get_json(TRACE_KEY)["2"]["status"] == "failed"


def test_previously_failed_page_is_retried_even_if_version_unchanged(tmp_path: Path):
    doc = FakeDocumentSource()
    _seed_tree(doc)
    store = FakeObjectStore()

    real_get_page = doc.get_page
    calls = {"n": 0}

    def flaky_get_page(page_id):
        if page_id == "2" and calls["n"] == 0:
            calls["n"] += 1
            raise RuntimeError("Confluence API error")
        return real_get_page(page_id)

    doc.get_page = flaky_get_page

    first = sync_tree(doc, store, root_page_id="1", data_dir=tmp_path, dry_run=False)
    assert first.failed == ["2"]

    second = sync_tree(doc, store, root_page_id="1", data_dir=tmp_path, dry_run=False)
    assert "2" in second.updated
    assert store.get_json(TRACE_KEY)["2"]["status"] == "success"
