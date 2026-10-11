import pytest

from sdlc.agents.analyst.tasks import TASKS, resolve_task
from sdlc.agents.runtime import LocalAgentRuntime
from sdlc.workflow.registry import WorkflowRegistry


@pytest.mark.parametrize("ref", ["solution_requirements", "analyst.solution_requirements"])
def test_resolve_task_accepts_task_id_or_automation_reference(ref):
    assert resolve_task(ref) is TASKS["solution_requirements"]


def test_resolve_task_unknown_is_none():
    assert resolve_task("analyst.nope") is None


def test_every_automated_workflow_step_resolves_to_a_task():
    """The orchestrator passes step.automation to AgentRuntime.run; every one in
    config/workflow.yaml must be runnable (this used to fail: 'analyst.solution_requirements')."""
    for step in WorkflowRegistry.load().all_steps():
        if step.automation:
            assert resolve_task(step.automation) is not None, step.id


def test_local_runtime_runs_a_task_given_its_automation_reference():
    calls = []
    runtime = LocalAgentRuntime(doc_source="d", knowledge_index="k", tickets="t", store="s", cfg={})
    original = TASKS["solution_requirements"]
    try:
        from dataclasses import replace
        TASKS["solution_requirements"] = replace(
            original, run=lambda context, **kw: calls.append((context, kw["dry_run"])) or {"status": "ok"})
        result = runtime.run("analyst.solution_requirements", {"dry_run": False})
    finally:
        TASKS["solution_requirements"] = original
    assert result == {"status": "ok"} and calls == [({"dry_run": False}, False)]
