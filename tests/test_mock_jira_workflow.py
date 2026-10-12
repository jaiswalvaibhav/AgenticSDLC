"""The orchestrator end to end on the mock Jira: workflow start -> a person finishes the input
steps -> the poller sees it -> the next step starts. No fakes of the ticket system."""
from sdlc.adapters.fake import FakeAgentRuntime
from sdlc.adapters.jira_polling import PollingStatusSource
from sdlc.wiring import jira_client, jira_project_key, object_store
from sdlc.workflow import orchestrator
from sdlc.workflow.registry import WorkflowRegistry

UC = "demo_order_fulfilment"
TARGET = "develop_solution_requirements"


def test_workflow_runs_against_the_mock(tmp_path):
    cfg = {"profile": "local", "state_dir": str(tmp_path), "use_case": UC,
           "jira": {"mode": "mock", "approval_label": "sdlc-approved", "reject_label": "sdlc-rejected",
                    "components": {"analyst": None, "engineer": "Engineering", "tester": "Testing"},
                    "assignees": {"analyst": None, "engineer": None, "tester": None},
                    "mock": {"project_key": "MOCK"}},
           "orchestrator": {"auto_start_manual_steps": True, "reopen_parent_on_child_reopen": True}}
    jira, store, registry = jira_client(cfg), object_store(cfg), WorkflowRegistry.load()

    steps = orchestrator.workflow_start(tickets=jira, store=store, registry=registry, cfg=cfg,
                                         use_case=UC, targets=[TARGET], dry_run=False)
    assert set(steps) >= {TARGET} and all(k.startswith("MOCK-") for k in steps.values())
    [epic] = jira.search('issuetype = "Epic"')
    assert {i.parent_key for i in jira.search('issuetype = "Story"')} == {epic.key}
    sprint = jira.get_or_create_future_sprint(dry_run=False)  # the step stories were placed here
    assert {jira._doc(k)["sprint_id"] for k in steps.values()} == {int(sprint)}

    inputs = next(s for s in registry.all_steps() if s.id == TARGET).inputs
    # the orchestrator links each dependency as blocking the step, so the step depends on its inputs
    assert {lk["key"]: lk["direction"] for lk in jira._doc(steps[TARGET])["issue_links"]} == {steps[i]: "inward" for i in inputs}
    assert all(jira._doc(steps[i])["issue_links"][0] == {"type": "Blocks", "direction": "outward", "key": steps[TARGET]}
               for i in inputs)

    status_source = PollingStatusSource(jira, store, jira_project_key(cfg))
    assert status_source.poll(dry_run=False) == []  # first poll only sets the baseline
    inputs = next(s for s in registry.all_steps() if s.id == TARGET).inputs
    for page_id, step_id in enumerate(inputs, start=100):  # a person attaches the artifact and marks the step Done
        key = steps[step_id]
        jira.add_remote_link(key, f"https://x.atlassian.net/wiki/spaces/S/pages/{page_id}/T", "page", dry_run=False)
        jira.transition_issue(key, "Done", dry_run=False)

    agent = FakeAgentRuntime()
    events = status_source.poll(dry_run=False)
    assert {e.issue_key for e in events} == {steps[s] for s in inputs}
    for event in events:
        orchestrator.handle_status_change(event, tickets=jira, store=store, cfg=cfg,
                                           registry=registry, agent=agent, dry_run=False)

    assert jira.get_issue(steps[TARGET]).status == "In Progress"
    assert [call[1]["step_id"] for call in agent.calls] == [TARGET]  # the agent ran once, for the target
    replayed = {e.issue_key for e in status_source.poll(dry_run=False)} & {steps[s] for s in inputs}
    assert not replayed  # the input steps' Done events are not replayed on the next poll
