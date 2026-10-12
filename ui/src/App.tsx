import { useCallback, useEffect, useState } from "react";
import * as api from "./api";
import { PollerStatus } from "./components/PollerStatus";
import { PreviewPanel } from "./components/PreviewPanel";
import { ProgressTable } from "./components/ProgressTable";
import { RunBanner } from "./components/RunBanner";
import { StartFreshDialog } from "./components/StartFreshDialog";
import { StepPicker } from "./components/StepPicker";
import { TriggerDialog } from "./components/TriggerDialog";
import type { Meta, PollerState, Preview, Progress, ProgressRow, RunInfo, Step } from "./types";

const PROGRESS_REFRESH_MS = 5000;
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

export default function App() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [steps, setSteps] = useState<Step[]>([]);
  const [useCase, setUseCase] = useState("");
  const [profile, setProfile] = useState("local");
  const [targets, setTargets] = useState<string[]>([]);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [poller, setPoller] = useState<PollerState | null>(null);
  const [runInfo, setRunInfo] = useState<RunInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [progressError, setProgressError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [freshOpen, setFreshOpen] = useState(false);
  const [freshError, setFreshError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.getMeta(), api.getSteps()])
      .then(([m, s]) => {
        setMeta(m);
        setSteps(s);
        setUseCase(m.default_use_case);
        setProfile(m.default_profile);
      })
      .catch((e) => setError(`Can't reach the backend (is \`make api\` running?): ${message(e)}`));
  }, []);

  // what `workflow start` would create for the ticked steps (no Jira calls), debounced
  useEffect(() => {
    if (targets.length === 0) {
      setPreview(null);
      return;
    }
    let stale = false;
    const timer = setTimeout(() => {
      api
        .getPreview(targets)
        .then((p) => !stale && setPreview(p))
        .catch((e) => !stale && setError(message(e)));
    }, 200);
    return () => {
      stale = true;
      clearTimeout(timer);
    };
  }, [targets]);

  const load = useCallback(async () => {
    if (!useCase) return;
    const [p, s, r] = await Promise.allSettled([
      api.getProgress(useCase, profile), api.getPoller(), api.getRunInfo(useCase, profile)]);
    if (p.status === "fulfilled") {
      setProgress(p.value);
      setProgressError(null);
    } else {
      setProgressError(message(p.reason));
    }
    if (s.status === "fulfilled") setPoller(s.value);
    if (r.status === "fulfilled") setRunInfo(r.value);
  }, [useCase, profile]);

  useEffect(() => {
    load();
    const timer = setInterval(load, PROGRESS_REFRESH_MS);
    return () => clearInterval(timer);
  }, [load]);

  const act = async (fn: () => Promise<PollerState>) => {
    setBusy(true);
    setError(null);
    try {
      setPoller(await fn());
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };

  const startPolling = () => {
    const billed = profile === "local" && meta?.external_llm_provider;
    if (billed && !meta.auto_confirm_external_llm && !window.confirm(`Allow billed calls to ${meta.external_llm_provider} (your own account) while polling?`)) {
      return;
    }
    act(() => api.startPoller(useCase, profile, Boolean(billed)));
  };

  const trigger = async (confirmExternalLlm: boolean) => {
    setBusy(true);
    setDialogError(null);
    try {
      const run = await api.startRun({ use_case: useCase, targets, profile, confirm_external_llm: confirmExternalLlm });
      setDialogOpen(false);
      setNotice(`Created ${Object.keys(run.steps).length} step tickets for ${run.use_case}.`);
      setPoller(run.poller);
      await load();
    } catch (e) {
      setDialogError(message(e));
    } finally {
      setBusy(false);
    }
  };

  const startFresh = async (deletePages: boolean) => {
    setBusy(true);
    setFreshError(null);
    try {
      const result = await api.resetRun(useCase, profile, deletePages);
      setFreshOpen(false);
      setPoller(result.poller);
      setProgress(null);
      const failed = Object.keys(result.page_errors);
      setNotice(
        "Started fresh: the saved run was cleared and polling stopped. " +
          (meta?.jira_mode === "mock"
            ? `${result.deleted.length} mock ticket(s) were deleted. `
            : "Jira tickets were left as they are. ") +
          (deletePages ? "The Confluence pages the workflow created were deleted." : "Confluence pages were left as they are."),
      );
      if (failed.length) setError(`Couldn't delete ${failed.length} Confluence page(s): ${failed.map((id) => `${id} (${result.page_errors[id]})`).join("; ")}`);
      await load();
    } catch (e) {
      setFreshError(message(e));
    } finally {
      setBusy(false);
    }
  };

  const decide = async (row: ProgressRow, decision: "approve" | "reject") => {
    let feedback: string | undefined;
    if (decision === "approve") {
      if (!window.confirm(`Approve the plan for "${row.artifact}"?\n\nThis creates one Story per requirement (each with Engineering and Testing sub-tasks) in Jira and marks this step Done.`)) return;
    } else {
      feedback = window.prompt(`Why is the plan for "${row.artifact}" rejected?\n\nThis is added to the Jira ticket as a comment. The plan is not regenerated; fix the issues, then re-run the step.`)?.trim();
      if (!feedback) return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await api.decide(useCase, row.step_id, decision, profile, feedback);
      setNotice(`Added the ${result.label} label to ${result.key}.${result.acting_now ? " The orchestrator is acting on it now." : " It will be acted on when polling runs."}`);
      await load();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };

  const rerun = async (row: ProgressRow) => {
    if (!window.confirm(`Re-run the agent for "${row.artifact}"? This is another LLM run.`)) return;
    setBusy(true);
    setError(null);
    try {
      await api.rerun(useCase, row.step_id, profile);
      await load();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };

  const toggle = (stepId: string) =>
    setTargets((current) => (current.includes(stepId) ? current.filter((t) => t !== stepId) : [...current, stepId]));
  const changeUseCase = (next: string) => {
    setUseCase(next);
    setTargets([]);
    setProgress(null);
    setNotice(null);
  };
  const refresh = () =>
    api.refreshProgress(useCase, profile).then((p) => (setProgress(p), setProgressError(null)), (e) => setProgressError(message(e)));

  return (
    <div className="app">
      <header>
        <h1>Agentic SDLC workflow</h1>
        {meta && (
          <div className="controls">
            <label>
              Use case{" "}
              <select value={useCase} onChange={(e) => changeUseCase(e.target.value)}>
                {meta.use_cases.map((u) => (
                  <option key={u}>{u}</option>
                ))}
              </select>
            </label>
            <span className="profiles">
              {["local", "aws"].map((p) => {
                const available = meta.profiles.includes(p);
                return (
                  <label key={p} title={available ? undefined : "Available after aws-deploy creates the agent runtime"}>
                    <input type="radio" name="profile" value={p} checked={profile === p} disabled={!available}
                      onChange={() => setProfile(p)} />
                    {p}
                  </label>
                );
              })}
            </span>
          </div>
        )}
        {meta?.jira_mode === "mock" && (
          <a href={`/jira/backlog${profile === meta.default_profile ? "" : `?profile=${profile}`}`} target="_blank"
            rel="noreferrer">Open Mock Jira ↗</a>
        )}
        <PollerStatus poller={poller} busy={busy} onStart={startPolling}
          onStop={() => act(api.stopPoller)} onSyncNow={() => act(api.syncNow)} />
      </header>

      {error && <div className="banner error">{error}</div>}
      {notice && <div className="banner ok">{notice}</div>}

      <main>
        <section className="panel">
          <h2>Artifacts</h2>
          <p className="muted">Tick what you want generated. Its direct inputs are added as “provide”.</p>
          <StepPicker steps={steps} targets={targets} onToggle={toggle}
            provideIds={new Set(preview?.provide.map((s) => s.id))} />
        </section>
        <PreviewPanel preview={preview} hasTargets={targets.length > 0}
          onTrigger={() => { setDialogError(null); setDialogOpen(true); }} />
      </main>

      {progressError && <div className="banner error">Progress unavailable: {progressError}</div>}
      <RunBanner info={runInfo} busy={busy} onStartFresh={() => { setFreshError(null); setFreshOpen(true); }} />
      <ProgressTable progress={progress} busy={busy} onRefresh={refresh} onDecide={decide} onRerun={rerun} />

      {freshOpen && meta && runInfo?.exists && (
        <StartFreshDialog info={runInfo} jiraMode={meta.jira_mode} busy={busy} error={freshError}
          onCancel={() => setFreshOpen(false)} onConfirm={startFresh} />
      )}
      {dialogOpen && meta && preview && (
        <TriggerDialog meta={meta} profile={profile} useCase={useCase} preview={preview} busy={busy}
          error={dialogError} onCancel={() => setDialogOpen(false)} onConfirm={trigger} />
      )}
    </div>
  );
}
