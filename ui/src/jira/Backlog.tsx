import { useState, type DragEvent, type ReactNode } from "react";
import * as api from "./api";
import { CompletedSprints } from "./CompletedSprints";
import { CompleteSprintDialog, SprintDialog } from "./Dialogs";
import { Avatar, Priority, TypeIcon, day, isSubtask, message, points, statusCategory } from "./parts";
import { Link } from "./route";
import type { BoardView, IssueSummary, Sprint } from "./types";

interface Props {
  board: BoardView;
  reload: () => Promise<unknown>;
  onCreateIssue: (sprintId: number | null, type?: string) => void;
  epic: string; // the epic filter, shared with the sidebar
  onEpicChange: (key: string) => void;
}

type SprintDialogState = { kind: "start" | "edit" | "complete"; sprint: Sprint } | null;

export function Backlog({ board, reload, onCreateIssue, epic, onEpicChange }: Props) {
  const [dialog, setDialog] = useState<SprintDialogState>(null);
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [dragKey, setDragKey] = useState<string | null>(null);
  const [over, setOver] = useState<string | null>(null); // "sprint:<id|backlog>" or "issue:<key>"

  const byKey = new Map(board.issues.map((i) => [i.key, i]));
  const epics = board.issues.filter((i) => i.issuetype === "Epic");
  const matches = (i: IssueSummary) =>
    (!epic || i.parent === epic) && (!text || `${i.key} ${i.summary}`.toLowerCase().includes(text.toLowerCase()));
  const rowsOf = (sprintId: number | null) =>
    board.issues.filter((i) => !isSubtask(i) && i.issuetype !== "Epic" && i.sprint_id === sprintId && matches(i));
  const sprints = board.sprints
    .filter((s) => s.state !== "closed")
    .sort((a, b) => Number(b.state === "active") - Number(a.state === "active") || a.id - b.id);
  const hasActive = sprints.some((s) => s.state === "active");

  const run = async (action: () => Promise<unknown>) => {
    try {
      await action();
      setError(null);
      await reload();
    } catch (e) {
      setError(message(e));
    }
  };
  const drop = (sprintId: number | null, beforeKey: string | null) => {
    const key = dragKey;
    setDragKey(null);
    setOver(null);
    if (key && key !== beforeKey) run(() => api.rank([key], sprintId, beforeKey));
  };
  const allow = (target: string) => (e: DragEvent) => {
    if (!dragKey) return;
    e.preventDefault();
    e.stopPropagation();
    setOver(target);
  };

  const row = (issue: IssueSummary, sprintId: number | null) => {
    const parent = issue.parent ? byKey.get(issue.parent) : undefined;
    const subtasks = board.issues.filter((s) => isSubtask(s) && s.parent === issue.key);
    return (
      <div key={issue.key}>
        <div
          className={`mj-row${over === `issue:${issue.key}` ? " over" : ""}${dragKey === issue.key ? " dragging" : ""}`}
          draggable
          onDragStart={(e) => { e.dataTransfer.setData("text/plain", issue.key); setDragKey(issue.key); }}
          onDragEnd={() => { setDragKey(null); setOver(null); }}
          onDragOver={allow(`issue:${issue.key}`)}
          onDrop={(e) => { e.preventDefault(); e.stopPropagation(); drop(sprintId, issue.key); }}
        >
          <TypeIcon type={issue.issuetype} />
          <Link to={`/jira/browse/${issue.key}`} className="mj-key">{issue.key}</Link>
          <span className="mj-summary">{issue.summary}</span>
          {parent?.issuetype === "Epic" && <span className="mj-epic" title={parent.summary}>{parent.summary}</span>}
          {issue.labels.slice(0, 3).map((l) => <span key={l} className="mj-label">{l}</span>)}
          <span className="mj-spacer" />
          <select
            className={`mj-status-select ${statusCategory(board.statuses, issue.status)}`}
            value={issue.status}
            onChange={(e) => run(() => api.transition(issue.key, e.target.value))}
          >
            {board.statuses.map((s) => <option key={s}>{s}</option>)}
          </select>
          <Priority value={issue.priority} />
          <span className="mj-points">{issue.story_points ?? ""}</span>
          <Avatar name={issue.assignee} />
        </div>
        {subtasks.map((s) => (
          <div key={s.key} className="mj-row sub">
            <TypeIcon type={s.issuetype} />
            <Link to={`/jira/browse/${s.key}`} className="mj-key">{s.key}</Link>
            <span className="mj-summary">{s.summary}</span>
            <span className="mj-spacer" />
            <span className={`mj-lozenge ${statusCategory(board.statuses, s.status)}`}>{s.status}</span>
            <Avatar name={s.assignee} />
          </div>
        ))}
      </div>
    );
  };

  const container = (id: number | null, rows: IssueSummary[], header: ReactNode) => {
    const target = `sprint:${id ?? "backlog"}`;
    return (
      <section
        key={target}
        className={`mj-container${over === target ? " over" : ""}`}
        onDragOver={allow(target)}
        onDrop={(e) => { e.preventDefault(); drop(id, null); }}
      >
        <div className="mj-container-head">{header}</div>
        {rows.length === 0 && <p className="mj-empty">{id === null ? "Your backlog is empty." : "Plan a sprint by dragging issues here."}</p>}
        {rows.map((i) => row(i, id))}
        <button className="link-button mj-create" onClick={() => onCreateIssue(id)}>+ Create issue</button>
      </section>
    );
  };

  return (
    <div>
      <div className="mj-toolbar">
        <input placeholder="Search backlog" value={text} onChange={(e) => setText(e.target.value)} />
        <select value={epic} onChange={(e) => onEpicChange(e.target.value)} aria-label="Epic">
          <option value="">All epics</option>
          {epics.map((e) => <option key={e.key} value={e.key}>{e.key} {e.summary}</option>)}
        </select>
        <span className="mj-spacer" />
        <button onClick={() => run(() => api.createSprint())}>Create sprint</button>
      </div>
      {error && <div className="banner error">{error}</div>}

      {sprints.map((sprint) => {
        const rows = rowsOf(sprint.id);
        const all = board.issues.filter((i) => i.sprint_id === sprint.id && i.issuetype !== "Epic");
        return container(sprint.id, rows, (
          <>
            <strong>{sprint.name}</strong>
            <span className={`mj-sprint-state ${sprint.state}`}>{sprint.state === "active" ? "Active" : "Future"}</span>
            {sprint.start_date && <span className="muted small">{day(sprint.start_date)} – {day(sprint.end_date)}</span>}
            <span className="muted small">{all.length} issue{all.length === 1 ? "" : "s"} · {points(all)} pts</span>
            {sprint.goal && <span className="muted small mj-goal">Goal: {sprint.goal}</span>}
            <span className="mj-spacer" />
            <button className="link-button" onClick={() => setDialog({ kind: "edit", sprint })}>Edit</button>
            {sprint.state === "future" && (
              <button className="link-button" onClick={() => window.confirm(`Delete ${sprint.name}? Its issues go back to the backlog.`) && run(() => api.deleteSprint(sprint.id))}>Delete</button>
            )}
            {sprint.state === "active" ? (
              <button onClick={() => setDialog({ kind: "complete", sprint })}>Complete sprint</button>
            ) : (
              <button disabled={hasActive} title={hasActive ? "Complete the active sprint first" : undefined}
                onClick={() => setDialog({ kind: "start", sprint })}>Start sprint</button>
            )}
          </>
        ));
      })}

      {container(null, rowsOf(null), (
        <>
          <strong>Backlog</strong>
          <span className="muted small">{rowsOf(null).length} issue{rowsOf(null).length === 1 ? "" : "s"}</span>
        </>
      ))}

      <CompletedSprints board={board} />

      {dialog && dialog.kind !== "complete" && (
        <SprintDialog mode={dialog.kind} sprint={dialog.sprint} onClose={() => setDialog(null)}
          onDone={() => { setDialog(null); reload(); }} />
      )}
      {dialog?.kind === "complete" && (
        <CompleteSprintDialog board={board} sprint={dialog.sprint} onClose={() => setDialog(null)}
          onDone={() => { setDialog(null); reload(); }} />
      )}
    </div>
  );
}
