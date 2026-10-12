import { useState, type KeyboardEvent } from "react";

// Click-to-edit text: Enter saves a one-line field, Ctrl/Cmd+Enter a multi-line one, Esc cancels.
export function InlineText({ value, onSave, multiline, placeholder, className }: {
  value: string; onSave: (v: string) => void; multiline?: boolean; placeholder?: string; className?: string;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  if (draft === null) {
    return (
      <span className={`mj-inline ${className ?? ""}`} onClick={() => setDraft(value)} title="Click to edit">
        {value || <span className="muted">{placeholder ?? "None"}</span>}
      </span>
    );
  }
  const save = () => { if (draft !== value) onSave(draft); setDraft(null); };
  const keys = (e: KeyboardEvent) => {
    if (e.key === "Escape") setDraft(null);
    if (e.key === "Enter" && (!multiline || e.metaKey || e.ctrlKey)) { e.preventDefault(); save(); }
  };
  return multiline ? (
    <span className="mj-inline-edit">
      <textarea autoFocus rows={5} value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={keys} />
      <span className="mj-inline-buttons"><button className="primary" onClick={save}>Save</button><button onClick={() => setDraft(null)}>Cancel</button></span>
    </span>
  ) : (
    <input autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={keys} onBlur={save} />
  );
}
