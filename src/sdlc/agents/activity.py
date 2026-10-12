"""What the analyst agent is doing and did, for the workflow UI.

Two kinds of doc, both in the ObjectStore (so the AgentCore agent on the aws profile feeds the
same UI as a local run):
- live status, one small doc per step, overwritten at each phase change
  (`workflow/<use_case>/activity/<step_id>.json`): without it a run that takes minutes looks
  stuck, because the progress snapshot is cached and only rebuilt when the poller cycle (which
  is blocked inside the agent) ends.
- a trace per run (`workflow/<use_case>/runs/<step_id>/<run_id>.json`, newest TRACE_KEEP kept,
  listed by `.../index.json` since an ObjectStore can't list): what the model was sent (system
  prompt and the anchor text in full; images and PDFs as name/size stubs), each model response,
  each knowledge-base query with the chunks it returned, and the plan.

Nothing is written on a dry run or without a step id. Strands is imported lazily (see `_Hooks`),
so importing this module stays cheap.
"""
from datetime import datetime, timezone

from sdlc.ports import ObjectStore

TRACE_KEEP = 10


def activity_key(use_case: str, step_id: str) -> str:
    return f"workflow/{use_case}/activity/{step_id}.json"


def trace_key(use_case: str, step_id: str, run_id: str) -> str:
    return f"workflow/{use_case}/runs/{step_id}/{run_id}.json"


def index_key(use_case: str, step_id: str) -> str:
    return f"workflow/{use_case}/runs/{step_id}/index.json"


def list_runs(store: ObjectStore, use_case: str, step_id: str) -> list[dict]:
    """Newest first: [{run_id, started_at, outcome, model_calls, kb_queries}]."""
    return (store.get_json(index_key(use_case, step_id)) or {}).get("runs", [])


def read_trace(store: ObjectStore, use_case: str, step_id: str, run_id: str) -> dict | None:
    return store.get_json(trace_key(use_case, step_id, run_id))


def clear_step(store: ObjectStore, use_case: str, step_id: str, dry_run: bool = False) -> None:
    """Forgets a step's live status and all its recorded runs (used when a run is reset)."""
    for run in list_runs(store, use_case, step_id):
        store.delete(trace_key(use_case, step_id, run["run_id"]), dry_run=dry_run)
    store.delete(index_key(use_case, step_id), dry_run=dry_run)
    store.delete(activity_key(use_case, step_id), dry_run=dry_run)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _block(block: dict) -> dict:
    """One content block as stored in the trace: text in full, binary content as a stub."""
    if "text" in block:
        return {"kind": "text", "text": block["text"]}
    if "toolUse" in block:
        return {"kind": "tool_use", "name": block["toolUse"].get("name"), "input": block["toolUse"].get("input")}
    for kind in ("image", "document"):
        if kind in block:
            body = block[kind]
            return {"kind": kind, "format": body.get("format"), "name": body.get("name"),
                    "bytes": len(body.get("source", {}).get("bytes", b""))}
    return {"kind": next(iter(block), "unknown")}


class _Hooks:
    """A Strands HookProvider: records each model call and its response."""

    def __init__(self, recorder: "RunRecorder"):
        self.recorder = recorder

    def register_hooks(self, registry, **kwargs) -> None:
        from strands.hooks import AfterModelCallEvent, BeforeModelCallEvent
        registry.add_callback(BeforeModelCallEvent, self._before_model_call)
        registry.add_callback(AfterModelCallEvent, self._after_model_call)

    def _before_model_call(self, event) -> None:
        self.recorder.model_call()

    def _after_model_call(self, event) -> None:
        response = event.stop_response
        self.recorder.model_response(response.message if response else None,
                                     response.stop_reason if response else None, event.exception)


