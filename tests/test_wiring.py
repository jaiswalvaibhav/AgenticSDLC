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


def test_kb_enabled_needs_the_flag_and_a_knowledge_base_id():
    from sdlc.wiring import kb_enabled
    assert kb_enabled({"aws": {"knowledge_base_id": "KB1"}}) == (True, "")  # flag defaults to on
    assert kb_enabled({"analyst": {"use_knowledge_base": True}, "aws": {"knowledge_base_id": "KB1"}})[0]
    off, why = kb_enabled({"analyst": {"use_knowledge_base": False}, "aws": {"knowledge_base_id": "KB1"}})
    assert not off and "use_knowledge_base" in why
    off, why = kb_enabled({"aws": {"knowledge_base_id": ""}})
    assert not off and "knowledge_base_id" in why
