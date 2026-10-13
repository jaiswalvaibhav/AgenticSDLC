import { useEffect, useRef, useState } from "react";
import * as api from "../api";
import type { AgentRunSummary, AgentTrace, KbChunk, TraceBlock, TraceEvent } from "../types";
import { formatElapsed } from "./AgentActivity";

interface Props {
  useCase: string;
  profile: string;
  stepId: string;
  title: string;
  onClose: () => void;
}

const LOG_REFRESH_MS = 5000;
const time = (iso: string | null) => (iso ? new Date(iso).toLocaleTimeString() : "…");
const took = (a: string, b: string | null) => (b ? formatElapsed(new Date(b).getTime() - new Date(a).getTime()) : "running");
const kb = (n: number) => (n < 1024 ? `${n} B` : `${(n / 1024).toFixed(0)} KB`);

function Block({ block }: { block: TraceBlock }) {
  if (block.kind === "text" && "text" in block) {
    const first = block.text.split("\n").find((l) => l.trim()) ?? "";
    return (
      <details>
        <summary>
          {first.slice(0, 80)} <span className="muted small">({block.text.length.toLocaleString()} chars)</span>
        </summary>
        <pre className="log-text">{block.text}</pre>
      </details>
    );
  }
  if (block.kind === "tool_use" && "name" in block) {
    return (
      <div>
        <span className="chip generate">tool call</span> <code>{block.name}</code>
        <pre className="log-text">{JSON.stringify(block.input, null, 2)}</pre>
      </div>
    );
  }
  if ("bytes" in block) {
    return (
      <div className="muted small">
        {block.kind} {block.name ?? ""} ({block.format ?? "?"}, {kb(block.bytes)}) — sent to the model; not stored
      </div>
    );
  }
  return <div className="muted small">{block.kind}</div>;
}

function Chunk({ chunk }: { chunk: KbChunk }) {
  return (
    <details>
      <summary>
        {chunk.title || "(untitled)"} <span className="muted small">score {chunk.score.toFixed(3)}</span>
      </summary>
      <pre className="log-text">{chunk.text}</pre>
      <div className="muted small">
        {chunk.url && (
          <a href={chunk.url} target="_blank" rel="noreferrer">
            page
          </a>
        )}{" "}
        {chunk.s3_uri}
      </div>
    </details>
  );
}

function Event({ event }: { event: TraceEvent }) {
  if (event.type === "model_call") {
    return (
      <li>
        <strong>Model call {event.n}</strong>{" "}
        <span className="muted small">
          {time(event.started_at)} · {took(event.started_at, event.ended_at)}
          {event.stop_reason ? ` · stopped: ${event.stop_reason}` : ""}
        </span>
        {event.error && <div className="error">{event.error}</div>}
        {event.content.map((b, i) => (
          <Block key={i} block={b} />
        ))}
        {!event.ended_at && <div className="muted">waiting for the response…</div>}
      </li>
    );
  }
  if (event.type === "tool_error") {
    return (
      <li>
        <strong>Tool error</strong> <code>{event.tool}</code> <span className="muted small">{time(event.at)}</span>
        <pre className="log-text error">{event.text}</pre>
      </li>
    );
  }
  return (
    <li>
      <strong>Knowledge base search</strong> <code>{event.query}</code>{" "}
      <span className="muted small">
        {time(event.started_at)} · {took(event.started_at, event.ended_at)} · {event.chunks.length} result
        {event.chunks.length === 1 ? "" : "s"}
      </span>
      {event.error && <div className="error">{event.error}</div>}
      {event.chunks.map((c, i) => (
        <Chunk key={i} chunk={c} />
      ))}
    </li>
  );
}