class RunRecorder:
    def __init__(self, store: ObjectStore, use_case: str, step_id: str | None, dry_run: bool):
        self.store, self.use_case, self.step_id = store, use_case, step_id
        self.live = bool(step_id) and not dry_run
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        self.doc = {"running": True, "run_id": self.run_id, "phase": "starting", "detail": "",
                    "started_at": _now(), "updated_at": _now(), "model_calls": 0, "kb_queries": 0,
                    "outcome": None, "error": None}
        self.trace = {"run_id": self.run_id, "step_id": step_id, "started_at": self.doc["started_at"],
                      "outcome": None, "error": None, "kb_enabled": None, "system_prompt": None,
                      "input": [], "events": [], "result": None}
        if self.live:
            self._register()
        self._flush()

    # ------------------------------------------------------------------ storage --
    def _flush(self) -> None:
        if self.live:
            self.doc["updated_at"] = _now()
            self.store.put_json(activity_key(self.use_case, self.step_id), self.doc, dry_run=False)

    def _flush_trace(self) -> None:
        if self.live:
            self.store.put_json(trace_key(self.use_case, self.step_id, self.run_id), self.trace, dry_run=False)

    def _entry(self) -> dict:
        return {"run_id": self.run_id, "started_at": self.doc["started_at"], "outcome": self.doc["outcome"],
                "model_calls": self.doc["model_calls"], "kb_queries": self.doc["kb_queries"]}

    def _register(self) -> None:
        runs = [self._entry(), *list_runs(self.store, self.use_case, self.step_id)]
        for old in runs[TRACE_KEEP:]:
            self.store.delete(trace_key(self.use_case, self.step_id, old["run_id"]), dry_run=False)
        self.store.put_json(index_key(self.use_case, self.step_id), {"runs": runs[:TRACE_KEEP]}, dry_run=False)

    def _update_index(self) -> None:
        runs = [self._entry() if r["run_id"] == self.run_id else r
                for r in list_runs(self.store, self.use_case, self.step_id)]
        self.store.put_json(index_key(self.use_case, self.step_id), {"runs": runs}, dry_run=False)

    # ------------------------------------------------------------------- status --
    def phase(self, name: str, detail: str = "") -> None:
        self.doc.update(phase=name, detail=detail)
        self._flush()

    def model_call(self) -> None:
        self.doc["model_calls"] += 1
        self.trace["events"].append({"type": "model_call", "n": self.doc["model_calls"],
                                     "started_at": _now(), "ended_at": None, "stop_reason": None,
                                     "error": None, "content": []})
        self.phase("calling_model", f"model call {self.doc['model_calls']}")
        self._flush_trace()

    def hooks(self) -> _Hooks:
        return _Hooks(self)

    # -------------------------------------------------------------------- trace --
    def record_input(self, system_prompt: str, blocks: list[dict], kb_enabled: bool) -> None:
        self.trace.update(system_prompt=system_prompt, input=[_block(b) for b in blocks], kb_enabled=kb_enabled)
        self._flush_trace()

    def model_response(self, message: dict | None, stop_reason: str | None, exception: Exception | None) -> None:
        call = next((e for e in reversed(self.trace["events"]) if e["type"] == "model_call"), None)
        if call is None:
            return
        call.update(ended_at=_now(), stop_reason=stop_reason,
                    error=f"{type(exception).__name__}: {exception}" if exception else None,
                    content=[_block(b) for b in (message or {}).get("content", [])])
        self._flush_trace()

    def on_search(self, query: str) -> None:
        self.doc["kb_queries"] += 1
        self.trace["events"].append({"type": "kb_search", "query": query, "started_at": _now(),
                                     "ended_at": None, "chunks": [], "error": None})
        self.phase("searching_kb", query)
        self._flush_trace()

    def on_search_result(self, chunks: list, error: str | None = None) -> None:
        search = next((e for e in reversed(self.trace["events"]) if e["type"] == "kb_search"), None)
        if search is None:
            return
        search.update(ended_at=_now(), error=error, chunks=[
            {"title": c.page_title, "score": c.score, "url": c.page_url, "s3_uri": c.s3_uri, "text": c.text}
            for c in chunks])
        self._flush_trace()

    def result(self, data: dict) -> None:
        self.trace["result"] = data
        self._flush_trace()

    def finish(self, outcome: str, error: str | None = None) -> None:
        """outcome: "planned" | "blocked" | "failed" (whatever the task returned)."""
        self.doc.update(running=False, phase="done" if outcome == "planned" else outcome,
                        detail="", outcome=outcome, error=error)
        self.trace.update(outcome=outcome, error=error)
        self._flush()
        self._flush_trace()
        if self.live:
            self._update_index()
