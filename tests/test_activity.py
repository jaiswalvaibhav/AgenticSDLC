from sdlc.adapters.fake import FakeObjectStore
from sdlc.agents.activity import RunRecorder, activity_key

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
