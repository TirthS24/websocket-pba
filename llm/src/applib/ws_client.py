"""
WebSocket client that connects the LLM service to ws_server.

- Connects to ws_server at /ws/session/<thread_id>/ with user_type "ai" so session_message shows AI responses as user_type "ai".
- Listens for session_message with data.type == "chat" or "chat_message", runs the graph,
  broadcasts token / escalation / end / error events.
- On should_escalate true, disconnects so the user can talk to another user (human agent).

Use start_connection(thread_id) from the /session/connect endpoint; one connection per thread.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict

from applib.config import config

logger = logging.getLogger(__name__)

# One background task per thread_id; avoid duplicate connections
_active_tasks: Dict[str, asyncio.Task[None]] = {}
_lock = asyncio.Lock()


def _ws_url(thread_id: str) -> str:
    base = (config.WS_SERVER_URL or "").rstrip("/")
    if not base:
        raise ValueError("WS_SERVER_URL is not set")
    return f"{base}/ws/session/{thread_id}/"


async def _run_connection(thread_id: str) -> None:
    """Single connection loop: connect, hello, then receive and handle session_message."""
    import websockets

    # Lazy imports to avoid circular dependency with applib.api.routes
    from applib.api.routes import generate_stream_events
    from applib.models.api import ChatRequest
    from applib.models.invoice import Invoice
    from applib.types import Channel

    url = _ws_url(thread_id)
    extra_headers: list[tuple[str, str]] = []
    if config.WS_SERVER_ORIGIN:
        extra_headers.append(("Origin", config.WS_SERVER_ORIGIN))

    connect_kwargs: Dict[str, Any] = {}
    try:
        sig = __import__("inspect").signature(websockets.connect)
        if "additional_headers" in sig.parameters:
            connect_kwargs["additional_headers"] = extra_headers
        elif "extra_headers" in sig.parameters:
            connect_kwargs["extra_headers"] = extra_headers
    except Exception:
        pass

    try:
        async with websockets.connect(url, **connect_kwargs) as ws:
            # Initial server message (connected)
            try:
                await asyncio.wait_for(ws.recv(), timeout=5)
            except Exception:
                pass

            hello = {"type": "hello", "user_type": "ai"}
            await ws.send(json.dumps(hello, separators=(",", ":"), ensure_ascii=False))

            try:
                ack = await asyncio.wait_for(ws.recv(), timeout=5)
            except Exception:
                ack = None
            if ack:
                try:
                    parsed = json.loads(ack)
                    if parsed.get("type") == "hello_ack":
                        logger.info("LLM WS connected for thread_id=%s", thread_id)
                except json.JSONDecodeError:
                    pass

            while True:
                raw = await ws.recv()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                if msg.get("type") != "session_message":
                    continue

                data = msg.get("data") or {}
                msg_type = data.get("type")
                # Support FE payload: type "chat" (message, thread_id, channel, invoice, stripe_payment_link, web_app_link)
                # or legacy type "chat_message".
                if msg_type not in ("chat", "chat_message"):
                    continue

                message = data.get("message") or ""
                if not message:
                    continue

                req_thread_id = (data.get("thread_id") or thread_id) or thread_id
                channel_str = (data.get("channel") or "web").lower()
                try:
                    channel = Channel(channel_str)
                except ValueError:
                    channel = Channel.WEB

                invoice = None
                if data.get("invoice"):
                    try:
                        invoice = Invoice.model_validate(data["invoice"])
                        logger.info("Invoice parsed for thread_id=%s (practice=%s, claims=%d)", req_thread_id, getattr(invoice.practice, "name", None), len(invoice.claims))
                    except Exception as inv_err:
                        logger.warning("Invalid invoice in chat payload: %s", inv_err)

                stripe_link = data.get("stripe_payment_link") or (data.get("invoice") or {}).get("stripe_payment_link")
                webapp_link = data.get("web_app_link") or (data.get("invoice") or {}).get("web_app_link")

                try:
                    chat_request = ChatRequest(
                        thread_id=req_thread_id,
                        message=message,
                        channel=channel,
                        invoice=invoice,
                        stripe_link=stripe_link or "",
                        webapp_link=webapp_link or "",
                    )
                except Exception as req_err:
                    logger.warning("ChatRequest validation failed: %s", req_err)
                    await ws.send(
                        json.dumps(
                            {
                                "type": "broadcast",
                                "data": {"type": "error", "content": str(req_err)},
                            },
                            separators=(",", ":"),
                            ensure_ascii=False,
                        )
                    )
                    await ws.send(
                        json.dumps(
                            {"type": "broadcast", "data": {"type": "end", "content": ""}},
                            separators=(",", ":"),
                            ensure_ascii=False,
                        )
                    )
                    continue

                should_disconnect = False
                try:
                    async for event_dict in generate_stream_events(chat_request):
                        await ws.send(
                            json.dumps(
                                {"type": "broadcast", "data": event_dict},
                                separators=(",", ":"),
                                ensure_ascii=False,
                            )
                        )
                        if event_dict.get("type") == "escalation" and event_dict.get("should_escalate"):
                            should_disconnect = True
                except Exception as e:
                    logger.exception("Graph stream error for thread_id=%s", thread_id)
                    await ws.send(
                        json.dumps(
                            {
                                "type": "broadcast",
                                "data": {"type": "error", "content": str(e)},
                            },
                            separators=(",", ":"),
                            ensure_ascii=False,
                        )
                    )
                    await ws.send(
                        json.dumps(
                            {"type": "broadcast", "data": {"type": "end", "content": ""}},
                            separators=(",", ":"),
                            ensure_ascii=False,
                        )
                    )

                if should_disconnect:
                    logger.info(
                        "Escalation detected for thread_id=%s; disconnecting LLM from session so user can talk to human agent",
                        thread_id,
                    )
                    break

    except asyncio.CancelledError:
        logger.info("LLM WS task cancelled for thread_id=%s", thread_id)
        raise
    except Exception as e:
        logger.exception("LLM WS connection error for thread_id=%s: %s", thread_id, e)
    finally:
        async with _lock:
            _active_tasks.pop(thread_id, None)


async def start_connection(thread_id: str) -> bool:
    """
    Start a WebSocket connection for the given thread_id.
    Idempotent: if already connected for this thread_id, returns True without starting another.
    Returns True if a connection was started or already active.
    """
    if not (thread_id and thread_id.strip()):
        return False
    thread_id = thread_id.strip()
    if not config.WS_SERVER_URL:
        logger.warning("WS_SERVER_URL not set; cannot start LLM WS connection")
        return False

    async with _lock:
        if thread_id in _active_tasks:
            task = _active_tasks[thread_id]
            if not task.done():
                return True
            _active_tasks.pop(thread_id, None)

        task = asyncio.create_task(_run_connection(thread_id))
        _active_tasks[thread_id] = task

    return True


def is_connected(thread_id: str) -> bool:
    """Return True if there is an active connection task for this thread_id."""
    task = _active_tasks.get(thread_id)
    return task is not None and not task.done()
