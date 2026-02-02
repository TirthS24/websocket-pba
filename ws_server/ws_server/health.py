from __future__ import annotations

import os
import time

from django.http import JsonResponse


def health(request):
    """
    ALB target group health check endpoint.

    Keep it cheap and dependency-free:
    - No DB query (this service can be websocket-only)
    - No Redis call (optional; avoid cascading failure during Redis maintenance)
    """

    return JsonResponse(
        {
            "status": "ok",
            "ts": int(time.time()),
            "instance_id": os.environ.get("INSTANCE_ID", "unknown-instance"),
        }
    )

