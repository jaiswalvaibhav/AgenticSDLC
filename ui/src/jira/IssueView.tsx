import { useCallback, useEffect, useState, type FormEvent } from "react";
import * as api from "./api";
import { CommentBody, CommentBox } from "./Comments";
import { Checklist } from "./Checklist";
import { InlineText } from "./InlineText";
import { IssuePicker } from "./IssuePicker";
import { ParentPicker } from "./ParentPicker";
import { Avatar, Lozenge, ME, Priority, TypeIcon, dateTime, hasSections, isSubtask, linkLabel, message, statusCategory } from "./parts";
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
  const [editing, setEditing] = useState<string | null>(null); // id of the comment being edited
  const [labelDraft, setLabelDraft] = useState("");
  const [linkRelation, setLinkRelation] = useState<"depends on" | "is depended by">("depends on");
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
    if (!error) return <p className="muted">Loading…</p>;
    if (!/not found/.test(error)) return <div className="banner error">{error}</div>;
    // an old address, e.g. a bookmark made before the project key was renamed: same number, new prefix
    const number = issueKey.slice(issueKey.lastIndexOf("-") + 1);
    const renamed = `${board.project.key}-${number}`;
    const sameTicket = !issueKey.startsWith(`${board.project.key}-`) && board.issues.some((i) => i.key === renamed);
    return (
      <div className="mj-empty-state">
        <h2>{issueKey} doesn't exist</h2>
        {sameTicket ? (
          <p>
            Tickets in this project are named <strong>{board.project.key}-n</strong>. Did you mean{" "}
            <Link to={`/jira/browse/${renamed}`}>{renamed}</Link>?
          </p>
        ) : (
          <p className="muted">It may have been deleted.</p>
        )}
        <Link to="/jira/backlog" className="mj-button-link">Go to the backlog</Link>
      </div>
    );
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

        {hasSections(issue.issuetype) && (
          <>
            <h3>Brief description</h3>
            <div className="mj-description">
              <InlineText multiline value={issue.brief_description} placeholder="Add a brief description…"
                onSave={(v) => patch({ brief_description: v })} />
            </div>
          </>
        )}

        <h3>Description</h3>
        <div className="mj-description">
          <InlineText multiline value={issue.description} placeholder="Add a description…" onSave={(v) => patch({ description: v })} />
        </div>

        {hasSections(issue.issuetype) && (
          <>
            <Checklist title="Acceptance criteria" items={issue.acceptance_criteria} onChange={(items) => patch({ acceptance_criteria: items })} />
            <Checklist title="Definition of done" items={issue.definition_of_done} onChange={(items) => patch({ definition_of_done: items })} />
            <Checklist title="Test coverage" items={issue.test_coverage} onChange={(items) => patch({ test_coverage: items })} />
          </>
        )}

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
              <span className="muted">{linkLabel(l.type, l.direction)}</span>{" "}
              <TypeIcon type={l.issue.issuetype} /> <Link to={`/jira/browse/${l.key}`}>{l.key}</Link> {l.issue.summary}
              <span className="mj-spacer" /><Lozenge board={board} status={l.issue.status} />
              <button className="link-button" title="Remove link" onClick={() => act(() => api.unlinkIssue(issue.key, l.key))}>✕</button>
            </li>
          ))}
        </ul>
        <form className="mj-add" onSubmit={submit(() => api.linkIssue(issue.key, linkTarget, linkRelation), () => setLinkTarget(""))}>
          <select value={linkRelation} onChange={(e) => setLinkRelation(e.target.value as typeof linkRelation)}>
            <option>depends on</option><option>is depended by</option>
          </select>
          <IssuePicker board={board} value={linkTarget} onChange={setLinkTarget} noneLabel={null}
            candidates={others.filter((i) => !issue.links.some((l) => l.key === i.key))}
            placeholder="Choose a work item…" emptyText="Every other work item is already linked." />
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
            <CommentBox issueKey={issue.key} submitLabel="Comment" clearOnSubmit
              placeholder="Add a comment… (paste or drop images here)"
              onSubmit={async (body) => { await api.addComment(issue.key, body); await Promise.all([load(), reload()]); }} />
            {issue.comments.length === 0 && <p className="muted">No comments yet.</p>}
            {[...issue.comments].reverse().map((c) => (
              <div key={c.id} className="mj-comment">
                <Avatar name={c.author} />
                <div className="mj-comment-main">
                  <strong>{c.author}</strong> <span className="muted small">{dateTime(c.created)}</span>
                  {c.updated && <span className="muted small" title={dateTime(c.updated)}> (edited)</span>}{" "}
                  {c.author === ME && editing !== c.id && <button className="link-button" onClick={() => setEditing(c.id)}>Edit</button>}{" "}
                  <button className="link-button" onClick={() => window.confirm("Delete this comment?") && act(() => api.deleteComment(issue.key, c.id))}>Delete</button>
                  {editing === c.id ? (
                    <CommentBox issueKey={issue.key} initial={c.body} submitLabel="Save" onCancel={() => setEditing(null)}
                      onSubmit={async (body) => { await api.editComment(issue.key, c.id, body); setEditing(null); await Promise.all([load(), reload()]); }} />
                  ) : (
                    <CommentBody issueKey={issue.key} body={c.body} />
                  )}
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
