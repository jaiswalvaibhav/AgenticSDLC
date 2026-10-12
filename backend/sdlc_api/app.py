"""Workflow UI backend: a thin FastAPI layer over the existing `sdlc` package. No
business logic lives here — selection is WorkflowRegistry.resolve_targets, a run is
orchestrator.workflow_start, progress is built by progress.py from Jira + the ObjectStore,
and poller.py keeps sdlc.sync_once running in the background after a local-profile trigger.

Local use only: bind 127.0.0.1 (see Makefile `api`); there is no auth. Secrets never
leave this process — routes return only the specific fields they name, never cfg.
"""
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from sdlc.agents.activity import list_runs, read_trace
from sdlc.config import load_config
from sdlc.sync_once import sync_once
from sdlc.wiring import confluence_client, jira_client, jira_mode, jira_project_key, kb_enabled, object_store
from sdlc.workflow import orchestrator
from sdlc.workflow.cleanup import cleanup_run
from sdlc.workflow.pages import delete_pages, recorded_pages
from sdlc.workflow.registry import Step, WorkflowError, WorkflowRegistry

from sdlc_api.mockjira import build_router
from sdlc_api.poller import Poller
from sdlc_api.progress import build_progress, live_activity, remember_selection, reset_run, run_info


class PreviewRequest(BaseModel):
    targets: list[str]


class DecisionRequest(BaseModel):
    feedback: str = ""


class PollerRequest(BaseModel):
    use_case: str
    profile: str = "local"
    confirm_external_llm: bool = False


class RunRequest(PollerRequest):
    targets: list[str]


def _step(step: Step) -> dict:
    return {"id": step.id, "artifact": step.artifact, "owner": step.owner,
            "inputs": list(step.inputs), "automation": step.automation, "optional": step.optional}


def _use_cases() -> list[str]:
    return sorted(p.parent.name for p in Path("usecases").glob("*/usecase.yaml"))


def _profiles(cfg: dict) -> list[str]:
    # aws only once aws-deploy has created the AgentCore runtime
    return ["local"] + (["aws"] if cfg.get("aws", {}).get("agent_runtime_arn") else [])


