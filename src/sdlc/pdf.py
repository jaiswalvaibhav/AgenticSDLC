"""Renders a corpus page (page.html + _attachments/, from confluence_sync) to a
self-contained PDF, with diagrams embedded inline, for Knowledge Base ingestion. See
CLAUDE.md/BRIEF.md: there's no official Confluence Cloud PDF export API, so we render
from the same `export_view` HTML the corpus already stores, with WeasyPrint.

Needs the `weasyprint` package AND its system libraries (Pango, Cairo, GDK-Pixbuf —
`brew install pango` on macOS, `apt install libpango-1.0-0` on Debian/Ubuntu).
`weasyprint` is imported lazily here (not at module scope) so importing this module, or
anything that imports it, doesn't require those system libraries unless a PDF is
actually rendered.
"""
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

_IMG_SRC_RE = re.compile(r'(<img[^>]+src=")([^"]+)(")')


def render_page_pdf(page_dir: str | Path) -> bytes:
    import weasyprint

    page_dir = Path(page_dir)
    html = (page_dir / "page.html").read_text()
    attachments_dir = page_dir / "_attachments"
    local_names = {p.name for p in attachments_dir.glob("*")} if attachments_dir.exists() else set()

    def _rewrite(match: re.Match) -> str:
        prefix, src, suffix = match.groups()
        filename = unquote(Path(urlparse(src).path).name)
        if filename not in local_names:
            return match.group(0)  # not one of our attachments; leave as-is
        local_path = (attachments_dir / filename).resolve()
        return f"{prefix}{local_path.as_uri()}{suffix}"

    html = _IMG_SRC_RE.sub(_rewrite, html)
    return weasyprint.HTML(string=html, base_url=str(page_dir)).write_pdf()
