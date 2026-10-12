import { useEffect, useRef, useState } from "react";
import { TypeIcon, eligibleParents, parentAllowed, parentRequired } from "./parts";
import type { BoardView } from "./types";

interface Props {
  board: BoardView;
  issueType: string; // the type of the ticket being created or edited; decides which parents are offered
  selfKey?: string;
  value: string; // parent key, "" = none
  onChange: (key: string) => void;
}

// A dropdown that shows each candidate's type symbol, key, summary and type name (a native <select>
// can't draw the symbol), with a search box for long lists. Only valid parents are offered.
export function ParentPicker({ board, issueType, selfKey, value, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => !box.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);

  if (!parentAllowed(issueType)) return <span className="muted">Epics have no parent.</span>;
  const candidates = eligibleParents(board, issueType, selfKey);
  const selected = board.issues.find((i) => i.key === value);
  const shown = candidates.filter((i) => !text || `${i.key} ${i.summary} ${i.issuetype}`.toLowerCase().includes(text.toLowerCase()));
  const pick = (key: string) => { onChange(key); setOpen(false); setText(""); };

  return (
    <div className="mj-picker" ref={box}>
      <button type="button" className="mj-picker-button" onClick={() => setOpen(!open)} aria-haspopup="listbox" aria-expanded={open}>
        {selected ? (
          <>
            <TypeIcon type={selected.issuetype} /> <strong>{selected.key}</strong>
            <span className="mj-picker-text">{selected.summary}</span>
          </>
        ) : (
          <span className="muted">{parentRequired(issueType) ? "Choose a parent…" : "None"}</span>
        )}
        <span className="mj-spacer" /> ▾
      </button>
      {open && (
        <div className="mj-picker-pop" role="listbox">
          <input autoFocus placeholder={`Search ${candidates.length} ${candidates.length === 1 ? "issue" : "issues"}`} value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); setOpen(false); } }} />
          {!parentRequired(issueType) && (
            <div className="mj-picker-option" role="option" aria-selected={!value} onClick={() => pick("")}>
              <span className="muted">None</span>
            </div>
          )}
          {shown.map((i) => (
            <div key={i.key} className={`mj-picker-option${i.key === value ? " selected" : ""}`} role="option"
              aria-selected={i.key === value} onClick={() => pick(i.key)}>
              <TypeIcon type={i.issuetype} />
              <strong>{i.key}</strong>
              <span className="mj-picker-text">{i.summary}</span>
              <span className="muted small">{i.issuetype}</span>
            </div>
          ))}
          {shown.length === 0 && (
            <p className="muted small mj-picker-empty">
              {candidates.length === 0
                ? `No ${parentRequired(issueType) ? "stories, tasks or bugs" : "epics"} to choose from yet.`
                : "No match."}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
