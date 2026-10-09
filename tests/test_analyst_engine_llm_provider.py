"""Tests the local-profile LLM provider dispatch in engine._build_real_agent /
_build_local_llm_model: ANALYST_LLM_PROVIDER picks Anthropic, Gemini, or (default/aws
profile) Bedrock. No real network calls — typer.confirm is stubbed and only the model
object's construction is checked, never used to make a request."""
import pytest

from sdlc.agents.analyst.engine import _build_real_agent


@pytest.fixture(autouse=True)
def _no_confirm(monkeypatch):
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)


@pytest.fixture
def cfg():
    return {"profile": "local", "aws": {"llm_model_id": "fake-bedrock-id", "region": "ap-southeast-2"}}


def test_defaults_to_bedrock_when_no_provider_set(monkeypatch, cfg):
    monkeypatch.delenv("ANALYST_LLM_PROVIDER", raising=False)
    agent = _build_real_agent(knowledge_index=None, use_case="uc", cfg=cfg)
    assert agent.model.config["model_id"] == "fake-bedrock-id"


def test_aws_profile_ignores_provider_env_var(monkeypatch, cfg):
    monkeypatch.setenv("ANALYST_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    cfg = {**cfg, "profile": "aws"}
    agent = _build_real_agent(knowledge_index=None, use_case="uc", cfg=cfg)
    assert agent.model.config["model_id"] == "fake-bedrock-id"


def test_anthropic_provider_builds_anthropic_model(monkeypatch, cfg):
    monkeypatch.setenv("ANALYST_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_MODEL_ID", "claude-sonnet-5")
    agent = _build_real_agent(knowledge_index=None, use_case="uc", cfg=cfg)
    from strands.models.anthropic import AnthropicModel

    assert isinstance(agent.model, AnthropicModel)
    assert agent.model.config["model_id"] == "claude-sonnet-5"


def test_gemini_provider_builds_gemini_model_via_vertex_adc(monkeypatch, cfg):
    monkeypatch.setenv("ANALYST_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-gcp-project")
    agent = _build_real_agent(knowledge_index=None, use_case="uc", cfg=cfg)
    from strands.models.gemini import GeminiModel

    assert isinstance(agent.model, GeminiModel)
    assert agent.model.config["model_id"] == "gemini-3.8-flash"
    assert agent.model.client_args == {
        "vertexai": True, "project": "my-gcp-project", "location": "us-central1",
    }


def test_gemini_provider_respects_model_and_location_overrides(monkeypatch, cfg):
    monkeypatch.setenv("ANALYST_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-gcp-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "australia-southeast1")
    monkeypatch.setenv("GEMINI_MODEL_ID", "gemini-2.5-pro")
    agent = _build_real_agent(knowledge_index=None, use_case="uc", cfg=cfg)
    assert agent.model.config["model_id"] == "gemini-2.5-pro"
    assert agent.model.client_args["location"] == "australia-southeast1"


def test_unknown_provider_raises(monkeypatch, cfg):
    monkeypatch.setenv("ANALYST_LLM_PROVIDER", "openai")
    with pytest.raises(ValueError, match="Unknown ANALYST_LLM_PROVIDER"):
        _build_real_agent(knowledge_index=None, use_case="uc", cfg=cfg)
