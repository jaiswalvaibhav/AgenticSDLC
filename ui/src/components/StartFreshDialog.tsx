import { useState } from "react";
import type { RunInfo } from "../types";

interface Props {
  info: Extract<RunInfo, { exists: true }>;
  jiraMode: "mock" | "real";
  busy: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: (deletePages: boolean) => void;
}

// "Start fresh" forgets the saved run. With the mock Jira it also deletes the run's tickets; with
// real Jira it never touches them. Deleting the Confluence pages the workflow created is opt-in.
export function StartFreshDialog({ info, jiraMode, busy, error, onCancel, onConfirm }: Props) {
  const [deletePages, setDeletePages] = useState(false);
  return (
    <div className="overlay" role="dialog" aria-modal="true">
      <div className="dialog">
        <h2>Start fresh</h2>
        <p>
          This clears the saved run for this use case (ticket map, run state, plan) and stops polling.{" "}
          {jiraMode === "mock" ? (
            <>
              It <strong>deletes the {info.ticket_count} tickets</strong> of this use case from the Jira board (with their
              subtasks).
            </>
          ) : (
            <>Jira tickets are <strong>not</strong> deleted or changed.</>
          )}
        </p>
        <label className="warn">
          <input type="checkbox" checked={deletePages} onChange={(e) => setDeletePages(e.target.checked)} />
          <span>
            Also delete the Confluence pages the workflow created (they go to the Confluence trash). Pages you linked
            as inputs, like the DDS and TDS, are never touched.
          </span>
        </label>
        {!deletePages && (
          <p className="muted small">
            If you leave this off, the pages stay, and the next plan replaces the content of the existing plan page.
          </p>
        )}
        {error && <p className="error">{error}</p>}
        <div className="buttons">
          <button onClick={onCancel} disabled={busy}>Cancel</button>
          <button className="primary" disabled={busy} onClick={() => onConfirm(deletePages)}>
            {busy ? "Working…" : "Start fresh"}
          </button>
        </div>
      </div>
    </div>
  );
}
