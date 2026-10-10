"""Mocked boto3 unit tests for jira_aws_sync: no real AWS account needed. Exercises
the upload trace (success/failure/retry) against a corpus_jira/ tree written by
jira_sync.py, independently of the Confluence aws_sync pipeline."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from sdlc.jira_aws_sync import UPLOAD_TRACE_KEY, jira_aws_sync
from sdlc.jira_sync import TRACE_KEY as DOWNLOAD_TRACE_KEY


def _cfg(tmp_path: Path) -> dict:
    (tmp_path / "state").mkdir()
    (tmp_path / "data").mkdir()
    return {
        "profile": "local",
        "use_case": "demo_order_fulfilment",
        "state_dir": str(tmp_path / "state"),
        "data_dir": str(tmp_path / "data"),
        "aws": {
            "region": "ap-southeast-2", "bucket": "my-bucket", "jira_docs_prefix": "jira/",
            "jira_knowledge_base_id": "KBID", "jira_data_source_id": "DSID",
        },
    }


def _write_ticket(cfg: dict, epic: str, updated: str = "2026-01-01T00:00:00+0000"):
    ticket_dir = Path(cfg["data_dir"]) / "corpus_jira" / epic
    ticket_dir.mkdir(parents=True, exist_ok=True)
    (ticket_dir / "issue.md").write_text("# Delivery\n")
    (ticket_dir / "meta.json").write_text(json.dumps({"epicKey": epic, "issues": {
        epic: {"issueType": "Epic", "status": "To Do", "updated": updated,
               "summary": "Delivery", "attachments": [], "path": "."},
    }}))
    from sdlc.adapters.local_store import LocalObjectStore
    store = LocalObjectStore(cfg["state_dir"])
    store.put_json(DOWNLOAD_TRACE_KEY, {epic: {"status": "success", "last_success_updated": updated}},
                    dry_run=False)


def _no_contents_paginator(mock_client):
    mock_client.get_paginator.return_value.paginate.return_value = [{"Contents": []}]


def test_dry_run_uploads_nothing(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _write_ticket(cfg, "DEMO-1")
    with patch("boto3.client") as client_factory:
        mock_client = MagicMock()
        client_factory.return_value = mock_client
        _no_contents_paginator(mock_client)

        jira_aws_sync(cfg, dry_run=True)

        mock_client.put_object.assert_not_called()


def test_uploads_issue_md_and_records_success_in_trace(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _write_ticket(cfg, "DEMO-1")
    with patch("boto3.client") as client_factory:
        mock_client = MagicMock()
        client_factory.return_value = mock_client
        _no_contents_paginator(mock_client)
        mock_client.start_ingestion_job.return_value = {"ingestionJob": {"ingestionJobId": "job-1"}}
        mock_client.get_ingestion_job.return_value = {"ingestionJob": {"status": "COMPLETE"}}

        result = jira_aws_sync(cfg, dry_run=False)

        assert result.uploaded == ["DEMO-1"]
        put_keys = [kwargs["Key"] for _, kwargs in mock_client.put_object.call_args_list]
        assert "jira/demo_order_fulfilment/jira/DEMO-1/issue.md" in put_keys
        mock_client.start_ingestion_job.assert_called_once_with(
            knowledgeBaseId="KBID", dataSourceId="DSID")

        from sdlc.adapters.local_store import LocalObjectStore
        trace = LocalObjectStore(cfg["state_dir"]).get_json(UPLOAD_TRACE_KEY)
        assert trace["DEMO-1"]["status"] == "success"


def test_second_run_with_no_changes_skips_upload(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _write_ticket(cfg, "DEMO-1")
    with patch("boto3.client") as client_factory:
        mock_client = MagicMock()
        client_factory.return_value = mock_client
        _no_contents_paginator(mock_client)
        mock_client.start_ingestion_job.return_value = {"ingestionJob": {"ingestionJobId": "job-1"}}
        mock_client.get_ingestion_job.return_value = {"ingestionJob": {"status": "COMPLETE"}}
        jira_aws_sync(cfg, dry_run=False)
        mock_client.put_object.reset_mock()

        result = jira_aws_sync(cfg, dry_run=False)

        assert result.uploaded == []
        assert result.unchanged == ["DEMO-1"]
        mock_client.put_object.assert_not_called()


def test_upload_failure_is_retried_next_run(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _write_ticket(cfg, "DEMO-1")
    with patch("boto3.client") as client_factory:
        mock_client = MagicMock()
        client_factory.return_value = mock_client
        _no_contents_paginator(mock_client)
        mock_client.put_object.side_effect = RuntimeError("S3 error")

        result = jira_aws_sync(cfg, dry_run=False)
        assert result.failed == ["DEMO-1"]

        mock_client.put_object.side_effect = None
        mock_client.start_ingestion_job.return_value = {"ingestionJob": {"ingestionJobId": "job-1"}}
        mock_client.get_ingestion_job.return_value = {"ingestionJob": {"status": "COMPLETE"}}
        result = jira_aws_sync(cfg, dry_run=False)
        assert result.uploaded == ["DEMO-1"]