def create_app(cfg: dict | None = None, registry: WorkflowRegistry | None = None, *,
                tickets=None, store=None, sync=None, docs=None) -> FastAPI:
    """A factory (run with `uvicorn --factory`) so importing this module never reads
    config; tests pass their own cfg/registry and fake tickets/store/sync (otherwise built
    from cfg via sdlc.wiring, per run, so the chosen profile picks local vs S3 state). `docs`
    is the Confluence client "Start fresh" uses to delete pages (built only when asked to)."""
    cfg = cfg if cfg is not None else load_config()
    registry = registry or WorkflowRegistry.load()
    snapshots: dict[str, dict] = {}  # use_case -> last progress snapshot (rebuilt on demand)

    def run_cfg(use_case: str, profile: str, **extra) -> dict:
        return {**cfg, "use_case": use_case, "profile": profile, **extra}

    def rebuild(use_case: str, profile: str) -> dict:
        c = run_cfg(use_case, profile)
        snapshots[use_case] = build_progress(
            use_case=use_case, registry=registry, tickets=tickets or jira_client(c),
            store=store or object_store(c), cfg=c)
        return snapshots[use_case]

    def refresh(use_case: str, profile: str) -> dict:
        try:
            return rebuild(use_case, profile)
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"Jira request failed: {exc}")

    # the poller's own state is the laptop UI process's, so it always lives in the local store
    poller = Poller(cfg=cfg, sync=sync or sync_once, on_cycle=rebuild,
                    state_store=store or object_store(run_cfg(cfg["use_case"], "local")),
                    interval=cfg.get("ui", {}).get("poll_interval_seconds", 30))

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        poller.resume()
        yield
        poller.shutdown()

    app = FastAPI(title="Agentic Data SDLC workflow UI", lifespan=lifespan)
    app.state.poller = poller

    if jira_mode(cfg) == "mock":
        def mock_client(profile: str | None):
            c = run_cfg(cfg["use_case"], profile or cfg["profile"])
            return (tickets or jira_client(c)).acting_as("You")

        def mock_changed() -> None:  # a person changed the board: refresh, and let the poller react now
            snapshots.clear()
            if poller.run:
                poller.sync_now()
        app.include_router(build_router(mock_client, mock_changed))

    auto_confirm = bool(cfg.get("analyst", {}).get("auto_confirm_external_llm"))  # config.yaml: always on

    def check_request(body: PollerRequest) -> None:
        if body.use_case not in _use_cases():
            raise HTTPException(status_code=400, detail=f"unknown use case: {body.use_case}")
        if body.profile not in _profiles(cfg):
            raise HTTPException(status_code=400, detail=f"profile {body.profile!r} is not available")
        if (body.profile == "local" and os.environ.get("EXTERNAL_LLM_PROVIDER")
                and not (auto_confirm or body.confirm_external_llm)):
            raise HTTPException(
                status_code=400,
                detail="EXTERNAL_LLM_PROVIDER is set: calls are billed to your own account. "
                       "Confirm once at trigger (confirm_external_llm).")

    @app.get("/api/meta")
    def meta() -> dict:
        return {
            "use_cases": _use_cases(),
            "default_use_case": cfg["use_case"],
            "profiles": _profiles(cfg),
            "default_profile": cfg["profile"],
            "external_llm_provider": os.environ.get("EXTERNAL_LLM_PROVIDER") or None,
            "auto_confirm_external_llm": auto_confirm,
            "knowledge_base": dict(zip(("enabled", "reason"), kb_enabled(cfg))),
            "jira_mode": jira_mode(cfg),
            "jira_project_key": jira_project_key(cfg),
            "jira_base_url": cfg["atlassian"]["base_url"],
        }

    @app.get("/api/workflow")
    def workflow() -> dict:
        return {"steps": [_step(s) for s in registry.all_steps()]}

    @app.post("/api/preview")
    def preview(body: PreviewRequest) -> dict:
        """What `workflow start` would create for these ticked steps — no Jira calls."""
        try:
            selection = registry.resolve_targets(body.targets)
        except WorkflowError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"generate": [_step(s) for s in selection.selected],
                "provide": [_step(s) for s in selection.artifact_only]}

    @app.post("/api/runs")
    def start_run(body: RunRequest) -> dict:
        """Creates the epic + step tickets for the ticked steps (live Jira writes — the UI
        previews first, see /api/preview), remembers which steps were ticked and, for the
        local profile, starts the poller (the aws profile's Lambda already polls)."""
        check_request(body)
        try:
            selection = registry.resolve_targets(body.targets)
        except WorkflowError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        c = run_cfg(body.use_case, body.profile)
        run_store = store or object_store(c)
        try:
            steps_map = orchestrator.workflow_start(
                tickets=tickets or jira_client(c), store=run_store, registry=registry, cfg=c,
                use_case=body.use_case, targets=body.targets, dry_run=False)
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"Jira request failed: {exc}")
        generate, provide = [s.id for s in selection.selected], [s.id for s in selection.artifact_only]
        remember_selection(run_store, body.use_case, generate, provide, jira_project_key(cfg))
        snapshots.pop(body.use_case, None)
        if body.profile == "local":
            poller.start(body.use_case, body.profile, body.confirm_external_llm)
        return {"use_case": body.use_case, "steps": steps_map, "generate": generate, "provide": provide,
                "poller": poller.snapshot()}

    @app.get("/api/runs/{use_case}/progress")
    def progress(use_case: str, profile: str | None = None) -> dict:
        """The last snapshot (the UI polls this every few seconds, so it doesn't hit Jira
        each time); built on first request. POST .../refresh rebuilds it."""
        if use_case not in _use_cases():
            raise HTTPException(status_code=404, detail=f"unknown use case: {use_case}")
        snapshot = snapshots.get(use_case) or refresh(use_case, profile or cfg["profile"])
        activity = live_activity(store or object_store(run_cfg(use_case, profile or cfg["profile"])), use_case)
        return {**snapshot, "steps": [{**row, "activity": activity.get(row["step_id"])} for row in snapshot["steps"]],
                "poller": poller.snapshot()}

    @app.post("/api/runs/{use_case}/refresh")
    def refresh_progress(use_case: str, profile: str | None = None) -> dict:
        if use_case not in _use_cases():
            raise HTTPException(status_code=404, detail=f"unknown use case: {use_case}")
        return {**refresh(use_case, profile or cfg["profile"]), "poller": poller.snapshot()}  # no activity: the next poll adds it

    def agent_run_store(use_case: str, step_id: str, profile: str | None):
        if use_case not in _use_cases():
            raise HTTPException(status_code=404, detail=f"unknown use case: {use_case}")
        if step_id not in {s.id for s in registry.all_steps()}:
            raise HTTPException(status_code=404, detail=f"unknown step: {step_id}")
        return store or object_store(run_cfg(use_case, profile or cfg["profile"]))

    @app.get("/api/runs/{use_case}/steps/{step_id}/agent-runs")
    def agent_runs(use_case: str, step_id: str, profile: str | None = None) -> dict:
        """The step's recorded agent runs, newest first (what the UI's "View log" lists)."""
        return {"runs": list_runs(agent_run_store(use_case, step_id, profile), use_case, step_id)}

    @app.get("/api/runs/{use_case}/steps/{step_id}/agent-runs/{run_id}")
    def agent_run(use_case: str, step_id: str, run_id: str, profile: str | None = None) -> dict:
        """One run's trace: what the model was sent, each response, each knowledge-base query."""
        if not re.fullmatch(r"\d{8}T\d{12}Z", run_id):
            raise HTTPException(status_code=404, detail="unknown run")
        trace = read_trace(agent_run_store(use_case, step_id, profile), use_case, step_id, run_id)
        if trace is None:
            raise HTTPException(status_code=404, detail="unknown run")
        return trace

    @app.post("/api/runs/{use_case}/steps/{step_id}/rerun")
    def rerun(use_case: str, step_id: str, profile: str | None = None) -> dict:
        """After a rejection: asks the orchestrator to run the agent again for this step."""
        if use_case not in _use_cases():
            raise HTTPException(status_code=404, detail=f"unknown use case: {use_case}")
        c = run_cfg(use_case, profile or cfg["profile"])
        run_store = store or object_store(c)
        state = orchestrator._run_state(run_store, use_case)
        if state.get(step_id) != "rejected":
            raise HTTPException(status_code=409, detail=f"{step_id} has no rejected plan to re-run")
        state[step_id] = "rerun_requested"
        orchestrator._save_run_state(run_store, use_case, state, False)
        snapshots.pop(use_case, None)
        woke = bool(poller.run and poller.run["use_case"] == use_case and poller.sync_now())
        return {"acting_now": woke}

    @app.post("/api/runs/{use_case}/steps/{step_id}/{decision}")
    def decide(use_case: str, step_id: str, decision: Literal["approve", "reject"],
               body: DecisionRequest | None = None, profile: str | None = None) -> dict:
        """Approve or reject a step's plan from the UI: adds the same sdlc-approved /
        sdlc-rejected label you would add in Jira, and wakes the poller so the orchestrator
        acts on it now rather than at the next interval. A rejection needs feedback, which
        goes onto the ticket as a comment; the plan is not regenerated."""
        if use_case not in _use_cases():
            raise HTTPException(status_code=404, detail=f"unknown use case: {use_case}")
        c = run_cfg(use_case, profile or cfg["profile"])
        run_store = store or object_store(c)
        key = orchestrator._steps_map(run_store, use_case).get(step_id)
        if not key:
            raise HTTPException(status_code=404, detail=f"no ticket for step {step_id!r} in this run")
        if orchestrator._run_state(run_store, use_case).get(step_id) != "awaiting_approval":
            raise HTTPException(status_code=409, detail=f"{step_id} has no plan awaiting approval")
        label = cfg["jira"]["approval_label" if decision == "approve" else "reject_label"]
        feedback = (body.feedback if body else "").strip()
        if decision == "reject" and not feedback:
            raise HTTPException(status_code=422, detail="say why the plan is rejected")
        try:
            client = tickets or jira_client(c)
            if {cfg["jira"]["approval_label"], cfg["jira"]["reject_label"]} & set(client.get_issue(key).labels):
                raise HTTPException(status_code=409, detail="a decision is already pending on this ticket")
            if decision == "reject":
                client.add_comment(key, f"Plan rejected: {feedback}", dry_run=False)
            client.add_label(key, label, dry_run=False)
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"Jira request failed: {exc}")
        snapshots.pop(use_case, None)
        woke = bool(poller.run and poller.run["use_case"] == use_case and poller.sync_now())
        return {"key": key, "label": label, "acting_now": woke}

    @app.get("/api/runs/{use_case}/info")
    def info(use_case: str, profile: str | None = None) -> dict:
        """Which run the saved state describes (project, start time, stale?) — no Jira calls."""
        if use_case not in _use_cases():
            raise HTTPException(status_code=404, detail=f"unknown use case: {use_case}")
        c = run_cfg(use_case, profile or cfg["profile"])
        return run_info(store or object_store(c), use_case, jira_project_key(cfg))

    @app.post("/api/runs/{use_case}/reset")
    def reset(use_case: str, profile: str | None = None, delete_pages_too: bool = False) -> dict:
        """'Start fresh': forgets the saved run for this use case (and stops the poller if it
        is polling it). Real Jira tickets are left as they are; with the mock Jira they are
        deleted too. delete_pages_too also deletes the Confluence pages the run created (they
        go to the space trash), never pages a person linked as inputs; without it they stay,
        and the next run rewrites the plan page instead of appending to it."""
        if use_case not in _use_cases():
            raise HTTPException(status_code=404, detail=f"unknown use case: {use_case}")
        if poller.run and poller.run["use_case"] == use_case:
            poller.stop()
        c = run_cfg(use_case, profile or cfg["profile"])
        run_store = store or object_store(c)
        doc_source = (docs or confluence_client(c)) if delete_pages_too else None
        deleted: list[str] = []
        page_errors: dict[str, str] = {}
        try:
            if jira_mode(cfg) == "mock":
                result = cleanup_run(tickets=tickets or jira_client(c), doc_source=doc_source,
                                     store=run_store, use_case=use_case, dry_run=False)
                if result["failed"]:
                    raise HTTPException(status_code=409, detail=f"could not delete: {result['failed']}")
                deleted, page_errors = result["issues"], result["page_errors"]
            else:
                if doc_source:
                    page_errors = delete_pages(doc_source, run_store, use_case,
                                               set(recorded_pages(run_store, use_case)), dry_run=False)
                reset_run(run_store, use_case)
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"Confluence request failed: {exc}")
        snapshots.pop(use_case, None)
        return {"reset": True, "deleted": deleted, "page_errors": page_errors, "poller": poller.snapshot()}

    @app.get("/api/poller")
    def poller_status() -> dict:
        return poller.snapshot()

    @app.post("/api/poller/start")
    def poller_start(body: PollerRequest) -> dict:
        check_request(body)
        if body.profile != "local":
            raise HTTPException(status_code=400, detail="the aws profile is polled by the scheduled "
                                                        "orchestrator Lambda, not by the UI")
        poller.start(body.use_case, body.profile, body.confirm_external_llm)
        return poller.snapshot()

    @app.post("/api/poller/stop")
    def poller_stop() -> dict:
        poller.stop()
        return poller.snapshot()

    @app.post("/api/poller/sync-now")
    def poller_sync_now() -> dict:
        if not poller.sync_now():
            raise HTTPException(status_code=409, detail="the poller is not running")
        return poller.snapshot()

    return app
