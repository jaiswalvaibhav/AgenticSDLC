from pathlib import Path

from sdlc.adapters.local_store import LocalObjectStore
from sdlc.adapters.s3_store import S3ObjectStore
from sdlc.wiring import object_store

LOCAL_CFG = {"profile": "local", "state_dir": ".state"}
AWS_CFG = {"profile": "aws", "aws": {"bucket": "my-bucket", "region": "ap-southeast-2",
                                       "state_prefix": "state/"}}


def test_local_profile_uses_local_store():
    store = object_store(LOCAL_CFG)
    assert isinstance(store, LocalObjectStore)
    assert store.root == Path(LOCAL_CFG["state_dir"])


def test_aws_profile_uses_s3_store():
    store = object_store(AWS_CFG)
    assert isinstance(store, S3ObjectStore)
    assert store.bucket == "my-bucket"
    assert store.prefix == "state/"


def test_s3_store_key_prefixing():
    store = S3ObjectStore(bucket="b", region="ap-southeast-2", prefix="state/")
    assert store._key("manifest.json") == "state/manifest.json"
