"""
Custom WebSocket origin validator for deployments behind a proxy (ALB, nginx).

AllowedHostsOriginValidator rejects when the Origin header is missing. Behind a proxy,
the browser's Origin can be dropped or the connection may come from a server (e.g. LLM)
that does not send Origin. This validator:
- Allows when Origin's host is in ALLOWED_HOSTS (same as Channels).
- Allows when Origin is missing or not in list BUT Host or X-Forwarded-Host is in
  ALLOWED_HOSTS (trust the proxy to have routed to the correct host).
- Logs when a connection is denied (origin/host values, no secrets) for debugging.
"""
from __future__ import annotations

import logging
from urllib.parse import urlparse

from django.conf import settings
from django.http.request import is_same_domain

from channels.security.websocket import WebsocketDenier

logger = logging.getLogger(__name__)

# ASGI app that denies WebSocket connections (used when origin validation fails).
_denier_app = WebsocketDenier.as_asgi()


def _get_header(scope: dict, name: str) -> str | None:
    want = name.lower().encode("ascii")
    for key, value in scope.get("headers") or []:
        if key == want:
            return value.decode("utf-8", errors="replace").strip()
    return None


def _hostname_from_host_header(host: str) -> str:
    """Return hostname part (strip port) from Host header."""
    if not host:
        return ""
    return host.split(":", 1)[0].strip().lower()


def _origin_host_in_allowed(origin_value: str, allowed_hosts: list[str]) -> bool:
    try:
        parsed = urlparse(origin_value)
        if not parsed.hostname:
            return False
        for pattern in allowed_hosts:
            if pattern == "*":
                return True
            pattern_host = urlparse("//" + pattern).hostname if "://" not in pattern else urlparse(pattern).hostname
            if pattern_host and is_same_domain(parsed.hostname, pattern_host):
                return True
        return False
    except Exception:
        return False


def _host_in_allowed(host_header: str, allowed_hosts: list[str]) -> bool:
    hostname = _hostname_from_host_header(host_header)
    if not hostname:
        return False
    for pattern in allowed_hosts:
        if pattern == "*":
            return True
        pattern_host = (urlparse("//" + pattern).hostname or pattern).lower() if "://" not in pattern else urlparse(pattern).hostname
        if pattern_host and is_same_domain(hostname, pattern_host):
            return True
    return False


def _is_private_ip(hostname: str) -> bool:
    """True if hostname looks like a VPC/private IP (ALB may send task IP as Host)."""
    if not hostname:
        return False
    parts = hostname.split(".")
    if len(parts) != 4:
        return False
    try:
        a, b, c, d = (int(p) for p in parts)
        if 10 <= a <= 10 and 0 <= b <= 255 and 0 <= c <= 255 and 0 <= d <= 255:
            return True  # 10.0.0.0/8
        if a == 172 and 16 <= b <= 31:
            return True  # 172.16.0.0/12
        if a == 192 and b == 168:
            return True  # 192.168.0.0/16
    except (ValueError, TypeError):
        pass
    return False


class AllowedHostsOrForwardedHostOriginValidator:
    """
    ASGI middleware that validates WebSocket origin. Allows connection if:
    1. Origin header's host is in ALLOWED_HOSTS, or
    2. Origin is missing/invalid but Host or X-Forwarded-Host is in ALLOWED_HOSTS.
    Logs when denying for debugging.
    """

    def __init__(self, application):
        self.application = application

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "websocket":
            raise ValueError("AllowedHostsOrForwardedHostOriginValidator only supports WebSocket")

        allowed_hosts = list(getattr(settings, "ALLOWED_HOSTS", None) or [])
        if settings.DEBUG and not allowed_hosts:
            allowed_hosts = ["localhost", "127.0.0.1", "[::1]"]

        origin_value = _get_header(scope, "origin")
        host_value = _get_header(scope, "host")
        forwarded_host = _get_header(scope, "x-forwarded-host")
        if forwarded_host:
            forwarded_host = forwarded_host.split(",")[0].strip()

        allowed = False
        if origin_value and _origin_host_in_allowed(origin_value, allowed_hosts):
            allowed = True
        if not allowed and host_value and _host_in_allowed(host_value, allowed_hosts):
            allowed = True
        if not allowed and forwarded_host and _host_in_allowed(forwarded_host, allowed_hosts):
            allowed = True
        # When behind ALB, Host can be the task private IP; allow if no Origin (proxy dropped it).
        if not allowed and not origin_value and host_value and _is_private_ip(_hostname_from_host_header(host_value)):
            allowed = True

        if allowed:
            return await self.application(scope, receive, send)
        path_repr = scope.get("path") or (scope.get("raw_path", b"") or b"").decode("utf-8", errors="replace")
        logger.warning(
            "WebSocket origin denied: origin=%s host=%s x_forwarded_host=%s allowed_hosts=%s path=%s",
            origin_value or "(none)",
            host_value or "(none)",
            forwarded_host or "(none)",
            allowed_hosts,
            path_repr,
        )
        return await _denier_app(scope, receive, send)
