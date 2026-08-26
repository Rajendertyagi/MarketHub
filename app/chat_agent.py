"""Provider-neutral Chat agent with a market-tool execution loop.

Talks to any OpenAI-compatible /chat/completions endpoint (OpenAI, Groq,
OpenRouter, LM Studio, Ollama's compat server) using function calling.
Streams provider deltas as SSE-friendly events and executes MarketHub
tools through the shared ChatToolRegistry.

Events yielded by run():
    {"type": "tool_start", "name": ..., "arguments": ...}
    {"type": "tool_result", "name": ..., "result": ...}
    {"type": "delta", "text": ...}
    {"type": "error", "message": ...}
    {"type": "done"}
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Callable

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 6

SYSTEM_PROMPT = (
    "You are MarketHub's assistant. You answer questions about Indian "
    "market data (indices, equities, futures, options) using ONLY the "
    "provided tools — never invent prices, OI, or instrument names.\n"
    "Rules:\n"
    "- Resolve every symbol through instrument_search before quoting it.\n"
    "- Report freshness: mention received_at/stale when a quote may not "
    "be live. Never call stale data live.\n"
    "- Option-chain analytics are scoped to the loaded strike window; say "
    "so when relevant.\n"
    "- You can create/manage price alerts via tools. You CANNOT place "
    "orders or trade — refuse such requests clearly.\n"
    "- Keep answers compact and concrete."
)


class ChatAgent:
    """One conversation turn runner against an OpenAI-compatible API."""

    def __init__(self, *, api_endpoint: str, api_key: str, model: str,
                 tool_registry, http_post: Callable | None = None,
                 max_rounds: int = MAX_TOOL_ROUNDS) -> None:
        self._endpoint = api_endpoint.rstrip("/")
        self._key = api_key
        self._model = model
        self._tools = tool_registry
        self._max_rounds = max_rounds
        self._http_post = http_post or self._default_http_post

    # -- transport -----------------------------------------------------------

    @staticmethod
    async def _default_http_post(url: str, headers: dict,
                                 payload: dict) -> dict[str, Any]:
        """Non-streaming completion call (tool rounds need full JSON)."""
        import httpx
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            return resp.json()

    def _payload(self, messages: list[dict], stream: bool) -> dict:
        payload = {
            "model": self._model,
            "messages": messages,
            "stream": stream,
        }
        if self._tools.definitions:
            payload["tools"] = self._tools.definitions
            payload["tool_choice"] = "auto"
        return payload

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
        }

    # -- main loop --------------------------------------------------------------

    async def run(self, messages: list[dict]) -> AsyncIterator[dict]:
        convo = list(messages)
        if convo and convo[0].get("role") != "system":
            convo = [{"role": "system", "content": SYSTEM_PROMPT}] + convo

        for _round in range(self._max_rounds):
            try:
                data = await self._http_post(
                    f"{self._endpoint}/chat/completions",
                    self._headers(),
                    self._payload(convo, stream=False))
            except Exception as exc:
                yield {"type": "error",
                       "message":
                           f"AI provider unreachable ({type(exc).__name__}). "
                           "Check Settings → AI Provider configuration."}
                yield {"type": "done"}
                return

            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            tool_calls = message.get("tool_calls") or []

            if not tool_calls:
                content = message.get("content") or ""
                if content:
                    yield {"type": "delta", "text": content}
                yield {"type": "done"}
                return

            # Execute each requested tool, feed results back, loop again.
            convo.append({"role": "assistant",
                          "content": message.get("content") or "",
                          "tool_calls": tool_calls})
            for call in tool_calls:
                name = (call.get("function") or {}).get("name", "")
                arguments = (call.get("function") or {}).get(
                    "arguments", "{}")
                yield {"type": "tool_start", "name": name,
                       "arguments": arguments}
                result = await self._tools.execute(name, arguments)
                result_json = json.dumps(result)[:6000]   # bounded context
                yield {"type": "tool_result", "name": name,
                       "result": result}
                convo.append({
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "content": result_json,
                })

        yield {"type": "error",
               "message": "Too many tool rounds; answer truncated."}
        yield {"type": "done"}


class FakeChatAgent:
    """Deterministic agent for tests/CI: scripted tool calls + replies."""

    def __init__(self, *, script: list[dict], tool_registry) -> None:
        """script items: {"tool": name, "arguments": {...}} and/or
        {"say": text}. Executed in order."""
        self._script = script
        self._tools = tool_registry

    async def run(self, messages: list[dict]) -> AsyncIterator[dict]:
        for step in self._script:
            if "tool" in step:
                yield {"type": "tool_start", "name": step["tool"],
                       "arguments": json.dumps(step.get("arguments", {}))}
                result = await self._tools.execute(
                    step["tool"], step.get("arguments", {}))
                yield {"type": "tool_result", "name": step["tool"],
                       "result": result}
            elif "say" in step:
                yield {"type": "delta", "text": step["say"]}
        yield {"type": "done"}
