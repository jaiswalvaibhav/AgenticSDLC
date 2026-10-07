"""Real implementation of the `solution_requirements` analyst task: Strands + Bedrock
Claude, full anchor reads (DDS/TDS, section by section, diagrams as images), the
search_knowledge tool for supporting pages, and a structured plan.json/plan.md output.

This never creates Jira issues itself — orchestrator.apply_plan does that, and only
after the step ticket gets the sdlc-approved label.

`agent` is injectable (an object with .structured_output(Model, content) -> Model) so
tests can exercise the anchor-reading/prompt-building/plan-writing logic without a real
Bedrock call; the CLI/AgentRuntime path leaves it unset and gets the real Strands Agent.
"""
from pathlib import Path

from sdlc.agents.analyst.anchors import AnchorNotConfirmed, ImageRef, read_anchor
from sdlc.agents.analyst.plan_schema import SolutionRequirementsPlan
from sdlc.ports import DocumentSource, KnowledgeIndex, ObjectStore, TicketSystem

SYSTEM_PROMPT = (
    "You are the data analyst agent in an Autonomous Data SDLC. Below is the Data "
    "Design Solution and the Technical Design Solution, given to you in full — read "
    "them section by section, including the architecture diagrams. Use the "
    "search_knowledge tool for anything else you need (stakeholder requirements, "
    "source data analysis, conceptual data model, feasibility analysis, glossary). "
    "Produce solution requirements grouped into workstreams (raw_ingestion, "
    "curated_modelling, presentation_bi, data_quality), each with clear acceptance "
    "criteria, and a citation back to the exact DDS/TDS section heading it came from "
    "plus any stakeholder requirement ids (e.g. SR-1) it traces to."
)


def _build_real_agent(knowledge_index: KnowledgeIndex, use_case: str, cfg: dict):
    model_id = cfg["aws"]["llm_model_id"]
    if not model_id:
        raise ValueError(
            "config.yaml aws.llm_model_id is not set. Find a Claude inference profile id "
            "enabled on this account with: aws bedrock list-inference-profiles "
            f"--region {cfg['aws']['region']} (ap-southeast-2 needs a cross-region "
            "inference profile, not a plain foundation-model id — see CLAUDE.md)."
        )
    from strands import Agent
    from strands.models import BedrockModel

    from sdlc.agents.analyst.tools import make_search_knowledge_tool

    model = BedrockModel(model_id=model_id, region_name=cfg["aws"]["region"])
    return Agent(model=model, tools=[make_search_knowledge_tool(knowledge_index, use_case)],
                 system_prompt=SYSTEM_PROMPT)


def _content_blocks(dds, tds) -> list[dict]:
    blocks = [{"text": "## Data Design Solution\n\n" + _sections_text(dds.sections)}]
    blocks += [_image_block(img) for img in dds.images]
    blocks.append({"text": "## Technical Design Solution\n\n" + _sections_text(tds.sections)})
    blocks += [_image_block(img) for img in tds.images]
    return blocks


def _image_block(img: ImageRef) -> dict:
    return {"image": {"format": img.format, "source": {"bytes": img.data}}}


def _sections_text(sections: list[tuple[str, str]]) -> str:
    return "\n\n".join(f"### {heading}\n{text}" for heading, text in sections)


def _plan_markdown(plan: SolutionRequirementsPlan) -> str:
    lines = [f"# Solution Requirements — {plan.use_case}", "", plan.summary, ""]
    workstreams = sorted({r.workstream for r in plan.requirements})
    for ws in workstreams:
        lines.append(f"## {ws}")
        for r in (r for r in plan.requirements if r.workstream == ws):
            lines.append(f"- **{r.requirement_id}** {r.title}")
            lines.append(f"  - {r.description}")
            lines.append("  - AC: " + "; ".join(r.acceptance_criteria))
            sr_ids = ", ".join(r.stakeholder_requirement_ids) or "-"
            lines.append(f"  - Source: {r.source_doc} § {r.source_section} (stakeholder: {sr_ids})")
        lines.append("")
    return "\n".join(lines)


def run_solution_requirements(context: dict, *, doc_source: DocumentSource,
                               knowledge_index: KnowledgeIndex, tickets: TicketSystem,
                               store: ObjectStore, cfg: dict, dry_run: bool = True,
                               agent=None) -> dict:
    use_case, issue_key = context["use_case"], context["issue_key"]
    data_dir = cfg["data_dir"]

    try:
        dds = read_anchor("data_design_solution", use_case=use_case, doc_source=doc_source,
                           store=store, data_dir=data_dir)
        tds = read_anchor("technical_design_solution", use_case=use_case, doc_source=doc_source,
                           store=store, data_dir=data_dir)
    except AnchorNotConfirmed as exc:
        tickets.add_comment(
            issue_key, f"Can't resolve anchor {exc.role!r} automatically "
                       f"({exc}). Set page_id in usecases/{use_case}/page_roles.yaml, then re-run.",
            dry_run=dry_run)
        return {"status": "blocked", "reason": str(exc)}

    agent = agent or _build_real_agent(knowledge_index, use_case, cfg)
    plan = agent.structured_output(SolutionRequirementsPlan, _content_blocks(dds, tds))
    plan.use_case = use_case

    store.put_json(f"workflow/{use_case}/plan.json", plan.model_dump(), dry_run=dry_run)
    store.put_bytes(f"workflow/{use_case}/plan.md", _plan_markdown(plan).encode(), dry_run=dry_run)

    workstream_count = len({r.workstream for r in plan.requirements})
    tickets.add_comment(
        issue_key,
        f"Plan ready: {len(plan.requirements)} requirements across {workstream_count} "
        f"workstream(s).\n\n{plan.summary}\n\nAdd the '{cfg['jira']['approval_label']}' "
        f"label to apply it, or '{cfg['jira']['reject_label']}' to regenerate.",
        dry_run=dry_run,
    )
    return {"status": "planned", "requirement_count": len(plan.requirements)}