export function AgentLog({ useCase, profile, stepId, title, onClose }: Props) {
  const [runs, setRuns] = useState<AgentRunSummary[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [trace, setTrace] = useState<AgentTrace | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showPlan, setShowPlan] = useState(false);
  const traceRef = useRef<AgentTrace | null>(null); // read by the polling effect without re-running it
  traceRef.current = trace;

  // the run list, refreshed while open (a new run can start); the picked run's trace is
  // re-fetched only until it has an outcome, since a trace can be large
  useEffect(() => {
    let stale = false;
    const load = async () => {
      try {
        const list = await api.getAgentRuns(useCase, stepId, profile);
        if (stale) return;
        setRuns(list);
        const runId = selected ?? list[0]?.run_id ?? null;
        if (!selected && runId) setSelected(runId);
        const have = traceRef.current;
        if (runId && (have?.run_id !== runId || have.outcome === null)) {
          const fetched = await api.getAgentRun(useCase, stepId, runId, profile);
          if (!stale) setTrace(fetched);
        }
        setError(null);
      } catch (e) {
        if (!stale) setError(e instanceof Error ? e.message : String(e));
      }
    };
    load();
    const timer = setInterval(load, LOG_REFRESH_MS);
    return () => {
      stale = true;
      clearInterval(timer);
    };
  }, [useCase, stepId, profile, selected]);

  const shown = trace && trace.run_id === selected ? trace : null;
  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="panel-head">
          <h2>Agent log · {title}</h2>
          <button onClick={onClose}>Close</button>
        </div>
        {error && <div className="banner error">{error}</div>}
        {runs && runs.length === 0 && <p className="muted">No runs recorded yet. The log appears when the agent starts.</p>}
        {runs && runs.length > 0 && (
          <label>
            Run{" "}
            <select value={selected ?? ""} onChange={(e) => { setSelected(e.target.value); setTrace(null); }}>
              {runs.map((r) => (
                <option key={r.run_id} value={r.run_id}>
                  {new Date(r.started_at).toLocaleString()} — {r.outcome ?? "running"} ({r.model_calls} model call
                  {r.model_calls === 1 ? "" : "s"}, {r.kb_queries} KB search{r.kb_queries === 1 ? "" : "es"})
                </option>
              ))}
            </select>
          </label>
        )}
        {shown && (
          <>
            <h3>Sent to the model</h3>
            <p className="muted small">
              Knowledge base: {shown.kb_enabled === null ? "not reached" : shown.kb_enabled ? "on" : "off for this run"}
            </p>
            {shown.system_prompt && (
              <details>
                <summary>System prompt</summary>
                <pre className="log-text">{shown.system_prompt}</pre>
              </details>
            )}
            {shown.input.length === 0 && <p className="muted small">Not reached (see the outcome below).</p>}
            {shown.input.map((b, i) => (
              <Block key={i} block={b} />
            ))}
            <h3>Timeline</h3>
            {shown.events.length === 0 ? (
              <p className="muted small">No model calls yet.</p>
            ) : (
              <ol className="log-events">
                {shown.events.map((e, i) => (
                  <Event key={i} event={e} />
                ))}
              </ol>
            )}
            <h3>Outcome</h3>
            <p>
              <strong>{shown.outcome ?? "running"}</strong>
              {shown.error && <span className="error"> — {shown.error}</span>}
            </p>
            {shown.result && (
              <>
                <p>
                  {shown.result.requirement_count} requirement{shown.result.requirement_count === 1 ? "" : "s"} —{" "}
                  {shown.result.summary}
                </p>
                {shown.result.usage.totalTokens !== undefined && (
                  <p className="muted small">
                    Tokens: {shown.result.usage.inputTokens?.toLocaleString()} in ·{" "}
                    {shown.result.usage.outputTokens?.toLocaleString()} out
                  </p>
                )}
                <button className="link-button" onClick={() => setShowPlan(!showPlan)}>
                  {showPlan ? "Hide" : "Show"} the plan as JSON
                </button>
                {showPlan && <pre className="log-text">{JSON.stringify(shown.result.plan, null, 2)}</pre>}
              </>
            )}
          </>
        )}
      </aside>
    </div>
  );
}
