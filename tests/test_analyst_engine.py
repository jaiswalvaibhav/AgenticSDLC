"""Tests the engine's anchor-reading/prompt-building/plan-writing logic end-to-end
with a fake Strands agent (no real Bedrock call) and fakes for everything else."""
from pathlib import Path

import pytest

from sdlc.adapters.fake import FakeDocumentSource, FakeKnowledgeIndex, FakeObjectStore, FakeTicketSystem
from sdlc.agents.analyst.engine import run_solution_requirements
from sdlc.agents.analyst.plan_schema import Requirement, SolutionRequirementsPlan

UC = "demo_order_fulfilment"


class FakeStrandsAgent:
    """Records the content it was called with and returns a canned plan — stands in
    for `strands.Agent.structured_output` without a real Bedrock call."""

    def __init__(self, plan: SolutionRequirementsPlan):
        self.plan = plan
        self.calls: list[tuple[type, list[dict]]] = []

    def structured_output(self, model, content):
        self.calls.append((model, content))
        return self.plan


@pytest.fixture
def cfg(tmp_path):
    return {"data_dir": str(tmp_path), "aws": {"llm_model_id": "fake", "region": "ap-southeast-2"},
            "jira": {"approval_label": "sdlc-approved", "reject_label": "sdlc-rejected"}}


def _seed_anchor(tmp_path: Path, store: FakeObjectStore, page_id: str, title: str, html: str) -> None:
    page_dir = tmp_path / "corpus" / "FAKE" / f"{title.lower().replace(' ', '-')}__{page_id}"
    page_dir.mkdir(parents=True)
    (page_dir / "page.html").write_text(html)
    (page_dir / "meta.json").write_text(f'{{"title": "{title}", "url": "fake://{page_id}", "attachments": []}}')
    manifest = store.get_json("manifest.json") or {}
    manifest[page_id] = {"version": 1, "path": f"FAKE/{page_dir.name}"}
    store.put_json("manifest.json", manifest, dry_run=False)


@pytest.fixture(autouse=True)
def page_roles(monkeypatch):
    monkeypatch.setattr(
        "sdlc.agents.analyst.anchors._page_roles",
        lambda use_case: {"data_design_solution": {"page_id": "1"},
                           "technical_design_solution": {"page_id": "2"}},
    )


def test_writes_plan_and_comments_on_success(tmp_path, cfg):
    store = FakeObjectStore()
    _seed_anchor(tmp_path, store, "1", "Data Design Solution", "<h2>Layers</h2><p>Raw, Curated.</p>")
    _seed_anchor(tmp_path, store, "2", "Technical Design Solution", "<h2>Pipeline</h2><p>Daily batch.</p>")
    tickets = FakeTicketSystem()
    issue = tickets.create_issue("Story", "Solution Requirements", dry_run=False)
    plan = SolutionRequirementsPlan(summary="Two requirements.", requirements=[
        Requirement(requirement_id="REQ-1", workstream="raw_ingestion", title="Ingest Orders",
                    description="...", acceptance_criteria=["Lands by 3am"],
                    source_doc="data_design_solution", source_section="Layers",
                    stakeholder_requirement_ids=["SR-1"]),
    ])
    fake_agent = FakeStrandsAgent(plan)

    result = run_solution_requirements(
        {"use_case": UC, "issue_key": issue.key}, doc_source=FakeDocumentSource(),
        knowledge_index=FakeKnowledgeIndex(), tickets=tickets, store=store, cfg=cfg,
        dry_run=False, agent=fake_agent,
    )

    assert result == {"status": "planned", "requirement_count": 1}
    # the agent was given both anchors' section text
    [(model, content)] = fake_agent.calls
    assert model is SolutionRequirementsPlan
    joined_text = " ".join(b["text"] for b in content if "text" in b)
    assert "Layers" in joined_text and "Pipeline" in joined_text

    stored_plan = store.get_json(f"workflow/{UC}/plan.json")
    assert stored_plan["requirements"][0]["requirement_id"] == "REQ-1"
    plan_md = store.get_bytes(f"workflow/{UC}/plan.md").decode()
    assert "REQ-1" in plan_md and "raw_ingestion" in plan_md

    comment = tickets.comments[issue.key][0]
    assert "1 requirements" in comment
    assert "sdlc-approved" in comment


def test_blocked_when_anchor_not_configured(tmp_path, cfg, monkeypatch):
    monkeypatch.setattr("sdlc.agents.analyst.anchors._page_roles", lambda use_case: {})
    tickets = FakeTicketSystem()
    issue = tickets.create_issue("Story", "Solution Requirements", dry_run=False)

    result = run_solution_requirements(
        {"use_case": UC, "issue_key": issue.key}, doc_source=FakeDocumentSource(),
        knowledge_index=FakeKnowledgeIndex(), tickets=tickets, store=FakeObjectStore(), cfg=cfg,
        dry_run=False, agent=FakeStrandsAgent(SolutionRequirementsPlan(summary="", requirements=[])),
    )

    assert result["status"] == "blocked"
    assert "data_design_solution" in tickets.comments[issue.key][0]
