"""Optimistic-concurrency writes (get_bytes_versioned / put_bytes_if) on every ObjectStore."""
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from sdlc.adapters.fake import FakeObjectStore
from sdlc.adapters.local_store import LocalObjectStore
from sdlc.adapters.s3_store import S3ObjectStore
from sdlc.ports import VersionConflict


@pytest.fixture(params=["local", "fake"])
def store(request, tmp_path):
    return LocalObjectStore(tmp_path) if request.param == "local" else FakeObjectStore()


def test_create_then_update_with_current_version(store):
    assert store.get_bytes_versioned("k") == (None, None)
    store.put_bytes_if("k", b"one", None, dry_run=False)
    data, version = store.get_bytes_versioned("k")
    assert data == b"one"
    store.put_bytes_if("k", b"two", version, dry_run=False)
    assert store.get_bytes("k") == b"two"


def test_create_conflicts_when_key_already_exists(store):
    store.put_bytes("k", b"one", dry_run=False)
    with pytest.raises(VersionConflict):
        store.put_bytes_if("k", b"two", None, dry_run=False)


def test_update_with_stale_version_conflicts_and_leaves_data(store):
    store.put_bytes_if("k", b"one", None, dry_run=False)
    _, stale = store.get_bytes_versioned("k")
    store.put_bytes("k", b"changed elsewhere", dry_run=False)
    with pytest.raises(VersionConflict):
        store.put_bytes_if("k", b"mine", stale, dry_run=False)
    assert store.get_bytes("k") == b"changed elsewhere"


def test_dry_run_writes_nothing(store):
    store.put_bytes_if("k", b"one", None, dry_run=True)
    assert store.get_bytes("k") is None


def _s3():
    with patch("boto3.client") as factory:
        client = MagicMock()
        factory.return_value = client
        return S3ObjectStore(bucket="b", region="ap-southeast-2", prefix="state/"), client


def _error(code):
    return ClientError({"Error": {"Code": code}}, "PutObject")


def test_s3_create_uses_if_none_match_and_update_uses_if_match():
    store, client = _s3()
    store.put_bytes_if("k", b"x", None, dry_run=False)
    client.put_object.assert_called_with(Bucket="b", Key="state/k", Body=b"x", IfNoneMatch="*")
    store.put_bytes_if("k", b"y", '"etag1"', dry_run=False)
    client.put_object.assert_called_with(Bucket="b", Key="state/k", Body=b"y", IfMatch='"etag1"')


@pytest.mark.parametrize("code", ["PreconditionFailed", "ConditionalRequestConflict"])
def test_s3_precondition_errors_become_version_conflict(code):
    store, client = _s3()
    client.put_object.side_effect = _error(code)
    with pytest.raises(VersionConflict):
        store.put_bytes_if("k", b"x", '"etag1"', dry_run=False)


def test_s3_other_errors_propagate():
    store, client = _s3()
    client.put_object.side_effect = _error("AccessDenied")
    with pytest.raises(ClientError):
        store.put_bytes_if("k", b"x", None, dry_run=False)


def test_s3_get_versioned_returns_etag_and_none_when_missing():
    import io
    store, client = _s3()
    client.get_object.return_value = {"Body": io.BytesIO(b"hi"), "ETag": '"abc"'}
    assert store.get_bytes_versioned("k") == (b"hi", '"abc"')
    client.get_object.side_effect = _error("NoSuchKey")
    assert store.get_bytes_versioned("k") == (None, None)
