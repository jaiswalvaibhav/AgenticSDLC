import zipfile
from pathlib import Path

import pytest

from sdlc.aws.agent_deploy import _read_agent_runtime_arn, _zip_lambda_code


def test_zip_lambda_code_excludes_heavy_modules(tmp_path):
    zip_path = tmp_path / "out.zip"
    _zip_lambda_code(zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert "sdlc/aws/orchestrator_lambda.py" in names
    assert "sdlc/sync_once.py" in names
    for heavy in ("confluence.py", "bedrock_kb.py", "pdf.py", "seed.py", "agents/"):
        assert not any(heavy in n for n in names), f"{heavy} should not be in the Lambda zip"


def test_read_agent_runtime_arn_from_known_key(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path(".bedrock_agentcore.yaml").write_text(
        "agent_runtime_arn: arn:aws:bedrock-agentcore:ap-southeast-2:123456789012:runtime/abc123\n")
    assert _read_agent_runtime_arn() == "arn:aws:bedrock-agentcore:ap-southeast-2:123456789012:runtime/abc123"


def test_read_agent_runtime_arn_falls_back_to_regex(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path(".bedrock_agentcore.yaml").write_text(
        "some_other_key: whatever\n"
        "nested:\n  deep_arn: arn:aws:bedrock-agentcore:ap-southeast-2:123456789012:runtime/xyz\n")
    assert _read_agent_runtime_arn() == "arn:aws:bedrock-agentcore:ap-southeast-2:123456789012:runtime/xyz"


def test_read_agent_runtime_arn_raises_when_missing_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError, match="not found"):
        _read_agent_runtime_arn()
