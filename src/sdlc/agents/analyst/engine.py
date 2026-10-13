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
import asyncio
import html
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import yaml

from sdlc.agents.activity import RunRecorder
from sdlc.agents.analyst import anchors
from sdlc.agents.analyst.anchors import AnchorResult, DocumentRef, ImageRef, read_anchor
from sdlc.agents.analyst.plan_schema import SolutionRequirementsPlan
from sdlc.agents.analyst.ticket_anchors import AnchorMissing, pick_anchor_source
from sdlc.ports import DocumentSource, KnowledgeIndex, ObjectStore, Page, TicketSystem
from sdlc.workflow.orchestrator import _steps_map, load_terminology
from sdlc.workflow.pages import record_page

_PROMPT_INTRO = (
    "You are the data analyst agent in an Autonomous Data SDLC. Below is the Data "
    "Design Solution and the Technical Design Solution, given to you in full — read "
    "them section by section, including the architecture diagrams. "
)
_KB_SENTENCE = (
    "Use the search_knowledge tool for anything else you need (stakeholder requirements, "
    "source data analysis, conceptual data model, feasibility analysis, glossary). "
)
_NO_KB_SENTENCE = (
    "No knowledge base is available for this run: work only from the documents given, "
    "don't invent stakeholder requirement ids, and state any gap as an assumption in the "
    "requirement's description. "
)
_PROMPT_BODY = (
    "Produce solution requirements grouped into workstreams as per the design and requirements, each with a citation back to "
    "the exact DDS/TDS section heading it came from plus any stakeholder requirement "
    "ids (e.g. SR-1) it traces to. For a document given as a PDF, cite its own "
    "section headings as source_section. "
    "Each requirement becomes a Jira ticket with these sections: a Brief Description "
    "(one or two sentences), a full Description, and three checklists that the team "
    "ticks off as they work: Acceptance Criteria, Definition of Done and Test "
    "Coverage. You decide what goes in each section, based on the documents. Write "
    "every checklist item as one short, independently verifiable line (not a "
    "paragraph), and only include items that apply to this requirement: acceptance "
    "criteria say what the delivered result must do, the definition of done covers "
    "what else must be true to close the ticket (review, documentation, deployment, "
    "monitoring), and test coverage lists the tests needed (unit, data quality, "
    "reconciliation, performance and so on)."
)


def system_prompt(kb: bool = True) -> str:
    return _PROMPT_INTRO + (_KB_SENTENCE if kb else _NO_KB_SENTENCE) + _PROMPT_BODY


SYSTEM_PROMPT = system_prompt(True)


