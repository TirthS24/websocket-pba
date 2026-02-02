"""
WebSocket consumer for multi-subscriber session streams.

Key behavior:
- URL: /ws/session/<session_id>/
- Multiple clients may connect to the SAME session_id concurrently (listeners).
- Uses Channels groups (backed by Redis channel layer) for cross-instance fan-out.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import Any, Dict, Optional

from channels.generic.websocket import AsyncWebsocketConsumer

from .presence import list_connections, remove_connection, refresh_connection, upsert_connection


class SessionConsumer(AsyncWebsocketConsumer):
    """
    Production-safe consumer.

    Notes:
    - Works behind ALB + AutoScaling (no sticky sessions required).
    - Uses Redis channel layer for cross-instance broadcast.
    """

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.session_id: Optional[str] = None
        self.group_name: Optional[str] = None
        self.connection_id: str = uuid.uuid4().hex  # server-assigned per-connection id
        self.user_type: Optional[str] = None
        self.client_type: str = "unknown"
        self._presence_task: Optional[asyncio.Task] = None
        self._presence_registered: bool = False

    # Presence TTL: if an instance dies, these expire automatically.
    PRESENCE_TTL_SECONDS = 120
    PRESENCE_REFRESH_SECONDS = 30

    @staticmethod
    def _group_name(session_id: str) -> str:
        """
        Channels group name must be ASCII and relatively short.
        We sanitize session_id so any client-provided value is safe.
        """

        safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", session_id)[:80]
        return f"session.{safe}"

    async def connect(self) -> None:
        self.session_id = self.scope["url_route"]["kwargs"]["session_id"]
        self.group_name = self._group_name(self.session_id)

        await self.accept()

        # Join the session group so this socket receives broadcasts for session_id
        # across all instances (Redis channel layer fan-out).
        await self.channel_layer.group_add(self.group_name, self.channel_name)

        # Optional: send an initial "connected" message for clients that want confirmation.
        await self.send_json(
            {
                "type": "connected",
                "session_id": self.session_id,
                "connection_id": self.connection_id,
                # user_type is required on the FIRST client message, not at connect time.
                "user_type_required": True,
            }
        )

    async def disconnect(self, close_code: int) -> None:
        if self._presence_task:
            self._presence_task.cancel()
            self._presence_task = None

        if self.group_name:
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

        if self.session_id and self._presence_registered:
            await remove_connection(session_id=self.session_id, connection_id=self.connection_id)

    async def receive(self, text_data: Optional[str] = None, bytes_data: Optional[bytes] = None) -> None:
        """
        Minimal receive handler.

        This is intentionally conservative: in production you should validate schemas and
        avoid expensive per-message processing in the event loop.
        """

        if not text_data:
            return

        try:
            msg = json.loads(text_data)
        except json.JSONDecodeError:
            await self.send_json({"type": "error", "error": "invalid_json"})
            return

        # Example protocol:
        # - {"type":"hello","user_type":"admin","client_type":"web"} -> REQUIRED first message to set user_type
        # - {"type":"presence"} -> returns count + member list for this session_id
        # - {"type":"broadcast","msg":"hi"} -> fan out (only allowed AFTER user_type is set)
        #   (backward compat: you may also include user_type/client_type on broadcast; first one wins)
        # - {"type":"broadcast","data":{...}} (backward compat) -> fan out to all listeners
        # - anything else -> echo back to sender only

        # user_type is mandatory, but enforced on the FIRST client message (not on connect).
        # Once set, it is immutable for the life of this WebSocket connection.
        if self.user_type is None:
            incoming_user_type = msg.get("user_type") or msg.get("from")  # backward compat alias
            if isinstance(incoming_user_type, str) and incoming_user_type.strip():
                self.user_type = incoming_user_type.strip()
            else:
                await self.send_json({"type": "error", "error": "user_type_required"})
                await self.close(code=4401)
                return

            # Optional client_type on first message
            if "client_type" in msg and isinstance(msg.get("client_type"), str) and msg["client_type"].strip():
                self.client_type = msg["client_type"].strip()

            # Register presence now that user_type is known.
            if self.session_id:
                await upsert_connection(
                    session_id=self.session_id,
                    connection_id=self.connection_id,
                    user_type=self.user_type,
                    client_type=self.client_type,
                    ttl_seconds=self.PRESENCE_TTL_SECONDS,
                )
                self._presence_registered = True

                # Internal presence TTL refresh (no client heartbeat messages required).
                if not self._presence_task:
                    self._presence_task = asyncio.create_task(self._presence_refresh_loop())

        # After user_type is set, allow client_type updates (optional).
        if "client_type" in msg and isinstance(msg.get("client_type"), str) and msg["client_type"].strip():
            self.client_type = msg["client_type"].strip()

        # Refresh presence metadata on activity.
        if self.session_id and self._presence_registered:
            await upsert_connection(
                session_id=self.session_id,
                connection_id=self.connection_id,
                user_type=self.user_type or "",
                client_type=self.client_type,
                ttl_seconds=self.PRESENCE_TTL_SECONDS,
            )

        if msg.get("type") == "hello":
            await self.send_json(
                {
                    "type": "hello_ack",
                    "session_id": self.session_id,
                    "connection_id": self.connection_id,
                    "user_type": self.user_type,
                    "client_type": self.client_type,
                }
            )
            return

        if msg.get("type") == "presence":
            if not self.session_id:
                return
            if not self._presence_registered:
                await self.send_json({"type": "error", "error": "user_type_required"})
                await self.close(code=4401)
                return
            members = await list_connections(session_id=self.session_id, cleanup=True)
            by_type: Dict[str, int] = {}
            for m in members:
                by_type[m.client_type] = by_type.get(m.client_type, 0) + 1
            await self.send_json(
                {
                    "type": "presence",
                    "session_id": self.session_id,
                    "count": len(members),
                    "by_type": by_type,
                    "members": [
                        {
                            "connection_id": m.connection_id,
                            "user_type": m.user_type,
                            "client_type": m.client_type,
                            "connected_at": m.connected_at,
                            "last_seen": m.last_seen,
                        }
                        for m in members
                    ],
                }
            )
            return

        if msg.get("type") == "broadcast":
            if not self.group_name:
                return
            if not self._presence_registered:
                await self.send_json({"type": "error", "error": "user_type_required"})
                await self.close(code=4401)
                return
            payload: Dict[str, Any] = {
                "type": "session_message",
                "user_type": self.user_type,
                "client_type": self.client_type,
            }
            # Support both shapes:
            # - {"msg":"..."} (simple)
            # - {"data":{...}} (structured)
            if "msg" in msg:
                payload["msg"] = msg.get("msg")
            if "data" in msg:
                payload["data"] = msg.get("data")
            await self.channel_layer.group_send(
                self.group_name,
                payload,
            )
            return

        await self.send_json({"type": "echo", "data": msg})

    async def session_message(self, event: Dict[str, Any]) -> None:
        """
        Handler for group broadcasts.
        """
        # Fan-out message includes sender identity so receivers can show who sent it.
        await self.send_json(
            {
                "type": "session_message",
                "user_type": event.get("user_type", "anonymous"),
                "client_type": event.get("client_type", "unknown"),
                "msg": event.get("msg"),
                "data": event.get("data"),
            }
        )

    async def _presence_refresh_loop(self) -> None:
        """
        Keeps this connection's presence record alive in Redis.

        No messages are sent to the client (this is not a heartbeat).
        """

        try:
            while True:
                await asyncio.sleep(self.PRESENCE_REFRESH_SECONDS)
                ok = await refresh_connection(connection_id=self.connection_id, ttl_seconds=self.PRESENCE_TTL_SECONDS)
                if not ok and self.session_id:
                    # Record expired/was deleted; recreate it.
                    await upsert_connection(
                        session_id=self.session_id,
                        connection_id=self.connection_id,
                        user_type=self.user_type,
                        client_type=self.client_type,
                        ttl_seconds=self.PRESENCE_TTL_SECONDS,
                    )
        except asyncio.CancelledError:
            return

    async def send_json(self, payload: Dict[str, Any]) -> None:
        await self.send(text_data=json.dumps(payload, separators=(",", ":"), ensure_ascii=False))

