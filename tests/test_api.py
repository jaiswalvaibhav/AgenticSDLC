import pytest
from fastapi.testclient import TestClient

from sdlc_api.app import create_app
from sdlc.workflow.registry import WorkflowRegistry


@pytest.fixture
def cfg():
    return {"profile": "local", "use_case": "demo_order_fulfilment",
            "atlassian": {"base_url": "https://x.atlassian.net", "email": "a@b.com",
                          "api_token": "SECRET-TOKEN"},
            "jira": {"project_key": "DEMO"}, "aws": {"agent_runtime_arn": ""}}


@pytest.fixture
def client(cfg):
    return TestClient(create_app(cfg=cfg, registry=WorkflowRegistry.load()))


def test_meta_lists_use_cases_and_never_leaks_secrets(client, monkeypatch):
    monkeypatch.delenv("EXTERNAL_LLM_PROVIDER", raising=False)
    response = client.get("/api/meta")

    assert "SECRET-TOKEN" not in response.text and "a@b.com" not in response.text
    assert response.json() == {
        "use_cases": ["demo_order_fulfilment"], "default_use_case": "demo_order_fulfilment",
        "profiles": ["local"], "default_profile": "local", "external_llm_provider": None,
        "jira_project_key": "DEMO", "jira_base_url": "https://x.atlassian.net"}


def test_meta_offers_aws_only_once_the_agent_runtime_is_deployed(cfg, monkeypatch):
    monkeypatch.setenv("EXTERNAL_LLM_PROVIDER", "anthropic")
    cfg["aws"]["agent_runtime_arn"] = "arn:aws:bedrock-agentcore:ap-southeast-2:1:runtime/x"
    body = TestClient(create_app(cfg=cfg, registry=WorkflowRegistry.load())).get("/api/meta").json()
    assert body["profiles"] == ["local", "aws"] and body["external_llm_provider"] == "anthropic"


def test_workflow_lists_every_step_with_its_inputs(client):
    steps = client.get("/api/workflow").json()["steps"]
    by_id = {s["id"]: s for s in steps}
    assert len(steps) == len(WorkflowRegistry.load().all_steps())
    assert by_id["develop_solution_requirements"] == {
        "id": "develop_solution_requirements", "artifact": "Develop Solution Requirements",
        "owner": "analyst", "inputs": ["data_design_solution", "technical_design_solution"],
        "automation": "analyst.solution_requirements", "optional": False}


def test_preview_returns_generate_and_provide_with_direct_inputs(client):
    body = client.post("/api/preview", json={"targets": ["develop_solution_requirements"]}).json()
    assert [s["id"] for s in body["generate"]] == ["develop_solution_requirements"]
    assert {s["id"] for s in body["provide"]} == {"data_design_solution", "technical_design_solution"}


def test_preview_rejects_empty_and_unknown_targets(client):
    empty = client.post("/api/preview", json={"targets": []})
    assert empty.status_code == 400 and "no steps selected" in empty.json()["detail"]
    unknown = client.post("/api/preview", json={"targets": ["nope"]})
    assert unknown.status_code == 400 and "unknown step" in unknown.json()["detail"]
