import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from sdlc.aws.agent_deploy import _read_agent_runtime_arn, _redacted, _runtime_env, _zip_lambda_code


def test_zip_lambda_code_excludes_heavy_modules(tmp_path):
    zip_path = tmp_path / "out.zip"
    _zip_lambda_code(zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert "sdlc/aws/orchestrator_lambda.py" in names
    assert "sdlc/sync_once.py" in names
    assert "sdlc/adapters/mock_jira.py" in names
    for heavy in ("confluence.py", "bedrock_kb.py", "pdf.py", "seed.py", "agents/analyst/engine.py"):
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


def test_lambda_handler_imports_from_the_zip_alone(tmp_path):
    """Every sdlc module the handler's import chain needs must be in the zip (third-party
    packages come from the venv here; the Lambda runtime has to supply those itself)."""
    zip_path, unpacked = tmp_path / "out.zip", tmp_path / "lambda"
    _zip_lambda_code(zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(unpacked)
    script = (f"import sys; sys.path.insert(0, {str(unpacked)!r}); import sdlc.aws.orchestrator_lambda; "
              f"bad = [m for m, mod in sys.modules.items() if m.startswith('sdlc') and "
              f"not getattr(mod, '__file__', {str(unpacked)!r}).startswith({str(unpacked)!r})]; "
              "print(','.join(bad))")
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""  # nothing came from the repo's own src/


def test_runtime_env_puts_the_aws_profile_and_jira_mode_on_the_runtime():
    cfg = {"atlassian": {"base_url": "https://x.atlassian.net", "email": "a@b.c", "api_token": "TOK"},
           "jira": {"mode": "mock", "project_key": "REAL", "mock": {"project_key": "MOCK"}}}
    env = _runtime_env(cfg)
    assert env["SDLC__PROFILE"] == "aws" and env["SDLC__JIRA__MODE"] == "mock"
    assert env["SDLC__JIRA__MOCK__PROJECT_KEY"] == "MOCK" and env["SDLC__ATLASSIAN__API_TOKEN"] == "TOK"
    cfg["atlassian"]["email"] = ""
    assert "SDLC__ATLASSIAN__EMAIL" not in _runtime_env(cfg)  # blanks aren't sent


def test_secret_env_values_are_hidden_in_printed_commands():
    cmd = ["agentcore", "launch", "--env", "SDLC__ATLASSIAN__API_TOKEN=TOK", "--env", "SDLC__PROFILE=aws"]
    assert _redacted(cmd) == ["agentcore", "launch", "--env", "SDLC__ATLASSIAN__API_TOKEN=***",
                              "--env", "SDLC__PROFILE=aws"]
