// Data hooks for Chat. Status is a plain react-query; the conversation is local
// state with a streaming send (the backend streams SSE events, not a JSON body).
import { useCallback, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getChatStatus, sendChatMessage } from "./api";
import { CHAT_MAX_MESSAGE } from "./constants";
import type { ChatEvent, ChatMessage } from "./types";

export function useChatStatus() {
  return useQuery({
    queryKey: ["chat", "status"],
    queryFn: ({ signal }) => getChatStatus(signal),
    refetchInterval: 30_000,
  });
}

export interface ChatConversation {
  messages: ChatMessage[];
  activity: string;
  error: string | null;
  streaming: boolean;
  send: (text: string) => void;
  clear: () => void;
}

export function useChatConversation(): ChatConversation {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [activity, setActivity] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [streaming, setStreaming] = useState(false);

  const clear = useCallback(() => {
    setMessages([]);
    setActivity("");
    setError(null);
  }, []);

  const send = useCallback(
    (text: string) => {
      const content = text.trim();
      if (!content || streaming) return;
      if (content.length > CHAT_MAX_MESSAGE) {
        setError(`Message too long (max ${CHAT_MAX_MESSAGE} characters).`);
        return;
      }
      setError(null);
      setActivity("");
      const prior = messages;
      const history: ChatMessage[] = [...prior, { role: "user", content }];
      setMessages(history);

      const patchAssistant = (content: string) =>
        setMessages((m) => {
          const copy = [...m];
          copy[copy.length - 1] = { role: "assistant", content };
          return copy;
        });

      let acc = "";
      patchAssistant("…");
      setStreaming(true);

      sendChatMessage({
        message: content,
        history: prior,
        onEvent: (e: ChatEvent) => {
          if (e.type === "tool_start") {
            setActivity(`using tool: ${e.name ?? ""}`);
          } else if (e.type === "delta") {
            acc += e.text ?? "";
            patchAssistant(acc);
            setActivity("");
          } else if (e.type === "error") {
            setError(e.message ?? "Chat error");
          } else if (e.type === "done") {
            setActivity("");
          }
        },
      })
        .then(() => patchAssistant(acc || "(no answer)"))
        .catch((e: unknown) => {
          setError(e instanceof Error ? e.message : String(e));
          patchAssistant("(failed)");
        })
        .finally(() => {
          setStreaming(false);
          setActivity("");
        });
    },
    [messages, streaming],
  );

  return { messages, activity, error, streaming, send, clear };
}
