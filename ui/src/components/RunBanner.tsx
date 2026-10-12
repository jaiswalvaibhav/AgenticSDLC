import type { RunInfo } from "../types";

interface Props {
  info: RunInfo | null;
  busy: boolean;
  onStartFresh: () => void;
}

// Says which saved run the Progress table is showing, and offers "Start fresh" (App opens its dialog). A run saved in
// a different Jira project than the configured one gets a warning: its tickets aren't in this
// project, and triggering would skip creating tickets for steps that are already in the run.
export function RunBanner({ info, busy, onStartFresh }: Props) {
  if (!info || !info.exists) return null;
  return (
    <div className={info.stale ? "banner warn" : "banner info"}>
      <span>
        Showing the saved run: <strong>{info.ticket_count}</strong> tickets in Jira project{" "}
        <strong>{info.project_key}</strong>
        {info.started_at && `, started ${new Date(info.started_at).toLocaleString()}`}.
        {info.stale && (
          <>
            {" "}
            <strong>You're now on project {info.current_project_key}</strong> — these tickets aren't in it. Start
            fresh to create tickets there.
          </>
        )}
      </span>
      <button onClick={onStartFresh} disabled={busy}>
        Start fresh…
      </button>
    </div>
  );
}
