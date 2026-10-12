import type { Anchor, Progress, ProgressRow } from "../types";
import { Clamp } from "./Clamp";

interface Props {
  progress: Progress | null;
  busy: boolean;
  onRefresh: () => void;
  onDecide: (row: ProgressRow, decision: "approve" | "reject") => void;
  onRerun: (row: ProgressRow) => void;
}

function artifactCell(row: ProgressRow) {
  const found = [...row.files, ...row.links.map((l) => l.title || l.url)];
  const anchor: Anchor | null = row.anchor;
  return (
    <>
      {found.length > 0 ? found.join(", ") : <span className="muted">none attached</span>}
      {anchor && !anchor.ok && <div className="error">{anchor.reason}</div>}
      {anchor && anchor.ok && (
        <div className="ok">
          usable ({anchor.kind === "link" && anchor.via !== "web link" ? `link in ${anchor.via}` : anchor.kind})
        </div>
      )}
    </>
  );
}

function stateCell(row: ProgressRow, progress: Progress, busy: boolean, onDecide: Props["onDecide"], onRerun: Props["onRerun"]) {
  if (row.run_state === "awaiting_approval" && row.decision_pending) {
    return row.decision_pending === "approve"
      ? "approving: creating the Stories and sub-tasks in Jira… (can take a minute)"
      : "rejecting: recording the feedback in Jira…";
  }
  if (row.run_state === "awaiting_approval") {
    return (
      <>
        <strong>plan ready</strong>
        <div className="actions">
          <button className="approve" disabled={busy} onClick={() => onDecide(row, "approve")}>
            Approve
          </button>
          <button disabled={busy} onClick={() => onDecide(row, "reject")}>
            Reject
          </button>
        </div>
        <div className="muted small">
          or add <code>{progress.approval_label}</code> / <code>{progress.reject_label}</code> in{" "}
          <a href={row.jira_url} target="_blank" rel="noreferrer">
            Jira
          </a>
        </div>
      </>
    );
  }
  if (row.run_state === "regenerating") return "regenerating the plan…";
  if (row.run_state === "applied") return "applied";
  if (row.run_state === "rejected" || row.run_state === "ready") {
    return (
      <>
        <strong>rejected</strong>
        <div className="muted small">Feedback is on the Jira ticket. Fix the issues, then re-run.</div>
        <div className="actions">
          <button disabled={busy} onClick={() => onRerun(row)}>Re-run</button>
        </div>
      </>
    );
  }
  if (row.run_state === "rerun_requested") return "re-run requested, starting…";
  return row.plan_ready ? "plan exists" : "";
}

export function ProgressTable({ progress, busy, onRefresh, onDecide, onRerun }: Props) {
  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Progress</h2>
        <button onClick={onRefresh}>Refresh</button>
      </div>
      {!progress || progress.steps.length === 0 ? (
        <p className="muted">No tickets yet for this use case. Trigger a workflow to create them.</p>
      ) : (
        <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Ticket</th>
              <th>Artifact</th>
              <th>Role</th>
              <th>Status</th>
              <th>Attached / linked</th>
              <th>Agent</th>
              <th>Latest comment</th>
            </tr>
          </thead>
          <tbody>
            {progress.steps.map((row) => (
              <tr key={row.step_id} className={row.done ? "done" : undefined}>
                <td>
                  <a href={row.jira_url} target="_blank" rel="noreferrer">
                    {row.key}
                  </a>
                  {row.llm_usage && (
                    <div className="muted small">
                      LLM called {row.llm_usage.calls}× · {row.llm_usage.runs} run{row.llm_usage.runs === 1 ? "" : "s"}
                    </div>
                  )}
                </td>
                <td>{row.artifact}</td>
                <td>
                  <span className={`chip ${row.role}`}>{row.role}</span>
                </td>
                <td>{row.status}</td>
                <td>{artifactCell(row)}</td>
                <td>{stateCell(row, progress, busy, onDecide, onRerun)}</td>
                <td className="comment">
                  <Clamp text={row.recent_comments.at(-1) ?? ""} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
      {progress && <p className="muted">Updated {new Date(progress.built_at).toLocaleTimeString()}</p>}
    </section>
  );
}
