"""Analyst task registry: each task declares its anchors, retrieval scope and outputs,
so a future task (e.g. data_contract) can be added without changing the orchestrator.
Task bodies are implemented in Phase 6 (Strands + Bedrock Claude); this is the registry only.
"""
from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class AnalystTask:
    id: str
    workflow_step: str  # id in config/workflow.yaml this task automates
    anchor_roles: tuple[str, ...]  # page_roles.yaml roles read in full
    retrieval_scope: tuple[str, ...]  # page_roles.yaml roles available via search_knowledge
    outputs: tuple[str, ...]
    upstream: tuple[str, ...] = field(default_factory=tuple)
    downstream: tuple[str, ...] = field(default_factory=tuple)
    run: Callable[[dict], dict] | None = None  # set in Phase 6


TASKS: dict[str, AnalystTask] = {}


def register(task: AnalystTask) -> AnalystTask:
    TASKS[task.id] = task
    return task


register(AnalystTask(
    id="solution_requirements",
    workflow_step="solution_requirements",
    anchor_roles=("data_design_solution", "technical_design_solution"),
    retrieval_scope=("stakeholder_requirements", "source_data_analysis",
                      "conceptual_data_model", "feasibility_analysis", "glossary"),
    outputs=("jira_epic_stories_subtasks", "plan_file"),
    upstream=("data_design_solution", "technical_design_solution"),
    downstream=("data_contract", "test_case_specification"),
))
