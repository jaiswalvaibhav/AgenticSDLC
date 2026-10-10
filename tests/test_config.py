"""Tests save_aws_values: it must write only the given aws.* keys back into
config.yaml, re-reading the raw file rather than re-dumping load_config()'s
env-merged result — which can carry secrets from .env overrides (see
src/sdlc/aws/{deploy,jira_kb_deploy,agent_deploy}.py, which all call this instead
of yaml.safe_dump(cfg, ...) for exactly this reason)."""
from pathlib import Path

import yaml

from sdlc.config import load_config, save_aws_values


def _write_config(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({
        "profile": "local",
        "atlassian": {"base_url": "https://example.atlassian.net", "email": "", "api_token": ""},
        "aws": {"region": "ap-southeast-2", "bucket": "my-bucket", "knowledge_base_id": ""},
    }))
    return path


def test_save_aws_values_only_touches_aws_keys(tmp_path):
    path = _write_config(tmp_path)

    save_aws_values({"knowledge_base_id": "KBID", "data_source_id": "DSID"}, path)

    raw = yaml.safe_load(path.read_text())
    assert raw["aws"]["knowledge_base_id"] == "KBID"
    assert raw["aws"]["data_source_id"] == "DSID"
    assert raw["aws"]["bucket"] == "my-bucket"  # untouched keys survive
    assert raw["atlassian"]["base_url"] == "https://example.atlassian.net"


def test_save_aws_values_never_writes_env_merged_secrets(tmp_path, monkeypatch):
    """load_config()'s merged result (what used to be re-dumped) includes any
    SDLC__* env override, e.g. a real Atlassian API token from .env — that must
    never end up written into the tracked config.yaml."""
    path = _write_config(tmp_path)
    monkeypatch.setenv("SDLC__ATLASSIAN__API_TOKEN", "super-secret-token")
    monkeypatch.setenv("SDLC__ATLASSIAN__EMAIL", "real-email@example.com")

    cfg = load_config(path)  # merged cfg now carries the "secret" env override
    assert cfg["atlassian"]["api_token"] == "super-secret-token"  # sanity check

    save_aws_values({"knowledge_base_id": "KBID"}, path)

    raw = yaml.safe_load(path.read_text())
    assert raw["atlassian"]["api_token"] == ""  # not the env-merged value
    assert raw["atlassian"]["email"] == ""
    assert raw["aws"]["knowledge_base_id"] == "KBID"
