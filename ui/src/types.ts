// Mirrors the backend's responses (backend/sdlc_api/app.py, progress.py, poller.py).
export interface Meta {
  use_cases: string[];
  default_use_case: string;
  profiles: string[];
  default_profile: string;
  external_llm_provider: string | null;
  auto_confirm_external_llm: boolean;
  knowledge_base: { enabled: boolean; reason: string }; // reason is empty when enabled
  jira_mode: "mock" | "real";
  jira_project_key: string;
  jira_base_url: string;
}

export interface Step {
  id: string;
  artifact: string;
  owner: string;
  inputs: string[];
  automation: string | null;
  optional: boolean;
}

export interface Preview {
  generate: Step[];
  provide: Step[];
}

export type Anchor =
  | { ok: true; kind: "file"; name: string }
  | { ok: true; kind: "link"; name: string; via: string }
  | { ok: false; reason: string };

// The agent's live status (src/sdlc/agents/activity.py), read fresh on every progress poll.
export interface Activity {
  running: boolean;
  run_id: string; // the trace to open (AgentRun)
  phase: string; // reading_anchors | calling_model | searching_kb | writing_plan | publishing_page | done | blocked | failed
  detail: string;
  started_at: string;
  updated_at: string;
  model_calls: number;
  kb_queries: number;
  outcome: string | null;
  error: string | null;
}

export interface ProgressRow {
  step_id: string;
  artifact: string;
  key: string;
  jira_url: string;
  role: "generate" | "provide" | "automated";
  status: string;
  done: boolean;
  files: string[];
  links: { url: string; title: string }[];
  anchor: Anchor | null;
  run_state: string | null;
  decision_pending: "approve" | "reject" | null;
  plan_ready: boolean | null;
  llm_usage: { calls: number; runs: number } | null;
  activity: Activity | null;
  recent_comments: string[];
}

export interface PollerState {
  active: boolean;
  use_case: string | null;
  profile: string | null;
  interval_seconds: number;
  last_sync_at: string | null;
  last_result: Record<string, unknown> | null;
  last_error: string | null;
  cycles: number;
  note: string | null;
  cycle_started_at: string | null; // set while a poll cycle (which includes any agent run) is in progress
}

export interface Progress {
  built_at: string;
  approval_label: string;
  reject_label: string;
  steps: ProgressRow[];
  poller: PollerState;
}

export interface RunRequest {
  use_case: string;
  targets: string[];
  profile: string;
  confirm_external_llm: boolean;
}

export interface RunResult {
  use_case: string;
  steps: Record<string, string>;
  generate: string[];
  provide: string[];
  poller: PollerState;
}

export type RunInfo =
  | { exists: false; current_project_key: string }
  | {
      exists: true;
      project_key: string;
      started_at: string | null;
      ticket_count: number;
      current_project_key: string;
      stale: boolean;
    };

export interface ResetResult {
  reset: true;
  deleted: string[]; // mock Jira tickets deleted (empty with real Jira)
  page_errors: Record<string, string>; // Confluence pages that could not be deleted
  poller: PollerState;
}

// A run's recorded trace (src/sdlc/agents/activity.py).
export type TraceBlock =
  | { kind: "text"; text: string }
  | { kind: "tool_use"; name: string; input: unknown }
  | { kind: "image" | "document"; format: string | null; name: string | null; bytes: number }
  | { kind: string };

export interface KbChunk {
  title: string;
  score: number;
  url: string;
  s3_uri: string;
  text: string;
}

export type TraceEvent =
  | {
      type: "model_call";
      n: number;
      started_at: string;
      ended_at: string | null;
      stop_reason: string | null;
      error: string | null;
      content: TraceBlock[];
    }
  | { type: "tool_error"; tool: string; at: string; text: string }
  | { type: "kb_search"; query: string; started_at: string; ended_at: string | null; chunks: KbChunk[]; error: string | null };

export interface AgentRunSummary {
  run_id: string;
  started_at: string;
  outcome: string | null; // null while running
  model_calls: number;
  kb_queries: number;
}

export interface AgentTrace {
  run_id: string;
  started_at: string;
  outcome: string | null;
  error: string | null;
  kb_enabled: boolean | null;
  system_prompt: string | null;
  input: TraceBlock[];
  events: TraceEvent[];
  result: {
    summary: string;
    requirement_count: number;
    usage: { inputTokens?: number; outputTokens?: number; totalTokens?: number };
    plan: unknown;
  } | null;
}
