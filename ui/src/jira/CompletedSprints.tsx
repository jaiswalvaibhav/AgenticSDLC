import { useState } from "react";
import { Avatar, Lozenge, TypeIcon, day, isSubtask, points } from "./parts";
import { Link } from "./route";
import type { BoardView, IssueSummary } from "./types";

// Finished sprints and the tickets that finished in them. Unfinished tickets moved on when the sprint
// was completed, so they are in the next sprint or the backlog, not here.
export function CompletedSprints({ board }: { board: BoardView }) {
  const [open, setOpen] = useState<Set<number>>(new Set());
  const closed = board.sprints.filter((s) => s.state === "closed").sort((a, b) => b.id - a.id);
  if (closed.length === 0) return null;
  const toggle = (id: number) => setOpen((o) => { const n = new Set(o); if (n.has(id)) n.delete(id); else n.add(id); return n; });

  const row = (i: IssueSummary, nested = false) => (
    <div key={i.key} className={`mj-row${nested ? " sub" : ""} readonly`}>
      <TypeIcon type={i.issuetype} />
      <Link to={`/jira/browse/${i.key}`} className="mj-key">{i.key}</Link>
      <span className="mj-summary">{i.summary}</span>
      <span className="mj-spacer" />
      <Lozenge board={board} status={i.status} />
      {!nested && <span className="mj-points">{i.story_points ?? ""}</span>}
      <Avatar name={i.assignee} />
    </div>
  );

  return (
    <section className="mj-completed">
      <h3>Completed sprints <span className="mj-count">{closed.length}</span></h3>
      {closed.map((s) => {
        const all = board.issues.filter((i) => i.sprint_id === s.id && i.issuetype !== "Epic");
        const tops = all.filter((i) => !isSubtask(i));
        const isOpen = open.has(s.id);
        return (
          <div key={s.id} className="mj-container">
            <div className="mj-container-head">
              <button className="link-button" onClick={() => toggle(s.id)} aria-expanded={isOpen} aria-label={`Show tickets of ${s.name}`}>
                {isOpen ? "▾" : "▸"}
              </button>
              <strong>{s.name}</strong>
              <span className="mj-sprint-state closed">Completed</span>
              <span className="muted small">{day(s.start_date)} – {day(s.complete_date)}</span>
              <span className="muted small">
                {s.completed ?? all.length} completed
                {s.not_completed ? ` · ${s.not_completed} not completed (moved on)` : ""} · {points(all)} pts
              </span>
              {s.goal && <span className="muted small mj-goal">Goal: {s.goal}</span>}
            </div>
            {isOpen && (tops.length === 0
              ? <p className="mj-empty">No tickets finished in this sprint.</p>
              : tops.map((t) => (
                <div key={t.key}>
                  {row(t)}
                  {all.filter((c) => isSubtask(c) && c.parent === t.key).map((c) => row(c, true))}
                </div>
              )))}
          </div>
        );
      })}
    </section>
  );
}
