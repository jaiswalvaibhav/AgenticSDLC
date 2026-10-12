import pytest

from sdlc.adapters.fake import FakeAgentRuntime, FakeObjectStore, FakeTicketSystem
from sdlc.ports import StatusEvent
from sdlc.workflow import orchestrator
from sdlc.workflow.registry import Step, WorkflowRegistry

STATUSES = {"todo": "To Do", "in_progress": "In Progress", "done": "Done"}
UC = "demo_order_fulfilment"  # terminology.yaml for this use case has the statuses above


@pytest.fixture
def cfg():
    return {
        "use_case": UC,
        "jira": {"project_key": "DEMO", "approval_label": "sdlc-approved", "reject_label": "sdlc-rejected",
                  "components": {"analyst": None, "engineer": "Engineering", "tester": "Testing"},
                  "assignees": {"analyst": None, "engineer": None, "tester": None}},
        "orchestrator": {"auto_start_manual_steps": True, "reopen_parent_on_child_reopen": True},
    }


@pytest.fixture
def tickets():
    return FakeTicketSystem()


@pytest.fixture
def store():
    return FakeObjectStore()


# --------------------------------------------------------------------- rollup --
def _hierarchy(tickets: FakeTicketSystem) -> tuple[str, str, str]:
    """epic -> story -> sub-task, all created for real (not dry-run)."""
    epic = tickets.create_issue("Epic", "Epic", dry_run=False)
    story = tickets.create_issue("Story", "Story", parent_key=epic.key, dry_run=False)
    subtask = tickets.create_issue("Sub-task", "Sub-task", parent_key=story.key, dry_run=False)
    return epic.key, story.key, subtask.key


def test_any_active_child_moves_parent_to_in_progress(tickets, cfg):
    epic, story, subtask = _hierarchy(tickets)
    tickets.transition_issue(subtask, STATUSES["in_progress"], dry_run=False)

    orchestrator._rollup(subtask, tickets=tickets, project_key="DEMO", statuses=STATUSES,
                         reopen_on_child_reopen=False, dry_run=False)

    assert tickets.get_issue(story).status == STATUSES["in_progress"]


def test_all_children_done_cascades_to_epic(tickets, cfg):
    epic, story, subtask = _hierarchy(tickets)
    tickets.transition_issue(subtask, STATUSES["done"], dry_run=False)

    orchestrator._rollup(subtask, tickets=tickets, project_key="DEMO", statuses=STATUSES,
                         reopen_on_child_reopen=False, dry_run=False)

    assert tickets.get_issue(story).status == STATUSES["done"]
    assert tickets.get_issue(epic).status == STATUSES["done"]


def test_reopen_on_child_reopen_when_allowed(tickets):
    epic, story, subtask = _hierarchy(tickets)
    tickets.transition_issue(subtask, STATUSES["done"], dry_run=False)
    orchestrator._rollup(subtask, tickets=tickets, project_key="DEMO", statuses=STATUSES,
                         reopen_on_child_reopen=False, dry_run=False)
    assert tickets.get_issue(story).status == STATUSES["done"]

    tickets.transition_issue(subtask, STATUSES["in_progress"], dry_run=False)
    orchestrator._rollup(subtask, tickets=tickets, project_key="DEMO", statuses=STATUSES,
                         reopen_on_child_reopen=True, dry_run=False)

    assert tickets.get_issue(story).status == STATUSES["in_progress"]
    assert "Reopened" in tickets.comments[story][0]


def test_reopen_falls_back_to_comment_when_workflow_blocks_it(tickets):
    epic, story, subtask = _hierarchy(tickets)
    tickets.transition_issue(subtask, STATUSES["done"], dry_run=False)
    orchestrator._rollup(subtask, tickets=tickets, project_key="DEMO", statuses=STATUSES,
                         reopen_on_child_reopen=False, dry_run=False)
    tickets.blocked_transitions.add((story, STATUSES["done"], STATUSES["in_progress"]))

    tickets.transition_issue(subtask, STATUSES["in_progress"], dry_run=False)
    orchestrator._rollup(subtask, tickets=tickets, project_key="DEMO", statuses=STATUSES,
                         reopen_on_child_reopen=True, dry_run=False)

    assert tickets.get_issue(story).status == STATUSES["done"]  # unchanged
    assert "doesn't allow" in tickets.comments[story][0]


