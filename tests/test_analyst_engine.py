"""Tests the engine's anchor-reading/prompt-building/plan-writing logic end-to-end
with a fake Strands agent (no real Bedrock call) and fakes for everything else."""
import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from sdlc.adapters.fake import FakeDocumentSource, FakeKnowledgeIndex, FakeObjectStore, FakeTicketSystem
from sdlc.agents.analyst.engine import read_task_anchors, run_solution_requirements
from sdlc.agents.analyst.plan_schema import Requirement, SolutionRequirementsPlan
from sdlc.ports import Attachment

UC = "demo_order_fulfilment"


class _FakeAgentResult:
    def __init__(self, structured_output, cycles=None):
        self.structured_output = structured_output
        if cycles is not None:  # Strands' AgentResult.metrics.cycle_count: one per model call
            self.metrics = SimpleNamespace(cycle_count=cycles)


class FakeStrandsAgent:
    """Records the content it was called with and returns a canned plan — stands in
    for `strands.Agent(content, structured_output_model=...)` without a real Bedrock
    call."""

    def __init__(self, plan: SolutionRequirementsPlan, cycles=None):
        self.plan = plan
        self.cycles = cycles
        self.calls: list[tuple[type, list[dict]]] = []

    def __call__(self, content, structured_output_model):
        self.calls.append((structured_output_model, content))
        return _FakeAgentResult(self.plan, self.cycles)


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
    # still used for the use case root (where the plan page is published) and title numbering
    monkeypatch.setattr(
        "sdlc.agents.analyst.anchors._page_roles",
        lambda use_case: {"usecase_root": {"page_id": "0"},
                           "data_design_solution": {"page_id": "1"},
                           "technical_design_solution": {"page_id": "2"}},
    )


def _step_tickets(store: FakeObjectStore, tickets: FakeTicketSystem, *, dds=None, tds=None) -> None:
    """Creates the DDS/TDS step tickets and records them in steps.json. `dds`/`tds` are
    ("link", page_id), ("text", page_id) or ("file", filename, bytes) artifacts on the ticket."""
    steps = {}
    for step_id, artifact in (("data_design_solution", dds), ("technical_design_solution", tds)):
        key = tickets.create_issue("Story", step_id, dry_run=False).key
        steps[step_id] = key
        if artifact and artifact[0] == "text":  # the page URL only mentioned in a comment
            tickets.text_links[key] = [f"https://x.atlassian.net/wiki/spaces/S/pages/{artifact[1]}/T"]
        elif artifact and artifact[0] == "link":
            tickets.add_remote_link(key, f"https://x.atlassian.net/wiki/spaces/S/pages/{artifact[1]}/T",
                                    step_id, dry_run=False)
        elif artifact:
            tickets.attachments[key] = [Attachment("att-" + key, artifact[1], "x/y", "https://dl")]
            tickets.attachment_bytes["att-" + key] = artifact[2]
    store.put_json(f"workflow/{UC}/steps.json", steps, dry_run=False)


def test_writes_plan_and_comments_on_success(tmp_path, cfg):
    store = FakeObjectStore()
    _seed_anchor(tmp_path, store, "1", "Data Design Solution", "<h2>Layers</h2><p>Raw, Curated.</p>")
    _seed_anchor(tmp_path, store, "2", "Technical Design Solution", "<h2>Pipeline</h2><p>Daily batch.</p>")
    doc_source = FakeDocumentSource()
    doc_source.add_page("Order Fulfilment Performance", page_id="0")
    tickets = FakeTicketSystem()
    _step_tickets(store, tickets, dds=("link", "1"), tds=("link", "2"))
    issue = tickets.create_issue("Story", "Solution Requirements", dry_run=False)
    plan = SolutionRequirementsPlan(summary="Two requirements.", requirements=[
        Requirement(requirement_id="REQ-1", workstream="raw_ingestion", title="Ingest Orders",
                    brief_description="Daily order landing.", description="...",
                    acceptance_criteria=["Lands by 3am"], definition_of_done=["Peer reviewed"],
                    test_coverage=["Row count reconciliation"],
                    source_doc="data_design_solution", source_section="Layers",
                    stakeholder_requirement_ids=["SR-1"]),
    ])
    fake_agent = FakeStrandsAgent(plan)

    result = run_solution_requirements(
        {"use_case": UC, "issue_key": issue.key}, doc_source=doc_source,
        knowledge_index=FakeKnowledgeIndex(), tickets=tickets, store=store, cfg=cfg,
        dry_run=False, agent=fake_agent,
    )

    assert result == {"status": "planned", "requirement_count": 1}
    # the agent was given both anchors' section text
    [(model, content)] = fake_agent.calls
    assert model is SolutionRequirementsPlan
    joined_text = " ".join(b["text"] for b in content if "text" in b)
    assert "## Data Design Solution" in joined_text and "Layers" in joined_text
    assert "## Technical Design Solution" in joined_text and "Pipeline" in joined_text

    stored_plan = store.get_json(f"workflow/{UC}/plan.json")
    assert stored_plan["requirements"][0]["requirement_id"] == "REQ-1"
    plan_md = store.get_bytes(f"workflow/{UC}/plan.md").decode()
    assert "REQ-1" in plan_md and "raw_ingestion" in plan_md

    # the full plan was also published as a Confluence page under the use case root,
    # not just the short summary that goes in the Jira comment
    plan_page = doc_source.find_page_by_title("3. Solution Requirements")
    assert plan_page is not None
    assert "REQ-1" in plan_page.html and "Lands by 3am" in plan_page.html
    # every section of the ticket is on the plan page the reviewer approves, and in plan.md
    for text in ("Daily order landing.", "Definition of done", "Peer reviewed", "Test coverage", "Row count reconciliation"):
        assert text in plan_page.html
    assert "DoD: Peer reviewed" in plan_md and "Tests: Row count reconciliation" in plan_md

    comment = tickets.comments[issue.key][0]
    assert "1 requirements" in comment
    assert "sdlc-approved" in comment

    # and the Jira issue got a remote link to that page
    [(url, title)] = tickets.remote_links[issue.key]
    assert url == plan_page.url
    assert title == "3. Solution Requirements"


