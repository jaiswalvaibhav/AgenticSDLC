import threading
import time

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
            "jira": {"project_key": "DEMO", "approval_label": "sdlc-approved", "reject_label": "sdlc-rejected",
                     "components": {"analyst": None, "engineer": "Engineering", "tester": "Testing"},
                     "assignees": {"analyst": None, "engineer": None, "tester": None}},
            "aws": {"agent_runtime_arn": ""}}


@pytest.fixture
def client(cfg):
    return TestClient(create_app(cfg=cfg, registry=WorkflowRegistry.load(), store=FakeObjectStore()))


def test_meta_lists_use_cases_and_never_leaks_secrets(client, monkeypatch):
    monkeypatch.delenv("EXTERNAL_LLM_PROVIDER", raising=False)
    response = client.get("/api/meta")

    assert "SECRET-TOKEN" not in response.text and "a@b.com" not in response.text
    assert response.json() == {
        "use_cases": ["demo_order_fulfilment"], "default_use_case": "demo_order_fulfilment",
        "profiles": ["local"], "default_profile": "local", "external_llm_provider": None,
        "auto_confirm_external_llm": False, "jira_project_key": "DEMO", "jira_base_url": "https://x.atlassian.net"}


def test_meta_offers_aws_only_once_the_agent_runtime_is_deployed(cfg, monkeypatch):
    monkeypatch.setenv("EXTERNAL_LLM_PROVIDER", "anthropic")
    cfg["aws"]["agent_runtime_arn"] = "arn:aws:bedrock-agentcore:ap-southeast-2:1:runtime/x"
    app = create_app(cfg=cfg, registry=WorkflowRegistry.load(), store=FakeObjectStore())
    body = TestClient(app).get("/api/meta").json()
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
def gate():
    return threading.Event()


@pytest.fixture
def live(cfg, tickets, store, gate, monkeypatch):
    """A client wired to fakes — nothing here can reach real Jira or AWS. A local run starts
    the poller; its sync is a stub that blocks until teardown, so no cycle's snapshot
    rebuild races the assertions below."""
    monkeypatch.delenv("EXTERNAL_LLM_PROVIDER", raising=False)
    app = create_app(cfg=cfg, registry=WorkflowRegistry.load(), tickets=tickets, store=store,
                     sync=lambda c, dry_run: gate.wait(5) and {"status_events": 0})
    yield TestClient(app)
    gate.set()
    app.state.poller.shutdown(wait=2)


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
    assert rows["technical_design_solution"]["anchor"] == {
        "ok": True, "kind": "link", "name": "TDS", "via": "web link"}
    assert rows["technical_design_solution"]["links"] == [
        {"url": "https://x.atlassian.net/wiki/spaces/S/pages/42/TDS", "title": "TDS"}]
    automated = rows["develop_solution_requirements"]
    assert automated["run_state"] == "awaiting_approval" and automated["plan_ready"] is True
    assert automated["recent_comments"] == ["c2", "c3", "c4"]


def test_progress_shows_where_a_comment_link_was_found(live, tickets):
    steps = _run(live).json()["steps"]
    tickets.text_links[steps["technical_design_solution"]] = [
        "https://x.atlassian.net/wiki/spaces/S/pages/42/9.+Technical+Design+Solution"]

    rows = {r["step_id"]: r for r in live.post(f"/api/runs/{UC}/refresh").json()["steps"]}

    assert rows["technical_design_solution"]["anchor"] == {
        "ok": True, "kind": "link", "name": "9. Technical Design Solution", "via": "comment/description"}


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