def test_reopen_disabled_leaves_done_parent_alone(tickets):
    epic, story, subtask = _hierarchy(tickets)
    tickets.transition_issue(subtask, STATUSES["done"], dry_run=False)
    orchestrator._rollup(subtask, tickets=tickets, project_key="DEMO", statuses=STATUSES,
                         reopen_on_child_reopen=False, dry_run=False)

    tickets.transition_issue(subtask, STATUSES["in_progress"], dry_run=False)
    orchestrator._rollup(subtask, tickets=tickets, project_key="DEMO", statuses=STATUSES,
                         reopen_on_child_reopen=False, dry_run=False)

    assert tickets.get_issue(story).status == STATUSES["done"]


# ------------------------------------------------------------------ readiness --
@pytest.fixture
def registry():
    return WorkflowRegistry([
        Step(id="a", owner="analyst", inputs=(), artifact="A", automation=None),
        Step(id="b", owner="analyst", inputs=("a",), artifact="B", automation="analyst.b"),
    ])


def _step_issue(tickets, store, use_case, step_id, *, status="To Do"):
    issue = tickets.create_issue("Story", step_id, labels=[f"uc:{use_case}", f"step:{step_id}"], dry_run=False)
    if status != "To Do":
        tickets.transition_issue(issue.key, status, dry_run=False)
    steps_map = store.get_json(f"workflow/{use_case}/steps.json") or {}
    steps_map[step_id] = issue.key
    store.put_json(f"workflow/{use_case}/steps.json", steps_map, dry_run=False)
    return issue.key


def test_downstream_step_starts_once_input_is_done_with_artifact(tickets, store, cfg, registry, monkeypatch):
    monkeypatch.setattr(orchestrator, "load_terminology", lambda uc: {"statuses": STATUSES})
    a_key = _step_issue(tickets, store, UC, "a")
    b_key = _step_issue(tickets, store, UC, "b")
    tickets.transition_issue(a_key, STATUSES["done"], dry_run=False)
    tickets.artifacts.add(a_key)
    agent = FakeAgentRuntime()

    event = StatusEvent(a_key, STATUSES["in_progress"], STATUSES["done"])
    orchestrator.handle_status_change(event, tickets=tickets, store=store, cfg=cfg,
                                       registry=registry, agent=agent, dry_run=False)

    assert tickets.get_issue(b_key).status == STATUSES["in_progress"]
    assert agent.calls == [("analyst.b", {"use_case": UC, "step_id": "b", "issue_key": b_key,
                                           "dry_run": False})]
    run_state = store.get_json(f"workflow/{UC}/run_state.json")
    assert run_state["b"] == "awaiting_approval"


def test_blocked_agent_result_does_not_set_awaiting_approval(tickets, store, cfg, registry, monkeypatch):
    """engine.run_solution_requirements returns status "blocked" (e.g. an anchor
    page_id isn't configured yet) and posts its own explanatory comment — the
    orchestrator must not also move run_state to awaiting_approval or add a
    second, misleading "plan ready" comment on top of it."""
    monkeypatch.setattr(orchestrator, "load_terminology", lambda uc: {"statuses": STATUSES})
    a_key = _step_issue(tickets, store, UC, "a")
    b_key = _step_issue(tickets, store, UC, "b")
    tickets.transition_issue(a_key, STATUSES["done"], dry_run=False)
    tickets.artifacts.add(a_key)

    class BlockedAgentRuntime:
        def run(self, task_id, context):
            return {"status": "blocked", "reason": "anchor not configured"}

    event = StatusEvent(a_key, STATUSES["in_progress"], STATUSES["done"])
    orchestrator.handle_status_change(event, tickets=tickets, store=store, cfg=cfg,
                                       registry=registry, agent=BlockedAgentRuntime(), dry_run=False)

    run_state = store.get_json(f"workflow/{UC}/run_state.json") or {}
    assert run_state.get("b") is None
    # Only the "Inputs ready (...)" comment from _start_step itself — no extra
    # "Agent plan generated" comment on top of it.
    assert tickets.comments[b_key] == [f"Inputs ready (a: {a_key})."]


def test_downstream_step_waits_without_artifact(tickets, store, cfg, registry, monkeypatch):
    monkeypatch.setattr(orchestrator, "load_terminology", lambda uc: {"statuses": STATUSES})
    a_key = _step_issue(tickets, store, UC, "a")
    b_key = _step_issue(tickets, store, UC, "b")
    tickets.transition_issue(a_key, STATUSES["done"], dry_run=False)
    # no artifact set on a_key

    event = StatusEvent(a_key, STATUSES["in_progress"], STATUSES["done"])
    orchestrator.handle_status_change(event, tickets=tickets, store=store, cfg=cfg,
                                       registry=registry, agent=FakeAgentRuntime(), dry_run=False)

    assert tickets.get_issue(b_key).status == STATUSES["todo"]


