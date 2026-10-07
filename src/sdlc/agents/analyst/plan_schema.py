"""The structured output the analyst's `solution_requirements` task produces. Used
both as the Strands `structured_output` target schema and as the on-disk plan.json
shape (see engine.py and orchestrator.apply_plan)."""
from pydantic import BaseModel, Field


class Requirement(BaseModel):
    requirement_id: str = Field(description="Short stable id, e.g. REQ-1")
    workstream: str = Field(description="One of: raw_ingestion, curated_modelling, "
                                          "presentation_bi, data_quality")
    title: str = Field(description="Short Jira Story summary")
    description: str = Field(description="What needs to be built and why")
    acceptance_criteria: list[str] = Field(description="Testable acceptance criteria")
    source_doc: str = Field(description="data_design_solution or technical_design_solution")
    source_section: str = Field(description="The exact heading this requirement was drawn from")
    stakeholder_requirement_ids: list[str] = Field(
        default_factory=list, description="Stakeholder requirement ids this traces back to, e.g. SR-1")


class SolutionRequirementsPlan(BaseModel):
    use_case: str = ""
    summary: str = Field(description="A few sentences for the Jira comment")
    requirements: list[Requirement]
