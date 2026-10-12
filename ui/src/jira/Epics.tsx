import { TypeIcon, isSubtask, Lozenge } from "./parts";
import { Link } from "./route";
import type { BoardView } from "./types";

interface Props {
  board: BoardView;
  onCreate: () => void;
  onShowInBacklog: (epicKey: string) => void;
}

// The Epics tab: every epic with how far its stories are, so you can see how many exist.
export function EpicsPage({ board, onCreate, onShowInBacklog }: Props) {
  const epics = board.issues.filter((i) => i.issuetype === "Epic");
  const doneStatus = board.statuses[board.statuses.length - 1];
  return (
    <div>
      <div className="mj-board-head">
        <div>
          <h2>Epics</h2>
          <span className="muted small">{epics.length} epic{epics.length === 1 ? "" : "s"}</span>
        </div>
        <button className="primary" onClick={onCreate}>Create epic</button>
      </div>
      {epics.length === 0 ? (
        <p className="mj-empty">No epics yet. Create one to group related stories.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Key</th><th>Summary</th><th>Status</th><th>Progress</th><th /></tr>
            </thead>
            <tbody>
              {epics.map((e) => {
                const kids = board.issues.filter((i) => i.parent === e.key && !isSubtask(i));
                const done = kids.filter((k) => k.status === doneStatus).length;
                return (
                  <tr key={e.key}>
                    <td className="mj-nowrap"><TypeIcon type={e.issuetype} /> <Link to={`/jira/browse/${e.key}`} className="mj-key">{e.key}</Link></td>
                    <td>{e.summary}</td>
                    <td><Lozenge board={board} status={e.status} /></td>
                    <td>
                      <div className="mj-progress">
                        <span className="mj-bar" aria-hidden>
                          <span style={{ width: kids.length ? `${(100 * done) / kids.length}%` : "0%" }} />
                        </span>
                        <span className="muted small mj-nowrap">{done}/{kids.length} stories done</span>
                      </div>
                    </td>
                    <td><button className="link-button" onClick={() => onShowInBacklog(e.key)}>Show in backlog</button></td>
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