def _done_event(tickets, store, monkeypatch, *, link=None):
    monkeypatch.setattr(orchestrator, "load_terminology", lambda uc: {"statuses": STATUSES})
    key = _step_issue(tickets, store, UC, "a")
    tickets.transition_issue(key, STATUSES["done"], dry_run=False)
    if link:
        tickets.add_remote_link(key, link, "TDS", dry_run=False)
    return key, StatusEvent(key, STATUSES["in_progress"], STATUSES["done"])


def _handle(event, tickets, store, cfg, steps):
    orchestrator.handle_status_change(event, tickets=tickets, store=store, cfg=cfg,
                                       registry=WorkflowRegistry(steps), agent=FakeAgentRuntime(), dry_run=False)


_FEEDS_AUTOMATED = [Step("a", "analyst", (), "A", None), Step("b", "analyst", ("a",), "B", "analyst.b")]


def test_done_step_feeding_automated_step_without_artifact_gets_a_comment(tickets, store, cfg, monkeypatch):
    key, event = _done_event(tickets, store, monkeypatch)
    _handle(event, tickets, store, cfg, _FEEDS_AUTOMATED)
    assert "Marked Done but no usable artifact" in tickets.comments[key][0]


def test_done_step_with_confluence_link_gets_no_comment(tickets, store, cfg, monkeypatch):
    key, event = _done_event(tickets, store, monkeypatch, link="https://x.atlassian.net/wiki/spaces/S/pages/7/T")
    _handle(event, tickets, store, cfg, _FEEDS_AUTOMATED)
    assert key not in tickets.comments


def test_done_step_with_a_confluence_page_in_a_comment_gets_no_comment(tickets, store, cfg, monkeypatch):
    key, event = _done_event(tickets, store, monkeypatch)
    tickets.text_links[key] = ["https://x.atlassian.net/wiki/spaces/S/pages/7/T"]
    _handle(event, tickets, store, cfg, _FEEDS_AUTOMATED)
    assert key not in tickets.comments


def test_downstream_step_starts_when_the_inputs_artifact_is_only_a_comment_link(tickets, store, cfg, registry,
                                                                              monkeypatch):
    monkeypatch.setattr(orchestrator, "load_terminology", lambda uc: {"statuses": STATUSES})
    a_key = _step_issue(tickets, store, UC, "a")
    b_key = _step_issue(tickets, store, UC, "b")
    tickets.transition_issue(a_key, STATUSES["done"], dry_run=False)
    tickets.text_links[a_key] = ["https://x.atlassian.net/wiki/spaces/S/pages/7/T"]  # no attachment/Web link

    orchestrator.handle_status_change(StatusEvent(a_key, STATUSES["in_progress"], STATUSES["done"]),
                                       tickets=tickets, store=store, cfg=cfg, registry=registry,
                                       agent=FakeAgentRuntime(), dry_run=False)

    assert tickets.get_issue(b_key).status == STATUSES["in_progress"]


def test_done_step_feeding_only_manual_steps_is_not_checked(tickets, store, cfg, monkeypatch):
    key, event = _done_event(tickets, store, monkeypatch)
    _handle(event, tickets, store, cfg, [Step("a", "analyst", (), "A", None), Step("b", "analyst", ("a",), "B", None)])
    assert key not in tickets.comments


# ------------------------------------------------------------------- approval --
_SAMPLE_PLAN = {
    "use_case": UC,
    "summary": "Three requirements across two workstreams.",
    "requirements": [
        {"requirement_id": "REQ-1", "workstream": "raw_ingestion", "title": "Ingest Orders",
         "description": "Land Orders daily.", "acceptance_criteria": ["Orders land by 3am"],
         "source_doc": "data_design_solution", "source_section": "Layers",
         "stakeholder_requirement_ids": ["SR-1"]},
    ],
}


