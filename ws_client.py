"""
Minimal WebSocket client for this repo's Channels consumer.

Server protocol (from `ws_server/realtime/consumers.py`):
- Connect:  /ws/session/<session_id>/
- First client message MUST include `user_type` (optionally `client_type`)
  e.g. {"type":"hello","user_type":"admin","client_type":"python"}
- Then you can:
  - presence:   {"type":"presence"}
  - broadcast:  {"type":"broadcast","msg":"hi"} or {"type":"broadcast","data":{...}}

Usage examples:
  python ws_client.py --base ws://localhost:8000 --session test --user admin --client python --presence
  python ws_client.py --base ws://localhost:8000 --session test --user admin --client python --broadcast-msg "hello"
  python ws_client.py --url ws://localhost:8000/ws/session/test/ --user admin --broadcast-json '{"foo":1}'

Notes:
- This script uses the third-party `websockets` package:
    pip install websockets
- In production `AllowedHostsOriginValidator` may require an Origin header.
  Provide `--origin https://your-site` if you get 403/handshake failures.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import sys
from typing import Any, Dict, Optional


def _build_ws_url(*, base: Optional[str], url: Optional[str], session_id: Optional[str]) -> str:
    if url:
        return url
    if not base or not session_id:
        raise ValueError("Provide either --url OR (--base and --session).")
    base = base.rstrip("/")
    return f"{base}/ws/session/{session_id}/"


def _parse_json_arg(s: str) -> Any:
    try:
        return json.loads(s)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON for --broadcast-json: {e}") from e


async def _stdin_lines() -> str:
    return await asyncio.to_thread(sys.stdin.readline)


async def main() -> int:
    parser = argparse.ArgumentParser(description="WS client for ws_server SessionConsumer")
    parser.add_argument("--base", help="Base URL like ws://localhost:8000 (alternative to --url)")
    parser.add_argument("--url", help="Full WS URL like ws://localhost:8000/ws/session/<id>/")
    parser.add_argument("--session", help="Session id (used with --base)")

    parser.add_argument("--user", required=True, help="user_type (required by server on first message)")
    parser.add_argument("--client", default="python", help="client_type (optional)")
    parser.add_argument("--origin", help="Optional Origin header (often required in production)")

    parser.add_argument("--presence", action="store_true", help="Request presence once and exit")
    parser.add_argument("--broadcast-msg", help="Broadcast a simple string message and exit")
    parser.add_argument(
        "--broadcast-json",
        help='Broadcast structured JSON data and exit (string containing JSON, e.g. \'{"a":1}\')',
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Interactive mode: each line typed is broadcast as msg",
    )

    args = parser.parse_args()

    ws_url = _build_ws_url(base=args.base, url=args.url, session_id=args.session)

    try:
        import websockets  # type: ignore
    except Exception:
        print("Missing dependency: websockets. Install with: pip install websockets", file=sys.stderr)
        return 2

    extra_headers = []
    if args.origin:
        extra_headers.append(("Origin", args.origin))

    # `websockets.connect()` has changed header kwarg names across versions.
    # Some versions use `extra_headers`, newer versions use `additional_headers`.
    # If neither is available, we connect without headers (Origin can't be set).
    async def _connect():
        kwargs: Dict[str, Any] = {}
        if extra_headers:
            sig = inspect.signature(websockets.connect)
            if "additional_headers" in sig.parameters:
                kwargs["additional_headers"] = extra_headers
            elif "extra_headers" in sig.parameters:
                kwargs["extra_headers"] = extra_headers
            else:
                print(
                    "Warning: installed websockets doesn't support passing Origin headers; ignoring --origin.",
                    file=sys.stderr,
                )
        return await websockets.connect(ws_url, **kwargs)

    async with (await _connect()) as ws:
        # Receive initial server message (connected)
        try:
            initial = await asyncio.wait_for(ws.recv(), timeout=5)
            print(f"< {initial}")
        except Exception:
            pass

        # REQUIRED first client message: set user_type (+ optional client_type)
        hello: Dict[str, Any] = {"type": "hello", "user_type": args.user, "client_type": args.client}
        await ws.send(json.dumps(hello, separators=(",", ":"), ensure_ascii=False))
        print(f"> {json.dumps(hello, separators=(',', ':'), ensure_ascii=False)}")

        # Print hello_ack if provided
        try:
            ack = await asyncio.wait_for(ws.recv(), timeout=5)
            print(f"< {ack}")
        except Exception:
            pass

        if args.presence:
            msg = {"type": "presence"}
            await ws.send(json.dumps(msg, separators=(",", ":"), ensure_ascii=False))
            print(f"> {json.dumps(msg, separators=(',', ':'), ensure_ascii=False)}")
            resp = await ws.recv()
            print(f"< {resp}")
            return 0

        if args.broadcast_msg is not None:
            msg = {"type": "broadcast", "msg": args.broadcast_msg}
            await ws.send(json.dumps(msg, separators=(",", ":"), ensure_ascii=False))
            print(f"> {json.dumps(msg, separators=(',', ':'), ensure_ascii=False)}")
            # Wait briefly for fan-out echo
            try:
                resp = await asyncio.wait_for(ws.recv(), timeout=5)
                print(f"< {resp}")
            except Exception:
                pass
            return 0

        if args.broadcast_json is not None:
            data = _parse_json_arg(args.broadcast_json)
            msg = {"type": "broadcast", "data": data}
            await ws.send(json.dumps(msg, separators=(",", ":"), ensure_ascii=False))
            print(f"> {json.dumps(msg, separators=(',', ':'), ensure_ascii=False)}")
            try:
                resp = await asyncio.wait_for(ws.recv(), timeout=5)
                print(f"< {resp}")
            except Exception:
                pass
            return 0

        if args.interactive:
            print("Interactive mode. Type a line and press Enter to broadcast. Ctrl+C to quit.", file=sys.stderr)

            async def receiver() -> None:
                while True:
                    incoming = await ws.recv()
                    print(f"< {incoming}")

            async def sender() -> None:
                while True:
                    line = (await _stdin_lines()).rstrip("\n")
                    if not line:
                        continue
                    out = {"type": "broadcast", "msg": line}
                    await ws.send(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
                    print(f"> {json.dumps(out, separators=(',', ':'), ensure_ascii=False)}")

            await asyncio.gather(receiver(), sender())
            return 0

        # Default: keep reading until server closes.
        while True:
            incoming = await ws.recv()
            print(f"< {incoming}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

