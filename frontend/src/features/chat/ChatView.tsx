import { ChatInput } from "./components/ChatInput";
import { ChatMessageList } from "./components/ChatMessageList";
import { ChatStatusChip } from "./components/ChatStatusChip";
import { useChatConversation, useChatStatus } from "./useChat";

export function ChatView() {
  const { data: status, isLoading: statusLoading } = useChatStatus();
  const { messages, activity, error, streaming, send, clear } = useChatConversation();

  return (
    <div className="panel chat-panel">
      <div className="page-header">
        <h1 className="page-title">Chat</h1>
        <ChatStatusChip status={status} loading={statusLoading} />
        <button type="button" className="btn chat-new" onClick={clear} disabled={streaming}>
          New Conversation
        </button>
      </div>

      {error && <div className="hint err">{error}</div>}
      {activity && <div className="hint chat-activity">{activity}</div>}

      <ChatMessageList messages={messages} />

      <ChatInput onSend={send} streaming={streaming} />
    </div>
  );
}
