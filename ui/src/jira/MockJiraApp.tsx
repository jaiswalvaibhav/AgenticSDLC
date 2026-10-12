import { useCallback, useEffect, useState } from "react";
import * as api from "./api";
import { Backlog } from "./Backlog";
import { Board } from "./Board";
import { CreateIssueDialog } from "./Dialogs";
import { EpicsPage } from "./Epics";
import { IssuesPage } from "./IssuesPage";
import { IssueView } from "./IssueView";
import { message } from "./parts";
import { Link, navigate, useRoute } from "./route";
import type { BoardView } from "./types";
import "./jira.css";

// A local stand-in for a Jira company-managed Scrum project (backlog, sprints, board, issues),
// served at /jira by the same dev server as the workflow page. The agent and the orchestrator
// write the same tickets through the backend, so it re-reads the board every few seconds.
export default function MockJiraApp() {
  const route = useRoute();
  const [board, setBoard] = useState<BoardView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [epic, setEpic] = useState(""); // backlog filter, set from the sidebar or the backlog toolbar
  const [toast, setToast] = useState<string | null>(null); // key of the issue just created
  const [creating, setCreating] = useState<{ sprintId: number | null; type?: string } | null>(null);

  const reload = useCallback(
    () => api.getBoard().then((b) => (setBoard(b), setError(null)), (e) => setError(message(e))),
    [],
  );
  useEffect(() => {
    reload();
    const timer = setInterval(reload, 5000);
    return () => clearInterval(timer);
  }, [reload]);
  useEffect(() => {
    if (window.location.pathname.replace(/\/$/, "") === "/jira") navigate("/jira/backlog", true);
  }, []);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), 7000);
    return () => clearTimeout(timer);
  }, [toast]);
  useEffect(() => {
    document.title = board ? `${board.project.name} – Jira` : "Jira";
  }, [board]);

  return (
    <div className="mj-app">
      <div className="mj-topbar">
        <strong className="mj-logo">Jira</strong>
        <span className="muted small">local board — nothing here reaches your Jira site</span>
        <span className="mj-spacer" />
        <button className="primary mj-create-button" disabled={!board} onClick={() => setCreating({ sprintId: null })}>Create</button>
      </div>
      <div className="mj-body">
        <nav className="mj-sidebar">
          <div className="mj-project">
            <span className="mj-project-icon">{board?.project.key[0] ?? "M"}</span>
            <div>
              <strong>{board?.project.name ?? "…"}</strong>
              <div className="muted small">Scrum project · {board?.project.key}</div>
            </div>
          </div>
          <Link to="/jira/backlog" className={route.page === "backlog" ? "active" : ""}>Backlog</Link>
          <Link to="/jira/board" className={route.page === "board" ? "active" : ""}>Active sprint</Link>
          <Link to="/jira/epics" className={route.page === "epics" ? "active" : ""}>Epics</Link>
          <Link to="/jira/issues" className={route.page === "issues" ? "active" : ""}>Issues</Link>
        </nav>
        <div className="mj-content">
          {error && <div className="banner error">Can't reach the backend (is `make api` running?): {error}</div>}
          {!board ? <p className="muted">Loading…</p> : route.page === "backlog" ? (
            <Backlog board={board} reload={reload} epic={epic} onEpicChange={setEpic}
              onCreateIssue={(sprintId, type) => setCreating({ sprintId, type })} />
          ) : route.page === "board" ? (
            <Board board={board} reload={reload} />
          ) : route.page === "issues" ? (
            <IssuesPage board={board} />
          ) : route.page === "epics" ? (
            <EpicsPage board={board} onCreate={() => setCreating({ sprintId: null, type: "Epic" })}
              onShowInBacklog={(key) => { setEpic(key); navigate("/jira/backlog"); }} />
          ) : (
            <IssueView key={route.key} issueKey={route.key} board={board} reload={reload} />
          )}
        </div>
      </div>
      {toast && (
        <div className="mj-toast" role="status">
          ✓ Created <Link to={`/jira/browse/${toast}`}>{toast}</Link>
          <button className="link-button" onClick={() => setToast(null)} aria-label="Dismiss">✕</button>
        </div>
      )}
      {creating && board && (
        <CreateIssueDialog board={board} sprintId={creating.sprintId} initialType={creating.type} onClose={() => setCreating(null)}
          onCreated={(key) => { setToast(key); reload(); }} />
      )}
    </div>
  );
}
