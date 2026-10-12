import type { ReactNode } from "react";
import type { BoardView, IssueSummary } from "./types";

export const isSubtask = (i: { issuetype: string }) => ["subtask", "sub-task"].includes(i.issuetype.toLowerCase());
// The name the UI writes comments and history as (the backend's acting_as("You")): only its own comments are editable.
export const ME = "You";
// A "Blocks" link seen from one issue: it is blocked by the other (it depends on it), or it blocks the other
// (the other depends on it). Other link types show their own name.
export const linkLabel = (type: string, direction: "inward" | "outward") =>
  type === "Blocks" ? (direction === "inward" ? "depends on" : "is depended by") : type;
export const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

// first status = to do, last = done, anything between = in progress (like Jira's status categories)
export function statusCategory(statuses: string[], status: string): "todo" | "progress" | "done" {
  const i = statuses.indexOf(status);
  return i <= 0 ? "todo" : i === statuses.length - 1 ? "done" : "progress";
}

export function Lozenge({ board, status }: { board: Pick<BoardView, "statuses">; status: string }) {
  return <span className={`mj-lozenge ${statusCategory(board.statuses, status)}`}>{status}</span>;
}

// Issue-type icons drawn like Jira's (own SVGs, same colours and glyphs): a coloured rounded square with a white mark.
const TYPE_ICONS: Record<string, { color: string; mark: ReactNode }> = {
  epic: { color: "#904ee2", mark: <polygon points="9.2,2.5 4.8,9.1 7.4,9.1 6.8,13.5 11.2,6.9 8.6,6.9" fill="#fff" /> },
  story: { color: "#63ba3c", mark: <path d="M5 3h6v10l-3-2.4L5 13z" fill="#fff" /> },
  task: { color: "#4bade8", mark: <polyline points="4.6,8.3 7,10.7 11.4,5.6" fill="none" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /> },
  bug: { color: "#e5493a", mark: <circle cx="8" cy="8" r="3.4" fill="#fff" /> },
  subtask: {
    color: "#4bade8",
    mark: (
      <>
        <rect x="3.8" y="3.8" width="3.8" height="3.8" rx="0.6" fill="#fff" />
        <rect x="8.4" y="8.4" width="3.8" height="3.8" rx="0.6" fill="#fff" />
        <path d="M5.7 7.6v2.7h2.7" fill="none" stroke="#fff" strokeWidth="1.3" />
      </>
    ),
  },
};

export function TypeIcon({ type }: { type: string }) {
  const key = type.toLowerCase().replace("-", "");
  const icon = TYPE_ICONS[key] ?? TYPE_ICONS.task; // a type we have no drawing for looks like a task
  return (
    <svg className="mj-type" viewBox="0 0 16 16" width="18" height="18" role="img" aria-label={type}>
      <title>{type}</title>
      <rect width="16" height="16" rx="3" fill={icon.color} />
      {icon.mark}
    </svg>
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
