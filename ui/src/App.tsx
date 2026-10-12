import { useCallback, useEffect, useState } from "react";
import * as api from "./api";
import { PollerStatus } from "./components/PollerStatus";
import { PreviewPanel } from "./components/PreviewPanel";
import { ProgressTable } from "./components/ProgressTable";
import { StepPicker } from "./components/StepPicker";
import { TriggerDialog } from "./components/TriggerDialog";
import type { Meta, PollerState, Preview, Progress, Step } from "./types";

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
  const [error, setError] = useState<string | null>(null);
  const [progressError, setProgressError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

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
    const [p, s] = await Promise.allSettled([api.getProgress(useCase, profile), api.getPoller()]);
    if (p.status === "fulfilled") {
      setProgress(p.value);
      setProgressError(null);
    } else {
      setProgressError(message(p.reason));
    }
    if (s.status === "fulfilled") setPoller(s.value);
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
    if (billed && !window.confirm(`Allow billed calls to ${meta.external_llm_provider} (your own account) while polling?`)) {
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
      <ProgressTable progress={progress} onRefresh={refresh} />

      {dialogOpen && meta && preview && (
        <TriggerDialog meta={meta} profile={profile} useCase={useCase} preview={preview} busy={busy}
          error={dialogError} onCancel={() => setDialogOpen(false)} onConfirm={trigger} />
      )}
    </div>
  );
}
