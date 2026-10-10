"""Real implementation of the `solution_requirements` analyst task: Strands + Bedrock
Claude (or, for profile: local only, the Anthropic/Gemini stopgaps — see
_build_local_llm_model), full anchor reads (DDS/TDS, section by section, diagrams as
images), the
search_knowledge tool for supporting pages, and a structured plan.json/plan.md output.

Besides plan.json/plan.md in the ObjectStore (.state/ for profile: local, S3 for
profile: aws — see ports.ObjectStore), the full plan is also published as a Confluence
page (DocumentSource is Confluence in both profiles), under the use case root page,
alongside the other numbered anchor pages, so a reviewer can see every requirement —
not just the short summary in the Jira comment — before approving.

This never creates Jira issues itself — orchestrator.apply_plan does that, and only
after the step ticket gets the sdlc-approved label.

`agent` is injectable (a callable agent(content, structured_output_model=Model) ->
result with a `.structured_output` attribute) so tests can exercise the
anchor-reading/prompt-building/plan-writing logic without a real Bedrock call; the
CLI/AgentRuntime path leaves it unset and gets the real Strands Agent. We call the
Strands `Agent` this way (not the deprecated `agent.structured_output(...)`) because
that older API only ever sends the output-schema tool to the model — never the
agent's own registered tools (e.g. search_knowledge) — so the model has no way to
honour SYSTEM_PROMPT's instruction to use search_knowledge for supporting context.
"""
import html
from pathlib import Path

import yaml

from sdlc.agents.analyst import anchors
from sdlc.agents.analyst.anchors import AnchorNotConfirmed, ImageRef, read_anchor
from sdlc.agents.analyst.plan_schema import SolutionRequirementsPlan
from sdlc.ports import DocumentSource, KnowledgeIndex, ObjectStore, Page, TicketSystem

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


def _build_local_llm_model(provider: str):
    """Build the Strands model for one of the local-only, non-Bedrock stopgap providers.

    Both are billed to the user's own account rather than AWS, so both confirm before
    every call — see docs/DECISIONS.md "Decisions (local-only Anthropic API stopgap)".
    """
    import os

    import typer

    if provider == "anthropic":
        anthropic_key = os.environ["ANTHROPIC_API_KEY"]
        # claude-sonnet-5-5 rejects forced tool_choice ("any"/"tool"), which Strands'
        # structured_output() hardcodes — verified against the installed strands-agents
        # 1.58.1. claude-sonnet-5 has no such restriction. Revisit once Strands supports
        # the newer structured-outputs API for this model family.
        model_id = os.environ.get("ANTHROPIC_MODEL_ID", "claude-sonnet-5")
        typer.confirm(
            f"About to call the Anthropic API directly (model={model_id}), billed to "
            "your ANTHROPIC_API_KEY, not AWS Bedrock. Continue?",
            abort=True,
        )
        from strands.models.anthropic import AnthropicModel

        return AnthropicModel(client_args={"api_key": anthropic_key}, model_id=model_id, max_tokens=128000)

    if provider == "gemini":
        project = os.environ["GOOGLE_CLOUD_PROJECT"]
        location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
        # gemini-3.8-flash is the user's own choice of default (not independently
        # doc-verified against ai.google.dev/gemini-api/docs/models this session) —
        # override with GEMINI_MODEL_ID if it's wrong/unavailable on your project.
        model_id = os.environ.get("GEMINI_MODEL_ID", "gemini-3.8-flash")
        typer.confirm(
            f"About to call the Gemini API via Vertex AI (model={model_id}, "
            f"project={project}), billed to that GCP project, not AWS Bedrock. Uses "
            "Application Default Credentials — run `gcloud auth application-default "
            "login` first if you haven't. Continue?",
            abort=True,
        )
        from strands.models.gemini import GeminiModel

        return GeminiModel(
            client_args={"vertexai": True, "project": project, "location": location},
            model_id=model_id,
        )

    raise ValueError(f"Unknown ANALYST_LLM_PROVIDER={provider!r} (expected 'anthropic' or 'gemini')")