def _run(store, tickets, cfg, agent=None, step_id=None, dry_run=False):
    issue = tickets.create_issue("Story", "Solution Requirements", dry_run=False)
    doc_source = FakeDocumentSource()
    doc_source.add_page("Order Fulfilment Performance", page_id="0")
    result = run_solution_requirements(
        {"use_case": UC, "issue_key": issue.key, "step_id": step_id}, doc_source=doc_source,
        knowledge_index=FakeKnowledgeIndex(), tickets=tickets, store=store, cfg=cfg, dry_run=dry_run,
        agent=agent or FakeStrandsAgent(SolutionRequirementsPlan(summary="", requirements=[])),
    )
    return result, tickets.comments.get(issue.key, [])


def test_blocked_when_step_ticket_has_no_artifact(cfg):
    store, tickets = FakeObjectStore(), FakeTicketSystem()
    _step_tickets(store, tickets, dds=None, tds=("link", "2"))

    result, comments = _run(store, tickets, cfg)

    assert result["status"] == "blocked"
    assert "Data Design Solution" in comments[0] and "nothing usable" in comments[0]


def test_blocked_when_steps_were_never_created(cfg):
    result, comments = _run(FakeObjectStore(), FakeTicketSystem(), cfg)
    assert result["status"] == "blocked" and "workflow start" in comments[0]


def test_xlsx_and_pdf_anchors_become_text_and_document_blocks(tmp_path, cfg):
    from openpyxl import Workbook

    wb = Workbook()
    wb.active.title = "Mappings"
    wb.active.append(["src", "tgt"])
    xlsx = io.BytesIO()
    wb.save(xlsx)
    store, tickets = FakeObjectStore(), FakeTicketSystem()
    _step_tickets(store, tickets, dds=("file", "DDS.xlsx", xlsx.getvalue()),
                  tds=("file", "TDS.pdf", b"%PDF-1.7"))
    plan = SolutionRequirementsPlan(summary="", requirements=[])
    agent = FakeStrandsAgent(plan)

    result, _ = _run(store, tickets, cfg, agent)

    assert result["status"] == "planned"
    [(_, content)] = agent.calls
    assert "### Mappings\nsrc,tgt" in content[0]["text"]
    assert content[2]["document"] == {"format": "pdf", "name": "Technical Design Solution",
                                      "source": {"bytes": b"%PDF-1.7"}}


def test_blocked_when_text_over_budget(cfg):
    store, tickets = FakeObjectStore(), FakeTicketSystem()
    _step_tickets(store, tickets, dds=("file", "a.csv", b"x" * 50), tds=("file", "b.csv", b"y" * 50))
    cfg["analyst"] = {"max_anchor_chars": 60}

    result, comments = _run(store, tickets, cfg)

    assert result["status"] == "blocked"
    assert "too large" in comments[0] and "Data Design Solution / a.csv: 50" in comments[0]


def test_blocked_when_pdf_over_budget(cfg):
    store, tickets = FakeObjectStore(), FakeTicketSystem()
    _step_tickets(store, tickets, dds=("file", "a.pdf", b"x" * 20), tds=("file", "b.csv", b"y"))
    cfg["analyst"] = {"max_pdf_bytes": 10}

    result, comments = _run(store, tickets, cfg)

    assert result["status"] == "blocked" and "PDF is 20 bytes" in comments[0]


def test_read_task_anchors_is_generic_over_roles(tmp_path):
    store, tickets = FakeObjectStore(), FakeTicketSystem()
    _step_tickets(store, tickets, dds=("file", "a.csv", b"a,b"), tds=None)

    [(label, doc)] = read_task_anchors(("data_design_solution",), use_case=UC, tickets=tickets,
                                        doc_source=FakeDocumentSource(), store=store, data_dir=tmp_path)

    assert label == "Data Design Solution" and doc.sections == [("a.csv", "a,b")]


