"""`jira-aws-sync`: uploads the local corpus_jira/ tree (written by jira_sync.py) to
S3, under its own prefix and its own Managed Knowledge Base — entirely separate from
the Confluence pipeline's bucket prefix/KB/ingestion. Dry-run by default. Never
called by `aws-sync`, `workflow start`, `sync-progress`/`watch-progress`, or any
orchestrator path: this pipeline is explicit-trigger-only, by the user's own
decision (see docs/DECISIONS.md "Jira downloader").

issue.md is uploaded as-is (Bedrock's S3 data source ingests .md directly — see
jira_sync.py's module docstring for why no PDF render step is needed). Each file
in a ticket's _attachments/ folder is uploaded as its own separate S3 object, so a
Knowledge Base hit on it resolves to exactly that file, not just "somewhere in
this issue" (see jira_url_from_s3_uri in jira_citations.py). Ticket discovery
walks each epic's single meta.json (see jira_sync.py — one file per epic tree,
not one per ticket), using its "path" entries to find each ticket's directory.

A separate upload trace ("jira_upload_trace.json" in the local ObjectStore, keyed
by issue key) records success/failure independently of jira_sync.py's own download
trace (jira_trace.json) — an upload can fail for reasons unrelated to the download
(a transient S3 error, say), so it needs its own retry bookkeeping. An issue is
re-uploaded when its last successful upload doesn't match jira_trace.json's
last_success_updated for that issue, or the last upload attempt failed.
"""
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import boto3

from sdlc.aws_sync import run_ingestion
from sdlc.jira_sync import TRACE_KEY as DOWNLOAD_TRACE_KEY
from sdlc.wiring import object_store

UPLOAD_TRACE_KEY = "jira_upload_trace.json"


@dataclass
class JiraAwsSyncResult:
    uploaded: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    ingestion_job_id: str | None = None
    ingestion_status: str | None = None


def _epic_dirs(corpus_jira: Path) -> list[Path]:
    return [meta.parent for meta in corpus_jira.glob("*/meta.json")]


def _ticket_files(ticket_dir: Path) -> list[Path]:
    files = [ticket_dir / "issue.md"]
    attachments_dir = ticket_dir / "_attachments"
    if attachments_dir.exists():
        files += sorted(p for p in attachments_dir.iterdir() if p.is_file())
    return files


def jira_aws_sync(cfg: dict, *, dry_run: bool = True) -> JiraAwsSyncResult:
    aws, data_dir = cfg["aws"], Path(cfg["data_dir"])
    region, bucket = aws["region"], aws["bucket"]
    jira_docs_prefix, use_case = aws["jira_docs_prefix"], cfg["use_case"]
    store = object_store(cfg)
    download_trace = store.get_json(DOWNLOAD_TRACE_KEY) or {}
    upload_trace = store.get_json(UPLOAD_TRACE_KEY) or {}
    s3 = boto3.client("s3", region_name=region)
    result = JiraAwsSyncResult()

    corpus_jira = data_dir / "corpus_jira"
    now = datetime.now(timezone.utc).isoformat()
    live_keys: set[str] = set()

    for epic_dir in _epic_dirs(corpus_jira):
        epic_meta = json.loads((epic_dir / "meta.json").read_text())
        epic_key = epic_meta["epicKey"]

        for issue_key, issue_entry in epic_meta["issues"].items():
            ticket_dir = epic_dir if issue_entry["path"] == "." else epic_dir / issue_entry["path"]
            last_success_updated = download_trace.get(issue_key, {}).get("last_success_updated")
            entry = upload_trace.get(issue_key)

            if entry and entry.get("status") == "success" and entry.get("uploaded_updated") == last_success_updated:
                live_keys.update(entry.get("s3_keys", []))
                result.unchanged.append(issue_key)
                continue

            files = _ticket_files(ticket_dir)
            keys = [f"{jira_docs_prefix}{use_case}/jira/{epic_key}/{f.relative_to(epic_dir).as_posix()}"
                    for f in files]

            try:
                if dry_run:
                    for f, key in zip(files, keys):
                        print(f"[dry-run] would upload s3://{bucket}/{key} ({f.stat().st_size} bytes)")
                else:
                    for f, key in zip(files, keys):
                        content_type = "text/markdown" if f.suffix == ".md" else None
                        extra = {"ContentType": content_type} if content_type else {}
                        s3.put_object(Bucket=bucket, Key=key, Body=f.read_bytes(), **extra)
                upload_trace[issue_key] = {"status": "success", "error": None, "last_attempt": now,
                                            "uploaded_updated": last_success_updated, "s3_keys": keys}
                live_keys.update(keys)
                result.uploaded.append(issue_key)
            except Exception as exc:  # noqa: BLE001 -- isolate per-ticket, keep going
                print(f"[error] failed to upload Jira issue {issue_key} to S3: {exc}")
                previous = upload_trace.get(issue_key, {})
                upload_trace[issue_key] = {"status": "failed", "error": str(exc), "last_attempt": now,
                                            "uploaded_updated": previous.get("uploaded_updated"),
                                            "s3_keys": previous.get("s3_keys", [])}
                live_keys.update(previous.get("s3_keys", []))
                result.failed.append(issue_key)

    # Orphan cleanup: delete S3 objects under this prefix that no longer belong to
    # any ticket currently on disk (deleted tickets, renamed/removed attachments).
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=f"{jira_docs_prefix}{use_case}/jira/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key in live_keys:
                continue
            if dry_run:
                print(f"[dry-run] would delete orphaned s3://{bucket}/{key}")
            else:
                s3.delete_object(Bucket=bucket, Key=key)
            result.deleted.append(key)

    store.put_json(UPLOAD_TRACE_KEY, upload_trace, dry_run=dry_run)

    if not result.uploaded and not result.deleted:
        print("nothing changed since the last jira-aws-sync; skipping ingestion")
        return result

    result.ingestion_job_id, result.ingestion_status = run_ingestion(
        region=region, knowledge_base_id=aws["jira_knowledge_base_id"],
        data_source_id=aws["jira_data_source_id"], dry_run=dry_run,
    )
    return result