def _build_real_agent(knowledge_index: KnowledgeIndex, use_case: str, cfg: dict):
    import os

    from strands import Agent

    from sdlc.agents.analyst.tools import make_search_knowledge_tool

    provider = os.environ.get("ANALYST_LLM_PROVIDER")
    if cfg["profile"] == "local" and provider:
        # Stopgap until Bedrock Claude model access is granted on this account (see
        # docs/DECISIONS.md "Decisions (local-only Anthropic API stopgap)"). local-profile
        # only — never used by the aws profile's Lambda/AgentCore path.
        model = _build_local_llm_model(provider)
    else:
        model_id = cfg["aws"]["llm_model_id"]
        if not model_id:
            raise ValueError(
                "config.yaml aws.llm_model_id is not set. Find a Claude inference profile id "
                "enabled on this account with: aws bedrock list-inference-profiles "
                f"--region {cfg['aws']['region']} (ap-southeast-2 needs a cross-region "
                "inference profile, not a plain foundation-model id — see CLAUDE.md)."
            )
        from strands.models import BedrockModel

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


def _plan_html(plan: SolutionRequirementsPlan) -> str:
    e = html.escape
    parts = [f"<p>{e(plan.summary)}</p>"]
    workstreams = sorted({r.workstream for r in plan.requirements})
    for ws in workstreams:
        parts.append(f"<h2>{e(ws)}</h2>")
        for r in (r for r in plan.requirements if r.workstream == ws):
            sr_ids = ", ".join(r.stakeholder_requirement_ids) or "-"
            ac_items = "".join(f"<li>{e(ac)}</li>" for ac in r.acceptance_criteria)
            parts.append(
                f"<h3>{e(r.requirement_id)} {e(r.title)}</h3>"
                f"<p>{e(r.description)}</p>"
                f"<p><strong>Acceptance criteria:</strong></p><ul>{ac_items}</ul>"
                f"<p><strong>Source:</strong> {e(r.source_doc)} § {e(r.source_section)} "
                f"(stakeholder: {e(sr_ids)})</p>"
            )
    return "".join(parts)


def _solution_requirements_page_title(use_case: str) -> str:
    roles = anchors._page_roles(use_case)
    terms = yaml.safe_load((Path("usecases") / use_case / "terminology.yaml").read_text())["terms"]
    number = len([r for r in roles if r != "usecase_root"]) + 1
    return f"{number}. {terms['solution_requirements']}"


def _publish_plan_page(plan: SolutionRequirementsPlan, *, use_case: str, doc_source: DocumentSource,
                        dry_run: bool) -> Page:
    title = _solution_requirements_page_title(use_case)
    body_html = _plan_html(plan)
    existing = doc_source.find_page_by_title(title)
    if existing:
        return doc_source.append_to_page(existing.page_id, body_html, dry_run=dry_run)
    parent_id = anchors.resolve_anchor_page_id("usecase_root", use_case=use_case, doc_source=doc_source)
    return doc_source.create_page(parent_id, title, body_html, dry_run=dry_run)


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
    plan = agent(_content_blocks(dds, tds), structured_output_model=SolutionRequirementsPlan).structured_output
    plan.use_case = use_case

    store.put_json(f"workflow/{use_case}/plan.json", plan.model_dump(), dry_run=dry_run)
    store.put_bytes(f"workflow/{use_case}/plan.md", _plan_markdown(plan).encode(), dry_run=dry_run)
    page = _publish_plan_page(plan, use_case=use_case, doc_source=doc_source, dry_run=dry_run)

    workstream_count = len({r.workstream for r in plan.requirements})
    tickets.add_comment(
        issue_key,
        f"Plan ready: {len(plan.requirements)} requirements across {workstream_count} "
        f"workstream(s).\n\n{plan.summary}\n\nAdd the '{cfg['jira']['approval_label']}' "
        f"label to apply it, or '{cfg['jira']['reject_label']}' to regenerate.",
        dry_run=dry_run,
    )
    tickets.add_remote_link(issue_key, page.url, page.title, dry_run=dry_run)
    return {"status": "planned", "requirement_count": len(plan.requirements)}
