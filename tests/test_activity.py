from sdlc.adapters.fake import FakeObjectStore
from sdlc.agents.activity import RunRecorder, activity_key, trace_key

UC = "uc"
KEY = activity_key(UC, "step")


def test_phases_and_counters_are_written_as_they_happen():
    store = FakeObjectStore()
    recorder = RunRecorder(store, UC, "step", dry_run=False)
    assert store.get_json(KEY)["running"] is True

    recorder.model_call()
    assert store.get_json(KEY)["phase"] == "calling_model" and store.get_json(KEY)["model_calls"] == 1
    recorder.on_search("order grain")
    doc = store.get_json(KEY)
    assert (doc["phase"], doc["detail"], doc["kb_queries"]) == ("searching_kb", "order grain", 1)

    recorder.finish("planned")
    doc = store.get_json(KEY)
    assert doc["running"] is False and doc["phase"] == "done" and doc["outcome"] == "planned"


def test_a_failed_or_blocked_run_keeps_its_outcome_and_reason():
    store = FakeObjectStore()
    recorder = RunRecorder(store, UC, "step", dry_run=False)
    recorder.finish("blocked", "no DDS attached")
    doc = store.get_json(KEY)
    assert (doc["phase"], doc["error"], doc["running"]) == ("blocked", "no DDS attached", False)


def test_nothing_is_written_on_a_dry_run_or_without_a_step_id():
    store = FakeObjectStore()
    RunRecorder(store, UC, "step", dry_run=True).phase("calling_model")
    RunRecorder(store, UC, None, dry_run=False).phase("calling_model")
    assert store._data == {}


def test_the_strands_hook_counts_each_model_call():
    from strands.hooks import BeforeModelCallEvent
    store = FakeObjectStore()
    recorder = RunRecorder(store, UC, "step", dry_run=False)
    registered = {}

    class Registry:
        def add_callback(self, event_type, callback):
            registered[event_type] = callback

    recorder.hooks().register_hooks(Registry())
    registered[BeforeModelCallEvent](object())
    registered[BeforeModelCallEvent](object())
    assert store.get_json(KEY)["model_calls"] == 2


def test_the_hook_provider_is_accepted_by_a_real_strands_agent():
    from strands import Agent
    recorder = RunRecorder(FakeObjectStore(), UC, "step", dry_run=False)
    Agent(model="fake", hooks=[recorder.hooks()], callback_handler=None)  # no model call; construction only


def test_the_trace_keeps_the_input_each_response_and_each_kb_query_with_its_chunks():
    from sdlc.agents.activity import list_runs, read_trace
    from sdlc.ports import Chunk

    store = FakeObjectStore()
    recorder = RunRecorder(store, UC, "step", dry_run=False)
    recorder.record_input("SYSTEM", [
        {"text": "## DDS\n\nLayers"},
        {"image": {"format": "png", "source": {"bytes": b"12345"}}},
        {"document": {"format": "pdf", "name": "TDS", "source": {"bytes": b"123"}}}], kb_enabled=True)
    recorder.model_call()
    recorder.model_response({"content": [{"text": "Let me look that up"},
                                          {"toolUse": {"name": "search_knowledge", "input": {"query": "orders"}}}]},
                            "tool_use", None)
    recorder.on_search("orders")
    recorder.on_search_result([Chunk(text="Order grain", score=0.9, page_title="SR", page_url="u", s3_uri="s3://x")])
    recorder.model_call()
    recorder.model_response(None, None, RuntimeError("throttled"))
    recorder.result({"summary": "S", "requirement_count": 1})
    recorder.finish("planned")

    trace = read_trace(store, UC, "step", recorder.run_id)
    assert trace["system_prompt"] == "SYSTEM" and trace["kb_enabled"] is True
    assert trace["input"] == [{"kind": "text", "text": "## DDS\n\nLayers"},
                              {"kind": "image", "format": "png", "name": None, "bytes": 5},
                              {"kind": "document", "format": "pdf", "name": "TDS", "bytes": 3}]
    first, search, second = trace["events"]
    assert first["content"] == [{"kind": "text", "text": "Let me look that up"},
                                {"kind": "tool_use", "name": "search_knowledge", "input": {"query": "orders"}}]
    assert first["stop_reason"] == "tool_use"
    assert search["query"] == "orders" and search["chunks"] == [
        {"title": "SR", "score": 0.9, "url": "u", "s3_uri": "s3://x", "text": "Order grain"}]
    assert second["error"] == "RuntimeError: throttled"
    assert trace["outcome"] == "planned" and trace["result"]["requirement_count"] == 1
    assert list_runs(store, UC, "step") == [{
        "run_id": recorder.run_id, "started_at": trace["started_at"], "outcome": "planned",
        "model_calls": 2, "kb_queries": 1}]


def test_only_the_newest_runs_are_kept():
    from sdlc.agents.activity import TRACE_KEEP, list_runs, read_trace
    store = FakeObjectStore()
    recorders = []
    for _ in range(TRACE_KEEP + 2):
        recorder = RunRecorder(store, UC, "step", dry_run=False)
        recorder.finish("planned")
        recorders.append(recorder)
    kept = [r["run_id"] for r in list_runs(store, UC, "step")]
    assert kept == [r.run_id for r in reversed(recorders)][:TRACE_KEEP]
    assert read_trace(store, UC, "step", recorders[0].run_id) is None  # pruned with its index entry
    assert read_trace(store, UC, "step", recorders[-1].run_id) is not None


def test_a_failed_tool_call_is_recorded_but_a_successful_one_is_not():
    from types import SimpleNamespace

    from strands.hooks import AfterToolCallEvent
    store = FakeObjectStore()
    recorder = RunRecorder(store, UC, "step", dry_run=False)
    registered = {}

    class Registry:
        def add_callback(self, event_type, callback):
            registered[event_type] = callback

    recorder.hooks().register_hooks(Registry())
    use = {"name": "SolutionRequirementsPlan"}
    registered[AfterToolCallEvent](SimpleNamespace(tool_use=use, result={
        "status": "success", "content": [{"text": "Successfully validated"}]}))
    registered[AfterToolCallEvent](SimpleNamespace(tool_use=use, result={
        "status": "error", "content": [{"text": "Field 'requirements -> 3 -> workstream': bad"}, {"text": "x" * 9000}]}))
    (event,) = store.get_json(trace_key(UC, "step", recorder.run_id))["events"]
    assert event["type"] == "tool_error" and event["tool"] == "SolutionRequirementsPlan"
    assert event["text"].startswith("Field 'requirements -> 3 -> workstream'") and len(event["text"]) == 4000
