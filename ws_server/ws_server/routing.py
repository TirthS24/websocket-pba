"""
Project-level Channels routing.

Keeping routing in the Django project package ensures `ws_server.asgi` can import it.
"""

from realtime.routing import websocket_urlpatterns

__all__ = ["websocket_urlpatterns"]

