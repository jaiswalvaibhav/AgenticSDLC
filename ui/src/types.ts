// Mirrors the backend's responses (backend/sdlc_api/app.py, progress.py, poller.py).
export interface Meta {
  use_cases: string[];
  default_use_case: string;
  profiles: string[];
  default_profile: string;
  external_llm_provider: string | null;
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

export type Anchor = { ok: true; kind: "file" | "link"; name: string } | { ok: false; reason: string };

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
  plan_ready: boolean | null;
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
}

export interface Progress {
  built_at: string;
  approval_label: string;
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
