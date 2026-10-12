"""Live status of one analyst-agent run, for the workflow UI. Without it a run that takes
minutes looks stuck: the progress snapshot is cached and only rebuilt when the poller cycle
(which is blocked inside the agent) ends.

The recorder overwrites one small doc per step in the ObjectStore (`workflow/<use_case>/
activity/<step_id>.json`) at each phase change, so it works for both profiles — the AgentCore
agent writes to the same S3 store the UI reads. It writes nothing on a dry run or without a
step id. Strands is imported lazily (see `hooks`), so importing this module stays cheap.
"""
from datetime import datetime, timezone

from sdlc.ports import ObjectStore


def activity_key(use_case: str, step_id: str) -> str:
    return f"workflow/{use_case}/activity/{step_id}.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _Hooks:
    """A Strands HookProvider: counts model calls."""

    def __init__(self, recorder: "RunRecorder"):
        self.recorder = recorder

    def register_hooks(self, registry, **kwargs) -> None:
        from strands.hooks import BeforeModelCallEvent
        registry.add_callback(BeforeModelCallEvent, self._before_model_call)

    def _before_model_call(self, event) -> None:
        self.recorder.model_call()


class RunRecorder:
    def __init__(self, store: ObjectStore, use_case: str, step_id: str | None, dry_run: bool):
        self.store, self.dry_run = store, dry_run
        self.key = activity_key(use_case, step_id) if step_id else None
        self.doc = {"running": True, "phase": "starting", "detail": "", "started_at": _now(),
                    "updated_at": _now(), "model_calls": 0, "kb_queries": 0,
                    "outcome": None, "error": None}
        self._flush()

    def _flush(self) -> None:
        if self.key and not self.dry_run:
            self.doc["updated_at"] = _now()
            self.store.put_json(self.key, self.doc, dry_run=False)

    def phase(self, name: str, detail: str = "") -> None:
        self.doc.update(phase=name, detail=detail)
        self._flush()

    def model_call(self) -> None:
        self.doc["model_calls"] += 1
        self.phase("calling_model", f"model call {self.doc['model_calls']}")

    def on_search(self, query: str) -> None:
        self.doc["kb_queries"] += 1
        self.phase("searching_kb", query)

    def hooks(self) -> _Hooks:
        return _Hooks(self)

    def finish(self, outcome: str, error: str | None = None) -> None:
        """outcome: "planned" | "blocked" | "failed" (whatever the task returned)."""
        self.doc.update(running=False, phase="done" if outcome == "planned" else outcome,
                        detail="", outcome=outcome, error=error)
        self._flush()
