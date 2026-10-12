"""The UI's background orchestrator loop: repeatedly runs sdlc.sync_once (poll Jira ->
rollup -> start ready steps / run the agent -> check approvals) for one use case, then
refreshes the progress snapshot. Live writes only (dry_run=False) — it is only started by an
explicit, confirmed trigger. Local profile only: with the aws profile the scheduled
orchestrator Lambda does this, so the UI never starts a second poller against it.

What was started is persisted (ui/poller.json) so a backend restart resumes polling.
`shutdown()` stops the thread without touching that saved state; `stop()` is the user's
"stop" and does. Cycles never overlap (one lock), and a failing cycle is recorded and the
loop carries on.
"""
import threading
from datetime import datetime, timezone

STATE_KEY = "ui/poller.json"
CHECKPOINT_KEY = "jira_checkpoint.json"
BASELINE_NOTE = ("The first poll only records a baseline: status changes made before it "
                 "are not seen. If you already marked a step Done, move it back and Done again.")


class Poller:
    def __init__(self, *, cfg: dict, sync, on_cycle, state_store, interval: float):
        self.cfg, self.sync, self.on_cycle = cfg, sync, on_cycle
        self.state_store, self.interval = state_store, interval
        self.run: dict | None = None  # {use_case, profile, external_llm_confirmed} while active
        self._status = {"last_sync_at": None, "last_result": None, "last_error": None,
                        "cycles": 0, "note": None, "cycle_started_at": None}
        self._cycle_lock = threading.Lock()  # one cycle at a time, even across a restart
        self._guard = threading.Lock()  # start/stop bookkeeping
        self._stop: threading.Event | None = None
        self._wake: threading.Event | None = None
        self._thread: threading.Thread | None = None

    # ---------------------------------------------------------------- control --
    def start(self, use_case: str, profile: str, external_llm_confirmed: bool = False) -> None:
        settings = {"use_case": use_case, "profile": profile,
                    "external_llm_confirmed": external_llm_confirmed}
        with self._guard:
            if self.run == settings and self._thread and self._thread.is_alive():
                return
            self._halt()
            self.run = settings
            baseline = self.state_store.get_json(CHECKPOINT_KEY) is None
            self._status.update(last_result=None, last_error=None, cycles=0,
                                note=BASELINE_NOTE if baseline else None)
            self._persist()
            self._stop, self._wake = threading.Event(), threading.Event()
            self._thread = threading.Thread(target=self._loop, args=(self._stop, self._wake, settings),
                                            daemon=True, name="sdlc-poller")
            self._thread.start()

    def stop(self, wait: float = 0) -> None:
        """The user's stop: halts the loop and remembers it is stopped."""
        with self._guard:
            thread = self._thread
            self._halt()
            self.run = None
            self._persist()
        if wait and thread:
            thread.join(wait)

    def shutdown(self, wait: float = 0) -> None:
        """Server shutdown: halts the thread but leaves the saved state active, so the
        next start resumes."""
        with self._guard:
            thread = self._thread
            self._halt()
        if wait and thread:
            thread.join(wait)

    def resume(self) -> None:
        saved = self.state_store.get_json(STATE_KEY) or {}
        if saved.get("active"):
            self.start(saved["use_case"], saved["profile"], saved.get("external_llm_confirmed", False))

    def sync_now(self) -> bool:
        """Wakes the loop for an immediate cycle. False when not running."""
        if not self.run or not self._wake:
            return False
        self._wake.set()
        return True

    def snapshot(self) -> dict:
        return {"active": self.run is not None,
                "use_case": (self.run or {}).get("use_case"), "profile": (self.run or {}).get("profile"),
                "interval_seconds": self.interval, **self._status}

    # --------------------------------------------------------------- internals --
    def _halt(self) -> None:
        if self._stop and self._wake:
            self._stop.set()
            self._wake.set()
        self._stop = self._wake = None

    def _persist(self) -> None:
        self.state_store.put_json(STATE_KEY, {"active": self.run is not None, **(self.run or {})},
                                  dry_run=False)

    def _loop(self, stop: threading.Event, wake: threading.Event, settings: dict) -> None:
        while not stop.is_set():
            self._cycle(stop, settings)
            wake.wait(self.interval)
            wake.clear()

    def _cycle(self, stop: threading.Event, settings: dict) -> None:
        with self._cycle_lock:
            if stop.is_set():
                return
            error = result = None
            self._status["cycle_started_at"] = datetime.now(timezone.utc).isoformat()
            try:
                result = self.sync({**self.cfg, **settings}, dry_run=False)
                self.on_cycle(settings["use_case"], settings["profile"])
            except Exception as exc:  # recorded for the UI; the loop must outlive one bad cycle
                error = f"{type(exc).__name__}: {exc}"
            self._status.update(last_sync_at=datetime.now(timezone.utc).isoformat(), cycle_started_at=None,
                                cycles=self._status["cycles"] + 1, last_error=error)
            if error is None:
                self._status.update(last_result=result, note=None)
