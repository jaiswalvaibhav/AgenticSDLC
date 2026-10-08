"""Mocked boto3 unit tests for S3ObjectStore: no real AWS account needed."""
import io
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from sdlc.adapters.s3_store import S3ObjectStore


def _store():
    with patch("boto3.client") as client_factory:
        mock_client = MagicMock()
        client_factory.return_value = mock_client
        store = S3ObjectStore(bucket="my-bucket", region="ap-southeast-2", prefix="state/")
        return store, mock_client


def test_get_json_reads_and_decodes_with_prefix():
    store, mock_client = _store()
    mock_client.get_object.return_value = {"Body": io.BytesIO(b'{"a": 1}')}

    result = store.get_json("manifest.json")

    assert result == {"a": 1}
    mock_client.get_object.assert_called_once_with(Bucket="my-bucket", Key="state/manifest.json")


def test_get_json_returns_none_on_no_such_key():
    store, mock_client = _store()
    mock_client.get_object.side_effect = ClientError(
        {"Error": {"Code": "NoSuchKey"}}, "GetObject")

    assert store.get_json("missing.json") is None


def test_put_json_dry_run_does_not_call_s3():
    store, mock_client = _store()
    store.put_json("x.json", {"a": 1}, dry_run=True)
    mock_client.put_object.assert_not_called()


def test_put_json_writes_prefixed_key():
    store, mock_client = _store()
    store.put_json("x.json", {"a": 1}, dry_run=False)
    args, kwargs = mock_client.put_object.call_args
    assert kwargs["Bucket"] == "my-bucket" and kwargs["Key"] == "state/x.json"
    assert kwargs["Body"] == b'{\n  "a": 1\n}'


def test_delete_dry_run_does_not_call_s3():
    store, mock_client = _store()
    store.delete("x.json", dry_run=True)
    mock_client.delete_object.assert_not_called()
