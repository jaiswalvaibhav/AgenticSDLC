import pytest

from sdlc.workflow.registry import Step, WorkflowError, WorkflowRegistry


def test_loads_default_workflow():
    registry = WorkflowRegistry.load()
    assert "develop_solution_requirements" in registry
    assert "data_contract" in registry
    assert registry["data_contract"].optional is False


def test_default_selection_includes_every_step():
    registry = WorkflowRegistry.load()
    selection = registry.resolve_selection()
    selected_ids = {s.id for s in selection.selected}
    assert selected_ids == {s.id for s in registry.all_steps()}
    assert "develop_solution_requirements" in selected_ids
    assert "test_solution_deployment" in selected_ids
    assert not selection.artifact_only


def test_from_develop_solution_requirements_creates_artifact_only_inputs():
    registry = WorkflowRegistry.load()
    selection = registry.resolve_selection(from_step="develop_solution_requirements")
    selected_ids = {s.id for s in selection.selected}
    artifact_only_ids = {s.id for s in selection.artifact_only}

    assert "develop_solution_requirements" in selected_ids
    assert "test_solution_report" in selected_ids
    assert "stakeholder_requirements" not in selected_ids
    assert {"data_design_solution", "technical_design_solution"} <= artifact_only_ids


def test_from_develop_solution_requirements_pulls_in_parallel_tester_branch():
    """test_case_specification/test_automation depend only on DDS+TDS (siblings of
    solution_requirements, not downstream of it) but must still be selected, not
    left as artifact-only, since starting from SR implies DDS+TDS are already done."""
    registry = WorkflowRegistry.load()
    selection = registry.resolve_selection(from_step="develop_solution_requirements")
    selected_ids = {s.id for s in selection.selected}
    assert {"test_case_specification", "test_automation"} <= selected_ids


def test_from_develop_solution_requirements_selects_data_contract_and_tail():
    registry = WorkflowRegistry.load()
    selected_ids = {s.id for s in registry.resolve_selection(from_step="develop_solution_requirements").selected}
    assert {"data_contract", "test_solution_report", "test_solution_deployment"} <= selected_ids


def test_optional_step_is_selected_only_when_explicit():
    registry = WorkflowRegistry([
        Step(id="a", owner="analyst", inputs=(), artifact="A", automation=None),
        Step(id="b", owner="analyst", inputs=("a",), artifact="B", automation=None, optional=True),
    ])
    assert {s.id for s in registry.resolve_selection().selected} == {"a"}
    assert {s.id for s in registry.resolve_selection(from_step="a", steps=["b"]).selected} == {"a", "b"}


def test_unknown_step_raises():
    registry = WorkflowRegistry.load()
    with pytest.raises(WorkflowError):
        registry.resolve_selection(from_step="not_a_real_step")


def test_unknown_input_raises():
    with pytest.raises(WorkflowError):
        WorkflowRegistry([Step(id="a", owner="analyst", inputs=("missing",), artifact="A", automation=None)])


def test_cycle_raises():
    with pytest.raises(WorkflowError):
        WorkflowRegistry([
            Step(id="a", owner="analyst", inputs=("b",), artifact="A", automation=None),
            Step(id="b", owner="analyst", inputs=("a",), artifact="B", automation=None),
        ])
