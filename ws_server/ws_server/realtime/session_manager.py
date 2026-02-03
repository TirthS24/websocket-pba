"""
WebSocket session lifecycle management.
Tracks active streaming sessions and handles cleanup.
"""

import asyncio
from typing import Optional, Dict
from uuid import uuid4


class StreamingSession:
    """Represents an active streaming session."""

    def __init__(self, session_id: str, thread_id: str):
        self.session_id = session_id
        self.thread_id = thread_id
        self.streaming_task: Optional[asyncio.Task] = None
        self.is_active = True

    def set_streaming_task(self, task: asyncio.Task):
        """Set the streaming task for this session."""
        self.streaming_task = task

    async def cancel(self):
        """Cancel the streaming task and mark session as inactive."""
        self.is_active = False
        if self.streaming_task and not self.streaming_task.done():
            self.streaming_task.cancel()
            try:
                await self.streaming_task
            except asyncio.CancelledError:
                pass


class SessionManager:
    """
    Manages WebSocket streaming sessions.
    Tracks active sessions per connection and by session_id for reuse.
    """

    def __init__(self):
        # Map connection_id -> StreamingSession (for connection-specific cleanup)
        self._sessions_by_connection: Dict[str, StreamingSession] = {}
        # Map session_id -> StreamingSession (for session lookup)
        self._sessions_by_id: Dict[str, StreamingSession] = {}

    def create_session(self, thread_id: str, connection_id: Optional[str] = None) -> StreamingSession:
        """Create a new streaming session.
        
        Args:
            thread_id: The thread ID (used as session identifier)
            connection_id: Optional connection ID to associate with the session
        """
        # Use thread_id as the session identifier
        session_id = thread_id
        
        # Check if session already exists
        if session_id in self._sessions_by_id:
            existing_session = self._sessions_by_id[session_id]
            # Update connection mapping if connection_id provided
            if connection_id:
                self._sessions_by_connection[connection_id] = existing_session
            return existing_session
        
        # Create new session
        session = StreamingSession(session_id, thread_id)
        if connection_id:
            self._sessions_by_connection[connection_id] = session
        self._sessions_by_id[session_id] = session
        return session

    def get_session(self, connection_id: str) -> Optional[StreamingSession]:
        """Get an existing session by connection ID."""
        return self._sessions_by_connection.get(connection_id)

    def get_session_by_id(self, session_id: str) -> Optional[StreamingSession]:
        """Get an existing session by session ID."""
        return self._sessions_by_id.get(session_id)

    def register_connection(self, connection_id: str, session_id: str) -> bool:
        """Register a connection with an existing session.
        
        Returns True if session exists and connection was registered, False otherwise.
        """
        session = self._sessions_by_id.get(session_id)
        if session:
            self._sessions_by_connection[connection_id] = session
            return True
        return False

    async def end_session(self, connection_id: str):
        """End a session and clean up resources."""
        session = self._sessions_by_connection.pop(connection_id, None)
        if session:
            # Only remove from session_id map if no other connections are using it
            # For now, we'll remove it - in future we could track connection count
            self._sessions_by_id.pop(session.session_id, None)
            await session.cancel()

    async def cleanup_all(self):
        """Clean up all active sessions (for shutdown)."""
        for connection_id in list(self._sessions_by_connection.keys()):
            await self.end_session(connection_id)


# Global session manager instance
session_manager = SessionManager()
