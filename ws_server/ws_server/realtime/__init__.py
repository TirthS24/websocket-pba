"""
Realtime WebSocket app.

This app contains:
- A Channels consumer for `/ws/session/<session_id>/`
- Redis-backed "session ownership" so a `session_id` has exactly one active socket
  across an Auto Scaling Group behind an ALB (no sticky sessions).
"""

