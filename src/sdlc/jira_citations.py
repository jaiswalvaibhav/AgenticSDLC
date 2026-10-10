"""Turns a Jira KB hit's s3_uri back into a Jira browse URL.

The Confluence KB's Chunk.page_url/page_title rely on the S3 .metadata.json
sidecar, which is confirmed non-functional in this account (bedrock_kb.py's own
docstring: numberOfMetadataDocumentsScanned stays 0). What IS reliable is
location.s3Location.uri, which Retrieve always returns regardless of that sidecar
issue, and already flows into Chunk.s3_uri. jira_aws_sync.py encodes the issue key
in the S3 key path itself (.../jira/<epicKey>/[.../<storyKey>/[.../<subtaskKey>/]]
...), so the deepest ticket key segment can be read straight back out of it.
"""
import re

_KEY_SEGMENT_RE = re.compile(r"/jira/((?:[A-Z][A-Z0-9]*-\d+/)*[A-Z][A-Z0-9]*-\d+)/")


def jira_url_from_s3_uri(s3_uri: str, base_url: str) -> str | None:
    match = _KEY_SEGMENT_RE.search(s3_uri)
    if not match:
        return None
    deepest_key = match.group(1).rstrip("/").rsplit("/", 1)[-1]
    return f"{base_url.rstrip('/')}/browse/{deepest_key}"