def test_anchor_pasted_in_a_comment_is_read_like_a_web_link(tmp_path, cfg):
    store = FakeObjectStore()
    _seed_anchor(tmp_path, store, "1", "Data Design Solution", "<h2>Layers</h2><p>Raw.</p>")
    _seed_anchor(tmp_path, store, "2", "Technical Design Solution", "<h2>Pipeline</h2><p>Daily.</p>")
    tickets = FakeTicketSystem()
    _step_tickets(store, tickets, dds=("text", "1"), tds=("text", "2"))
    agent = FakeStrandsAgent(SolutionRequirementsPlan(summary="", requirements=[]))

    result, _ = _run(store, tickets, cfg, agent)

    assert result["status"] == "planned"
    [(_, content)] = agent.calls
    assert "Layers" in content[0]["text"] and "Pipeline" in content[1]["text"]


# ------------------------------------------------------------------ LLM call counting --
USAGE_KEY = f"workflow/{UC}/llm_usage.json"
STEP = "develop_solution_requirements"


def _anchored(store, tickets):
    _step_tickets(store, tickets, dds=("file", "a.csv", b"a,b"), tds=("file", "b.csv", b"c,d"))


def test_model_calls_are_recorded_per_step_and_add_up_across_runs(cfg):
    store, tickets = FakeObjectStore(), FakeTicketSystem()
    _anchored(store, tickets)
    plan = SolutionRequirementsPlan(summary="", requirements=[])

    _run(store, tickets, cfg, FakeStrandsAgent(plan, cycles=3), step_id=STEP)
    assert store.get_json(USAGE_KEY) == {STEP: {"calls": 3, "runs": 1}}

    _run(store, tickets, cfg, FakeStrandsAgent(plan, cycles=5), step_id=STEP)  # e.g. a regenerate after reject
    assert store.get_json(USAGE_KEY) == {STEP: {"calls": 8, "runs": 2}}


def test_nothing_is_recorded_without_a_step_id_without_metrics_or_on_dry_run(cfg):
    store, tickets = FakeObjectStore(), FakeTicketSystem()
    _anchored(store, tickets)
    plan = SolutionRequirementsPlan(summary="", requirements=[])

    _run(store, tickets, cfg, FakeStrandsAgent(plan, cycles=3), step_id=None)
    _run(store, tickets, cfg, FakeStrandsAgent(plan), step_id=STEP)  # a result with no metrics
    _run(store, tickets, cfg, FakeStrandsAgent(plan, cycles=3), step_id=STEP, dry_run=True)

    assert store.get_json(USAGE_KEY) is None


def test_a_rerun_replaces_the_plan_page_instead_of_appending_and_records_it(tmp_path, cfg):
    from sdlc.workflow.pages import recorded_pages
    store = FakeObjectStore()
    _seed_anchor(tmp_path, store, "1", "Data Design Solution", "<h2>Layers</h2><p>Raw.</p>")
    _seed_anchor(tmp_path, store, "2", "Technical Design Solution", "<h2>Pipeline</h2><p>Batch.</p>")
    doc_source = FakeDocumentSource()
    doc_source.add_page("Order Fulfilment Performance", page_id="0")
    tickets = FakeTicketSystem()
    _step_tickets(store, tickets, dds=("link", "1"), tds=("link", "2"))
    issue = tickets.create_issue("Story", "Solution Requirements", dry_run=False)

    def plan(req_id):
        return SolutionRequirementsPlan(summary="s", requirements=[Requirement(
            requirement_id=req_id, workstream="raw_ingestion", title="T", brief_description="b",
            description="...", acceptance_criteria=["x"], definition_of_done=["d"], test_coverage=["t"], source_doc="data_design_solution", source_section="Layers",
            stakeholder_requirement_ids=["SR-1"])])

    for req_id in ("REQ-FIRST", "REQ-SECOND"):
        run_solution_requirements({"use_case": UC, "issue_key": issue.key}, doc_source=doc_source,
                                  knowledge_index=FakeKnowledgeIndex(), tickets=tickets, store=store,
                                  cfg=cfg, dry_run=False, agent=FakeStrandsAgent(plan(req_id)))

    page = doc_source.find_page_by_title("3. Solution Requirements")
    assert "REQ-SECOND" in page.html and "REQ-FIRST" not in page.html  # replaced, not appended
    assert [p.title for p in doc_source.pages.values()].count("3. Solution Requirements") == 1
    assert set(recorded_pages(store, UC)) == {page.page_id}
    assert recorded_pages(store, UC)[page.page_id]["issue_key"] == issue.key


def test_the_model_is_told_about_the_ticket_sections_and_which_are_checklists():
    from sdlc.agents.analyst.engine import SYSTEM_PROMPT
    for text in ("Brief Description", "Acceptance Criteria", "Definition of Done", "Test Coverage", "ticks off"):
        assert text in SYSTEM_PROMPT
    schema = SolutionRequirementsPlan.model_json_schema()["$defs"]["Requirement"]
    for field in ("brief_description", "acceptance_criteria", "definition_of_done", "test_coverage"):
        assert field in schema["required"]  # required, so the model can't skip them
    for field in ("acceptance_criteria", "definition_of_done", "test_coverage"):
        assert "checklist" in schema["properties"][field]["description"].lower()
