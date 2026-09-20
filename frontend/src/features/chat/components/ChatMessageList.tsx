import type { ChatMessage } from "../types";

interface Props {
  messages: ChatMessage[];
}

// Renders the conversation. User messages align right, assistant left. Content is
// backend-provided text only — never synthesized client-side.
export function ChatMessageList({ messages }: Props) {
  if (!messages.length) {
    return (
      <div className="chat-empty muted">
        Ask about NIFTY, RELIANCE, option chains, alerts — the AI uses MarketHub tools to answer.
      </div>
    );
  }
  return (
    <div className="chat-messages">
      {messages.map((m) => (
        <div key={m.id} className={`chat-msg chat-msg-${m.role}`}>
          <div className="chat-bubble">{m.content || "…"}</div>
        </div>
      ))}
    </div>
  );
}
