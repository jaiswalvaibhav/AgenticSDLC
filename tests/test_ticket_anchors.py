import pytest

from sdlc.adapters.fake import FakeTicketSystem
from sdlc.agents.analyst.ticket_anchors import (AnchorMissing, AttachmentSource, ConfluenceSource,
                                                 find_anchor_sources, pick_anchor_source)
from sdlc.ports import Attachment

PAGE = "https://x.atlassian.net/wiki/spaces/S/pages/4194305/Technical+Design"


def _tickets(links=(), files=()):
    t = FakeTicketSystem()
    issue = t.create_issue("Story", "DDS", dry_run=False)
    for url, title in links:
        t.add_remote_link(issue.key, url, title, dry_run=False)
    t.attachments[issue.key] = [Attachment(f"a{i}", name, "x/y", f"https://dl/{i}") for i, name in enumerate(files)]
    return t, issue.key


@pytest.mark.parametrize("url", [PAGE, "https://x.atlassian.net/wiki/pages/viewpage.action?pageId=4194305"])
def test_finds_confluence_page_in_both_url_shapes(url):
    t, key = _tickets(links=[(url, "TDS")])
    assert pick_anchor_source(key, t) == ConfluenceSource(page_id="4194305", title="TDS")


@pytest.mark.parametrize("name,fmt", [("a.xlsx", "xlsx"), ("a.XLSM", "xlsx"), ("a.pdf", "pdf"),
                                       ("a.docx", "docx"), ("a.csv", "csv")])
def test_accepts_each_supported_attachment(name, fmt):
    t, key = _tickets(files=[name])
    source = pick_anchor_source(key, t)
    assert isinstance(source, AttachmentSource) and source.format == fmt


def test_link_preferred_over_attachment():
    t, key = _tickets(links=[(PAGE, "TDS")], files=["a.xlsx"])
    assert isinstance(pick_anchor_source(key, t), ConfluenceSource)


def test_raises_when_nothing_attached():
    t, key = _tickets()
    with pytest.raises(AnchorMissing, match="nothing usable"):
        pick_anchor_source(key, t)


def test_unsupported_file_and_non_confluence_link_are_named_in_reason():
    t, key = _tickets(links=[("https://example.com/doc", "Spec")], files=["TDS.doc"])
    with pytest.raises(AnchorMissing) as exc:
        pick_anchor_source(key, t)
    assert "TDS.doc" in exc.value.reason and "Spec" in exc.value.reason
    assert find_anchor_sources(key, t)[0] == []


def test_raises_on_two_links_and_on_two_attachments():
    t, key = _tickets(links=[(PAGE, "A"), (PAGE.replace("4194305", "7"), "B")])
    with pytest.raises(AnchorMissing, match="2 Confluence links"):
        pick_anchor_source(key, t)
    t, key = _tickets(files=["a.xlsx", "b.pdf"])
    with pytest.raises(AnchorMissing, match="2 attachments"):
        pick_anchor_source(key, t)
