import { useEffect, useState } from "react";
import type { Activity } from "../types";

const PHASES: Record<string, string> = {
  starting: "Starting…",
  reading_anchors: "Reading the design documents…",
  calling_model: "Waiting for the model…",
  searching_kb: "Searching the knowledge base…",
  writing_plan: "Saving the plan…",
  publishing_page: "Publishing the plan to Confluence…",
};

export function formatElapsed(ms: number) {
  const s = Math.max(0, Math.floor(ms / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}

// Ticks locally every second, so the timer moves between the 5s progress polls.
export function Elapsed({ since }: { since: string }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  return <>{formatElapsed(now - new Date(since).getTime())}</>;
}

export function AgentActivity({ activity }: { activity: Activity }) {
  if (!activity.running) {
    return activity.outcome === "failed" || activity.outcome === "blocked" ? (
      <div className="error">
        Last run {activity.outcome}
        {activity.error ? `: ${activity.error}` : ""}
      </div>
    ) : null;
  }
  return (
    <div className="activity">
      <div>
        <span className="spinner" aria-hidden /> <strong>{PHASES[activity.phase] ?? activity.phase}</strong>{" "}
        <span className="muted">
          <Elapsed since={activity.started_at} />
        </span>
      </div>
      {activity.detail && <div className="muted small clamp-1">{activity.detail}</div>}
      <div className="muted small">
        {activity.model_calls} model call{activity.model_calls === 1 ? "" : "s"} · {activity.kb_queries} KB search
        {activity.kb_queries === 1 ? "" : "es"}
      </div>
    </div>
  );
}
