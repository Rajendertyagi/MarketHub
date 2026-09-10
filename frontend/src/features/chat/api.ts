// Thin API layer for Chat. Status uses the shared `request()` helper; the
// message endpoint is a streaming SSE response (not JSON), so it is read via a
// fetch stream and surfaced through the `onEvent` callback — no direct JSON
// parsing of the stream, no client-side response synthesis.
import { request } from "@/api/client";
import { chatStatusSchema } from "./schemas";
import { CHAT_HISTORY_LIMIT } from "./constants";
import type { ChatEvent, ChatMessage, ChatStatus } from "./types";

export async function getChatStatus(signal?: AbortSignal): Promise<ChatStatus> {
  return request("/chat/status", { schema: chatStatusSchema, signal });
}

export interface SendChatOptions {
  message: string;
  history: ChatMessage[];
  signal?: AbortSignal;
  onEvent: (event: ChatEvent) => void;
}

export async function sendChatMessage(opts: SendChatOptions): Promise<void> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message: opts.message,
      history: opts.history.slice(-CHAT_HISTORY_LIMIT),
    }),
    signal: opts.signal,
  });
  if (!res.ok) {
    let message = `HTTP ${res.status}`;
    try {
      const data = await res.json();
      if (data && typeof data.error === "string") message = data.error;
    } catch {
      /* keep status text */
    }
    throw new Error(message);
  }

  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const chunk = await reader.read();
    if (chunk.done) break;
    buffer += decoder.decode(chunk.value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const raw = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      if (!raw.startsWith("data:")) continue;
      const payload = raw.slice(5).trim();
      if (!payload) continue;
      try {
        const event = JSON.parse(payload) as ChatEvent;
        opts.onEvent(event);
      } catch {
        /* skip malformed frame */
      }
    }
  }
}
