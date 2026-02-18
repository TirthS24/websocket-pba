"""
Presence tracking (who is connected) per session_id.

WHY:
- Channels groups do not provide a way to list members.
- In an ALB + ASG setup, connections can be spread across instances.
- Redis is the shared source of truth for presence.

Design:
- One Redis SET per session_id: holds active connection_ids
  key: ws:presence:session:<session_id>
- One Redis HASH per connection_id: holds metadata (user_type, timestamps)
  key: ws:presence:conn:<connection_id>

Each connection hash has a TTL. A lightweight server-side refresh keeps the TTL alive
while the socket is connected (no client heartbeat messages required).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import redis.asyncio as redis


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def get_redis_url() -> str:
    return _env("REDIS_URL", "redis://127.0.0.1:6379/0") or "redis://127.0.0.1:6379/0"


_redis_client: Optional[redis.Redis] = None


def get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(
            get_redis_url(),
            decode_responses=True,
            health_check_interval=30,
        )
    return _redis_client


def session_set_key(session_id: str) -> str:
    return f"ws:presence:session:{session_id}"


def conn_hash_key(connection_id: str) -> str:
    return f"ws:presence:conn:{connection_id}"


@dataclass(frozen=True)
class PresenceMember:
    connection_id: str
    session_id: str
    user_type: str
    connected_at: int
    last_seen: int


async def upsert_connection(
    *,
    session_id: str,
    connection_id: str,
    user_type: str,
    ttl_seconds: int,
) -> None:
    """
    Register or update a connection's presence record.

    This is safe to call multiple times (e.g., after receiving a message with updated user_type).
    """

    r = get_redis()
    now = int(time.time())
    pipe = r.pipeline()
    pipe.sadd(session_set_key(session_id), connection_id)
    pipe.hset(
        conn_hash_key(connection_id),
        mapping={
            "session_id": session_id,
            "user_type": user_type,
            # connected_at is first-write-wins; we only set it if missing
            # (done via HSETNX below)
            "last_seen": str(now),
        },
    )
    pipe.hsetnx(conn_hash_key(connection_id), "connected_at", str(now))
    pipe.expire(conn_hash_key(connection_id), ttl_seconds)
    await pipe.execute()


async def refresh_connection(*, connection_id: str, ttl_seconds: int) -> bool:
    """
    Refresh TTL + last_seen for a connection.
    Returns True if the record exists, False if it was missing/expired.
    """

    r = get_redis()
    now = int(time.time())
    key = conn_hash_key(connection_id)
    pipe = r.pipeline()
    pipe.exists(key)
    pipe.hset(key, "last_seen", str(now))
    pipe.expire(key, ttl_seconds)
    exists, *_ = await pipe.execute()
    return bool(exists)


async def remove_connection(*, session_id: str, connection_id: str) -> None:
    r = get_redis()
    pipe = r.pipeline()
    pipe.srem(session_set_key(session_id), connection_id)
    pipe.delete(conn_hash_key(connection_id))
    await pipe.execute()


async def list_connections(*, session_id: str, cleanup: bool = True) -> List[PresenceMember]:
    """
    Returns active connections for the session_id.

    If `cleanup=True`, any stale connection_ids found in the session set are removed.
    """

    r = get_redis()
    ids = await r.smembers(session_set_key(session_id))
    if not ids:
        return []

    # Pipeline HGETALL for each id (efficient round trips).
    pipe = r.pipeline()
    for cid in ids:
        pipe.hgetall(conn_hash_key(cid))
    rows: List[Dict[str, str]] = await pipe.execute()

    members: List[PresenceMember] = []
    stale: List[str] = []
    for cid, data in zip(ids, rows):
        if not data:
            stale.append(cid)
            continue
        if data.get("session_id") != session_id:
            # Defensive: wrong-session record; treat as stale from this set.
            stale.append(cid)
            continue
        try:
            connected_at = int(data.get("connected_at") or "0")
        except ValueError:
            connected_at = 0
        try:
            last_seen = int(data.get("last_seen") or "0")
        except ValueError:
            last_seen = 0
        members.append(
            PresenceMember(
                connection_id=cid,
                session_id=session_id,
                user_type=data.get("user_type") or data.get("from") or "anonymous",
                connected_at=connected_at,
                last_seen=last_seen,
            )
        )

    if cleanup and stale:
        await r.srem(session_set_key(session_id), *stale)

    # Stable ordering for clients.
    members.sort(key=lambda m: (m.connected_at, m.connection_id))
    return members

