// Domain types for the Chat feature. Mirrors the backend `/api/chat` SSE
// contract. React only sends the user message + prior history and renders the
// streamed assistant events; it never evaluates or fabricates responses.
export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface ChatStatus {
  configured: boolean;
  endpoint?: string;
  model?: string;
}

export type ChatEventType = "tool_start" | "tool_result" | "delta" | "error" | "done";

export interface ChatEvent {
  type: ChatEventType;
  text?: string;
  name?: string;
  message?: string;
}
