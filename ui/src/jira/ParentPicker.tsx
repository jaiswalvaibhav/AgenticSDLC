import { IssuePicker } from "./IssuePicker";
import { eligibleParents, parentAllowed, parentRequired } from "./parts";
import type { BoardView } from "./types";

interface Props {
  board: BoardView;
  issueType: string; // the type of the ticket being created or edited; decides which parents are offered
  selfKey?: string;
  value: string; // parent key, "" = none
  onChange: (key: string) => void;
}

// Only valid parents are offered (see eligibleParents): an epic for a story/task/bug, a story/task/bug for a subtask.
export function ParentPicker({ board, issueType, selfKey, value, onChange }: Props) {
  if (!parentAllowed(issueType)) return <span className="muted">Epics have no parent.</span>;
  const required = parentRequired(issueType);
  return (
    <IssuePicker board={board} candidates={eligibleParents(board, issueType, selfKey)} value={value} onChange={onChange}
      placeholder={required ? "Choose a parent…" : "None"} noneLabel={required ? null : "None"}
      emptyText={`No ${required ? "stories, tasks or bugs" : "epics"} to choose from yet.`} />
  );
}
