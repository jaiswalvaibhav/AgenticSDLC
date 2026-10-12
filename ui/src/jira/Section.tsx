import { useState, type ReactNode } from "react";

interface Props {
  title: string;
  badge?: ReactNode; // a count or progress next to the title
  actions?: ReactNode; // right-aligned controls
  defaultOpen?: boolean;
  children: ReactNode;
}

// One titled card on the issue page, collapsible, so a long ticket can be folded down to what you need.
export function Section({ title, badge, actions, defaultOpen = true, children }: Props) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="mj-sec">
      <div className="mj-sec-head">
        <button type="button" className="mj-sec-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
          <span className="mj-caret">{open ? "▾" : "▸"}</span>
          <span className="mj-sec-title">{title}</span>
        </button>
        {badge}
        <span className="mj-spacer" />
        {open && actions}
      </div>
      {open && <div className="mj-sec-body">{children}</div>}
    </section>
  );
}
