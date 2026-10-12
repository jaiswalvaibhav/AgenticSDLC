import { useState } from "react";
import { InlineText } from "./InlineText";
import { Section } from "./Section";
import type { ChecklistItem } from "./types";

interface Props {
  title: string;
  items: ChecklistItem[];
  onChange: (items: ChecklistItem[]) => void; // always the whole list: the items, their order and their ticks
}

// A list of things to tick off (acceptance criteria, definition of done, test coverage): tick an item,
// click its text to edit it, ✕ to remove it. Pasting several lines adds one item per line.
export function Checklist({ title, items, onChange }: Props) {
  const [draft, setDraft] = useState("");
  const done = items.filter((i) => i.done).length;
  const add = (lines: string[]) => {
    const added = lines.map((l) => l.replace(/^\s*(?:[-*•]|\d+[.)])\s*/, "").trim()).filter(Boolean);
    if (added.length) onChange([...items, ...added.map((text) => ({ text, done: false }))]);
    setDraft("");
  };
  const replace = (index: number, item: ChecklistItem | null) =>
    onChange(item ? items.map((x, i) => (i === index ? item : x)) : items.filter((_, i) => i !== index));

  return (
    <Section title={title} badge={items.length > 0 && (
      <span className="mj-checklist-progress">
        <span className="mj-bar" aria-hidden><span style={{ width: `${(100 * done) / items.length}%` }} /></span>
        {done}/{items.length}
      </span>
    )}>
      <div className="mj-checklist">
      <ul>
        {items.map((item, index) => (
          <li key={index} className={item.done ? "done" : undefined}>
            <input type="checkbox" checked={item.done} aria-label={`Mark "${item.text}" ${item.done ? "not done" : "done"}`}
              onChange={() => replace(index, { ...item, done: !item.done })} />
            <InlineText value={item.text} onSave={(v) => replace(index, v.trim() ? { ...item, text: v.trim() } : null)} />
            <span className="mj-spacer" />
            <button className="link-button" title="Remove" onClick={() => replace(index, null)}>✕</button>
          </li>
        ))}
      </ul>
      <input className="mj-checklist-add" placeholder="Add an item, press Enter" value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add([draft]); } }}
        onPaste={(e) => {
          const text = e.clipboardData.getData("text");
          if (text.includes("\n")) { e.preventDefault(); add(text.split("\n")); }
        }} />
      </div>
    </Section>
  );
}
