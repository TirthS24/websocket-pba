"""
Presence tracking (who is connected) per session_id.

WHY:
- Channels groups do not provide a way to list members.
- With ALB sticky sessions, all connections for a session_id go to the same instance.
- In-memory storage is sufficient since we don't need cross-instance presence.

Design:
- In-memory dictionaries store presence data per instance.
- One dict per session_id: holds active connection_ids
- One dict per connection_id: holds metadata (user_type, client_type, timestamps)

Each connection record has a TTL. A lightweight server-side refresh keeps the TTL alive
while the socket is connected (no client heartbeat messages required).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

# In-memory storage
_session_connections: Dict[str, Set[str]] = {}  # session_id -> set of connection_ids
_connection_data: Dict[str, Dict[str, Any]] = {}  # connection_id -> metadata dict
_connection_expiry: Dict[str, float] = {}  # connection_id -> expiry timestamp
_cleanup_task: Optional[asyncio.Task] = None


def _ensure_cleanup_task() -> None:
    """Start background task to clean up expired connections."""
    global _cleanup_task
    if _cleanup_task is None or _cleanup_task.done():
        _cleanup_task = asyncio.create_task(_cleanup_expired_connections())


async def _cleanup_expired_connections() -> None:
    """Background task to periodically clean up expired connections."""
    while True:
        try:
            await asyncio.sleep(30)  # Check every 30 seconds
            now = time.time()
            expired = [
                conn_id
                for conn_id, expiry in _connection_expiry.items()
                if expiry < now
            ]
            for conn_id in expired:
                # Remove from connection data
                if conn_id in _connection_data:
                    session_id = _connection_data[conn_id].get("session_id")
                    del _connection_data[conn_id]
                    # Remove from session set
                    if session_id and session_id in _session_connections:
                        _session_connections[session_id].discard(conn_id)
                        # Clean up empty session sets
                        if not _session_connections[session_id]:
                            del _session_connections[session_id]
                # Remove from expiry tracking
                if conn_id in _connection_expiry:
                    del _connection_expiry[conn_id]
        except asyncio.CancelledError:
            break
        except Exception:
            # Log error but continue cleanup
            pass


@dataclass(frozen=True)
class PresenceMember:
    connection_id: str
    session_id: str
    user_type: str
    client_type: str
    connected_at: int
    last_seen: int


async def upsert_connection(
    *,
    session_id: str,
    connection_id: str,
    user_type: str,
    client_type: str,
    ttl_seconds: int,
) -> None:
    """
    Register or update a connection's presence record.

    This is safe to call multiple times (e.g., after receiving a message with updated `from`).
    """
    now = int(time.time())
    
    # Initialize session set if needed
    if session_id not in _session_connections:
        _session_connections[session_id] = set()
    
    # Add connection to session set
    _session_connections[session_id].add(connection_id)
    
    # Update or create connection data
    if connection_id not in _connection_data:
        _connection_data[connection_id] = {
            "session_id": session_id,
            "user_type": user_type,
            "client_type": client_type,
            "connected_at": now,
            "last_seen": now,
        }
    else:
        # Update existing connection
        _connection_data[connection_id].update({
            "session_id": session_id,
            "user_type": user_type,
            "client_type": client_type,
            "last_seen": now,
        })
        # Preserve connected_at (first-write-wins)
        if "connected_at" not in _connection_data[connection_id]:
            _connection_data[connection_id]["connected_at"] = now
    
    # Update expiry
    _connection_expiry[connection_id] = time.time() + ttl_seconds
    
    # Ensure cleanup task is running
    _ensure_cleanup_task()


async def refresh_connection(*, connection_id: str, ttl_seconds: int) -> bool:
    """
    Refresh TTL + last_seen for a connection.
    Returns True if the record exists, False if it was missing/expired.
    """
    if connection_id not in _connection_data:
        return False
    
    now = int(time.time())
    _connection_data[connection_id]["last_seen"] = now
    _connection_expiry[connection_id] = time.time() + ttl_seconds
    return True


async def remove_connection(*, session_id: str, connection_id: str) -> None:
    """Remove a connection from presence tracking."""
    # Remove from session set
    if session_id in _session_connections:
        _session_connections[session_id].discard(connection_id)
        # Clean up empty session sets
        if not _session_connections[session_id]:
            del _session_connections[session_id]
    
    # Remove connection data
    if connection_id in _connection_data:
        del _connection_data[connection_id]
    
    # Remove expiry tracking
    if connection_id in _connection_expiry:
        del _connection_expiry[connection_id]


async def list_connections(*, session_id: str, cleanup: bool = True) -> List[PresenceMember]:
    """
    Returns active connections for the session_id.

    If `cleanup=True`, any stale connection_ids found in the session set are removed.
    """
    if session_id not in _session_connections:
        return []
    
    connection_ids = list(_session_connections[session_id])
    members: List[PresenceMember] = []
    stale: List[str] = []
    
    for conn_id in connection_ids:
        if conn_id not in _connection_data:
            stale.append(conn_id)
            continue
        
        data = _connection_data[conn_id]
        if data.get("session_id") != session_id:
            # Defensive: wrong-session record; treat as stale
            stale.append(conn_id)
            continue
        
        # Check if expired
        if conn_id in _connection_expiry and _connection_expiry[conn_id] < time.time():
            stale.append(conn_id)
            continue
        
        try:
            connected_at = int(data.get("connected_at", 0))
        except (ValueError, TypeError):
            connected_at = 0
        
        try:
            last_seen = int(data.get("last_seen", 0))
        except (ValueError, TypeError):
            last_seen = 0
        
        members.append(
            PresenceMember(
                connection_id=conn_id,
                session_id=session_id,
                user_type=data.get("user_type") or data.get("from") or "anonymous",
                client_type=data.get("client_type", "unknown"),
                connected_at=connected_at,
                last_seen=last_seen,
            )
        )
    
    # Clean up stale connections
    if cleanup and stale:
        for conn_id in stale:
            if conn_id in _connection_data:
                del _connection_data[conn_id]
            if conn_id in _connection_expiry:
                del _connection_expiry[conn_id]
            _session_connections[session_id].discard(conn_id)
        
        # Clean up empty session sets
        if session_id in _session_connections and not _session_connections[session_id]:
            del _session_connections[session_id]
    
    # Stable ordering for clients
    members.sort(key=lambda m: (m.connected_at, m.connection_id))
    return members
