import { useState } from "react";

interface Props {
  text: string;
  maxChars?: number;
  maxLines?: number;
}

// Long Jira comments would stretch the Progress table, so show the start of them with a
// "Show more" toggle. Remounted (collapsed again) whenever the text changes.
export function Clamp({ text, maxChars = 160, maxLines = 3 }: Props) {
  return <ClampText key={text} text={text} maxChars={maxChars} maxLines={maxLines} />;
}

function ClampText({ text, maxChars, maxLines }: Required<Props>) {
  const [open, setOpen] = useState(false);
  const short = text.split("\n").slice(0, maxLines).join("\n").slice(0, maxChars);
  if (short.length >= text.length) return <>{text}</>;
  return (
    <>
      {open ? text : `${short.trimEnd()}…`}{" "}
      <button className="link-button" onClick={() => setOpen(!open)}>
        {open ? "Show less" : "Show more"}
      </button>
    </>
  );
}
