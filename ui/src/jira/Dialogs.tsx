import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import * as api from "./api";
import { isSubtask, message } from "./parts";
import type { BoardView, Sprint } from "./types";

// Reuses the workflow page's .overlay/.dialog look.
export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dialog mj-dialog" role="dialog" aria-label={title}>
        <h2>{title}</h2>
        {children}
      </div>
    </div>
  );
}

function useSubmit<T>(done: (result: T) => void) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = (action: () => Promise<T>) => async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      done(await action());
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  };
  return { error, busy, submit };
}

const openSprints = (board: BoardView) => board.sprints.filter((s) => s.state !== "closed");

export function CreateIssueDialog({ board, sprintId, initialType, onClose, onCreated }: {
  board: BoardView; sprintId: number | null; initialType?: string; onClose: () => void; onCreated: (key: string) => void;
}) {
  const types = board.issue_types;
  const [type, setType] = useState(initialType ?? types.find((t) => t === "Task") ?? types[0]);
  const [summary, setSummary] = useState("");
  const [description, setDescription] = useState("");
  const [parent, setParent] = useState("");
  const [priority, setPriority] = useState("Medium");
  const [pts, setPts] = useState("");
  const [labels, setLabels] = useState("");
  const [sprint, setSprint] = useState(sprintId === null ? "" : String(sprintId));
  const [another, setAnother] = useState(false);
  const summaryRef = useRef<HTMLInputElement>(null);
  // created: tell the page (it shows "Created MOCK-n"), then close, or clear the text for the next one
  const { error, busy, submit } = useSubmit<string>((key) => {
    onCreated(key);
    if (!another) return onClose();
    setSummary("");
    setDescription("");
    setLabels("");
    setPts("");
    summaryRef.current?.focus();
  });
  const needsParent = isSubtask({ issuetype: type });
  const parents = board.issues.filter((i) => !isSubtask(i));

  const create = submit(async () => {
    const result = await api.createIssue({
      issue_type: type, summary: summary.trim(), description, parent: parent || null, priority,
      story_points: pts === "" ? null : Number(pts),
      labels: labels.split(",").map((l) => l.trim()).filter(Boolean),
      sprint_id: sprint === "" ? null : Number(sprint),
    });
    return result.key;
  });

  return (
    <Modal title="Create issue" onClose={onClose}>
      <form onSubmit={create} className="mj-form">
        <label>Issue type
          <select value={type} onChange={(e) => setType(e.target.value)}>
            {types.map((t) => <option key={t}>{t}</option>)}
          </select>
        </label>
        <label>Summary
          <input ref={summaryRef} autoFocus required value={summary} onChange={(e) => setSummary(e.target.value)} />
        </label>
        <label>Description
          <textarea rows={4} value={description} onChange={(e) => setDescription(e.target.value)} />
        </label>
        <label>Parent {needsParent ? "(required)" : "(epic or story, optional)"}
          <select value={parent} required={needsParent} onChange={(e) => setParent(e.target.value)}>
            <option value="">None</option>
            {parents.map((i) => <option key={i.key} value={i.key}>{i.key} {i.summary}</option>)}
          </select>
        </label>
        <div className="mj-form-row">
          <label>Priority
            <select value={priority} onChange={(e) => setPriority(e.target.value)}>
              {board.priorities.map((p) => <option key={p}>{p}</option>)}
            </select>
          </label>
          <label>Story points
            <input type="number" min={0} step="0.5" value={pts} onChange={(e) => setPts(e.target.value)} />
          </label>
        </div>
        <label>Labels (comma separated)
          <input value={labels} onChange={(e) => setLabels(e.target.value)} />
        </label>
        <label>Sprint
          <select value={sprint} onChange={(e) => setSprint(e.target.value)}>
            <option value="">Backlog</option>
            {openSprints(board).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </label>
        {error && <p className="error">{error}</p>}
        <div className="buttons">
          <label className="mj-another">
            <input type="checkbox" checked={another} onChange={(e) => setAnother(e.target.checked)} /> Create another
          </label>
          <span className="mj-spacer" />
          <button type="button" onClick={onClose}>Cancel</button>
          <button className="primary" disabled={busy || !summary.trim()}>{busy ? "Creating…" : "Create"}</button>
        </div>
      </form>
    </Modal>
  );
}

const DAY = 24 * 60 * 60 * 1000;
const dateInput = (iso: string | null, fallback: number) => new Date(iso ?? fallback).toISOString().slice(0, 10);

// "start": dates are required and the sprint becomes active. "edit": name/goal/dates of a
// future or active sprint.
export function SprintDialog({ mode, sprint, onClose, onDone }: {
  mode: "start" | "edit"; sprint: Sprint; onClose: () => void; onDone: () => void;
}) {
  const [name, setName] = useState(sprint.name);
  const [goal, setGoal] = useState(sprint.goal);
  const [start, setStart] = useState(dateInput(sprint.start_date, Date.now()));
  const [end, setEnd] = useState(dateInput(sprint.end_date, Date.now() + 14 * DAY));
  const { error, busy, submit } = useSubmit(onDone);
  const iso = (d: string) => new Date(d).toISOString();

  const save = submit(async () => {
    await api.editSprint(sprint.id, { name: name.trim(), goal, ...(mode === "edit" ? { start_date: iso(start), end_date: iso(end) } : {}) });
    if (mode === "start") await api.startSprint(sprint.id, iso(start), iso(end));
  });

  return (
    <Modal title={mode === "start" ? `Start ${sprint.name}` : `Edit ${sprint.name}`} onClose={onClose}>
      <form onSubmit={save} className="mj-form">
        <label>Sprint name
          <input required value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <div className="mj-form-row">
          <label>Start date
            <input type="date" required value={start} onChange={(e) => setStart(e.target.value)} />
          </label>
          <label>End date
            <input type="date" required min={start} value={end} onChange={(e) => setEnd(e.target.value)} />
          </label>
        </div>
        <label>Sprint goal
          <textarea rows={3} value={goal} onChange={(e) => setGoal(e.target.value)} />
        </label>
        {error && <p className="error">{error}</p>}
        <div className="buttons">
          <button type="button" onClick={onClose}>Cancel</button>
          <button className="primary" disabled={busy || !name.trim()}>{mode === "start" ? "Start" : "Save"}</button>
        </div>
      </form>
    </Modal>
  );
}

export function CompleteSprintDialog({ board, sprint, onClose, onDone }: {
  board: BoardView; sprint: Sprint; onClose: () => void; onDone: () => void;
}) {
  const doneStatus = board.statuses[board.statuses.length - 1];
  const issues = board.issues.filter((i) => i.sprint_id === sprint.id && i.issuetype !== "Epic");
  const open = issues.filter((i) => i.status !== doneStatus).length;
  const [moveTo, setMoveTo] = useState("");
  const { error, busy, submit } = useSubmit(onDone);
  const futures = board.sprints.filter((s) => s.state === "future");
  const complete = submit(() => api.completeSprint(sprint.id, moveTo === "" ? null : Number(moveTo)));

  return (
    <Modal title={`Complete ${sprint.name}`} onClose={onClose}>
      <form onSubmit={complete} className="mj-form">
        <p>
          <strong>{issues.length - open}</strong> issue(s) done, <strong>{open}</strong> not done.
        </p>
        {open > 0 && (
          <label>Move the open issues to
            <select value={moveTo} onChange={(e) => setMoveTo(e.target.value)}>
              <option value="">Backlog</option>
              {futures.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </label>
        )}
        {error && <p className="error">{error}</p>}
        <div className="buttons">
          <button type="button" onClick={onClose}>Cancel</button>
          <button className="primary" disabled={busy}>Complete sprint</button>
        </div>
      </form>
    </Modal>
  );
}