def _build_local_llm_model(provider: str, confirmed: bool = False):
    """Build the Strands model for one of the local-only, non-Bedrock stopgap providers.

    Both are billed to the user's own account rather than AWS, so both confirm before
    every call — see docs/DECISIONS.md "Decisions (local-only Anthropic API stopgap)".
    `confirmed` skips the terminal prompt for callers with no terminal (the workflow UI's
    background poller), which collect the user's consent once at trigger instead.
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
        if not confirmed:
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
        if not confirmed:
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

    raise ValueError(f"Unknown EXTERNAL_LLM_PROVIDER={provider!r} (expected 'anthropic' or 'gemini')")


def _build_model(cfg: dict):
    import os

    provider = os.environ.get("EXTERNAL_LLM_PROVIDER")
    if cfg["profile"] == "local" and provider:
        # Stopgap until Bedrock Claude model access is granted on this account (see
        # docs/DECISIONS.md "Decisions (local-only Anthropic API stopgap)"). local-profile
        # only — never used by the aws profile's Lambda/AgentCore path.
        # confirmed by the UI at trigger, or for good by config (analyst.auto_confirm_external_llm)
        confirmed = cfg.get("external_llm_confirmed", False) or cfg.get("analyst", {}).get(
            "auto_confirm_external_llm", False)
        model = _build_local_llm_model(provider, confirmed=confirmed)
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
    return model


def _build_real_agent(knowledge_index: KnowledgeIndex | None, use_case: str, cfg: dict,
                       recorder: RunRecorder | None = None):
    from strands import Agent

    from sdlc.agents.analyst.tools import make_search_knowledge_tool

    model = _build_model(cfg)

    # no KnowledgeIndex = no search_knowledge tool and a prompt that doesn't mention it
    tools = [make_search_knowledge_tool(knowledge_index, use_case, on_search=recorder and recorder.on_search,
                                         on_result=recorder and recorder.on_search_result)
             ] if knowledge_index else []
    return Agent(model=model, tools=tools, system_prompt=system_prompt(kb=knowledge_index is not None),
                 hooks=[recorder.hooks()] if recorder else None)


def _content_blocks(anchors: list[tuple[str, AnchorResult]]) -> list[dict]:
    blocks: list[dict] = []
    for label, doc in anchors:
        body = _sections_text(doc.sections) if doc.sections else "(attached as a document below)"
        blocks.append({"text": f"## {label}\n\n{body}"})
        blocks += [_image_block(img) for img in doc.images]
        blocks += [_document_block(label, d) for d in doc.documents]
    return blocks


def _document_block(label: str, doc: DocumentRef) -> dict:
    # Converse document names allow only alphanumerics, single spaces, hyphens, parentheses
    # and square brackets (up to 200 chars); terminology labels are neutral names.
    name = re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 \-()\[\]]", " ", label)).strip()[:200] or "document"
    return {"document": {"format": doc.format, "name": name, "source": {"bytes": doc.data}}}


def read_task_anchors(anchor_roles: tuple[str, ...], *, use_case: str, tickets: TicketSystem,
                       doc_source: DocumentSource, store: ObjectStore,
                       data_dir: str | Path) -> list[tuple[str, AnchorResult]]:
    """Reads each anchor step's artifact off its Jira step ticket, in full. Raises
    AnchorMissing (naming the label) when a step ticket is absent or has no usable artifact."""
    steps_map = _steps_map(store, use_case)
    terms = load_terminology(use_case)["terms"]
    anchors = []
    for step_id in anchor_roles:
        label = terms.get(step_id, step_id)
        key = steps_map.get(step_id)
        if not key:
            raise AnchorMissing(step_id, f"no ticket was created for step {label!r}; run workflow start first")
        try:
            source = pick_anchor_source(key, tickets)
        except AnchorMissing as exc:
            raise AnchorMissing(key, f"{label}: {exc.reason}") from exc
        anchors.append((label, read_anchor(source, doc_source=doc_source, tickets=tickets,
                                            store=store, data_dir=data_dir)))
    return anchors


def _over_budget(anchors: list[tuple[str, AnchorResult]], cfg: dict) -> str | None:
    limits = cfg.get("analyst", {})
    max_chars, max_pdf = limits.get("max_anchor_chars", 400_000), limits.get("max_pdf_bytes", 4_500_000)
    sizes = [(f"{label} / {heading}", len(text)) for label, doc in anchors for heading, text in doc.sections]
    pdfs = [(label, len(d.data)) for label, doc in anchors for d in doc.documents]
    reasons = []
    if sum(n for _, n in sizes) > max_chars:
        reasons.append(f"text is over {max_chars} characters: " + ", ".join(f"{k}: {n}" for k, n in sizes))
    reasons += [f"{label} PDF is {n} bytes (limit {max_pdf})" for label, n in pdfs if n > max_pdf]
    return "; ".join(reasons) or None


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
            lines.append(f"  - {r.brief_description}")
            lines.append(f"  - {r.description}")
            for title, items in (("AC", r.acceptance_criteria), ("DoD", r.definition_of_done),
                                 ("Tests", r.test_coverage)):
                lines.append(f"  - {title}: " + "; ".join(items))
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
            def checklist(title, items):
                return f"<p><strong>{title}:</strong></p><ul>{''.join(f'<li>{e(i)}</li>' for i in items)}</ul>"
            parts.append(
                f"<h3>{e(r.requirement_id)} {e(r.title)}</h3>"
                f"<p><em>{e(r.brief_description)}</em></p>"
                f"<p>{e(r.description)}</p>"
                f"{checklist('Acceptance criteria', r.acceptance_criteria)}"
                f"{checklist('Definition of done', r.definition_of_done)}"
                f"{checklist('Test coverage', r.test_coverage)}"
                f"<p><strong>Source:</strong> {e(r.source_doc)} § {e(r.source_section)} "
                f"(stakeholder: {e(sr_ids)})</p>"
            )
    return "".join(parts)


class PlanGenerationFailed(RuntimeError):
    """The model run ended without a valid plan (call cap or timeout reached)."""


def _generate_plan(agent, content: list[dict], cfg: dict):
    """One bounded agent run. Strands feeds a schema-validation error back to the model with
    no retry cap of its own, so without these limits a field the model keeps getting wrong
    loops forever, re-sending the whole input every time. `max_model_calls` caps the loop and
    `timeout_seconds` cancels the run (checked between model calls and while streaming)."""
    limits = cfg.get("analyst", {})
    timer = None
    if cancel := getattr(agent, "cancel", None):
        timer = threading.Timer(limits.get("timeout_seconds", 900), cancel)
        timer.daemon = True
        timer.start()
    try:
        result = agent(content, structured_output_model=SolutionRequirementsPlan,
                       limits={"turns": limits.get("max_model_calls", 6)})
    finally:
        if timer:
            timer.cancel()
    if result.structured_output is None:
        raise PlanGenerationFailed(
            f"the model returned no valid plan (stop reason: {getattr(result, 'stop_reason', 'unknown')}). "
            "Raise analyst.max_model_calls / analyst.timeout_seconds in config.yaml, or check the agent log "
            "for the validation errors.")
    return result


def _generate_plan_native(model, system_prompt_text: str, content: list[dict], cfg: dict,
                           recorder: RunRecorder):
    """One native structured-output call, with no agent loop (analyst.native_structured_output,
    KB off only: with no tools the loop adds nothing). The provider constrains the response to
    the schema, so there is no validation-retry cycle; the flip side is no retry at all, so a
    cut-off or invalid response fails the run instead. Returns what _generate_plan returns."""
    timeout = cfg.get("analyst", {}).get("timeout_seconds", 900)
    messages = [{"role": "user", "content": content}]

    async def call():
        plan = None
        async for event in model.structured_output(SolutionRequirementsPlan, messages, system_prompt_text):
            plan = event.get("output", plan)
        return plan

    def run():
        return asyncio.run(asyncio.wait_for(call(), timeout))

    recorder.model_call()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:  # asyncio.run needs a thread with no running loop
            plan = pool.submit(run).result()
    except TimeoutError as exc:
        recorder.model_response(None, "timeout", exc)
        raise PlanGenerationFailed(f"the model call took longer than analyst.timeout_seconds ({timeout}s)") from exc
    except Exception as exc:
        recorder.model_response(None, None, exc)
        raise
    recorder.model_response(None, "end_turn", None)
    return SimpleNamespace(structured_output=plan, metrics=SimpleNamespace(cycle_count=1))


def llm_usage_key(use_case: str) -> str:
    return f"workflow/{use_case}/llm_usage.json"


def _record_llm_usage(result, *, store: ObjectStore, use_case: str, step_id: str | None,
                       dry_run: bool) -> None:
    """Adds this run's model calls to the step's running total (shown in the UI). Strands'
    `cycle_count` is the number of event-loop cycles, each of which is one model call; a
    throttling retry inside a cycle, or a run that raises before returning, isn't counted."""
    calls = getattr(getattr(result, "metrics", None), "cycle_count", 0)
    if not step_id or not calls:
        return
    usage = store.get_json(llm_usage_key(use_case)) or {}
    step = usage.get(step_id, {"calls": 0, "runs": 0})
    usage[step_id] = {"calls": step["calls"] + calls, "runs": step["runs"] + 1}
    store.put_json(llm_usage_key(use_case), usage, dry_run=dry_run)


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
        return doc_source.replace_page_body(existing.page_id, body_html, dry_run=dry_run)  # not appended: a re-run replaces the old plan
    parent_id = anchors.resolve_anchor_page_id("usecase_root", use_case=use_case, doc_source=doc_source)
    return doc_source.create_page(parent_id, title, body_html, dry_run=dry_run)


