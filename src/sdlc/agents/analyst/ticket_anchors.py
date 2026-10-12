"""Finds an anchor document (DDS/TDS/...) on a Jira step ticket. In order of preference:
a Confluence page Web link, an attached file in a supported format, or — only when the
ticket has neither — one Confluence page URL mentioned in its description or comments.

Light on purpose (only re/dataclasses/ports): the orchestrator imports this for its
"Done but no artifact" check, and the scheduled Lambda must not pull in bs4/openpyxl/
python-docx — see tests/test_orchestrator_lambda.py. Reading the content is anchors.py.
"""
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote_plus

from sdlc.ports import Attachment, TicketSystem

# file extension -> the reader format anchors.read_anchor dispatches on. No .xls/.doc:
# openpyxl and python-docx can't read the old binary formats.
SUPPORTED_ATTACHMENT_FORMATS = {"xlsx": "xlsx", "xlsm": "xlsx", "pdf": "pdf", "docx": "docx", "csv": "csv"}
_PAGE_ID_RES = (re.compile(r"/pages/(\d+)"), re.compile(r"[?&]pageId=(\d+)"))


def _page_id(url: str) -> str | None:
    return next((m.group(1) for r in _PAGE_ID_RES if (m := r.search(url))), None)


class AnchorMissing(Exception):
    def __init__(self, issue_key: str, reason: str):
        self.issue_key = issue_key
        self.reason = reason
        super().__init__(f"{issue_key}: {reason}")


@dataclass
class ConfluenceSource:
    page_id: str
    title: str
    via: str = "web link"  # or "comment/description": where on the ticket the link was found


@dataclass
class AttachmentSource:
    attachment: Attachment
    format: str


def find_anchor_sources(issue_key: str, tickets: TicketSystem) -> tuple[list, list[str]]:
    """Returns (usable sources, names/urls of artifacts we can't read)."""
    sources: list = []
    unusable: list[str] = []
    for url, title in tickets.get_remote_links(issue_key):
        page_id = _page_id(url)
        if page_id:
            sources.append(ConfluenceSource(page_id=page_id, title=title))
        else:
            unusable.append(title or url)
    for att in tickets.get_attachments(issue_key):
        fmt = SUPPORTED_ATTACHMENT_FORMATS.get(Path(att.title).suffix.lower().lstrip("."))
        if fmt:
            sources.append(AttachmentSource(attachment=att, format=fmt))
        else:
            unusable.append(att.title)
    return sources, unusable


def confluence_links_in_text(issue_key: str, tickets: TicketSystem) -> list[ConfluenceSource]:
    """The distinct Confluence pages mentioned in the ticket's description/comments (a smart
    link, a hyperlink or a bare URL — whichever way it was pasted). Only Confluence Cloud page
    URLs (".../wiki/.../pages/<id>/...") count; other links in a comment are just discussion."""
    pages: dict[str, ConfluenceSource] = {}
    for url in tickets.get_text_links(issue_key):
        page_id = _page_id(url) if "/wiki/" in url else None
        if page_id:
            slug = unquote_plus(url.split("?")[0].rstrip("/").rsplit("/", 1)[-1])
            pages.setdefault(page_id, ConfluenceSource(page_id, slug, via="comment/description"))
    return list(pages.values())


def pick_anchor_source(issue_key: str, tickets: TicketSystem):
    """The one Confluence Web link if there is exactly one, else the one supported attachment
    if there is exactly one, else (with neither) the one Confluence page mentioned in the
    description/comments. Anything else is ambiguous or empty — ask, don't guess."""
    sources, unusable = find_anchor_sources(issue_key, tickets)
    links = [s for s in sources if isinstance(s, ConfluenceSource)]
    files = [s for s in sources if isinstance(s, AttachmentSource)]
    if len(links) == 1:
        return links[0]
    if not links and len(files) == 1:
        return files[0]
    mentioned = [] if links or files else confluence_links_in_text(issue_key, tickets)
    if len(mentioned) == 1:
        return mentioned[0]
    accepted = ("a Confluence page link (Jira Web link, or pasted in a comment/description) or one "
                ".xlsx/.xlsm/.pdf/.docx/.csv attachment")
    if links:
        reason = f"found {len(links)} Confluence links ({', '.join(s.title or s.page_id for s in links)}); keep one"
    elif files:
        reason = f"found {len(files)} attachments ({', '.join(s.attachment.title for s in files)}); keep one"
    elif mentioned:
        reason = (f"found {len(mentioned)} different Confluence pages in the description/comments "
                  f"({', '.join(s.title or s.page_id for s in mentioned)}); add the right one as a Web "
                  "link to choose it")
    else:
        reason = f"nothing usable found{' (unusable: ' + ', '.join(unusable) + ')' if unusable else ''}"
    raise AnchorMissing(issue_key, f"{reason}. Accepted: {accepted}")
