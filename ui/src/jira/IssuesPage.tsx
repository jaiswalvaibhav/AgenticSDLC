import { useState } from "react";
import { Avatar, Lozenge, Priority, TypeIcon, dateTime } from "./parts";
import { Link } from "./route";
import type { BoardView } from "./types";

const keyNumber = (key: string) => Number(key.slice(key.lastIndexOf("-") + 1));

// Every ticket in the project, whatever its sprint or status (completed sprints' tickets included).
export function IssuesPage({ board }: { board: BoardView }) {
  const [text, setText] = useState("");
  const [type, setType] = useState("");
  const [status, setStatus] = useState("");
  const [sprint, setSprint] = useState(""); // "" = any, "backlog", or a sprint id
  const byKey = new Map(board.issues.map((i) => [i.key, i]));
  const sprintName = (id: number | null) => board.sprints.find((s) => s.id === id)?.name ?? "Backlog";

  const issues = board.issues
    .filter((i) =>
      (!type || i.issuetype === type) && (!status || i.status === status) &&
      (!sprint || (sprint === "backlog" ? i.sprint_id === null : String(i.sprint_id) === sprint)) &&
      (!text || `${i.key} ${i.summary} ${i.labels.join(" ")} ${i.assignee ?? ""}`.toLowerCase().includes(text.toLowerCase())))
    .sort((a, b) => keyNumber(b.key) - keyNumber(a.key));

  return (
    <div>
      <div className="mj-board-head">
        <div>
          <h2>Issues</h2>
          <span className="muted small">{issues.length} of {board.issues.length} issue{board.issues.length === 1 ? "" : "s"}</span>
        </div>
      </div>
      <div className="mj-toolbar">
        <input placeholder="Search key, summary, label, assignee" value={text} onChange={(e) => setText(e.target.value)} />
        <select value={type} onChange={(e) => setType(e.target.value)} aria-label="Type">
          <option value="">All types</option>
          {board.issue_types.map((t) => <option key={t}>{t}</option>)}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
          <option value="">All statuses</option>
          {board.statuses.map((s) => <option key={s}>{s}</option>)}
        </select>
        <select value={sprint} onChange={(e) => setSprint(e.target.value)} aria-label="Sprint">
          <option value="">Any sprint</option>
          <option value="backlog">Backlog (no sprint)</option>
          {board.sprints.map((s) => <option key={s.id} value={s.id}>{s.name}{s.state === "closed" ? " (completed)" : ""}</option>)}
        </select>
      </div>
      {issues.length === 0 ? (
        <p className="mj-empty">No issues match.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Key</th><th>Summary</th><th>Status</th><th>Sprint</th><th>Parent</th><th>Priority</th><th>Pts</th><th>Assignee</th><th>Updated</th></tr>
            </thead>
            <tbody>
              {issues.map((i) => {
                const parent = i.parent ? byKey.get(i.parent) : undefined;
                return (
                  <tr key={i.key}>
                    <td className="mj-nowrap"><TypeIcon type={i.issuetype} /> <Link to={`/jira/browse/${i.key}`} className="mj-key">{i.key}</Link></td>
                    <td>{i.summary}</td>
                    <td><Lozenge board={board} status={i.status} /></td>
                    <td className="mj-nowrap">{sprintName(i.sprint_id)}</td>
                    <td className="mj-nowrap">{parent ? <><TypeIcon type={parent.issuetype} /> <Link to={`/jira/browse/${parent.key}`}>{parent.key}</Link></> : ""}</td>
                    <td><Priority value={i.priority} /></td>
                    <td>{i.story_points ?? ""}</td>
                    <td><Avatar name={i.assignee} /></td>
                    <td className="muted small mj-nowrap">{dateTime(i.updated)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
