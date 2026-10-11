"""Workflow UI backend: a thin FastAPI layer over the existing `sdlc` package. No
business logic lives here — selection is WorkflowRegistry.resolve_targets, and the
rest (workflow start, polling, progress) is wired in by later routes.

Local use only: bind 127.0.0.1 (see Makefile `api`); there is no auth. Secrets never
leave this process — routes return only the specific fields they name, never cfg.
"""
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from sdlc.config import load_config
from sdlc.workflow.registry import Step, WorkflowError, WorkflowRegistry


class PreviewRequest(BaseModel):
    targets: list[str]


def _step(step: Step) -> dict:
    return {"id": step.id, "artifact": step.artifact, "owner": step.owner,
            "inputs": list(step.inputs), "automation": step.automation, "optional": step.optional}


def _use_cases() -> list[str]:
    return sorted(p.parent.name for p in Path("usecases").glob("*/usecase.yaml"))


def create_app(cfg: dict | None = None, registry: WorkflowRegistry | None = None) -> FastAPI:
    """A factory (run with `uvicorn --factory`) so importing this module never reads
    config; tests pass their own cfg/registry."""
    cfg = cfg if cfg is not None else load_config()
    registry = registry or WorkflowRegistry.load()
    app = FastAPI(title="Agentic SDLC workflow UI")

    @app.get("/api/meta")
    def meta() -> dict:
        return {
            "use_cases": _use_cases(),
            "default_use_case": cfg["use_case"],
            # aws only once aws-deploy has created the AgentCore runtime
            "profiles": ["local"] + (["aws"] if cfg.get("aws", {}).get("agent_runtime_arn") else []),
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

    return app
