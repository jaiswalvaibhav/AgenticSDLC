"""`aws-sync`: render each changed page's PDF, upload it + a .metadata.json sidecar to
S3, delete S3 objects for pages removed from the corpus, start a Knowledge Base
ingestion job, and poll it to completion. Dry-run by default.

Verified against the Bedrock KB docs (Oct 2026): StartIngestionJob(knowledgeBaseId,
dataSourceId) -> ingestionJobId; GetIngestionJob(...) -> status, COMPLETE when done.
"""
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import boto3

from sdlc.adapters.local_store import LocalObjectStore
from sdlc.pdf import render_page_pdf

POLL_INTERVAL_SECONDS = 15
POLL_TIMEOUT_SECONDS = 20 * 60


@dataclass
class AwsSyncResult:
    uploaded: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    ingestion_job_id: str | None = None
    ingestion_status: str | None = None


def _pdf_key(docs_prefix: str, space_key: str, page_id: str) -> str:
    return f"{docs_prefix}{space_key}/{page_id}.pdf"


def aws_sync(cfg: dict, *, dry_run: bool = True) -> AwsSyncResult:
    aws, data_dir = cfg["aws"], Path(cfg["data_dir"])
    region, bucket, docs_prefix = aws["region"], aws["bucket"], aws["docs_prefix"]
    use_case = cfg["use_case"]
    store = LocalObjectStore(cfg["state_dir"])
    manifest = store.get_json("manifest.json") or {}
    s3 = boto3.client("s3", region_name=region)
    result = AwsSyncResult()

    for page_id, entry in manifest.items():
        # Gate on the Confluence page version confluence_sync already tracked, not by
        # rendering every page's PDF just to hash-compare — same O(n) mistake we fixed
        # for Confluence sync itself (CLAUDE.md "Enterprise scale"). Only pages whose
        # version actually changed since the last aws-sync get rendered here.
        if entry.get("rendered_version") == entry["version"]:
            continue
        page_dir = data_dir / "corpus" / entry["path"]
        meta = json.loads((page_dir / "meta.json").read_text())
        pdf_bytes = render_page_pdf(page_dir)
        pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()

        key = _pdf_key(docs_prefix, meta["spaceKey"], page_id)
        # S3 data source metadata sidecars take flat values, not the {"value": {"type":
        # ...}} shape (that's IngestKnowledgeBaseDocuments' format, for a different, non-S3
        # ingestion path) — confirmed live: the wrapped form ingested with no error but
        # silently dropped every custom field, leaving only Bedrock's built-in `_...` ones.
        metadata = {
            "metadataAttributes": {
                "use_case": use_case,
                "space": meta["spaceKey"],
                "page_id": page_id,
                "title": meta["title"],
                "url": meta["url"],
                "version": meta["version"],
            }
        }
        if dry_run:
            print(f"[dry-run] would upload s3://{bucket}/{key} ({len(pdf_bytes)} bytes) "
                  f"+ {key}.metadata.json")
        else:
            s3.put_object(Bucket=bucket, Key=key, Body=pdf_bytes, ContentType="application/pdf")
            s3.put_object(Bucket=bucket, Key=f"{key}.metadata.json",
                           Body=json.dumps(metadata).encode(), ContentType="application/json")
            entry["pdf_hash"] = pdf_hash
            entry["s3_key"] = key
            entry["rendered_version"] = entry["version"]
        result.uploaded.append(page_id)

    # Delete S3 objects for pages no longer in the manifest (dropped by confluence_sync
    # when a page is deleted; see CLAUDE.md).
    manifest_keys = {e.get("s3_key") for e in manifest.values() if e.get("s3_key")}
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=docs_prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".metadata.json") or key in manifest_keys:
                continue
            if dry_run:
                print(f"[dry-run] would delete orphaned s3://{bucket}/{key} (+ .metadata.json)")
            else:
                s3.delete_object(Bucket=bucket, Key=key)
                s3.delete_object(Bucket=bucket, Key=f"{key}.metadata.json")
            result.deleted.append(key)

    store.put_json("manifest.json", manifest, dry_run=dry_run)

    if not result.uploaded and not result.deleted:
        print("nothing changed since the last aws-sync; skipping ingestion")
        return result

    bedrock_agent = boto3.client("bedrock-agent", region_name=region)
    if dry_run:
        print("[dry-run] would start-ingestion-job and poll until COMPLETE")
        return result

    job = bedrock_agent.start_ingestion_job(
        knowledgeBaseId=aws["knowledge_base_id"], dataSourceId=aws["data_source_id"],
    )["ingestionJob"]
    result.ingestion_job_id = job["ingestionJobId"]
    result.ingestion_status = _poll_ingestion_job(
        bedrock_agent, knowledge_base_id=aws["knowledge_base_id"],
        data_source_id=aws["data_source_id"], ingestion_job_id=job["ingestionJobId"],
    )
    return result


def _poll_ingestion_job(client, *, knowledge_base_id: str, data_source_id: str,
                         ingestion_job_id: str) -> str:
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    while True:
        job = client.get_ingestion_job(
            knowledgeBaseId=knowledge_base_id, dataSourceId=data_source_id,
            ingestionJobId=ingestion_job_id,
        )["ingestionJob"]
        status = job["status"]
        print(f"ingestion job {ingestion_job_id}: {status}")
        if status in ("COMPLETE", "FAILED"):
            return status
        if time.monotonic() > deadline:
            print(f"[warn] gave up polling after {POLL_TIMEOUT_SECONDS}s; still {status}")
            return status
        time.sleep(POLL_INTERVAL_SECONDS)
