import pytest
from fastapi.testclient import TestClient

from sdlc.adapters.fake import FakeObjectStore, FakeTicketSystem
from sdlc.ports import Attachment
from sdlc.workflow.registry import WorkflowRegistry
from sdlc_api.app import create_app

UC = "demo_order_fulfilment"


@pytest.fixture
def cfg():
    return {"profile": "local", "use_case": "demo_order_fulfilment",
            "atlassian": {"base_url": "https://x.atlassian.net", "email": "a@b.com",
                          "api_token": "SECRET-TOKEN"},
            "jira": {"project_key": "DEMO", "approval_label": "sdlc-approved",
                     "components": {"analyst": None, "engineer": "Engineering", "tester": "Testing"},
                     "assignees": {"analyst": None, "engineer": None, "tester": None}},
            "aws": {"agent_runtime_arn": ""}}


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


# ------------------------------------------------------------------ runs + progress --
@pytest.fixture
def tickets():
    return FakeTicketSystem()


@pytest.fixture
def store():
    return FakeObjectStore()


@pytest.fixture
def live(cfg, tickets, store, monkeypatch):
    """A client wired to fakes — nothing here can reach real Jira or AWS."""
    monkeypatch.delenv("EXTERNAL_LLM_PROVIDER", raising=False)
    return TestClient(create_app(cfg=cfg, registry=WorkflowRegistry.load(), tickets=tickets, store=store))


def _run(client, **overrides):
    body = {"use_case": UC, "targets": ["develop_solution_requirements"], "profile": "local", **overrides}
    return client.post("/api/runs", json=body)


def test_run_creates_tickets_for_targets_and_their_direct_inputs(live, tickets):
    response = _run(live)

    assert response.status_code == 200
    body = response.json()
    assert set(body["steps"]) == {"develop_solution_requirements", "data_design_solution",
                                   "technical_design_solution"}
    assert body["generate"] == ["develop_solution_requirements"]
    assert body["provide"] == ["data_design_solution", "technical_design_solution"]
    assert len(tickets.issues) == 3 + 1  # + the epic


@pytest.mark.parametrize("overrides,detail", [
    ({"use_case": "nope"}, "unknown use case"),
    ({"profile": "aws"}, "not available"),
    ({"targets": []}, "no steps selected"),
    ({"targets": ["nope"]}, "unknown step"),
])
def test_run_rejects_bad_requests_without_touching_jira(live, tickets, overrides, detail):
    response = _run(live, **overrides)
    assert response.status_code == 400 and detail in response.json()["detail"]
    assert tickets.issues == {}


def test_run_requires_one_time_confirmation_when_an_external_llm_is_configured(live, tickets, monkeypatch):
    monkeypatch.setenv("EXTERNAL_LLM_PROVIDER", "anthropic")

    refused = _run(live)
    assert refused.status_code == 400 and "confirm" in refused.json()["detail"]
    assert tickets.issues == {}

    assert _run(live, confirm_external_llm=True).status_code == 200


def test_progress_shows_roles_and_missing_anchor_reasons(live):
    _run(live)

    rows = {r["step_id"]: r for r in live.get("/api/runs/demo_order_fulfilment/progress").json()["steps"]}

    assert list(rows) == ["data_design_solution", "technical_design_solution",
                          "develop_solution_requirements"]  # workflow order
    assert [r["role"] for r in rows.values()] == ["provide", "provide", "automated"]
    dds = rows["data_design_solution"]
    assert dds["status"] == "To Do" and dds["done"] is False
    assert dds["jira_url"] == f"https://x.atlassian.net/browse/{dds['key']}"
    assert dds["anchor"]["ok"] is False and "nothing usable" in dds["anchor"]["reason"]
    assert rows["develop_solution_requirements"]["anchor"] is None  # feeds no automated step
    assert rows["develop_solution_requirements"]["plan_ready"] is False


def test_progress_reflects_uploaded_artifacts_run_state_and_comments(live, tickets, store):
    steps = _run(live).json()["steps"]
    dds, tds, plan = steps["data_design_solution"], steps["technical_design_solution"], steps["develop_solution_requirements"]
    tickets.attachments[dds] = [Attachment("a1", "DDS_v3.xlsx", "x/y", "https://dl/a1")]
    tickets.add_remote_link(tds, "https://x.atlassian.net/wiki/spaces/S/pages/42/TDS", "TDS", dry_run=False)
    tickets.transition_issue(dds, "Done", dry_run=False)
    tickets.issues[plan].comments = ["c1", "c2", "c3", "c4"]
    store.put_json(f"workflow/{UC}/run_state.json", {"develop_solution_requirements": "awaiting_approval"},
                   dry_run=False)
    store.put_json(f"workflow/{UC}/plan.json", {"requirements": []}, dry_run=False)

    rows = {r["step_id"]: r for r in live.post(f"/api/runs/{UC}/refresh").json()["steps"]}

    assert rows["data_design_solution"]["done"] is True
    assert rows["data_design_solution"]["files"] == ["DDS_v3.xlsx"]
    assert rows["data_design_solution"]["anchor"] == {"ok": True, "kind": "file", "name": "DDS_v3.xlsx"}
    assert rows["technical_design_solution"]["anchor"] == {"ok": True, "kind": "link", "name": "TDS"}
    assert rows["technical_design_solution"]["links"] == [
        {"url": "https://x.atlassian.net/wiki/spaces/S/pages/42/TDS", "title": "TDS"}]
    automated = rows["develop_solution_requirements"]
    assert automated["run_state"] == "awaiting_approval" and automated["plan_ready"] is True
    assert automated["recent_comments"] == ["c2", "c3", "c4"]


def test_progress_is_cached_until_refreshed(live, tickets):
    steps = _run(live).json()["steps"]
    first = live.get(f"/api/runs/{UC}/progress").json()

    tickets.transition_issue(steps["data_design_solution"], "Done", dry_run=False)

    assert live.get(f"/api/runs/{UC}/progress").json() == first  # no new Jira reads
    refreshed = {r["step_id"]: r for r in live.post(f"/api/runs/{UC}/refresh").json()["steps"]}
    assert refreshed["data_design_solution"]["done"] is True


def test_progress_before_any_run_is_empty_and_unknown_use_case_404s(live):
    assert live.get(f"/api/runs/{UC}/progress").json()["steps"] == []
    assert live.get("/api/runs/nope/progress").status_code == 404


def test_later_run_keeps_earlier_roles(live):
    _run(live)
    _run(live, targets=["data_contract"])  # DDS is already a provide step; data_contract is a new generate

    rows = {r["step_id"]: r["role"] for r in live.post(f"/api/runs/{UC}/refresh").json()["steps"]}

    assert rows["develop_solution_requirements"] == "automated"
    assert rows["data_design_solution"] == "provide"
    assert rows["data_contract"] == "generate"
