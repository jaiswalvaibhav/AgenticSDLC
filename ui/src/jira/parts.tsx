import type { BoardView, IssueSummary } from "./types";

export const isSubtask = (i: { issuetype: string }) => ["subtask", "sub-task"].includes(i.issuetype.toLowerCase());
// The name the UI writes comments and history as (the backend's acting_as("You")): only its own comments are editable.
export const ME = "You";
export const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

// first status = to do, last = done, anything between = in progress (like Jira's status categories)
export function statusCategory(statuses: string[], status: string): "todo" | "progress" | "done" {
  const i = statuses.indexOf(status);
  return i <= 0 ? "todo" : i === statuses.length - 1 ? "done" : "progress";
}

export function Lozenge({ board, status }: { board: Pick<BoardView, "statuses">; status: string }) {
  return <span className={`mj-lozenge ${statusCategory(board.statuses, status)}`}>{status}</span>;
}

const TYPE_LETTER: Record<string, string> = { epic: "E", story: "S", task: "T", bug: "B", subtask: "s", "sub-task": "s" };

export function TypeIcon({ type }: { type: string }) {
  const lower = type.toLowerCase();
  return (
    <span className={`mj-type ${lower.replace("-", "")}`} title={type}>
      {TYPE_LETTER[lower] ?? type[0]}
    </span>
  );
}

export function Avatar({ name }: { name: string | null }) {
  if (!name) return <span className="mj-avatar none" title="Unassigned">?</span>;
  return <span className="mj-avatar" title={name}>{name[0].toUpperCase()}</span>;
}

export function Priority({ value }: { value: string }) {
  return <span className={`mj-priority ${value.toLowerCase()}`} title={`Priority: ${value}`}>{value[0]}</span>;
}

export const points = (issues: IssueSummary[]) => issues.reduce((sum, i) => sum + (i.story_points ?? 0), 0);

export const day = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString() : "");
export const dateTime = (iso: string) => new Date(iso).toLocaleString();

// Jira's hierarchy (mirrors MockJiraClient._check_parent): an Epic has no parent; a Story/Task/Bug sits
// under an Epic; a Subtask sits under a Story/Task/Bug.
export const parentAllowed = (issueType: string) => issueType.toLowerCase() !== "epic";
export const parentRequired = (issueType: string) => isSubtask({ issuetype: issueType });
export function eligibleParents(board: BoardView, issueType: string, selfKey?: string): IssueSummary[] {
  if (!parentAllowed(issueType)) return [];
  return board.issues.filter((i) => i.key !== selfKey &&
    (parentRequired(issueType) ? i.issuetype.toLowerCase() !== "epic" && !isSubtask(i) : i.issuetype === "Epic"));
}
