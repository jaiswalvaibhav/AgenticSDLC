import threading
import time

from sdlc.adapters.fake import FakeObjectStore
from sdlc_api.poller import CHECKPOINT_KEY, STATE_KEY, Poller


def _wait(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.005)
    return False


def _poller(sync, *, store=None, interval=0.01, on_cycle=lambda use_case, profile: None):
    return Poller(cfg={"base": 1}, sync=sync, on_cycle=on_cycle,
                  state_store=store or FakeObjectStore(), interval=interval)


def test_cycles_repeat_and_merge_run_settings_into_cfg():
    seen = []
    poller = _poller(lambda cfg, dry_run: seen.append((cfg, dry_run)) or {"status_events": 1})
    poller.start("uc", "local", external_llm_confirmed=True)
    try:
        assert _wait(lambda: poller.snapshot()["cycles"] >= 3)
    finally:
        poller.stop(wait=2)

    cfg, dry_run = seen[0]
    assert dry_run is False
    assert cfg == {"base": 1, "use_case": "uc", "profile": "local", "external_llm_confirmed": True}
    assert poller.snapshot()["last_result"] == {"status_events": 1}


def test_a_failing_cycle_is_recorded_and_the_loop_carries_on():
    calls = []

    def flaky(cfg, dry_run):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("jira down")
        return {"status_events": 0}

    poller = _poller(flaky)
    poller.start("uc", "local")
    try:
        assert _wait(lambda: len(calls) >= 3)
        assert poller.snapshot()["last_error"] is None  # cleared by the later good cycle
        assert poller.snapshot()["last_result"] == {"status_events": 0}
    finally:
        poller.stop(wait=2)

    failed = _poller(lambda cfg, dry_run: 1 / 0)
    failed.start("uc", "local")
    try:
        assert _wait(lambda: failed.snapshot()["last_error"] is not None)
        assert failed.snapshot()["last_error"] == "ZeroDivisionError: division by zero"
        assert _wait(lambda: failed.snapshot()["cycles"] >= 2)
    finally:
        failed.stop(wait=2)


def test_on_cycle_failure_is_recorded_too():
    def broken(use_case, profile):
        raise RuntimeError("snapshot failed")

    poller = _poller(lambda cfg, dry_run: {}, on_cycle=broken)
    poller.start("uc", "local")
    try:
        assert _wait(lambda: poller.snapshot()["last_error"] == "RuntimeError: snapshot failed")
    finally:
        poller.stop(wait=2)


def test_stop_halts_the_loop():
    calls = []
    poller = _poller(lambda cfg, dry_run: calls.append(1) or {})
    poller.start("uc", "local")
    assert _wait(lambda: len(calls) >= 2)

    poller.stop(wait=2)
    settled = len(calls)
    time.sleep(0.1)

    assert len(calls) == settled and poller.snapshot()["active"] is False


def test_sync_now_wakes_the_loop_early_and_is_refused_when_stopped():
    calls = []
    poller = _poller(lambda cfg, dry_run: calls.append(1) or {}, interval=3600)
    assert poller.sync_now() is False

    poller.start("uc", "local")
    try:
        assert _wait(lambda: len(calls) == 1)
        assert poller.sync_now() is True
        assert _wait(lambda: len(calls) == 2)
    finally:
        poller.stop(wait=2)


def test_cycles_never_overlap_across_a_restart():
    running, peak, lock = 0, 0, threading.Lock()

    def slow(cfg, dry_run):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.05)
        with lock:
            running -= 1
        return {}

    poller = _poller(slow)
    poller.start("a", "local")
    time.sleep(0.02)  # a cycle for "a" is in flight
    poller.start("b", "local")  # replaces it; the old thread must finish its cycle first
    try:
        assert _wait(lambda: poller.snapshot()["cycles"] >= 2)
    finally:
        poller.stop(wait=2)

    assert peak == 1 and poller.snapshot()["use_case"] is None


def test_starting_again_with_the_same_settings_is_a_noop():
    calls = []
    poller = _poller(lambda cfg, dry_run: calls.append(1) or {}, interval=3600)
    poller.start("uc", "local")
    try:
        assert _wait(lambda: len(calls) == 1)
        poller.start("uc", "local")
        time.sleep(0.05)
        assert len(calls) == 1
    finally:
        poller.stop(wait=2)


def test_state_is_persisted_and_resume_restores_it():
    store = FakeObjectStore()
    first = _poller(lambda cfg, dry_run: {}, store=store, interval=3600)
    first.start("uc", "local", external_llm_confirmed=True)
    assert store.get_json(STATE_KEY) == {"active": True, "use_case": "uc", "profile": "local",
                                          "external_llm_confirmed": True}
    first.shutdown(wait=2)  # server stopping: stays marked active
    assert store.get_json(STATE_KEY)["active"] is True

    second = _poller(lambda cfg, dry_run: {}, store=store, interval=3600)
    second.resume()
    try:
        assert second.snapshot()["active"] and second.snapshot()["use_case"] == "uc"
    finally:
        second.stop(wait=2)
    assert store.get_json(STATE_KEY)["active"] is False

    third = _poller(lambda cfg, dry_run: {}, store=store, interval=3600)
    third.resume()  # the user stopped it: nothing to resume
    assert third.snapshot()["active"] is False


def test_note_warns_that_the_first_poll_only_records_a_baseline():
    gate = threading.Event()  # holds the first cycle, so the note can be read before it clears
    fresh = _poller(lambda cfg, dry_run: gate.wait(3) and {}, interval=3600)
    fresh.start("uc", "local")
    try:
        assert "baseline" in fresh.snapshot()["note"]
        gate.set()
        assert _wait(lambda: fresh.snapshot()["cycles"] >= 1)
        assert fresh.snapshot()["note"] is None  # gone after the first successful cycle
    finally:
        fresh.stop(wait=2)

    store = FakeObjectStore()
    store.put_json(CHECKPOINT_KEY, {"since": "2026-10-12T00:00:00+00:00"}, dry_run=False)
    established = _poller(lambda cfg, dry_run: {}, store=store, interval=3600)
    established.start("uc", "local")
    try:
        assert established.snapshot()["note"] is None
    finally:
        established.stop(wait=2)
