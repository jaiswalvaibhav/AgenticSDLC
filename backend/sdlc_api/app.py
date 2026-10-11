"""Workflow UI backend: a thin FastAPI layer over the existing `sdlc` package. No
business logic lives here — selection is WorkflowRegistry.resolve_targets, a run is
orchestrator.workflow_start, and progress is built by progress.py from Jira + the
ObjectStore. (The background poller is wired in by a later step.)

Local use only: bind 127.0.0.1 (see Makefile `api`); there is no auth. Secrets never
leave this process — routes return only the specific fields they name, never cfg.
"""
import os
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from sdlc.config import load_config
from sdlc.wiring import jira_client, object_store
from sdlc.workflow import orchestrator
from sdlc.workflow.registry import Step, WorkflowError, WorkflowRegistry

from sdlc_api.progress import build_progress, remember_selection


class PreviewRequest(BaseModel):
    targets: list[str]


class RunRequest(BaseModel):
    use_case: str
    targets: list[str]
    profile: str = "local"
    confirm_external_llm: bool = False


def _step(step: Step) -> dict:
    return {"id": step.id, "artifact": step.artifact, "owner": step.owner,
            "inputs": list(step.inputs), "automation": step.automation, "optional": step.optional}


def _use_cases() -> list[str]:
    return sorted(p.parent.name for p in Path("usecases").glob("*/usecase.yaml"))


def _profiles(cfg: dict) -> list[str]:
    # aws only once aws-deploy has created the AgentCore runtime
    return ["local"] + (["aws"] if cfg.get("aws", {}).get("agent_runtime_arn") else [])


def create_app(cfg: dict | None = None, registry: WorkflowRegistry | None = None, *,
                tickets=None, store=None) -> FastAPI:
    """A factory (run with `uvicorn --factory`) so importing this module never reads
    config; tests pass their own cfg/registry and fake tickets/store (otherwise built
    from cfg via sdlc.wiring, per run, so the chosen profile picks local vs S3 state)."""
    cfg = cfg if cfg is not None else load_config()
    registry = registry or WorkflowRegistry.load()
    app = FastAPI(title="Agentic SDLC workflow UI")
    snapshots: dict[str, dict] = {}  # use_case -> last progress snapshot (rebuilt on demand)

    def run_cfg(use_case: str, profile: str, **extra) -> dict:
        return {**cfg, "use_case": use_case, "profile": profile, **extra}

    def refresh(use_case: str, profile: str) -> dict:
        c = run_cfg(use_case, profile)
        try:
            snapshots[use_case] = build_progress(
                use_case=use_case, registry=registry, tickets=tickets or jira_client(c),
                store=store or object_store(c), cfg=c)
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"Jira request failed: {exc}")
        return snapshots[use_case]

    @app.get("/api/meta")
    def meta() -> dict:
        return {
            "use_cases": _use_cases(),
            "default_use_case": cfg["use_case"],
            "profiles": _profiles(cfg),
            "default_profile": cfg["profile"],
            "external_llm_provider": os.environ.get("EXTERNAL_LLM_PROVIDER") or None,
            "jira_project_key": cfg["jira"]["project_key"],
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
        previews first, see /api/preview) and remembers which steps were ticked."""
        if body.use_case not in _use_cases():
            raise HTTPException(status_code=400, detail=f"unknown use case: {body.use_case}")
        if body.profile not in _profiles(cfg):
            raise HTTPException(status_code=400, detail=f"profile {body.profile!r} is not available")
        if (body.profile == "local" and os.environ.get("EXTERNAL_LLM_PROVIDER")
                and not body.confirm_external_llm):
            raise HTTPException(
                status_code=400,
                detail="EXTERNAL_LLM_PROVIDER is set: calls are billed to your own account. "
                       "Confirm once at trigger (confirm_external_llm).")
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
        remember_selection(run_store, body.use_case, generate, provide)
        snapshots.pop(body.use_case, None)
        return {"use_case": body.use_case, "steps": steps_map, "generate": generate, "provide": provide}

    @app.get("/api/runs/{use_case}/progress")
    def progress(use_case: str, profile: str | None = None) -> dict:
        """The last snapshot (the UI polls this every few seconds, so it doesn't hit Jira
        each time); built on first request. POST .../refresh rebuilds it."""
        if use_case not in _use_cases():
            raise HTTPException(status_code=404, detail=f"unknown use case: {use_case}")
        return snapshots.get(use_case) or refresh(use_case, profile or cfg["profile"])

    @app.post("/api/runs/{use_case}/refresh")
    def refresh_progress(use_case: str, profile: str | None = None) -> dict:
        if use_case not in _use_cases():
            raise HTTPException(status_code=404, detail=f"unknown use case: {use_case}")
        return refresh(use_case, profile or cfg["profile"])

    return app