def test_check_approvals_applies_on_label(tickets, store, cfg):
    key = _step_issue(tickets, store, UC, "b")
    tickets.transition_issue(key, STATUSES["in_progress"], dry_run=False)
    store.put_json(f"workflow/{UC}/run_state.json", {"b": "awaiting_approval"}, dry_run=False)
    store.put_json(f"workflow/{UC}/plan.json", _SAMPLE_PLAN, dry_run=False)
    tickets.add_label(key, cfg["jira"]["approval_label"], dry_run=False)
    tickets.create_issue("Epic", "Epic", labels=[f"sdlc-epic:{UC}"], dry_run=False)

    acted = orchestrator.check_approvals(tickets=tickets, store=store, cfg=cfg, use_case=UC, dry_run=False)

    assert acted == ["b"]
    assert tickets.get_issue(key).status == STATUSES["done"]
    assert store.get_json(f"workflow/{UC}/run_state.json")["b"] == "applied"
    story = next(i for i in tickets.issues.values() if i.summary == "Ingest Orders")
    assert "ws:raw_ingestion" in story.labels
    subtasks = [i for i in tickets.issues.values() if i.parent_key == story.key]
    assert {s.summary for s in subtasks} == {"[Engineering] Ingest Orders", "[Testing] Ingest Orders"}
    traceability = store.get_json(f"workflow/{UC}/traceability.json")
    assert traceability["records"][0]["requirement_id"] == "REQ-1"
    assert traceability["records"][0]["jira_key"] == story.key
    assert tickets.properties[story.key]["sdlc.trace"]["requirement_id"] == "REQ-1"


def test_check_approvals_passes_through_in_progress_when_still_todo(tickets, store, cfg):
    """apply_plan must never jump straight To Do -> Done; it should pass through
    In Progress first, same as the normal orchestrator-driven flow would have."""
    key = _step_issue(tickets, store, UC, "b")  # left at "To Do"
    store.put_json(f"workflow/{UC}/run_state.json", {"b": "awaiting_approval"}, dry_run=False)
    store.put_json(f"workflow/{UC}/plan.json", _SAMPLE_PLAN, dry_run=False)
    tickets.add_label(key, cfg["jira"]["approval_label"], dry_run=False)
    tickets.create_issue("Epic", "Epic", labels=[f"sdlc-epic:{UC}"], dry_run=False)

    orchestrator.check_approvals(tickets=tickets, store=store, cfg=cfg, use_case=UC, dry_run=False)

    assert tickets.get_issue(key).status == STATUSES["done"]
    statuses_seen = [e.to_status for e in tickets.events if e.issue_key == key]
    assert statuses_seen == [STATUSES["in_progress"], STATUSES["done"]]


def test_check_approvals_places_new_requirement_stories_in_future_sprint(tickets, store, cfg):
    key = _step_issue(tickets, store, UC, "b")
    tickets.transition_issue(key, STATUSES["in_progress"], dry_run=False)
    store.put_json(f"workflow/{UC}/run_state.json", {"b": "awaiting_approval"}, dry_run=False)
    store.put_json(f"workflow/{UC}/plan.json", _SAMPLE_PLAN, dry_run=False)
    tickets.add_label(key, cfg["jira"]["approval_label"], dry_run=False)
    tickets.create_issue("Epic", "Epic", labels=[f"sdlc-epic:{UC}"], dry_run=False)

    orchestrator.check_approvals(tickets=tickets, store=store, cfg=cfg, use_case=UC, dry_run=False)

    story = next(i for i in tickets.issues.values() if i.summary == "Ingest Orders")
    [(_sprint_id, keys)] = tickets.sprints.items()
    assert story.key in keys


def test_reject_with_an_agent_removes_the_label_and_regenerates_the_plan(tickets, store, cfg):
    registry = WorkflowRegistry([Step("b", "analyst", (), "B", "analyst.b")])
    key = _step_issue(tickets, store, UC, "b")
    store.put_json(f"workflow/{UC}/run_state.json", {"b": "awaiting_approval"}, dry_run=False)
    tickets.add_label(key, cfg["jira"]["reject_label"], dry_run=False)
    agent = FakeAgentRuntime()

    acted = orchestrator.check_approvals(tickets=tickets, store=store, cfg=cfg, use_case=UC, dry_run=False,
                                          registry=registry, agent=agent)

    assert acted == ["b"]
    assert [c[0] for c in agent.calls] == ["analyst.b"]
    assert cfg["jira"]["reject_label"] not in tickets.get_issue(key).labels  # can't regenerate twice
    assert store.get_json(f"workflow/{UC}/run_state.json")["b"] == "awaiting_approval"
    assert tickets.comments[key] == ["Rejected — regenerating the plan."]


