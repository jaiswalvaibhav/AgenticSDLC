import { useState } from "react";
import * as api from "./api";
import { CompleteSprintDialog } from "./Dialogs";
import { Avatar, Priority, TypeIcon, day, message } from "./parts";
import { Link } from "./route";
import type { BoardView } from "./types";

interface Props {
  board: BoardView;
  reload: () => Promise<unknown>;
}

export function Board({ board, reload }: Props) {
  const sprint = board.sprints.find((s) => s.state === "active");
  const [dragKey, setDragKey] = useState<string | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const [completing, setCompleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!sprint) {
    return (
      <div className="mj-empty-state">
        <h2>No active sprint</h2>
        <p className="muted">Start a sprint from the backlog to see its issues on the board.</p>
        <Link to="/jira/backlog" className="mj-button-link">Go to the backlog</Link>
      </div>
    );
  }

  const cards = board.issues.filter((i) => i.sprint_id === sprint.id && i.issuetype !== "Epic");
  const byKey = new Map(board.issues.map((i) => [i.key, i]));

  const move = async (key: string, status: string) => {
    setDragKey(null);
    setOver(null);
    if (byKey.get(key)?.status === status) return;
    try {
      await api.transition(key, status);
      setError(null);
      await reload();
    } catch (e) {
      setError(message(e));
    }
  };

  return (
    <div>
      <div className="mj-board-head">
        <div>
          <h2>{sprint.name}</h2>
          <span className="muted small">{day(sprint.start_date)} – {day(sprint.end_date)}</span>
          {sprint.goal && <span className="muted small"> · Goal: {sprint.goal}</span>}
        </div>
        <button onClick={() => setCompleting(true)}>Complete sprint</button>
      </div>
      {error && <div className="banner error">{error}</div>}
      <div className="mj-columns" style={{ gridTemplateColumns: `repeat(${board.statuses.length}, minmax(220px, 1fr))` }}>
        {board.statuses.map((status) => {
          const column = cards.filter((c) => c.status === status);
          return (
            <section
              key={status}
              className={`mj-column${over === status ? " over" : ""}`}
              onDragOver={(e) => { if (dragKey) { e.preventDefault(); setOver(status); } }}
              onDragLeave={() => setOver((o) => (o === status ? null : o))}
              onDrop={(e) => { e.preventDefault(); const key = dragKey ?? e.dataTransfer.getData("text/plain"); if (key) move(key, status); }}
            >
              <h3>{status} <span className="muted">{column.length}</span></h3>
              {column.map((card) => {
                const parent = card.parent ? byKey.get(card.parent) : undefined;
                return (
                  <article
                    key={card.key}
                    className={`mj-card${dragKey === card.key ? " dragging" : ""}`}
                    draggable
                    onDragStart={(e) => { e.dataTransfer.setData("text/plain", card.key); setDragKey(card.key); }}
                    onDragEnd={() => { setDragKey(null); setOver(null); }}
                  >
                    <div className="mj-card-summary">{card.summary}</div>
                    {parent && <div className="mj-epic" title={parent.summary}>{parent.key} {parent.summary}</div>}
                    <div className="mj-card-foot">
                      <TypeIcon type={card.issuetype} />
                      <Link to={`/jira/browse/${card.key}`} className="mj-key">{card.key}</Link>
                      <span className="mj-spacer" />
                      <span className="mj-pill" title="Story points">{card.story_points ?? "–"}</span>
                      <Priority value={card.priority} />
                      <Avatar name={card.assignee} />
                    </div>
                  </article>
                );
              })}
            </section>
          );
        })}
      </div>
      {completing && (
        <CompleteSprintDialog board={board} sprint={sprint} onClose={() => setCompleting(false)}
          onDone={() => { setCompleting(false); reload(); }} />
      )}
    </div>
  );
}