# ------------------------------------------------------------------------ poller --
def _wait(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def test_local_run_starts_the_poller_for_that_use_case(live):
    body = _run(live).json()

    assert body["poller"]["active"] is True and body["poller"]["use_case"] == UC
    assert body["poller"]["profile"] == "local"
    assert live.get("/api/poller").json()["interval_seconds"] == 30
    assert live.get(f"/api/runs/{UC}/progress").json()["poller"]["active"] is True


def test_poller_can_be_stopped_started_and_synced_now(live):
    _run(live)
    assert live.post("/api/poller/sync-now").status_code == 200

    stopped = live.post("/api/poller/stop").json()
    assert stopped["active"] is False and stopped["use_case"] is None
    assert live.post("/api/poller/sync-now").status_code == 409

    restarted = live.post("/api/poller/start", json={"use_case": UC}).json()
    assert restarted["active"] is True


def test_poller_start_validates_like_a_run(live, monkeypatch):
    assert live.post("/api/poller/start", json={"use_case": "nope"}).status_code == 400
    assert live.post("/api/poller/start", json={"use_case": UC, "profile": "aws"}).status_code == 400

    monkeypatch.setenv("EXTERNAL_LLM_PROVIDER", "gemini")
    refused = live.post("/api/poller/start", json={"use_case": UC})
    assert refused.status_code == 400 and "confirm" in refused.json()["detail"]
    assert live.post("/api/poller/start", json={"use_case": UC, "confirm_external_llm": True}).status_code == 200


def test_aws_run_does_not_start_a_poller(cfg, tickets, store, monkeypatch):
    monkeypatch.delenv("EXTERNAL_LLM_PROVIDER", raising=False)
    cfg["aws"]["agent_runtime_arn"] = "arn:aws:bedrock-agentcore:ap-southeast-2:1:runtime/x"
    client = TestClient(create_app(cfg=cfg, registry=WorkflowRegistry.load(), tickets=tickets, store=store,
                                   sync=lambda c, dry_run: pytest.fail("the Lambda polls aws, not the UI")))

    body = _run(client, profile="aws").json()

    assert body["poller"]["active"] is False


def test_poller_cycles_sync_with_the_runs_cfg_and_rebuild_the_snapshot(cfg, tickets, store, monkeypatch):
    monkeypatch.delenv("EXTERNAL_LLM_PROVIDER", raising=False)
    cfg["ui"] = {"poll_interval_seconds": 0.01}
    calls = []
    app = create_app(cfg=cfg, registry=WorkflowRegistry.load(), tickets=tickets, store=store,
                     sync=lambda c, dry_run: calls.append((c["use_case"], c["profile"],
                                                           c["external_llm_confirmed"], dry_run)) or {})
    with TestClient(app) as client:
        steps = _run(client, confirm_external_llm=True).json()["steps"]
        assert _wait(lambda: client.get("/api/poller").json()["cycles"] >= 2)
        tickets.transition_issue(steps["data_design_solution"], "Done", dry_run=False)
        # no manual refresh: a poller cycle rebuilds the cached snapshot
        assert _wait(lambda: any(r["done"] for r in client.get(f"/api/runs/{UC}/progress").json()["steps"]))
    assert calls[0] == (UC, "local", True, False)  # live (dry_run=False), with the one-time consent


def test_backend_restart_resumes_a_running_poller_but_not_a_stopped_one(cfg, tickets, store, monkeypatch):
    monkeypatch.delenv("EXTERNAL_LLM_PROVIDER", raising=False)
    cfg["ui"] = {"poll_interval_seconds": 3600}

    def boot():
        return create_app(cfg=cfg, registry=WorkflowRegistry.load(), tickets=tickets, store=store,
                          sync=lambda c, dry_run: {})

    with TestClient(boot()) as first:
        _run(first)
    with TestClient(boot()) as second:  # server shut down while polling -> resumes
        assert second.get("/api/poller").json()["active"] is True
        second.post("/api/poller/stop")
    with TestClient(boot()) as third:  # the user stopped it -> stays stopped
        assert third.get("/api/poller").json()["active"] is False


# ----------------------------------------------------------- saved run: info + reset --
def test_info_labels_the_saved_run_with_its_project_and_start_time(live):
    assert live.get(f"/api/runs/{UC}/info").json() == {"exists": False, "current_project_key": "DEMO"}

    _run(live)
    info = live.get(f"/api/runs/{UC}/info").json()

    assert info["exists"] is True and info["project_key"] == "DEMO" and info["stale"] is False
    assert info["ticket_count"] == 3 and info["started_at"]
    assert live.get("/api/runs/nope/info").status_code == 404


def test_info_flags_a_run_saved_in_another_project_and_infers_it_for_old_state(live, store):
    # state saved before runs recorded their project: only steps.json, keyed by the old project
    store.put_json(f"workflow/{UC}/steps.json", {"data_design_solution": "SCRUM-6"}, dry_run=False)

    info = live.get(f"/api/runs/{UC}/info").json()

    assert info["project_key"] == "SCRUM" and info["current_project_key"] == "DEMO"
    assert info["stale"] is True and info["started_at"] is None


def test_reset_forgets_the_run_but_leaves_jira_alone_and_stops_the_poller(live, tickets, store):
    _run(live)
    store.put_json(f"workflow/{UC}/run_state.json", {"develop_solution_requirements": "applied"}, dry_run=False)
    store.put_json(f"workflow/{UC}/plan.json", {"requirements": []}, dry_run=False)
    store.put_json(f"workflow/{UC}/traceability.json", {"records": []}, dry_run=False)
    jira_tickets = set(tickets.issues)

    response = live.post(f"/api/runs/{UC}/reset").json()

    assert response["reset"] is True and response["poller"]["active"] is False
    assert set(tickets.issues) == jira_tickets  # nothing deleted in Jira
    for key in ("steps", "run_state", "plan", "traceability"):
        assert store.get_json(f"workflow/{UC}/{key}.json") is None
    assert live.get(f"/api/runs/{UC}/info").json()["exists"] is False
    assert live.get(f"/api/runs/{UC}/progress").json()["steps"] == []
    assert live.post("/api/runs/nope/reset").status_code == 404


def test_trigger_after_reset_readopts_the_same_projects_tickets_without_duplicates(live, tickets):
    first = _run(live).json()["steps"]
    live.post(f"/api/runs/{UC}/reset")

    again = _run(live).json()["steps"]

    assert again == first
    assert len(tickets.issues) == 3 + 1  # nothing was created twice


def test_reset_leaves_a_poller_running_for_another_use_case_alone(live):
    live.app.state.poller.start("other_use_case", "local")

    live.post(f"/api/runs/{UC}/reset")

    assert live.get("/api/poller").json()["use_case"] == "other_use_case"


# ------------------------------------------------ auto-confirm + LLM usage in progress --
def test_auto_confirm_in_config_removes_the_need_for_per_trigger_consent(cfg, tickets, store, gate, monkeypatch):
    monkeypatch.setenv("EXTERNAL_LLM_PROVIDER", "anthropic")
    cfg["analyst"] = {"auto_confirm_external_llm": True}
    app = create_app(cfg=cfg, registry=WorkflowRegistry.load(), tickets=tickets, store=store,
                     sync=lambda c, dry_run: gate.wait(5) and {})
    client = TestClient(app)
    try:
        assert client.get("/api/meta").json()["auto_confirm_external_llm"] is True
        assert _run(client).status_code == 200  # no confirm_external_llm needed
        assert client.post("/api/poller/start", json={"use_case": UC}).status_code == 200
    finally:
        gate.set()
        app.state.poller.shutdown(wait=2)


def test_progress_shows_how_many_times_the_llm_was_called_and_reset_clears_it(live, store):
    _run(live)
    store.put_json(f"workflow/{UC}/llm_usage.json",
                   {"develop_solution_requirements": {"calls": 8, "runs": 2}}, dry_run=False)

    rows = {r["step_id"]: r for r in live.post(f"/api/runs/{UC}/refresh").json()["steps"]}

    assert rows["develop_solution_requirements"]["llm_usage"] == {"calls": 8, "runs": 2}
    assert rows["data_design_solution"]["llm_usage"] is None

    live.post(f"/api/runs/{UC}/reset")
    assert store.get_json(f"workflow/{UC}/llm_usage.json") is None


# --------------------------------------------------------------- approve / reject from the UI --
def _awaiting(live, store):
    steps = _run(live).json()["steps"]
    store.put_json(f"workflow/{UC}/run_state.json", {"develop_solution_requirements": "awaiting_approval"},
                   dry_run=False)
    return steps["develop_solution_requirements"]


def test_approve_adds_the_approval_label_and_wakes_the_poller(live, tickets, store):
    key = _awaiting(live, store)

    response = live.post(f"/api/runs/{UC}/steps/develop_solution_requirements/approve")

    assert response.status_code == 200
    assert response.json() == {"key": key, "label": "sdlc-approved", "acting_now": True}
    assert tickets.issues[key].labels == [*tickets.issues[key].labels[:-1], "sdlc-approved"]


def test_reject_adds_the_reject_label(live, tickets, store):
    key = _awaiting(live, store)

    live.post(f"/api/runs/{UC}/steps/develop_solution_requirements/reject")

    assert "sdlc-rejected" in tickets.issues[key].labels and "sdlc-approved" not in tickets.issues[key].labels


def test_decision_needs_a_plan_awaiting_approval_and_only_one_at_a_time(live, tickets, store):
    steps = _run(live).json()["steps"]
    url = f"/api/runs/{UC}/steps/develop_solution_requirements/approve"
    assert live.post(url).status_code == 409  # nothing awaiting approval yet
    assert tickets.issues[steps["develop_solution_requirements"]].labels.count("sdlc-approved") == 0

    store.put_json(f"workflow/{UC}/run_state.json", {"develop_solution_requirements": "awaiting_approval"},
                   dry_run=False)
    assert live.post(url).status_code == 200
    again = live.post(f"/api/runs/{UC}/steps/develop_solution_requirements/reject")
    assert again.status_code == 409 and "already pending" in again.json()["detail"]


def test_decision_rejects_unknown_things(live, store):
    _awaiting(live, store)
    assert live.post("/api/runs/nope/steps/x/approve").status_code == 404
    assert live.post(f"/api/runs/{UC}/steps/data_contract_typo/approve").status_code == 404
    assert live.post(f"/api/runs/{UC}/steps/develop_solution_requirements/maybe").status_code == 422


def test_progress_exposes_the_reject_label_and_the_regenerating_state(live, store):
    _awaiting(live, store)
    store.put_json(f"workflow/{UC}/run_state.json", {"develop_solution_requirements": "regenerating"},
                   dry_run=False)

    body = live.post(f"/api/runs/{UC}/refresh").json()

    assert body["reject_label"] == "sdlc-rejected"
    assert {r["step_id"]: r["run_state"] for r in body["steps"]}["develop_solution_requirements"] == "regenerating"
