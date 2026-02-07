from django.urls import re_path

from .consumers import SessionConsumer, ChatConsumer


websocket_urlpatterns = [
    # Chat streaming endpoint (thread_id is required in message payload)
    re_path(r"^ws/chat/$", ChatConsumer.as_asgi()),
    # Session-based endpoint (existing)
    re_path(r"^ws/session/(?P<session_id>[^/]+)/$", SessionConsumer.as_asgi()),
]

