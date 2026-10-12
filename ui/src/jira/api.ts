import { request } from "../api";
import type { BoardView, IssueDetail, NewIssue, Sprint } from "./types";

// The mock board for the same profile (local state or S3) as the workflow page that linked here.
export const profile = () => new URLSearchParams(window.location.search).get("profile");
const q = () => (profile() ? `?profile=${encodeURIComponent(profile()!)}` : "");

function send<T>(method: string, path: string, body?: unknown) {
  return request<T>(`/mockjira${path}${q()}`, { method, body: body === undefined ? undefined : JSON.stringify(body) });
}

const enc = encodeURIComponent;

export const getBoard = () => send<BoardView>("GET", "/board");
export const getIssue = (key: string) => send<IssueDetail>("GET", `/issues/${enc(key)}`);
export const createIssue = (body: NewIssue) => send<{ key: string }>("POST", "/issues", body);
export const editIssue = (key: string, fields: Record<string, unknown>) => send("PATCH", `/issues/${enc(key)}`, fields);
export const deleteIssue = (key: string) => send("DELETE", `/issues/${enc(key)}`);
export const transition = (key: string, status: string) => send("POST", `/issues/${enc(key)}/transitions`, { status });

export const createSprint = (name?: string, goal?: string) => send<Sprint>("POST", "/sprints", { name, goal });
export const editSprint = (id: number, fields: Partial<Pick<Sprint, "name" | "goal" | "start_date" | "end_date">>) =>
  send<Sprint>("PATCH", `/sprints/${id}`, fields);
export const startSprint = (id: number, start_date: string, end_date: string) =>
  send<Sprint>("POST", `/sprints/${id}/start`, { start_date, end_date });
export const completeSprint = (id: number, move_to: number | null) =>
  send<{ moved: string[] }>("POST", `/sprints/${id}/complete`, { move_to });
export const deleteSprint = (id: number) => send("DELETE", `/sprints/${id}`);
export const rank = (keys: string[], sprint_id: number | null, before_key: string | null) =>
  send("POST", "/rank", { keys, sprint_id, before_key });
