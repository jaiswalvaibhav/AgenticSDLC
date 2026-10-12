import type { PollerState } from "../types";
import { Elapsed } from "./AgentActivity";

interface Props {
  poller: PollerState | null;
  busy: boolean;
  onStart: () => void;
  onStop: () => void;
  onSyncNow: () => void;
}

const time = (iso: string | null) => (iso ? new Date(iso).toLocaleTimeString() : "never");

export function PollerStatus({ poller, busy, onStart, onStop, onSyncNow }: Props) {
  if (!poller) return null;
  return (
    <div className="poller">
      <span className={poller.active ? "badge on" : "badge off"}>{poller.active ? "Polling" : "Stopped"}</span>
      <span className="muted">
        {poller.active && `${poller.use_case} · every ${poller.interval_seconds}s · `}
        last sync {time(poller.last_sync_at)} · {poller.cycles} cycle{poller.cycles === 1 ? "" : "s"}
      </span>
      {poller.active ? (
        <>
          <button onClick={onSyncNow} disabled={busy}>
            Sync now
          </button>
          <button onClick={onStop} disabled={busy}>
            Stop
          </button>
        </>
      ) : (
        <button onClick={onStart} disabled={busy}>
          Start
        </button>
      )}
      {poller.cycle_started_at && (
        <div className="muted note">
          Orchestrator working for <Elapsed since={poller.cycle_started_at} /> (a step's agent may be running; see
          the Agent column).
        </div>
      )}
      {poller.last_error && <div className="error">Last cycle failed: {poller.last_error}</div>}
      {poller.note && <div className="muted note">{poller.note}</div>}
    </div>
  );
}
