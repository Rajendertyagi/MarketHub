"""Chat API: SSE-streamed AI conversation over MarketHub tools.

POST /api/chat            — one user message; response is an SSE stream of
                            agent events (tool_start/tool_result/delta/
                            error/done). Dedicated stream — never mixed
                            with quote/alert SSE channels.
GET  /api/chat/status     — whether an AI provider is configured.
POST /api/chat/config     — save provider settings (endpoint/model in
                            config.json; API key encrypted in the store).

Conversation history is kept client-side (smallest useful model).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

logger = logging.getLogger(__name__)


def build_chat_routes(config_path: str, credential_store: Any,
                      tool_registry) -> list[Route]:
    from app.chat_agent import ChatAgent, FakeChatAgent

    def _ai_config() -> dict[str, Any]:
        """Read endpoint/model from config.json; key from encrypted store."""
        import os
        cfg: dict[str, Any] = {}
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
        ai = cfg.get("ai") or {}
        key = None
        try:
            creds = credential_store.load_app_credentials("ai")
            key = (creds or {}).get("api_secret") or None
        except Exception:
            key = None
        return {
            "endpoint": (ai.get("endpoint")
                         or "https://api.openai.com/v1").rstrip("/"),
            "model": ai.get("model") or "gpt-4o-mini",
            "configured": bool(key),
        }

    async def _status(request: Request) -> Response:
        cfg = await asyncio.to_thread(_ai_config)
        return _json({"configured": cfg["configured"],
                      "endpoint": cfg["endpoint"],
                      "model": cfg["model"]})

    async def _save_config(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "invalid JSON body"}, 400)
        endpoint = (body.get("endpoint") or "").strip()
        model = (body.get("model") or "").strip()
        api_key = (body.get("api_key") or "").strip()
        if endpoint and not endpoint.startswith(("http://", "https://")):
            return _json({"error": "endpoint must be an http(s) URL"}, 400)
        if api_key and len(api_key) > 512:
            return _json({"error": "api_key too long"}, 400)

        def _write() -> None:
            cfg: dict[str, Any] = {}
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                cfg = {}
            ai = cfg.get("ai") or {}
            if endpoint:
                ai["endpoint"] = endpoint
            if model:
                ai["model"] = model
            cfg["ai"] = ai
            import os
            import tempfile
            fd, tmp = tempfile.mkstemp(
                dir=os.path.dirname(os.path.abspath(config_path)),
                suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
                f.write("\n")
            os.replace(tmp, config_path)

        try:
            await asyncio.to_thread(_write)
        except Exception as exc:
            return _json({"error":
                          f"config write failed: {type(exc).__name__}"}, 500)
        if api_key:
            try:
                await asyncio.to_thread(
                    credential_store.save_app_credentials,
                    "ai", "chat-key", api_key)
            except Exception as exc:
                return _json({"error":
                              f"key storage failed: {type(exc).__name__}"},
                             500)
        return _json({"status": "saved"})

    async def _message(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "invalid JSON body"}, 400)
        message = (body.get("message") or "").strip()
        history = body.get("history") or []
        if not message:
            return _json({"error": "message is required"}, 400)
        if len(message) > 4000:
            return _json({"error": "message too long (max 4000)"}, 400)
        if not isinstance(history, list):
            return _json({"error": "history must be a list"}, 400)

        cfg = await asyncio.to_thread(_ai_config)
        if not cfg["configured"]:
            return _json({
                "error": "AI provider not configured. Set it up in "
                         "Settings → AI Provider.",
                "code": "not_configured",
            }, 503)

        use_fake = bool(body.get("_fake_script"))   # test seam only
        if use_fake:
            agent = FakeChatAgent(script=body["_fake_script"],
                                  tool_registry=tool_registry)
        else:
            try:
                creds = await asyncio.to_thread(
                    credential_store.load_app_credentials, "ai")
                api_key = (creds or {}).get("api_secret") or ""
            except Exception:
                api_key = ""
            agent = ChatAgent(api_endpoint=cfg["endpoint"],
                              api_key=api_key, model=cfg["model"],
                              tool_registry=tool_registry)

        convo = [{"role": m.get("role"), "content": m.get("content")}
                 for m in history[-20:]
                 if m.get("role") in ("user", "assistant")]
        convo.append({"role": "user", "content": message})

        async def _stream():
            try:
                async for event in agent.run(convo):
                    yield f"data: {json.dumps(event)}\n\n"
            except Exception as exc:
                yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
            yield "data: {\"type\": \"done\"}\n\n"

        return StreamingResponse(_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})

    return [
        Route("/api/chat/status", endpoint=_status, methods=["GET"]),
        Route("/api/chat/config", endpoint=_save_config, methods=["POST"]),
        Route("/api/chat", endpoint=_message, methods=["POST"]),
    ]


def _json(data: Any, status: int = 200):
    from starlette.responses import JSONResponse
    return JSONResponse(data, status_code=status)
