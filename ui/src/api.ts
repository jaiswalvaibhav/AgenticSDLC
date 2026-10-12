import type { Meta, PollerState, Preview, Progress, RunInfo, RunRequest, RunResult, Step } from "./types";

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = body?.detail;
    throw new Error(typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : response.statusText);
  }
  return response.json() as Promise<T>;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const getMeta = () => request<Meta>("/meta");
export const getSteps = () => request<{ steps: Step[] }>("/workflow").then((r) => r.steps);
export const getPreview = (targets: string[]) => post<Preview>("/preview", { targets });
export const startRun = (body: RunRequest) => post<RunResult>("/runs", body);

const progressPath = (useCase: string, profile: string) =>
  `/runs/${encodeURIComponent(useCase)}/progress?profile=${encodeURIComponent(profile)}`;
export const getProgress = (useCase: string, profile: string) => request<Progress>(progressPath(useCase, profile));
export const refreshProgress = (useCase: string, profile: string) =>
  post<Progress>(`/runs/${encodeURIComponent(useCase)}/refresh?profile=${encodeURIComponent(profile)}`);

const runPath = (useCase: string, profile: string, action: string) =>
  `/runs/${encodeURIComponent(useCase)}/${action}?profile=${encodeURIComponent(profile)}`;
export const getRunInfo = (useCase: string, profile: string) => request<RunInfo>(runPath(useCase, profile, "info"));
export const resetRun = (useCase: string, profile: string) =>
  post<{ reset: true; poller: PollerState }>(runPath(useCase, profile, "reset"));

export const decide = (useCase: string, stepId: string, decision: "approve" | "reject", profile: string, feedback?: string) =>
  post<{ key: string; label: string; acting_now: boolean }>(
    `/runs/${encodeURIComponent(useCase)}/steps/${encodeURIComponent(stepId)}/${decision}?profile=${encodeURIComponent(profile)}`,
    { feedback },
  );
export const rerun = (useCase: string, stepId: string, profile: string) =>
  post<{ acting_now: boolean }>(
    `/runs/${encodeURIComponent(useCase)}/steps/${encodeURIComponent(stepId)}/rerun?profile=${encodeURIComponent(profile)}`,
  );

export const getPoller = () => request<PollerState>("/poller");
export const startPoller = (useCase: string, profile: string, confirmExternalLlm: boolean) =>
  post<PollerState>("/poller/start", { use_case: useCase, profile, confirm_external_llm: confirmExternalLlm });
export const stopPoller = () => post<PollerState>("/poller/stop");
export const syncNow = () => post<PollerState>("/poller/sync-now");
