"""Which files Bedrock's S3 data source can ingest, shared by the Jira and Confluence
downloaders and jira_aws_sync.py. Verified against the Bedrock KB docs (Oct 2026,
"Prerequisites for your knowledge base data"): .txt/.md/.html/.doc/.docx/.csv/.xls/.xlsx/.pdf
up to 50MB, plus .jpeg/.png (multimodal) up to 3.75MB. `.jpg` is the same JPEG format
under its common extension (confirmed live earlier; the docs list only `.jpeg`).
"""
from pathlib import Path

IMAGE_EXTENSIONS = {".jpeg", ".jpg", ".png"}
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | {".txt", ".md", ".html", ".doc", ".docx",
                                            ".csv", ".xls", ".xlsx", ".pdf"}
IMAGE_SIZE_LIMIT = int(3.75 * 1024 * 1024)
OTHER_SIZE_LIMIT = 50 * 1024 * 1024


def check_attachment(file_size: int, filename: str) -> tuple[str, str | None]:
    """("downloaded", None) if the Knowledge Base can ingest it, else
    ("skipped_unsupported_type" | "skipped_too_large", reason)."""
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return "skipped_unsupported_type", f"{ext or 'no extension'} isn't ingestible by the Knowledge Base"
    limit = IMAGE_SIZE_LIMIT if ext in IMAGE_EXTENSIONS else OTHER_SIZE_LIMIT
    if file_size > limit:
        return "skipped_too_large", f"{file_size} bytes exceeds the {limit}-byte limit"
    return "downloaded", None
