import { useState } from "react";
import type { Meta, Preview } from "../types";

interface Props {
  meta: Meta;
  profile: string;
  useCase: string;
  preview: Preview;
  busy: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: (confirmExternalLlm: boolean) => void;
}

export function TriggerDialog({ meta, profile, useCase, preview, busy, error, onCancel, onConfirm }: Props) {
  const [confirmLlm, setConfirmLlm] = useState(false);
  const billed = profile === "local" && meta.external_llm_provider !== null;
  const needsLlmConfirm = billed && !meta.auto_confirm_external_llm;
  const ticketCount = preview.generate.length + preview.provide.length;
  return (
    <div className="overlay" role="dialog" aria-modal="true">
      <div className="dialog">
        <h2>Trigger workflow</h2>
        <p>
          This <strong>writes to Jira for real</strong>: it creates up to {ticketCount} step tickets (plus the epic,
          if it doesn't exist) in project <strong>{meta.jira_project_key}</strong> for use case{" "}
          <strong>{useCase}</strong>.
        </p>
        <p>
          {profile === "local"
            ? "The orchestrator then polls Jira continuously from this backend, and runs the agent on your laptop."
            : "The scheduled orchestrator Lambda picks the run up; this UI does not poll."}
        </p>
        {needsLlmConfirm && (
          <label className="warn">
            <input type="checkbox" checked={confirmLlm} onChange={(e) => setConfirmLlm(e.target.checked)} />
            Allow billed calls to <strong>{meta.external_llm_provider}</strong> (your own account, not AWS Bedrock)
            for this run
          </label>
        )}
        {billed && meta.auto_confirm_external_llm && (
          <p className="warn">
            Calls to <strong>{meta.external_llm_provider}</strong> are billed to your own account;{" "}
            <code>analyst.auto_confirm_external_llm</code> is on in config.yaml, so they run without asking.
          </p>
        )}
        {error && <p className="error">{error}</p>}
        <div className="buttons">
          <button onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button className="primary" disabled={busy || (needsLlmConfirm && !confirmLlm)} onClick={() => onConfirm(confirmLlm)}>
            {busy ? "Creating…" : "Create tickets and start"}
          </button>
        </div>
      </div>
    </div>
  );
}
