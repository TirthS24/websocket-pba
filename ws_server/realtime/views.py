"""
REST views for realtime app.

- POST /api/thread/connect: accept thread_id from FE, trigger LLM service to open
  WebSocket connection for that thread.
- POST /api/thread/summarize: proxy to LLM /thread/summarize (same request/response).
- POST /api/thread/history: proxy to LLM /thread/history (same request/response).

LLM is not exposed; all calls go through ws_server (e.g. ECS + Fargate).
"""

from __future__ import annotations

import json
import logging

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

logger = logging.getLogger(__name__)


def _llm_headers(request=None):
    """
    Headers to send to LLM. Always include x-api-key when AUTH_API_KEY is set
    so the LLM accepts the proxied request. Prefer the client's x-api-key when
    present (so the same key is forwarded), otherwise use the server's AUTH_API_KEY.
    """
    headers = {}
    api_key = None
    if request is not None:
        api_key = (
            (request.headers.get("X-Api-Key") or request.headers.get("x-api-key") or "").strip()
            or request.META.get("HTTP_X_API_KEY", "").strip()
        )
    if not api_key:
        api_key = getattr(settings, "AUTH_API_KEY", None) or ""
    if api_key:
        headers["x-api-key"] = api_key
    return headers


def _llm_request(method: str, path: str, request=None, json_body: dict | None = None, timeout: int = 30):
    """Call LLM service; returns JsonResponse. Pass request to forward x-api-key to LLM."""
    base = getattr(settings, "LLM_SERVICE_URL", None) or ""
    base = base.rstrip("/")
    if not base:
        return JsonResponse({"detail": "LLM_SERVICE_URL not configured"}, status=503)

    url = f"{base}{path}"
    headers = _llm_headers(request)

    try:
        import requests
    except ImportError:
        logger.error("requests not installed")
        return JsonResponse({"detail": "Server misconfiguration"}, status=503)

    try:
        if method == "POST":
            resp = requests.post(url, json=json_body or {}, headers=headers, timeout=timeout)
        else:
            resp = requests.get(url, headers=headers, timeout=timeout)
    except requests.RequestException as e:
        logger.warning("LLM service call failed %s %s: %s", method, path, e)
        return JsonResponse(
            {"detail": str(e)},
            status=502,
        )

    try:
        data = resp.json()
    except Exception:
        data = {"detail": resp.text or f"HTTP {resp.status_code}"}

    return JsonResponse(data, status=resp.status_code, safe=False)


@require_http_methods(["POST"])
def thread_connect(request):
    """
    Accept JSON body { "thread_id": "<id>", "user_type": "patient" | "operator" (optional) } from frontend.
    Call LLM service POST /thread/connect only when user_type is not "operator".
    Operator connections do not start the LLM; only patient (or missing user_type) triggers LLM connect.
    """
    if not getattr(settings, "LLM_SERVICE_URL", None) or not settings.LLM_SERVICE_URL.strip():
        return JsonResponse(
            {"detail": "LLM_SERVICE_URL not configured"},
            status=503,
        )

    try:
        body = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"detail": "Invalid JSON"}, status=400)

    thread_id = (body.get("thread_id") or "").strip()
    if not thread_id:
        return JsonResponse({"detail": "thread_id is required"}, status=400)

    user_type = (body.get("user_type") or "").strip().lower()
    if user_type == "operator":
        # Do not connect to LLM for operator; they only broadcast to patients
        return JsonResponse({"status": "ok", "thread_id": thread_id, "llm_connected": False})

    # LLM route is POST /thread/connect (no trailing slash)
    url = f"{settings.LLM_SERVICE_URL.rstrip('/')}/thread/connect"
    headers = _llm_headers(request)

    try:
        import requests

        resp = requests.post(url, json={"thread_id": thread_id}, headers=headers, timeout=10)
    except ImportError:
        logger.error("requests not installed; add requests to ws_server dependencies")
        return JsonResponse({"detail": "Server misconfiguration"}, status=503)
    except requests.RequestException as e:
        logger.warning("LLM service call failed: %s", e)
        return JsonResponse(
            {"detail": str(e)},
            status=502,
        )

    if resp.status_code == 200:
        try:
            data = resp.json()
            return JsonResponse({
                "status": "ok",
                "thread_id": data.get("thread_id", thread_id),
                "llm_connected": True,
            })
        except Exception:
            return JsonResponse({"status": "ok", "thread_id": thread_id, "llm_connected": True})

    try:
        err_body = resp.json()
        detail = err_body.get("detail", resp.text)
    except Exception:
        detail = resp.text or f"HTTP {resp.status_code}"

    return JsonResponse(
        {"detail": detail},
        status=resp.status_code if 400 <= resp.status_code < 600 else 502,
    )


@require_http_methods(["POST"])
def thread_summarize(request):
    """
    Proxy to LLM POST /thread/summarize.
    Body: { "thread_id": "<id>" }. Response: { "thread_id": "...", "summary": "..." }.
    """
    if not getattr(settings, "LLM_SERVICE_URL", None) or not settings.LLM_SERVICE_URL.strip():
        return JsonResponse({"detail": "LLM_SERVICE_URL not configured"}, status=503)
    try:
        body = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"detail": "Invalid JSON"}, status=400)
    thread_id = (body.get("thread_id") or "").strip()
    if not thread_id:
        return JsonResponse({"detail": "thread_id is required"}, status=400)
    return _llm_request("POST", "/thread/summarize", request=request, json_body={"thread_id": thread_id}, timeout=60)


@require_http_methods(["POST"])
def thread_history(request):
    """
    Proxy to LLM POST /thread/history.
    Body: { "thread_id": "<id>" }. Response: { "thread_id": "...", "messages": [...] }.
    """
    if not getattr(settings, "LLM_SERVICE_URL", None) or not settings.LLM_SERVICE_URL.strip():
        return JsonResponse({"detail": "LLM_SERVICE_URL not configured"}, status=503)
    try:
        body = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"detail": "Invalid JSON"}, status=400)
    thread_id = (body.get("thread_id") or "").strip()
    if not thread_id:
        return JsonResponse({"detail": "thread_id is required"}, status=400)
    return _llm_request("POST", "/thread/history", request=request, json_body={"thread_id": thread_id}, timeout=30)


@require_http_methods(["POST"])
def chat_sms(request):
    """
    Proxy to LLM POST /chat/sms.
    Body: { "message": "<string>", "thread_id": "<string>", "invoice": <Invoice> | null }.
    Response 200: { "message": "<string>", "thread_id": "<string>" }; 400/500: { "detail": "..." }.
    """
    if not getattr(settings, "LLM_SERVICE_URL", None) or not settings.LLM_SERVICE_URL.strip():
        return JsonResponse({"detail": "LLM_SERVICE_URL not configured"}, status=503)
    try:
        body = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"detail": "Invalid JSON"}, status=400)
    return _llm_request(
        "POST",
        "/chat/sms",
        request=request,
        json_body={
            "message": body.get("message", ""),
            "thread_id": (body.get("thread_id") or "").strip(),
            # "invoice": body.get("invoice"),
            "webapp_link": body.get("webapp_link"),
        },
        timeout=60,
    )


def websocket_test_page(request):
    """Serve the WebSocket + LLM + two-users test page (vanilla HTML/CSS/JS)."""
    return render(request, "realtime/websocket_test.html")
