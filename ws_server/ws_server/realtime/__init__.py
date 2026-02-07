"""
Realtime WebSocket app.

This app contains:
- A Channels consumer for `/ws/session/<session_id>/`
- In-memory presence tracking (works with ALB sticky sessions)
- ALB sticky sessions ensure all connections for a session_id go to the same instance
"""

