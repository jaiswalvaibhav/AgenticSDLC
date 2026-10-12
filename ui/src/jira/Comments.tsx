import { useRef, useState, type ClipboardEvent, type DragEvent } from "react";
import * as api from "./api";
import { message } from "./parts";

// An image in a comment is stored as an attachment of the ticket and referenced in the text:
// ![name](attachment:<id>). Other tools reading the comment just see that line.
const IMAGE_TOKEN = /!\[([^\]]*)\]\(attachment:([0-9a-f]+)\)/g;

export function CommentBody({ issueKey, body }: { issueKey: string; body: string }) {
  const parts: React.ReactNode[] = [];
  let last = 0;
  for (const m of body.matchAll(IMAGE_TOKEN)) {
    if (m.index > last) parts.push(body.slice(last, m.index).replace(/^\n+|\n+$/g, ""));
    const src = api.attachmentUrl(issueKey, m[2]);
    parts.push(
      <a key={m.index} href={src} target="_blank" rel="noreferrer" title={m[1]}>
        <img className="mj-comment-img" src={src} alt={m[1]} loading="lazy" />
      </a>,
    );
    last = m.index + m[0].length;
  }
  if (last < body.length) parts.push(body.slice(last).replace(/^\n+/, ""));
  return <div className="mj-comment-body">{parts}</div>;
}

interface Props {
  issueKey: string;
  initial?: string;
  submitLabel: string;
  placeholder?: string;
  clearOnSubmit?: boolean;
  onSubmit: (body: string) => Promise<unknown>; // must throw on failure, so the text is kept
  onCancel?: () => void;
}

// A comment editor: type, or paste / drop / pick images (uploaded straight away, then inserted at the cursor).
export function CommentBox({ issueKey, initial = "", submitLabel, placeholder, clearOnSubmit, onSubmit, onCancel }: Props) {
  const [text, setText] = useState(initial);
  const [uploading, setUploading] = useState(0);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const area = useRef<HTMLTextAreaElement>(null);

  const insert = (token: string) =>
    setText((t) => {
      const at = area.current?.selectionStart ?? t.length;
      return `${t.slice(0, at)}${at > 0 && t[at - 1] !== "\n" ? "\n" : ""}${token}\n${t.slice(at)}`;
    });

  const addImages = async (files: File[]) => {
    const images = files.filter((f) => f.type.startsWith("image/"));
    if (images.length < files.length) setError("Only images can go inside a comment; use Attachments for other files.");
    else setError(null);
    for (const file of images) {
      setUploading((n) => n + 1);
      try {
        const att = await api.uploadAttachment(issueKey, file);
        insert(`![${file.name.replace(/[[\]]/g, "")}](attachment:${att.id})`);
      } catch (e) {
        setError(message(e));
      } finally {
        setUploading((n) => n - 1);
      }
    }
  };

  const paste = (e: ClipboardEvent) => {
    const files = Array.from(e.clipboardData.files);
    if (files.length) { e.preventDefault(); addImages(files); } // plain text pastes as usual
  };
  const drop = (e: DragEvent) => {
    if (e.dataTransfer.files.length) { e.preventDefault(); addImages(Array.from(e.dataTransfer.files)); }
  };

  const save = async () => {
    setSaving(true);
    try {
      await onSubmit(text.trim());
      setError(null);
      if (clearOnSubmit) setText("");
    } catch (e) {
      setError(message(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mj-commentbox">
      <textarea ref={area} rows={4} value={text} placeholder={placeholder} onChange={(e) => setText(e.target.value)}
        onPaste={paste} onDrop={drop} onDragOver={(e) => e.dataTransfer.types.includes("Files") && e.preventDefault()} />
      <div className="mj-commentbox-bar">
        <button className="primary" disabled={saving || uploading > 0 || !text.trim()} onClick={save}>{saving ? "Saving…" : submitLabel}</button>
        {onCancel && <button onClick={onCancel} disabled={saving}>Cancel</button>}
        <label className="mj-attach">
          📎 Add image
          <input type="file" accept="image/*" multiple hidden onChange={(e) => { const f = Array.from(e.target.files ?? []); e.target.value = ""; addImages(f); }} />
        </label>
        {uploading > 0 && <span className="muted small">Uploading {uploading} image{uploading === 1 ? "" : "s"}…</span>}
      </div>
      {error && <p className="error small">{error}</p>}
    </div>
  );
}
