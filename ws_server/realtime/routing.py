from django.urls import re_path

from .consumers import SessionConsumer


websocket_urlpatterns = [
    # REQUIRED: /ws/session/<session_id>/
    re_path(r"^ws/session/(?P<session_id>[^/]+)/$", SessionConsumer.as_asgi()),
]

