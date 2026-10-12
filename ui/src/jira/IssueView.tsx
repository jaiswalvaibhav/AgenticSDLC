import { useCallback, useEffect, useState, type FormEvent, type KeyboardEvent } from "react";
import * as api from "./api";
import { ParentPicker } from "./ParentPicker";
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

// Click-to-edit text: Enter saves a one-line field, Ctrl/Cmd+Enter a multi-line one, Esc cancels.
function InlineText({ value, onSave, multiline, placeholder, className }: {
  value: string; onSave: (v: string) => void; multiline?: boolean; placeholder?: string; className?: string;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  if (draft === null) {
    return (
      <span className={`mj-inline ${className ?? ""}`} onClick={() => setDraft(value)} title="Click to edit">
        {value || <span className="muted">{placeholder ?? "None"}</span>}
      </span>
    );
  }
  const save = () => { if (draft !== value) onSave(draft); setDraft(null); };
  const keys = (e: KeyboardEvent) => {
    if (e.key === "Escape") setDraft(null);
    if (e.key === "Enter" && (!multiline || e.metaKey || e.ctrlKey)) { e.preventDefault(); save(); }
  };
  return multiline ? (
    <span className="mj-inline-edit">
      <textarea autoFocus rows={5} value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={keys} />
      <span className="mj-inline-buttons"><button className="primary" onClick={save}>Save</button><button onClick={() => setDraft(null)}>Cancel</button></span>
    </span>
  ) : (
    <input autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={keys} onBlur={save} />
  );
}

// The issue page: refreshes every few seconds, because the agent and the poller change tickets too.
export function IssueView({ issueKey, board, reload }: Props) {
  const [issue, setIssue] = useState<IssueDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"comments" | "history">("comments");
  const [comment, setComment] = useState("");
  const [labelDraft, setLabelDraft] = useState("");
  const [linkRelation, setLinkRelation] = useState<"blocks" | "is blocked by">("blocks");
  const [linkTarget, setLinkTarget] = useState("");
  const [webUrl, setWebUrl] = useState("");
  const [webTitle, setWebTitle] = useState("");

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
  const patch = (fields: Record<string, unknown>) => act(() => api.editIssue(issueKey, fields));

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

  const others = board.issues.filter((i) => i.key !== issue.key);
  const submit = (action: () => Promise<unknown>, reset: () => void) => (e: FormEvent) => {
    e.preventDefault();
    act(action).then(reset);
  };

  return (
    <div className="mj-issue">
      <div className="mj-issue-main">
        <nav className="mj-crumbs">
          {issue.parent_issue && <><Link to={`/jira/browse/${issue.parent_issue.key}`}>{issue.parent_issue.key}</Link> / </>}
          <TypeIcon type={issue.issuetype} /> {issue.key}
        </nav>
        <h1><InlineText value={issue.summary} onSave={(v) => v.trim() && patch({ summary: v.trim() })} /></h1>
        {error && <div className="banner error">{error}</div>}

        <h3>Description</h3>
        <div className="mj-description">
          <InlineText multiline value={issue.description} placeholder="Add a description…" onSave={(v) => patch({ description: v })} />
        </div>

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

        <h3>Linked issues</h3>
        <ul className="mj-refs">
          {issue.links.map((l) => (
            <li key={`${l.direction}-${l.key}`}>
              <span className="muted">{l.type === "Blocks" ? (l.direction === "outward" ? "blocks" : "is blocked by") : l.type}</span>{" "}
              <TypeIcon type={l.issue.issuetype} /> <Link to={`/jira/browse/${l.key}`}>{l.key}</Link> {l.issue.summary}
              <span className="mj-spacer" /><Lozenge board={board} status={l.issue.status} />
              <button className="link-button" title="Remove link" onClick={() => act(() => api.unlinkIssue(issue.key, l.key))}>✕</button>
            </li>
          ))}
        </ul>
        <form className="mj-add" onSubmit={submit(() => api.linkIssue(issue.key, linkTarget, linkRelation), () => setLinkTarget(""))}>
          <select value={linkRelation} onChange={(e) => setLinkRelation(e.target.value as typeof linkRelation)}>
            <option>blocks</option><option>is blocked by</option>
          </select>
          <select value={linkTarget} required onChange={(e) => setLinkTarget(e.target.value)}>
            <option value="">Choose an issue…</option>
            {others.map((i) => <option key={i.key} value={i.key}>{i.key} {i.summary}</option>)}
          </select>
          <button disabled={!linkTarget}>Link</button>
        </form>

        <h3>Web links</h3>
        <ul className="mj-refs">
          {issue.remote_links.map((r) => (
            <li key={r.id}>
              <a href={r.url} target="_blank" rel="noreferrer">{r.title || r.url}</a>
              <span className="mj-spacer" />
              <button className="link-button" title="Remove link" onClick={() => act(() => api.deleteWebLink(issue.key, r.id))}>✕</button>
            </li>
          ))}
        </ul>
        <form className="mj-add" onSubmit={submit(() => api.addWebLink(issue.key, webUrl.trim(), webTitle.trim()), () => (setWebUrl(""), setWebTitle("")))}>
          <input type="url" required placeholder="https://…" value={webUrl} onChange={(e) => setWebUrl(e.target.value)} />
          <input placeholder="Title (optional)" value={webTitle} onChange={(e) => setWebTitle(e.target.value)} />
          <button>Add</button>
        </form>

        <h3>Attachments</h3>
        <ul className="mj-refs">
          {issue.attachments.map((a) => (
            <li key={a.id}>
              <a href={`${a.url}${window.location.search}`}>{a.filename}</a>
              <span className="muted small"> {size(a.size)} · {a.author}, {dateTime(a.created)}</span>
              <span className="mj-spacer" />
              <button className="link-button" title="Delete attachment" onClick={() => window.confirm(`Delete ${a.filename}?`) && act(() => api.deleteAttachment(issue.key, a.id))}>✕</button>
            </li>
          ))}
        </ul>
        <label className="mj-upload">
          Attach files{" "}
          <input type="file" multiple onChange={(e) => {
            const files = Array.from(e.target.files ?? []);
            e.target.value = "";
            act(async () => { for (const f of files) await api.uploadAttachment(issue.key, f); });
          }} />
        </label>

        <h3>Activity</h3>
        <div className="mj-tabs">
          <button className={tab === "comments" ? "active" : ""} onClick={() => setTab("comments")}>Comments ({issue.comments.length})</button>
          <button className={tab === "history" ? "active" : ""} onClick={() => setTab("history")}>History ({issue.changelog.length})</button>
        </div>
        {tab === "comments" ? (
          <>
            <form className="mj-add-comment" onSubmit={submit(() => api.addComment(issue.key, comment.trim()), () => setComment(""))}>
              <textarea rows={3} placeholder="Add a comment…" value={comment} onChange={(e) => setComment(e.target.value)} />
              <button className="primary" disabled={!comment.trim()}>Comment</button>
            </form>
            {issue.comments.length === 0 && <p className="muted">No comments yet.</p>}
            {[...issue.comments].reverse().map((c) => (
              <div key={c.id} className="mj-comment">
                <Avatar name={c.author} />
                <div>
                  <strong>{c.author}</strong> <span className="muted small">{dateTime(c.created)}</span>{" "}
                  <button className="link-button" onClick={() => act(() => api.deleteComment(issue.key, c.id))}>Delete</button>
                  <p>{c.body}</p>
                </div>
              </div>
            ))}
          </>
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
          <dt>Assignee</dt>
          <dd><Avatar name={issue.assignee} /> <InlineText value={issue.assignee ?? ""} placeholder="Unassigned" onSave={(v) => patch({ assignee: v.trim() || null })} /></dd>
          <dt>Reporter</dt><dd><Avatar name={issue.reporter} /> {issue.reporter}</dd>
          <dt>Priority</dt>
          <dd>
            <Priority value={issue.priority} />
            <select value={issue.priority} onChange={(e) => patch({ priority: e.target.value })}>
              {board.priorities.map((p) => <option key={p}>{p}</option>)}
            </select>
          </dd>
          <dt>Story points</dt>
          <dd>
            <InlineText value={issue.story_points === null ? "" : String(issue.story_points)} placeholder="None"
              onSave={(v) => (v.trim() === "" ? patch({ story_points: null }) : Number.isFinite(Number(v)) ? patch({ story_points: Number(v) }) : setError("Story points must be a number"))} />
          </dd>
          <dt>Labels</dt>
          <dd>
            {issue.labels.map((l) => (
              <span key={l} className="mj-label">{l}{" "}
                <button className="link-button" title="Remove label" onClick={() => act(() => api.removeLabel(issue.key, l))}>✕</button>
              </span>
            ))}
            <form onSubmit={submit(() => api.addLabel(issue.key, labelDraft.trim()), () => setLabelDraft(""))}>
              <input className="mj-label-input" placeholder="Add label" value={labelDraft} onChange={(e) => setLabelDraft(e.target.value)} />
            </form>
          </dd>
          <dt>Parent</dt>
          <dd>
            <ParentPicker board={board} issueType={issue.issuetype} selfKey={issue.key} value={issue.parent ?? ""}
              onChange={(key) => patch({ parent: key || null })} />
          </dd>
          <dt>Sprint</dt>
          <dd>
            <select value={issue.sprint_id ?? ""} onChange={(e) => act(() => api.rank([issue.key], e.target.value === "" ? null : Number(e.target.value), null))}>
              <option value="">Backlog</option>
              {board.sprints.filter((s) => s.state !== "closed" || s.id === issue.sprint_id).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </dd>
          <dt>Components</dt><dd>{show(issue.components)}</dd>
          <dt>Created</dt><dd>{dateTime(issue.created)}</dd>
          <dt>Updated</dt><dd>{dateTime(issue.updated)}</dd>
        </dl>
      </aside>
    </div>
  );
}
