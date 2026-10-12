// Mirrors the mock Jira backend (backend/sdlc_api/mockjira.py, adapters/mock_jira.py's board_view/issue_view).
export interface Sprint {
  id: number;
  name: string;
  state: "future" | "active" | "closed";
  goal: string;
  start_date: string | null;
  end_date: string | null;
  complete_date: string | null;
  completed?: number; // sprint report, set when the sprint is completed
  not_completed?: number; // unfinished tickets that moved on
}

export interface IssueSummary {
  key: string;
  summary: string;
  issuetype: string;
  status: string;
  priority: string;
  assignee: string | null;
  story_points: number | null;
  labels: string[];
  parent: string | null;
  sprint_id: number | null;
  components: string[];
  updated: string;
}

export interface BoardView {
  project: { key: string; name: string };
  statuses: string[]; // board columns, in order; the last one is "Done"
  issue_types: string[];
  priorities: string[];
  sprints: Sprint[];
  issues: IssueSummary[]; // every issue, in rank order
}

export interface IssueRef {
  key: string;
  summary: string;
  issuetype: string;
  status: string;
}

export interface ChecklistItem {
  text: string;
  done: boolean;
}

export interface IssueDetail extends IssueSummary {
  id: string;
  brief_description: string; // the next three are checklists; only stories, tasks and bugs use them
  acceptance_criteria: ChecklistItem[];
  definition_of_done: ChecklistItem[];
  test_coverage: ChecklistItem[];
  description: string;
  reporter: string;
  created: string;
  comments: { id: string; author: string; body: string; created: string; updated?: string }[];
  attachments: { id: string; filename: string; mimeType: string; size: number; author: string; created: string; url: string }[];
  remote_links: { id: string; url: string; title: string }[];
  links: { type: string; direction: "inward" | "outward"; key: string; issue: IssueRef }[];
  changelog: { id: string; created: string; author: string; items: { field: string; from: unknown; to: unknown }[] }[];
  parent_issue: IssueRef | null;
  children: IssueRef[];
  sprint: Sprint | null;
}

export interface NewIssue {
  issue_type: string;
  summary: string;
  description?: string;
  parent?: string | null;
  labels?: string[];
  priority?: string | null;
  story_points?: number | null;
  sprint_id?: number | null;
}
