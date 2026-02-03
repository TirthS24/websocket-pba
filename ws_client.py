"""
CLI client for this repo's Django Channels + LangGraph chatbot.

Supports:
- WebSocket streaming chat:      /ws/chat/
- HTTP thread summary:           POST /api/thread/summarize
- HTTP thread message history:   POST /api/thread/history

WebSocket protocol (`ChatConsumer`):
- Connect: /ws/chat/ (optionally pass ?authorization=<AUTH_API_KEY>)
- Client sends:
  {
    "type": "chat",
    "message": "...",
    "channel": "web" | "sms",
    "thread_id": "<optional; server generates if omitted>",
    "data": [... optional ...],
    "context": {... optional ...},
    "task": "... optional ..."
  }
- Server sends:
  - {"type":"connected", ...}
  - {"type":"session_started","session_id":...,"thread_id":...}
  - {"type":"token","content":"..."}   (streaming chunks)
  - {"type":"escalation","should_escalate":true|false}
  - {"type":"end"}
  - {"type":"error","message":"..."}

HTTP endpoints require:
- Authorization header (if AUTH_API_KEY is configured)
- CSRF: call GET /api/csrf-token/ first, then include X-CSRFToken + cookies on POSTs.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import sys
import urllib.parse
from dataclasses import dataclass
from typing import Any, Dict, Optional


DEFAULT_WEB_DATA = [
    {
        "external_id": "practice-001",
        "name": "Demo Medical Practice",
        "platform": "PatriotPay",
        "email_address": "contact@demopractice.com",
        "phone_number": "555-0100",
        "patients": [
            {
                "external_id": "patient-001",
                "first_name": "John",
                "last_name": "Doe",
                "gender": "M",
                "phone_number": "555-0101",
                "email_address": "john.doe@email.com",
                "dob": "1985-03-15",
                "claims": [],
                "patient_payments": [],
            }
        ],
    }
]


def _rstrip_slash(s: str) -> str:
    return s[:-1] if s.endswith("/") else s


def _ws_chat_url(ws_base: str, api_key: Optional[str]) -> str:
    base = _rstrip_slash(ws_base)
    url = f"{base}/ws/chat/"
    if api_key:
        # WebSocketAuthMiddleware supports query-string auth
        url += f"?authorization={urllib.parse.quote(api_key)}"
    return url


def _http_url(http_base: str, path: str) -> str:
    return f"{_rstrip_slash(http_base)}{path}"


def _json_arg(s: Optional[str], *, name: str) -> Any:
    if s is None:
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON for {name}: {e}") from e


async def _stdin_lines() -> str:
    return await asyncio.to_thread(sys.stdin.readline)


@dataclass
class CsrfState:
    token: str


class HttpClient:
    def __init__(self, http_base: str, api_key: Optional[str]):
        self.http_base = http_base
        self.api_key = api_key
        self._csrf: Optional[CsrfState] = None
        self._session = None

    async def __aenter__(self) -> "HttpClient":
        try:
            import aiohttp  # type: ignore
        except Exception:
            print("Missing dependency: aiohttp. Install with: uv pip install aiohttp", file=sys.stderr)
            raise

        self._session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar())
        await self._ensure_csrf()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    def _headers(self) -> Dict[str, str]:
        h: Dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = self.api_key
        if self._csrf:
            h["X-CSRFToken"] = self._csrf.token
        return h

    async def _ensure_csrf(self) -> None:
        if self._csrf is not None:
            return
        assert self._session is not None
        url = _http_url(self.http_base, "/api/csrf-token/")
        headers = {"Authorization": self.api_key} if self.api_key else None
        async with self._session.get(url, headers=headers) as resp:
            data = await resp.json()
            token = data.get("csrf_token")
            if not token or not isinstance(token, str):
                raise RuntimeError(f"CSRF token endpoint returned unexpected payload: {data}")
            self._csrf = CsrfState(token=token)

    async def post_json(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        assert self._session is not None
        await self._ensure_csrf()
        url = _http_url(self.http_base, path)
        async with self._session.post(url, headers=self._headers(), data=json.dumps(payload)) as resp:
            text = await resp.text()
            try:
                data = json.loads(text) if text else {}
            except json.JSONDecodeError:
                raise RuntimeError(f"Non-JSON response from {path}: {resp.status} {text}")
            if resp.status >= 400:
                raise RuntimeError(f"HTTP {resp.status}: {data}")
            return data


async def ws_chat_stream(
    *,
    ws_base: str,
    api_key: Optional[str],
    origin: Optional[str],
    message: str,
    thread_id: Optional[str],
    channel: str,
    data: Optional[Any],
    context: Optional[Any],
    task: Optional[str],
    interactive: bool,
) -> int:
    try:
        import websockets  # type: ignore
    except Exception:
        print("Missing dependency: websockets. Install with: uv pip install websockets", file=sys.stderr)
        return 2

    ws_url = _ws_chat_url(ws_base, api_key if api_key else None)

    # Prefer Authorization header when possible (query-string auth is also supported).
    extra_headers = []
    if origin:
        extra_headers.append(("Origin", origin))
    if api_key:
        extra_headers.append(("Authorization", api_key))

    async def _connect():
        kwargs: Dict[str, Any] = {}
        if extra_headers:
            sig = inspect.signature(websockets.connect)
            if "additional_headers" in sig.parameters:
                kwargs["additional_headers"] = extra_headers
            elif "extra_headers" in sig.parameters:
                kwargs["extra_headers"] = extra_headers
        return await websockets.connect(ws_url, **kwargs)

    async with (await _connect()) as ws:
        current_thread_id = thread_id

        # Drain the initial "connected" frame (optional)
        try:
            await asyncio.wait_for(ws.recv(), timeout=2)
        except Exception:
            pass

        async def _send_chat(m: str) -> None:
            nonlocal current_thread_id
            payload: Dict[str, Any] = {
                "type": "chat",
                "message": m,
                "channel": channel,
            }
            if current_thread_id:
                payload["thread_id"] = current_thread_id
            if data is not None:
                payload["data"] = data
            elif channel == "web":
                payload["data"] = DEFAULT_WEB_DATA
            if context is not None:
                payload["context"] = context
            if task is not None:
                payload["task"] = task

            await ws.send(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))

            while True:
                raw = await ws.recv()
                msg = json.loads(raw)
                # Server may send either `{type: ...}` (WebSocket protocol) or `{event: ...}` (SSE-style).
                t = msg.get("type") or msg.get("event")
                if t == "session_started":
                    current_thread_id = msg.get("thread_id") or current_thread_id
                    sys.stderr.write(f"\n[thread_id={current_thread_id}]\n")
                    sys.stderr.flush()
                elif t == "token":
                    chunk = msg.get("content", "")
                    if isinstance(chunk, str) and chunk:
                        sys.stdout.write(chunk)
                        sys.stdout.flush()
                elif t == "escalation":
                    sys.stderr.write(f"\n[escalation should_escalate={msg.get('should_escalate')}]\n")
                    sys.stderr.flush()
                elif t == "error":
                    sys.stderr.write(f"\n[error {msg.get('message')}]\n")
                    sys.stderr.flush()
                    return
                elif t == "end":
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    return
                # ignore unknown frames

        await _send_chat(message)

        if not interactive:
            return 0

        sys.stderr.write("Interactive mode. Type a line and press Enter to send. Ctrl+C to quit.\n")
        sys.stderr.flush()
        while True:
            line = (await _stdin_lines()).rstrip("\n")
            if not line:
                continue
            await _send_chat(line)


async def main() -> int:
    parser = argparse.ArgumentParser(description="CLI client for Django Channels + LangGraph chatbot")
    parser.add_argument("--http", default="http://localhost:8000", help="HTTP base, e.g. http://localhost:8000")
    parser.add_argument("--ws", default="ws://localhost:8000", help="WS base, e.g. ws://localhost:8000")
    parser.add_argument("--api-key", help="Authorization header value (must match AUTH_API_KEY)")
    parser.add_argument("--origin", help="Optional Origin header for WebSocket handshake")

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_stream = sub.add_parser("stream", help="Stream chat over WebSocket")
    p_stream.add_argument("--message", required=True, help="User message")
    p_stream.add_argument("--thread-id", help="Optional thread_id (resume); if omitted server creates one")
    p_stream.add_argument("--channel", default="web", choices=["web", "sms"], help="Channel")
    p_stream.add_argument("--data-json", help="Optional JSON for 'data' field (defaults to demo web payload)")
    p_stream.add_argument("--context-json", help="Optional JSON for 'context' field")
    p_stream.add_argument("--task", help="Optional task")
    p_stream.add_argument("--interactive", action="store_true", help="Stay connected; send multiple messages")

    p_sum = sub.add_parser("summary", help="Summarize a thread (HTTP)")
    p_sum.add_argument("--thread-id", required=True)

    p_hist = sub.add_parser("history", help="Get thread history (HTTP)")
    p_hist.add_argument("--thread-id", required=True)

    args = parser.parse_args()

    if args.cmd == "stream":
        return await ws_chat_stream(
            ws_base=args.ws,
            api_key=args.api_key,
            origin=args.origin,
            message=args.message,
            thread_id=args.thread_id,
            channel=args.channel,
            data=_json_arg(args.data_json, name="--data-json"),
            context=_json_arg(args.context_json, name="--context-json"),
            task=args.task,
            interactive=bool(args.interactive),
        )

    async with HttpClient(args.http, args.api_key) as http:
        if args.cmd == "summary":
            data = await http.post_json("/api/thread/summarize", {"thread_id": args.thread_id})
            print(json.dumps(data, indent=2, ensure_ascii=False))
            return 0
        if args.cmd == "history":
            data = await http.post_json("/api/thread/history", {"thread_id": args.thread_id})
            print(json.dumps(data, indent=2, ensure_ascii=False))
            return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

