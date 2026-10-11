"""Finds an anchor document (DDS/TDS/...) on a Jira step ticket: a Confluence page link,
or an attached file in a supported format.

Light on purpose (only re/dataclasses/ports): the orchestrator imports this for its
"Done but no artifact" check, and the scheduled Lambda must not pull in bs4/openpyxl/
python-docx — see tests/test_orchestrator_lambda.py. Reading the content is anchors.py.
"""
import re
from dataclasses import dataclass
from pathlib import Path

from sdlc.ports import Attachment, TicketSystem

# file extension -> the reader format anchors.read_anchor dispatches on. No .xls/.doc:
# openpyxl and python-docx can't read the old binary formats.
SUPPORTED_ATTACHMENT_FORMATS = {"xlsx": "xlsx", "xlsm": "xlsx", "pdf": "pdf", "docx": "docx", "csv": "csv"}
_PAGE_ID_RES = (re.compile(r"/pages/(\d+)"), re.compile(r"[?&]pageId=(\d+)"))


class AnchorMissing(Exception):
    def __init__(self, issue_key: str, reason: str):
        self.issue_key = issue_key
        self.reason = reason
        super().__init__(f"{issue_key}: {reason}")


@dataclass
class ConfluenceSource:
    page_id: str
    title: str


@dataclass
class AttachmentSource:
    attachment: Attachment
    format: str


def find_anchor_sources(issue_key: str, tickets: TicketSystem) -> tuple[list, list[str]]:
    """Returns (usable sources, names/urls of artifacts we can't read)."""
    sources: list = []
    unusable: list[str] = []
    for url, title in tickets.get_remote_links(issue_key):
        page_id = next((m.group(1) for r in _PAGE_ID_RES if (m := r.search(url))), None)
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


def pick_anchor_source(issue_key: str, tickets: TicketSystem):
    """The one Confluence link if there is exactly one, else the one supported attachment
    if there is exactly one. Anything else is ambiguous or empty — ask, don't guess."""
    sources, unusable = find_anchor_sources(issue_key, tickets)
    links = [s for s in sources if isinstance(s, ConfluenceSource)]
    files = [s for s in sources if isinstance(s, AttachmentSource)]
    if len(links) == 1:
        return links[0]
    if not links and len(files) == 1:
        return files[0]
    accepted = "a Confluence page link or one .xlsx/.xlsm/.pdf/.docx/.csv attachment"
    if links:
        reason = f"found {len(links)} Confluence links ({', '.join(s.title or s.page_id for s in links)}); keep one"
    elif files:
        reason = f"found {len(files)} attachments ({', '.join(s.attachment.title for s in files)}); keep one"
    else:
        reason = f"nothing usable found{' (unusable: ' + ', '.join(unusable) + ')' if unusable else ''}"
    raise AnchorMissing(issue_key, f"{reason}. Accepted: {accepted}")
