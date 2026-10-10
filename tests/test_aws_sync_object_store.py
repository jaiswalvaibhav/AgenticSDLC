"""aws_sync and jira_aws_sync must read/write their manifest/trace through
wiring.object_store(cfg) — i.e. S3 under profile=aws, not an always-local
LocalObjectStore — so a profile=aws run doesn't see an empty manifest and treat
every object already in S3 as orphaned. No real AWS account needed: boto3.client
is mocked throughout."""
from unittest.mock import MagicMock, patch

from sdlc.aws_sync import aws_sync
from sdlc.jira_aws_sync import jira_aws_sync


def _aws_cfg(tmp_path, **extra_aws):
    return {
        "profile": "aws",
        "use_case": "demo_order_fulfilment",
        "data_dir": str(tmp_path / "data"),
        "aws": {
            "region": "ap-southeast-2", "bucket": "my-bucket", "docs_prefix": "confluence/",
            "jira_docs_prefix": "jira/", "state_prefix": "state/",
            "knowledge_base_id": "KBID", "data_source_id": "DSID",
            "jira_knowledge_base_id": "JKBID", "jira_data_source_id": "JDSID",
            **extra_aws,
        },
    }


def _no_contents_paginator(mock_client):
    paginator = MagicMock()
    paginator.paginate.return_value = [{}]
    mock_client.get_paginator.return_value = paginator


@patch("boto3.client")
def test_aws_sync_reads_manifest_from_s3_under_profile_aws(client_factory, tmp_path):
    (tmp_path / "data").mkdir()
    from botocore.exceptions import ClientError

    mock_client = MagicMock()
    client_factory.return_value = mock_client
    mock_client.get_object.side_effect = ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")  # empty manifest
    _no_contents_paginator(mock_client)

    aws_sync(_aws_cfg(tmp_path), dry_run=True)

    # get_object was called against the S3-backed ObjectStore (the ONLY way
    # manifest.json is read under profile=aws), proving it's not a LocalObjectStore
    # silently reading an on-disk .state/manifest.json instead.
    mock_client.get_object.assert_any_call(Bucket="my-bucket", Key="state/manifest.json")


@patch("boto3.client")
def test_jira_aws_sync_reads_trace_from_s3_under_profile_aws(client_factory, tmp_path):
    (tmp_path / "data" / "corpus_jira").mkdir(parents=True)
    mock_client = MagicMock()
    client_factory.return_value = mock_client
    from botocore.exceptions import ClientError
    mock_client.get_object.side_effect = ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
    _no_contents_paginator(mock_client)

    jira_aws_sync(_aws_cfg(tmp_path), dry_run=True)

    mock_client.get_object.assert_any_call(Bucket="my-bucket", Key="state/jira_trace.json")
    mock_client.get_object.assert_any_call(Bucket="my-bucket", Key="state/jira_upload_trace.json")
