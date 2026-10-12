import { useEffect, useRef, useState } from "react";
import { TypeIcon } from "./parts";
import type { BoardView, IssueSummary } from "./types";

interface Props {
  board: BoardView;
  candidates: IssueSummary[]; // what can be chosen; the caller decides (parent rules, already linked, ...)
  value: string; // chosen key, "" = nothing
  onChange: (key: string) => void;
  placeholder: string; // shown while nothing is chosen
  noneLabel: string | null; // text of the "clear" option; null = the choice can't be cleared
  emptyText: string; // shown when there is nothing to choose from
}

// A dropdown for choosing an issue: each option shows the type symbol, key, summary and type name (a
// native <select> can't draw the symbol), and a search box narrows long lists.
export function IssuePicker({ board, candidates, value, onChange, placeholder, noneLabel, emptyText }: Props) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => !box.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);

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
          <span className="muted">{placeholder}</span>
        )}
        <span className="mj-spacer" /> ▾
      </button>
      {open && (
        <div className="mj-picker-pop" role="listbox">
          <input autoFocus placeholder={`Search ${candidates.length} ${candidates.length === 1 ? "issue" : "issues"}`} value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); setOpen(false); } }} />
          {noneLabel !== null && (
            <div className="mj-picker-option" role="option" aria-selected={!value} onClick={() => pick("")}>
              <span className="muted">{noneLabel}</span>
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
            <p className="muted small mj-picker-empty">{candidates.length === 0 ? emptyText : "No match."}</p>
          )}
        </div>
      )}
    </div>
  );
}