def test_reject_regeneration_that_does_not_produce_a_plan_leaves_the_step_ready(tickets, store, cfg):
    registry = WorkflowRegistry([Step("b", "analyst", (), "B", "analyst.b")])
    key = _step_issue(tickets, store, UC, "b")
    store.put_json(f"workflow/{UC}/run_state.json", {"b": "awaiting_approval"}, dry_run=False)
    tickets.add_label(key, cfg["jira"]["reject_label"], dry_run=False)

    class Blocked:
        def run(self, task_id, context):
            return {"status": "blocked"}

    orchestrator.check_approvals(tickets=tickets, store=store, cfg=cfg, use_case=UC, dry_run=False,
                                  registry=registry, agent=Blocked())

    assert store.get_json(f"workflow/{UC}/run_state.json")["b"] == "ready"


def test_a_failing_agent_is_reported_on_the_ticket_instead_of_crashing_the_cycle(tickets, store, cfg, monkeypatch):
    monkeypatch.setattr(orchestrator, "load_terminology", lambda uc: {"statuses": STATUSES})
    registry = WorkflowRegistry([Step("a", "analyst", (), "A", None), Step("b", "analyst", ("a",), "B", "analyst.b")])
    a_key = _step_issue(tickets, store, UC, "a")
    b_key = _step_issue(tickets, store, UC, "b")
    tickets.transition_issue(a_key, STATUSES["done"], dry_run=False)
    tickets.artifacts.add(a_key)

    class Exploding:
        def run(self, task_id, context):
            raise RuntimeError("LoginRefreshRequired")

    orchestrator.handle_status_change(StatusEvent(a_key, STATUSES["in_progress"], STATUSES["done"]),
                                       tickets=tickets, store=store, cfg=cfg, registry=registry,
                                       agent=Exploding(), dry_run=False)

    assert tickets.comments[b_key][-1] == "Agent run failed: RuntimeError: LoginRefreshRequired"
    assert (store.get_json(f"workflow/{UC}/run_state.json") or {}).get("b") is None


def test_check_approvals_regenerates_on_reject(tickets, store, cfg):
    key = _step_issue(tickets, store, UC, "b")
    store.put_json(f"workflow/{UC}/run_state.json", {"b": "awaiting_approval"}, dry_run=False)
    tickets.add_label(key, cfg["jira"]["reject_label"], dry_run=False)

    acted = orchestrator.check_approvals(tickets=tickets, store=store, cfg=cfg, use_case=UC, dry_run=False)

    assert acted == ["b"]
    assert store.get_json(f"workflow/{UC}/run_state.json")["b"] == "ready"


# --------------------------------------------------------------- workflow_start --
def test_workflow_start_is_idempotent(tickets, store, cfg):
    registry = WorkflowRegistry.load()
    first = orchestrator.workflow_start(tickets=tickets, store=store, registry=registry, cfg=cfg,
                                        use_case=UC, from_step="develop_solution_requirements", dry_run=False)
    second = orchestrator.workflow_start(tickets=tickets, store=store, registry=registry, cfg=cfg,
                                         use_case=UC, from_step="develop_solution_requirements", dry_run=False)

    assert first == second
    assert len(tickets.issues) == len(first) + 1  # + the epic
    epic_issues = [i for i in tickets.issues.values() if i.issue_type == "Epic"]
    assert len(epic_issues) == 1


def test_workflow_start_with_targets_creates_only_targets_and_direct_inputs(tickets, store, cfg):
    registry = WorkflowRegistry.load()
    steps_map = orchestrator.workflow_start(tickets=tickets, store=store, registry=registry, cfg=cfg,
                                             use_case=UC, targets=["develop_solution_requirements"],
                                             dry_run=False)

    assert set(steps_map) == {"develop_solution_requirements", "data_design_solution",
                               "technical_design_solution"}
    assert len(tickets.issues) == 3 + 1  # + the epic
    # the DDS/TDS -> plan "Blocks" links are still created
    assert {(a, b) for a, b, _ in tickets.links} == {
        (steps_map["data_design_solution"], steps_map["develop_solution_requirements"]),
        (steps_map["technical_design_solution"], steps_map["develop_solution_requirements"])}


def test_workflow_start_places_new_step_stories_in_future_sprint(tickets, store, cfg):
    registry = WorkflowRegistry.load()
    steps_map = orchestrator.workflow_start(tickets=tickets, store=store, registry=registry, cfg=cfg,
                                             use_case=UC, from_step="develop_solution_requirements", dry_run=False)

    [(_sprint_id, keys)] = tickets.sprints.items()
    assert set(keys) == set(steps_map.values())  # every newly created step Story, none skipped
