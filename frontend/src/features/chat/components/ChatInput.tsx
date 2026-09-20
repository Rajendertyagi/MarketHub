import { useState } from "react";

interface Props {
  onSend: (text: string) => void;
  streaming: boolean;
}

// Chat composer. Enter sends; Shift+Enter is a newline. Disabled while the
// assistant is streaming a response.
export function ChatInput({ onSend, streaming }: Props) {
  const [value, setValue] = useState("");

  const submit = () => {
    const text = value.trim();
    if (!text || streaming) return;
    onSend(text);
    setValue("");
  };

  return (
    <div className="auth-row chat-composer">
      <textarea
        className="filter-input chat-input"
        rows={2}
        placeholder="Ask about NIFTY, RELIANCE, option chains, alerts…"
        aria-label="Chat message"
        value={value}
        disabled={streaming}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
      />
      <button type="button" className="btn" onClick={submit} disabled={streaming}>
        {streaming ? "…" : "Send"}
      </button>
    </div>
  );
}