def run_solution_requirements(context: dict, *, doc_source: DocumentSource,
                               knowledge_index: KnowledgeIndex | None, tickets: TicketSystem,
                               store: ObjectStore, cfg: dict, dry_run: bool = True,
                               agent=None) -> dict:
    """Runs the task and keeps its live status (activity.py) up to date for the UI."""
    recorder = RunRecorder(store, context["use_case"], context.get("step_id"), dry_run)
    try:
        result = _run_solution_requirements(context, doc_source=doc_source, knowledge_index=knowledge_index,
                                             tickets=tickets, store=store, cfg=cfg, dry_run=dry_run,
                                             agent=agent, recorder=recorder)
    except Exception as exc:  # the orchestrator reports it on the ticket; this is for the UI
        recorder.finish("failed", f"{type(exc).__name__}: {exc}")
        raise
    recorder.finish(result["status"], result.get("reason"))
    return result


def _run_solution_requirements(context: dict, *, doc_source: DocumentSource,
                                knowledge_index: KnowledgeIndex | None, tickets: TicketSystem,
                                store: ObjectStore, cfg: dict, dry_run: bool, agent,
                                recorder: RunRecorder) -> dict:
    use_case, issue_key = context["use_case"], context["issue_key"]
    recorder.phase("reading_anchors", "reading the DDS and TDS")
    data_dir = cfg["data_dir"]

    from sdlc.agents.analyst.tasks import TASKS  # lazy: tasks imports this module

    try:
        anchor_docs = read_task_anchors(TASKS["solution_requirements"].anchor_roles, use_case=use_case,
                                         tickets=tickets, doc_source=doc_source, store=store,
                                         data_dir=data_dir)
    except AnchorMissing as exc:
        tickets.add_comment(
            issue_key, f"Can't read the anchor documents ({exc.reason}) on {exc.issue_key}. Attach or "
                       "link it there, then re-run.", dry_run=dry_run)
        return {"status": "blocked", "reason": str(exc)}

    if reason := _over_budget(anchor_docs, cfg):
        tickets.add_comment(issue_key, f"Anchor documents are too large to read in full: {reason}. "
                                       "Reduce them or raise the analyst limits in config.yaml.",
                            dry_run=dry_run)
        return {"status": "blocked", "reason": reason}

    recorder.phase("calling_model", "preparing the model call")
    content = _content_blocks(anchor_docs)
    recorder.record_input(system_prompt(kb=knowledge_index is not None), content, knowledge_index is not None)
    if agent is None and knowledge_index is None and cfg.get("analyst", {}).get("native_structured_output"):
        result = _generate_plan_native(_build_model(cfg), system_prompt(kb=False), content, cfg, recorder)
    else:
        agent = agent or _build_real_agent(knowledge_index, use_case, cfg, recorder)
        result = _generate_plan(agent, content, cfg)
    _record_llm_usage(result, store=store, use_case=use_case, step_id=context.get("step_id"), dry_run=dry_run)
    plan = result.structured_output
    plan.use_case = use_case
    recorder.result({"summary": plan.summary, "requirement_count": len(plan.requirements),
                     "usage": dict(getattr(getattr(result, "metrics", None), "accumulated_usage", None) or {}),
                     "plan": plan.model_dump()})

    recorder.phase("writing_plan")
    store.put_json(f"workflow/{use_case}/plan.json", plan.model_dump(), dry_run=dry_run)
    store.put_bytes(f"workflow/{use_case}/plan.md", _plan_markdown(plan).encode(), dry_run=dry_run)
    recorder.phase("publishing_page", "publishing the plan to Confluence")
    page = _publish_plan_page(plan, use_case=use_case, doc_source=doc_source, dry_run=dry_run)
    record_page(store, use_case, page, issue_key, dry_run=dry_run)

    workstream_count = len({r.workstream for r in plan.requirements})
    tickets.add_comment(
        issue_key,
        f"Plan ready: {len(plan.requirements)} requirements across {workstream_count} "
        f"workstream(s).\n\n{plan.summary}\n\nAdd the '{cfg['jira']['approval_label']}' "
        f"label to apply it, or '{cfg['jira']['reject_label']}' to reject it (add a comment saying why).",
        dry_run=dry_run,
    )
    tickets.add_remote_link(issue_key, page.url, page.title, dry_run=dry_run)
    return {"status": "planned", "requirement_count": len(plan.requirements)}
