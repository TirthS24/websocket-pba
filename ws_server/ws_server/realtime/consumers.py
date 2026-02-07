"""
WebSocket consumer for multi-subscriber session streams.

Key behavior:
- URL: /ws/session/<session_id>/
- Multiple clients may connect to the SAME session_id concurrently (listeners).
- Uses Channels groups with in-memory channel layer (sticky sessions ensure same instance).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Any, Dict, Optional

from channels.generic.websocket import AsyncWebsocketConsumer

from .presence import list_connections, remove_connection, refresh_connection, upsert_connection
from .session_manager import session_manager
from ws_server.applib.graph.graph_manager import get_graph, graph_manager
from ws_server.applib.helpers import create_state_from_chat_request
from ws_server.realtime.serializers import ChatRequest, TokenEvent, EscalationEvent, EndEvent, ErrorEvent
from ws_server.applib.models.api import StaticEvent
from langchain_core.messages import AIMessage

logger = logging.getLogger(__name__)


class SessionConsumer(AsyncWebsocketConsumer):
    """
    Production-safe consumer.

    Notes:
    - Works behind ALB with sticky sessions enabled.
    - Uses in-memory channel layer (sticky sessions ensure same instance).
    """

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.session_id: Optional[str] = None
        self.group_name: Optional[str] = None
        self.connection_id: str = uuid.uuid4().hex  # server-assigned per-connection id
        self.user_type: Optional[str] = None
        self.client_type: str = "unknown"
        self._presence_task: Optional[asyncio.Task] = None
        self._presence_registered: bool = False

    # Presence TTL: if an instance dies, these expire automatically.
    PRESENCE_TTL_SECONDS = 120
    PRESENCE_REFRESH_SECONDS = 30

    @staticmethod
    def _group_name(session_id: str) -> str:
        """
        Channels group name must be ASCII and relatively short.
        We sanitize session_id so any client-provided value is safe.
        """

        safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", session_id)[:80]
        return f"session.{safe}"

    async def connect(self) -> None:
        self.session_id = self.scope["url_route"]["kwargs"]["session_id"]
        self.group_name = self._group_name(self.session_id)

        await self.accept()

        # Join the session group so this socket receives broadcasts for session_id
        # within the same instance (in-memory channel layer with sticky sessions).
        await self.channel_layer.group_add(self.group_name, self.channel_name)

        # Optional: send an initial "connected" message for clients that want confirmation.
        await self.send_json(
            {
                "type": "connected",
                "session_id": self.session_id,
                "connection_id": self.connection_id,
                # user_type is required on the FIRST client message, not at connect time.
                "user_type_required": True,
            }
        )

    async def disconnect(self, close_code: int) -> None:
        if self._presence_task:
            self._presence_task.cancel()
            self._presence_task = None

        if self.group_name:
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

        if self.session_id and self._presence_registered:
            await remove_connection(session_id=self.session_id, connection_id=self.connection_id)

    async def receive(self, text_data: Optional[str] = None, bytes_data: Optional[bytes] = None) -> None:
        """
        Minimal receive handler.

        This is intentionally conservative: in production you should validate schemas and
        avoid expensive per-message processing in the event loop.
        """

        if not text_data:
            return

        try:
            msg = json.loads(text_data)
        except json.JSONDecodeError:
            await self.send_json({"type": "error", "error": "invalid_json"})
            return

        # Example protocol:
        # - {"type":"hello","user_type":"admin","client_type":"web"} -> REQUIRED first message to set user_type
        # - {"type":"presence"} -> returns count + member list for this session_id
        # - {"type":"broadcast","msg":"hi"} -> fan out (only allowed AFTER user_type is set)
        #   (backward compat: you may also include user_type/client_type on broadcast; first one wins)
        # - {"type":"broadcast","data":{...}} (backward compat) -> fan out to all listeners
        # - anything else -> echo back to sender only

        # user_type is mandatory, but enforced on the FIRST client message (not on connect).
        # Once set, it is immutable for the life of this WebSocket connection.
        if self.user_type is None:
            incoming_user_type = msg.get("user_type") or msg.get("from")  # backward compat alias
            if isinstance(incoming_user_type, str) and incoming_user_type.strip():
                self.user_type = incoming_user_type.strip()
            else:
                await self.send_json({"type": "error", "error": "user_type_required"})
                await self.close(code=4401)
                return

            # Optional client_type on first message
            if "client_type" in msg and isinstance(msg.get("client_type"), str) and msg["client_type"].strip():
                self.client_type = msg["client_type"].strip()

            # Register presence now that user_type is known.
            if self.session_id:
                await upsert_connection(
                    session_id=self.session_id,
                    connection_id=self.connection_id,
                    user_type=self.user_type,
                    client_type=self.client_type,
                    ttl_seconds=self.PRESENCE_TTL_SECONDS,
                )
                self._presence_registered = True

                # Internal presence TTL refresh (no client heartbeat messages required).
                if not self._presence_task:
                    self._presence_task = asyncio.create_task(self._presence_refresh_loop())

        # After user_type is set, allow client_type updates (optional).
        if "client_type" in msg and isinstance(msg.get("client_type"), str) and msg["client_type"].strip():
            self.client_type = msg["client_type"].strip()

        # Refresh presence metadata on activity.
        if self.session_id and self._presence_registered:
            await upsert_connection(
                session_id=self.session_id,
                connection_id=self.connection_id,
                user_type=self.user_type or "",
                client_type=self.client_type,
                ttl_seconds=self.PRESENCE_TTL_SECONDS,
            )

        if msg.get("type") == "hello":
            await self.send_json(
                {
                    "type": "hello_ack",
                    "session_id": self.session_id,
                    "connection_id": self.connection_id,
                    "user_type": self.user_type,
                    "client_type": self.client_type,
                }
            )
            return

        if msg.get("type") == "presence":
            if not self.session_id:
                return
            if not self._presence_registered:
                await self.send_json({"type": "error", "error": "user_type_required"})
                await self.close(code=4401)
                return
            members = await list_connections(session_id=self.session_id, cleanup=True)
            by_type: Dict[str, int] = {}
            for m in members:
                by_type[m.client_type] = by_type.get(m.client_type, 0) + 1
            await self.send_json(
                {
                    "type": "presence",
                    "session_id": self.session_id,
                    "count": len(members),
                    "by_type": by_type,
                    "members": [
                        {
                            "connection_id": m.connection_id,
                            "user_type": m.user_type,
                            "client_type": m.client_type,
                            "connected_at": m.connected_at,
                            "last_seen": m.last_seen,
                        }
                        for m in members
                    ],
                }
            )
            return

        if msg.get("type") == "broadcast":
            if not self.group_name:
                return
            if not self._presence_registered:
                await self.send_json({"type": "error", "error": "user_type_required"})
                await self.close(code=4401)
                return
            payload: Dict[str, Any] = {
                "type": "session_message",
                "user_type": self.user_type,
                "client_type": self.client_type,
            }
            # Support both shapes:
            # - {"msg":"..."} (simple)
            # - {"data":{...}} (structured)
            if "msg" in msg:
                payload["msg"] = msg.get("msg")
            if "data" in msg:
                payload["data"] = msg.get("data")
            await self.channel_layer.group_send(
                self.group_name,
                payload,
            )
            return

        await self.send_json({"type": "echo", "data": msg})

    async def session_message(self, event: Dict[str, Any]) -> None:
        """
        Handler for group broadcasts.
        """
        # Fan-out message includes sender identity so receivers can show who sent it.
        await self.send_json(
            {
                "type": "session_message",
                "user_type": event.get("user_type", "anonymous"),
                "client_type": event.get("client_type", "unknown"),
                "msg": event.get("msg"),
                "data": event.get("data"),
            }
        )

    async def _presence_refresh_loop(self) -> None:
        """
        Keeps this connection's presence record alive in memory.

        No messages are sent to the client (this is not a heartbeat).
        """

        try:
            while True:
                await asyncio.sleep(self.PRESENCE_REFRESH_SECONDS)
                ok = await refresh_connection(connection_id=self.connection_id, ttl_seconds=self.PRESENCE_TTL_SECONDS)
                if not ok and self.session_id:
                    # Record expired/was deleted; recreate it.
                    await upsert_connection(
                        session_id=self.session_id,
                        connection_id=self.connection_id,
                        user_type=self.user_type,
                        client_type=self.client_type,
                        ttl_seconds=self.PRESENCE_TTL_SECONDS,
                    )
        except asyncio.CancelledError:
            return

    async def send_json(self, payload: Dict[str, Any]) -> None:
        await self.send(text_data=json.dumps(payload, separators=(",", ":"), ensure_ascii=False))


class ChatConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for chat streaming with LangGraph.
    
    Key behavior:
    - URL: /ws/chat/
    - Accepts ChatRequest messages and streams responses
    - Handles session lifecycle and cleanup
    """

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.connection_id: str = uuid.uuid4().hex
        self.session = None

    async def connect(self) -> None:
        """Accept WebSocket connection."""
        await self.accept()
        await self.send_json({
            "type": "connected",
            "connection_id": self.connection_id,
        })

    async def disconnect(self, close_code: int) -> None:
        """Clean up session on disconnect."""
        # Note: We don't automatically end sessions on disconnect
        # Sessions can persist across multiple connections
        # Explicit session cleanup should be done via end_session message or timeout
        pass

    async def receive(self, text_data: Optional[str] = None, bytes_data: Optional[bytes] = None) -> None:
        """Handle incoming WebSocket messages."""
        if not text_data:
            return

        try:
            msg = json.loads(text_data)
        except json.JSONDecodeError:
            await self.send_json(ErrorEvent(message="Invalid JSON").model_dump())
            return

        msg_type = msg.get("type")

        if msg_type == "chat":
            # Start a new chat streaming session
            await self._handle_chat_request(msg)
        elif msg_type == "end_session":
            # Explicitly end the session
            await self._end_session()
        else:
            await self.send_json(ErrorEvent(message=f"Unknown message type: {msg_type}").model_dump())

    async def _handle_chat_request(self, msg: Dict[str, Any]) -> None:
        """Handle a chat request and start streaming."""
        try:
            # Validate request using Pydantic
            chat_request = ChatRequest(**msg)
        except Exception as e:
            await self.send_json(ErrorEvent(message=f"Invalid chat request: {str(e)}").model_dump())
            return

        # Handle thread_id: generate if not provided, validate if provided
        thread_id = None
        is_new_thread = False
        
        if chat_request.thread_id:
            # Validate and trim existing thread_id
            thread_id = chat_request.thread_id.strip()
            if not thread_id:
                await self.send_json(ErrorEvent(message="thread_id cannot be empty if provided").model_dump())
                return
            
            # Check if session exists
            existing_session = session_manager.get_session_by_id(thread_id)
            if not existing_session:
                await self.send_json(ErrorEvent(
                    message=f"Session not found: {thread_id}. Please use a valid thread_id or omit it to create a new session."
                ).model_dump())
                return
            
            # Use existing session
            self.session = existing_session
        else:
            # Generate new thread_id and create session
            try:
                # Initialize graph if not already initialized (lazy initialization)
                if not graph_manager.graph_initialized():
                    logger.info("Initializing LangGraph during session creation...")
                    await graph_manager.initialize_graph()
                    logger.info("LangGraph initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize graph during session creation: {e}", exc_info=True)
                await self.send_json(ErrorEvent(
                    message=f"Failed to initialize graph: {str(e)}"
                ).model_dump())
                return
            
            # Generate a new thread_id
            thread_id = str(uuid.uuid4())
            is_new_thread = True
            
            # Create new session
            self.session = session_manager.create_session(thread_id, self.connection_id)
            
            # Send thread_id to client
            await self.send_json({
                "type": "thread_initialized",
                "thread_id": thread_id,
            })
        
        # Update request with thread_id
        chat_request.thread_id = thread_id
        
        # Register this connection with the session for cleanup tracking
        session_manager.register_connection(self.connection_id, thread_id)

        # Start streaming task
        streaming_task = asyncio.create_task(
            self._stream_chat_response(chat_request)
        )
        self.session.set_streaming_task(streaming_task)

    async def _stream_chat_response(self, request: ChatRequest) -> None:
        """Stream chat response using LangGraph."""
        # Ensure thread_id is always a valid non-empty string
        thread_id = request.thread_id.strip() if request.thread_id else None
        if not thread_id:
            await self.send_json(ErrorEvent(message="thread_id is required and cannot be empty").model_dump())
            return
        
        # Update request object with trimmed thread_id to ensure consistency
        request.thread_id = thread_id
        
        input_state = create_state_from_chat_request(request)
        escalation_detected: bool = False

        try:
            # Ensure graph is initialized (this should already be done during session creation)
            graph = await get_graph()
            
            # Verify graph has checkpointer initialized
            if not graph_manager.checkpointer_initialized():
                await self.send_json(ErrorEvent(
                    message="Graph checkpointer not initialized. Please reinitialize session."
                ).model_dump())
                return
            
            # Critical: Verify the graph was actually compiled with a checkpointer
            # If the graph doesn't have a checkpointer attached, it will fail with the config error
            try:
                # Try to access the graph's checkpointer to verify it exists
                # Some LangGraph versions store it differently, so we check multiple ways
                has_checkpointer = (
                    hasattr(graph, 'checkpointer') and graph.checkpointer is not None
                ) or (
                    hasattr(graph, '_checkpointer') and graph._checkpointer is not None
                ) or graph_manager.checkpointer_initialized()
                
                if not has_checkpointer:
                    logger.error("Graph was compiled without a checkpointer. This will cause config errors.")
                    await self.send_json(ErrorEvent(
                        message="Graph configuration error: checkpointer not attached to graph. Please reinitialize session."
                    ).model_dump())
                    return
            except Exception as e:
                logger.warning(f"Could not verify graph checkpointer: {e}. Proceeding anyway...")
            
            # Build graph config with thread_id - required by checkpointer
            # The config structure must be exactly: {'configurable': {'thread_id': <str>}}
            # This is the format LangGraph's AsyncPostgresSaver checkpointer expects
            graph_config = {
                'configurable': {
                    'thread_id': thread_id
                }
            }
            
            # Verify config structure before use
            if 'configurable' not in graph_config:
                await self.send_json(ErrorEvent(
                    message="Invalid graph config structure: missing 'configurable' key"
                ).model_dump())
                return
            
            if 'thread_id' not in graph_config['configurable']:
                await self.send_json(ErrorEvent(
                    message="Invalid graph config structure: missing 'thread_id' in 'configurable'"
                ).model_dump())
                return
            
            if not isinstance(graph_config['configurable']['thread_id'], str) or not graph_config['configurable']['thread_id']:
                await self.send_json(ErrorEvent(
                    message=f"Invalid thread_id in config: must be non-empty string, got {type(graph_config['configurable']['thread_id'])}"
                ).model_dump())
                return
            
            # Use version="v2" for astream_events to ensure proper checkpointer integration
            # This matches the working SSE implementation in routes.py
            # The config must include 'thread_id' in 'configurable' for the checkpointer to work
            # Track which nodes we've already processed to avoid duplicates
            processed_nodes = set()
            
            async for event in graph.astream_events(input_state, config=graph_config, version="v2"):
                if not self.session or not self.session.is_active:
                    # Session was cancelled
                    break

                event_type = event.get('event')
                node_name = event.get('name')

                # Handle escalation detection
                if event_type == "on_chain_end":
                    if node_name == "detect_escalation":
                        escalation_detected = event.get('data', {}).get('output', {}).get('should_escalate', False)

                # Handle streaming tokens from LLM model calls
                # This captures streaming chunks from web_respond, sms_respond, and other LLM nodes
                # LangGraph's astream_events automatically captures streaming from LLM calls
                # Chunks come character/word-wise from the LLM as it generates the response
                # Match the SSE implementation exactly (routes.py lines 61-68)
                if event_type == "on_chat_model_stream":
                    chunk = event.get('data', {}).get('chunk')
                    if chunk and hasattr(chunk, 'content') and chunk.content:
                        content = chunk.content[0]
                        # Match SSE implementation: direct access to 'type' and 'text' (not .get())
                        if content.get('type') == 'text':
                            text = content.get('text', '')
                            # Only send non-empty text chunks (these are streaming tokens from LLM)
                            # on_chat_model_stream events only contain AI responses, not user messages
                            if text:
                                await self.send_json({"type": "token", "content": text})

                # Handle static messages from static response nodes
                # Match the SSE implementation structure (routes.py lines 70-77)
                # SSE only handles escalation_respond, but we handle all static nodes
                # Also handle LLM response nodes if they come through on_chain_stream
                # (this happens when ainvoke is used instead of streaming)
                if event_type == "on_chain_stream":
                    # Handle LLM response nodes - these should ideally stream via on_chat_model_stream
                    # but if they don't (e.g., when using ainvoke), we capture them here
                    llm_response_nodes = ("sms_respond", "web_respond")
                    if node_name in llm_response_nodes:
                        chunk = event.get('data', {}).get('chunk', {})
                        if chunk and chunk.get('messages'):
                            message = chunk['messages'][0]
                            # Only send AI messages, filter out user messages
                            if isinstance(message, AIMessage) or (hasattr(message, 'type') and getattr(message, 'type', None) == 'ai'):
                                # Extract text content from LLM response
                                if hasattr(message, 'content'):
                                    content = message.content
                                    if isinstance(content, list) and len(content) > 0:
                                        # Handle list format: [{'type': 'text', 'text': '...'}]
                                        text_item = content[0]
                                        if isinstance(text_item, dict) and text_item.get('type') == 'text':
                                            text = text_item.get('text', '')
                                            if text:
                                                # Send the full response as a single token
                                                # This is a fallback when on_chat_model_stream doesn't work
                                                await self.send_json({"type": "token", "content": text})
                                    elif isinstance(content, str) and content:
                                        # Handle direct string content
                                        await self.send_json({"type": "token", "content": content})
                    
                    # Handle all static message nodes
                    static_nodes = (
                        "escalation_respond",  # From old graph structure (handled in SSE)
                        "sms_escalation_request_respond", "web_escalation_request_respond",
                        "sms_out_of_scope_respond", "web_out_of_scope_respond",
                        "sms_message_post_script_respond", "web_message_post_script_respond"
                    )
                    if node_name in static_nodes:
                        # Avoid processing the same node output multiple times
                        event_id = f"{node_name}_{event.get('run_id', '')}"
                        if event_id in processed_nodes:
                            continue
                        processed_nodes.add(event_id)
                        
                        chunk = event.get('data', {}).get('chunk', {})
                        if chunk and chunk.get('messages'):
                            # Match SSE implementation: direct access to content[0]
                            content = chunk['messages'][0].content[0]
                            if content.get('type') == 'text':
                                text = content.get('text', '')
                                if text:
                                    await self.send_json({"type": "static", "content": text})
            
            # Send escalation event if detected
            if escalation_detected:
                await self.send_json({"type": "escalation", "should_escalate": escalation_detected})

            # Send end event
            await self.send_json({"type": "end"})

        except asyncio.CancelledError:
            # Session was cancelled, send end event
            await self.send_json({"type": "end"})
        except Exception as e:
            logger.exception("ChatConsumer streaming error (thread_id=%s)", getattr(request, "thread_id", None))
            await self.send_json({"type": "error", "message": str(e)})
        finally:
            # Note: We don't end the session here as it may be reused for multiple messages
            # Session cleanup happens on WebSocket disconnect
            pass

    async def _end_session(self) -> None:
        """Explicitly end the current session."""
        if self.session:
            await session_manager.end_session(self.connection_id)
            self.session = None
            await self.send_json({
                "type": "session_ended",
                "connection_id": self.connection_id
            })

    async def send_json(self, payload: Dict[str, Any]) -> None:
        """Send JSON message to client."""
        await self.send(text_data=json.dumps(payload, separators=(",", ":"), ensure_ascii=False))

