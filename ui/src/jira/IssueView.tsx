import { useCallback, useEffect, useState } from "react";
import * as api from "./api";
import { Avatar, Lozenge, Priority, TypeIcon, dateTime, isSubtask, message, statusCategory } from "./parts";
import { Link, navigate } from "./route";
import type { BoardView, IssueDetail } from "./types";

interface Props {
  issueKey: string;
  board: BoardView;
  reload: () => Promise<unknown>;
}

const show = (v: unknown) => (Array.isArray(v) ? v.join(", ") || "None" : v === null || v === "" || v === undefined ? "None" : String(v));
const size = (bytes: number) => (bytes < 1024 ? `${bytes} B` : bytes < 1048576 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1048576).toFixed(1)} MB`);

// The issue page: refreshes every few seconds, because the agent and the poller change tickets too.
export function IssueView({ issueKey, board, reload }: Props) {
  const [issue, setIssue] = useState<IssueDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"comments" | "history">("comments");

  const load = useCallback(
    () => api.getIssue(issueKey).then((i) => (setIssue(i), setError(null)), (e) => setError(message(e))),
    [issueKey],
  );
  useEffect(() => {
    setIssue(null);
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [load]);

  const act = async (action: () => Promise<unknown>) => {
    try {
      await action();
      await Promise.all([load(), reload()]);
    } catch (e) {
      setError(message(e));
    }
  };

  const remove = async () => {
    if (!issue) return;
    const subtasks = issue.children.filter(isSubtask);
    const also = subtasks.length ? `\n\nIts ${subtasks.length} subtask(s) will be deleted too: ${subtasks.map((s) => s.key).join(", ")}.` : "";
    if (!window.confirm(`Delete ${issue.key} "${issue.summary}"?${also}\n\nThis can't be undone.`)) return;
    try {
      await api.deleteIssue(issue.key);
      await reload();
      navigate("/jira/backlog");
    } catch (e) {
      setError(message(e));
    }
  };

  if (!issue) {
    return error ? <div className="banner error">{error}</div> : <p className="muted">Loading…</p>;
  }

  return (
    <div className="mj-issue">
      <div className="mj-issue-main">
        <nav className="mj-crumbs">
          {issue.parent_issue && <><Link to={`/jira/browse/${issue.parent_issue.key}`}>{issue.parent_issue.key}</Link> / </>}
          <TypeIcon type={issue.issuetype} /> {issue.key}
        </nav>
        <h1>{issue.summary}</h1>
        {error && <div className="banner error">{error}</div>}

        <h3>Description</h3>
        <p className="mj-description">{issue.description || <span className="muted">No description.</span>}</p>

        {issue.children.length > 0 && (
          <>
            <h3>Child issues</h3>
            <ul className="mj-refs">
              {issue.children.map((c) => (
                <li key={c.key}>
                  <TypeIcon type={c.issuetype} /> <Link to={`/jira/browse/${c.key}`}>{c.key}</Link> {c.summary}
                  <span className="mj-spacer" /><Lozenge board={board} status={c.status} />
                </li>
              ))}
            </ul>
          </>
        )}

        {issue.links.length > 0 && (
          <>
            <h3>Linked issues</h3>
            <ul className="mj-refs">
              {issue.links.map((l) => (
                <li key={`${l.direction}-${l.key}`}>
                  <span className="muted">{l.type === "Blocks" ? (l.direction === "outward" ? "blocks" : "is blocked by") : l.type}</span>{" "}
                  <TypeIcon type={l.issue.issuetype} /> <Link to={`/jira/browse/${l.key}`}>{l.key}</Link> {l.issue.summary}
                  <span className="mj-spacer" /><Lozenge board={board} status={l.issue.status} />
                </li>
              ))}
            </ul>
          </>
        )}

        {issue.remote_links.length > 0 && (
          <>
            <h3>Web links</h3>
            <ul className="mj-refs">
              {issue.remote_links.map((r) => (
                <li key={r.id}><a href={r.url} target="_blank" rel="noreferrer">{r.title || r.url}</a></li>
              ))}
            </ul>
          </>
        )}

        {issue.attachments.length > 0 && (
          <>
            <h3>Attachments</h3>
            <ul className="mj-refs">
              {issue.attachments.map((a) => (
                <li key={a.id}>
                  <a href={`${a.url}${window.location.search}`}>{a.filename}</a>
                  <span className="muted small"> {size(a.size)} · {a.author}, {dateTime(a.created)}</span>
                </li>
              ))}
            </ul>
          </>
        )}

        <h3>Activity</h3>
        <div className="mj-tabs">
          <button className={tab === "comments" ? "active" : ""} onClick={() => setTab("comments")}>Comments ({issue.comments.length})</button>
          <button className={tab === "history" ? "active" : ""} onClick={() => setTab("history")}>History ({issue.changelog.length})</button>
        </div>
        {tab === "comments" ? (
          issue.comments.length === 0 ? <p className="muted">No comments yet.</p> : issue.comments.map((c) => (
            <div key={c.id} className="mj-comment">
              <Avatar name={c.author} />
              <div>
                <strong>{c.author}</strong> <span className="muted small">{dateTime(c.created)}</span>
                <p>{c.body}</p>
              </div>
            </div>
          ))
        ) : (
          issue.changelog.length === 0 ? <p className="muted">No changes yet.</p> : [...issue.changelog].reverse().map((entry) => (
            <div key={entry.id} className="mj-comment">
              <Avatar name={entry.author} />
              <div>
                <strong>{entry.author}</strong> <span className="muted small">{dateTime(entry.created)}</span>
                {entry.items.map((it, n) => (
                  <p key={n} className="small"><strong>{it.field}</strong>: {show(it.from)} → {show(it.to)}</p>
                ))}
              </div>
            </div>
          ))
        )}
      </div>

      <aside className="mj-issue-side">
        <div className="mj-side-actions">
          <select
            className={`mj-status-select ${statusCategory(board.statuses, issue.status)}`}
            value={issue.status}
            onChange={(e) => act(() => api.transition(issue.key, e.target.value))}
          >
            {board.statuses.map((s) => <option key={s}>{s}</option>)}
          </select>
          <button className="mj-danger" onClick={remove}>Delete</button>
        </div>
        <dl className="mj-details">
          <dt>Assignee</dt><dd><Avatar name={issue.assignee} /> {issue.assignee ?? "Unassigned"}</dd>
          <dt>Reporter</dt><dd><Avatar name={issue.reporter} /> {issue.reporter}</dd>
          <dt>Priority</dt><dd><Priority value={issue.priority} /> {issue.priority}</dd>
          <dt>Story points</dt><dd>{issue.story_points ?? "None"}</dd>
          <dt>Labels</dt><dd>{issue.labels.length ? issue.labels.map((l) => <span key={l} className="mj-label">{l}</span>) : "None"}</dd>
          <dt>Components</dt><dd>{show(issue.components)}</dd>
          <dt>Sprint</dt><dd>{issue.sprint ? issue.sprint.name : "None"}</dd>
          <dt>Created</dt><dd>{dateTime(issue.created)}</dd>
          <dt>Updated</dt><dd>{dateTime(issue.updated)}</dd>
        </dl>
      </aside>
    </div>
  );
}
