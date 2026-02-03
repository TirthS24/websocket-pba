"""
Redis "session ownership" helper.

WHY THIS EXISTS:
- ALB + AutoScaling means reconnects can land on any instance (no sticky sessions).
- We must guarantee **one active WebSocket per session_id** cluster-wide.
- Redis is the shared truth. We store (instance_id, channel_name, token) per session_id.
- We do atomic updates with Lua to avoid race conditions between fast reconnects.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Optional

import redis.asyncio as redis


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def get_instance_id() -> str:
    """
    Instance identifier injected at deploy time.

    REQUIREMENT: "Instance identification using EC2 metadata (INSTANCE_ID env variable)"
    In production, inject INSTANCE_ID via user-data, EC2 launch template, or systemd env file.
    """

    return _env("INSTANCE_ID", "unknown-instance") or "unknown-instance"


def get_redis_url() -> str:
    """
    Redis endpoint shared by all instances (typically ElastiCache).
    """

    return _env("REDIS_URL", "redis://127.0.0.1:6379/0") or "redis://127.0.0.1:6379/0"


_redis_client: Optional[redis.Redis] = None


def get_redis() -> redis.Redis:
    """
    Returns a process-wide Redis client.

    Using a singleton avoids creating a new TCP connection per connect/disconnect.
    """

    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(
            get_redis_url(),
            decode_responses=True,  # store/read strings; simplifies Lua + hashes
            health_check_interval=30,
        )
    return _redis_client


def session_key(session_id: str) -> str:
    # Key prefix isolates our ownership records from other Redis usage.
    return f"ws:session:{session_id}"


@dataclass(frozen=True)
class Ownership:
    session_id: str
    instance_id: str
    channel_name: str
    token: str
    updated_at: float


_LUA_REGISTER_OWNER = r"""
local key = KEYS[1]
local ttl = tonumber(ARGV[1])
local new_instance_id = ARGV[2]
local new_channel_name = ARGV[3]
local new_token = ARGV[4]
local now = ARGV[5]

local prev_instance_id = redis.call("HGET", key, "instance_id") or ""
local prev_channel_name = redis.call("HGET", key, "channel_name") or ""
local prev_token = redis.call("HGET", key, "token") or ""
local prev_updated_at = redis.call("HGET", key, "updated_at") or ""

redis.call("HSET", key,
  "instance_id", new_instance_id,
  "channel_name", new_channel_name,
  "token", new_token,
  "updated_at", now
)
redis.call("EXPIRE", key, ttl)

return {prev_instance_id, prev_channel_name, prev_token, prev_updated_at}
"""


_LUA_DELETE_IF_OWNER = r"""
local key = KEYS[1]
local token = ARGV[1]

local current = redis.call("HGET", key, "token")
if current == token then
  return redis.call("DEL", key)
end
return 0
"""


_LUA_REFRESH_TTL_IF_OWNER = r"""
local key = KEYS[1]
local token = ARGV[1]
local ttl = tonumber(ARGV[2])
local now = ARGV[3]

local current = redis.call("HGET", key, "token")
if current == token then
  redis.call("HSET", key, "updated_at", now)
  redis.call("EXPIRE", key, ttl)
  return 1
end
return 0
"""


async def register_owner(
    *,
    session_id: str,
    instance_id: str,
    channel_name: str,
    token: str,
    ttl_seconds: int,
) -> Optional[Ownership]:
    r = get_redis()
    now = str(time.time())
    # NOTE: redis-py `eval` signature is positional:
    #   eval(script, numkeys, *keys_and_args)
    # (it does NOT accept `keys=` / `args=` kwargs in many versions)
    prev = await r.eval(
        _LUA_REGISTER_OWNER,
        1,
        session_key(session_id),
        str(ttl_seconds),
        instance_id,
        channel_name,
        token,
        now,
    )

    prev_instance_id, prev_channel_name, prev_token, prev_updated_at = prev
    if not prev_channel_name:
        return None
    if prev_token == token and prev_channel_name == channel_name:
        return None

    try:
        prev_updated = float(prev_updated_at) if prev_updated_at else 0.0
    except ValueError:
        prev_updated = 0.0

    return Ownership(
        session_id=session_id,
        instance_id=prev_instance_id,
        channel_name=prev_channel_name,
        token=prev_token,
        updated_at=prev_updated,
    )


async def delete_if_owner(*, session_id: str, token: str) -> bool:
    r = get_redis()
    deleted = await r.eval(_LUA_DELETE_IF_OWNER, 1, session_key(session_id), token)
    return bool(deleted)


async def refresh_ttl_if_owner(*, session_id: str, token: str, ttl_seconds: int) -> bool:
    r = get_redis()
    now = str(time.time())
    refreshed = await r.eval(
        _LUA_REFRESH_TTL_IF_OWNER,
        1,
        session_key(session_id),
        token,
        str(ttl_seconds),
        now,
    )
    return bool(refreshed)

