import type { Preview } from "../types";

interface Props {
  preview: Preview | null;
  hasTargets: boolean;
  onTrigger: () => void;
}

export function PreviewPanel({ preview, hasTargets, onTrigger }: Props) {
  return (
    <section className="panel">
      <h2>Will create</h2>
      {!hasTargets && <p className="muted">Tick the artifacts you want to generate.</p>}
      {preview && (
        <>
          <h3>Generate</h3>
          <ul>
            {preview.generate.map((s) => (
              <li key={s.id}>
                {s.artifact}
                {s.automation && <span className="chip automated">automated</span>}
              </li>
            ))}
          </ul>
          {preview.provide.length > 0 && (
            <>
              <h3>Provide</h3>
              <ul>
                {preview.provide.map((s) => (
                  <li key={s.id}>{s.artifact}</li>
                ))}
              </ul>
              <p className="muted">
                Provide = attach the artifact (a Confluence page link — as a Jira Web link or pasted in a comment — or
                one .xlsx/.pdf/.docx/.csv file) to its Jira ticket and mark it Done. The workflow continues on its own from there.
              </p>
            </>
          )}
        </>
      )}
      <button className="primary" disabled={!hasTargets || !preview} onClick={onTrigger}>
        Trigger workflow…
      </button>
    </section>
  );
}
