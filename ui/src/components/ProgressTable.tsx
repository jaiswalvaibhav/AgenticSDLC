import type { Anchor, Progress, ProgressRow } from "../types";

interface Props {
  progress: Progress | null;
  onRefresh: () => void;
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

function stateCell(row: ProgressRow, approvalLabel: string) {
  if (row.run_state === "awaiting_approval") {
    return (
      <>
        <strong>plan ready</strong>
        <div>
          <a href={row.jira_url} target="_blank" rel="noreferrer">
            Add the <code>{approvalLabel}</code> label in Jira
          </a>{" "}
          to apply it
        </div>
      </>
    );
  }
  if (row.run_state === "applied") return "applied";
  if (row.run_state === "ready") return "regenerating after reject";
  return row.plan_ready ? "plan exists" : "";
}

export function ProgressTable({ progress, onRefresh }: Props) {
  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Progress</h2>
        <button onClick={onRefresh}>Refresh</button>
      </div>
      {!progress || progress.steps.length === 0 ? (
        <p className="muted">No tickets yet for this use case. Trigger a workflow to create them.</p>
      ) : (
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
                <td>{stateCell(row, progress.approval_label)}</td>
                <td className="comment">{row.recent_comments.at(-1) ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {progress && <p className="muted">Updated {new Date(progress.built_at).toLocaleTimeString()}</p>}
    </section>
  );
}
