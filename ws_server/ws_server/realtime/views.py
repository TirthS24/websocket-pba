"""
Django async views for non-streaming HTTP endpoints.
"""

import json
from datetime import datetime, timezone
from typing import Optional
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import ensure_csrf_cookie
from django.utils.decorators import method_decorator
from django.middleware.csrf import get_token
from ws_server.applib.graph.graph_manager import get_graph, graph_manager
from ws_server.applib.llms import get_bedrock_converse_model
from ws_server.applib.prompts.templates import JinjaEnvironments
from ws_server.applib.prompts import prompts
from ws_server.applib.config import config
from langchain_core.messages import AnyMessage, SystemMessage, HumanMessage, AIMessage
from ws_server.realtime.serializers import SummarizeRequest, ThreadHistoryRequest
from ws_server.applib.models.api import SmsChatRequest
from ws_server.applib.types import Channel


async def get_message_history(thread_id: str) -> list[AnyMessage]:
    """Get message history for a thread."""
    graph = await get_graph()
    graph_config = {'configurable': {'thread_id': thread_id}}
    history = graph.aget_state_history(graph_config)

    all_messages: list[AnyMessage] = []

    async for snapshot in history:
        messages = snapshot.values['messages']
        all_messages.extend(messages)
        break

    return all_messages


def extract_message_content(message: AnyMessage) -> Optional[str]:
    """Extract text content from a message, handling different message types."""
    if isinstance(message, AIMessage):
        # AIMessage content can be a list of dicts with 'type' and 'text' keys
        if isinstance(message.content, list):
            text_parts = []
            for item in message.content:
                if isinstance(item, dict) and item.get('type') == 'text':
                    text_parts.append(item.get('text', ''))
                elif isinstance(item, str):
                    text_parts.append(item)
            return ''.join(text_parts) if text_parts else None
        elif isinstance(message.content, str):
            return message.content
        return None
    elif isinstance(message, HumanMessage):
        if isinstance(message.content, str):
            return message.content
        elif isinstance(message.content, list):
            # Handle list content
            text_parts = []
            for item in message.content:
                if isinstance(item, str):
                    text_parts.append(item)
                elif isinstance(item, dict) and item.get('type') == 'text':
                    text_parts.append(item.get('text', ''))
            return ''.join(text_parts) if text_parts else None
        return None
    return None


def get_message_key(message: AnyMessage) -> str:
    """Generate a unique key for a message to track duplicates."""
    if hasattr(message, 'id') and message.id:
        return str(message.id)
    # Fallback: use type + content hash
    content = extract_message_content(message)
    msg_type = 'user' if isinstance(message, HumanMessage) else 'ai' if isinstance(message, AIMessage) else 'other'
    return f"{msg_type}:{hash(content) if content else 'empty'}"


async def get_thread_history_with_metadata(thread_id: str) -> list[dict]:
    """
    Get thread message history with checkpoint metadata.
    Returns a list of message dicts with id, sent_at, read_at, and previous_message_id.
    Only includes user/AI messages (not tool calls or system messages) with non-empty content.
    
    LangGraph StateSnapshot structure:
    - snapshot.config['configurable']['checkpoint_id']: checkpoint ID
    - snapshot.created_at: timestamp in ISO format
    - snapshot.values['messages']: list of messages (cumulative state)
    - snapshot.metadata['source']: 'input', 'loop', etc.
    - snapshot.metadata['step']: step number (-1 for input, 0+ for loop)
    """
    graph = await get_graph()
    graph_config = {'configurable': {'thread_id': thread_id}}
    
    # Get history to process all checkpoints
    history = graph.aget_state_history(graph_config)

    # Collect all snapshots (they come in reverse chronological order - newest first)
    snapshots = []
    async for snapshot in history:
        snapshots.append(snapshot)
    
    if not snapshots:
        return []
    
    # Reverse to process in chronological order (oldest first)
    snapshots.reverse()
    
    # Build a map of message_key -> (checkpoint_id, timestamp) by tracking when messages first appear
    message_to_checkpoint: dict[str, tuple[str, str]] = {}
    seen_message_keys = set()
    
    # First pass: identify which checkpoint each message belongs to
    # Process snapshots chronologically to find when each message first appears
    for snapshot in snapshots:
        # Extract checkpoint_id from config (per official LangGraph structure)
        checkpoint_id = None
        if hasattr(snapshot, 'config') and snapshot.config:
            configurable = snapshot.config.get('configurable', {})
            checkpoint_id = configurable.get('checkpoint_id')
        
        # Extract timestamp from created_at (per official LangGraph structure)
        timestamp = None
        if hasattr(snapshot, 'created_at'):
            timestamp = snapshot.created_at
            # Ensure it's in ISO format with Z suffix if needed
            if isinstance(timestamp, str):
                if not timestamp.endswith('Z') and '+' not in timestamp and 'T' in timestamp:
                    timestamp = timestamp + 'Z'
        
        # Fallback if checkpoint_id not found
        if not checkpoint_id:
            checkpoint_id = f"checkpoint_{snapshots.index(snapshot)}"
        
        # Fallback if timestamp not found
        if not timestamp:
            timestamp = datetime.now(timezone.utc).isoformat() + 'Z'
        
        messages = snapshot.values.get('messages', [])
        
        # Track new messages in this snapshot (messages that first appear here)
        for message in messages:
            if not isinstance(message, (HumanMessage, AIMessage)):
                continue
            
            message_key = get_message_key(message)
            
            # If we haven't seen this message before, associate it with this checkpoint
            if message_key not in seen_message_keys:
                content = extract_message_content(message)
                if content and content.strip():
                    message_to_checkpoint[message_key] = (checkpoint_id, timestamp)
                    seen_message_keys.add(message_key)
    
    # Second pass: get all messages from the latest snapshot and build result
    # The latest snapshot contains all messages (cumulative state)
    latest_snapshot = snapshots[-1]
    messages = latest_snapshot.values.get('messages', [])
    
    result: list[dict] = []
    previous_message_id: Optional[str] = None
    
    for message in messages:
        if not isinstance(message, (HumanMessage, AIMessage)):
            continue
        
        message_key = get_message_key(message)
        content = extract_message_content(message)
        
        if not content or content.strip() == '':
            continue
        
        # Get checkpoint info for this message
        if message_key in message_to_checkpoint:
            checkpoint_id, timestamp = message_to_checkpoint[message_key]
        else:
            # Fallback if message not found in history (shouldn't happen, but safety check)
            checkpoint_id = f"msg_{len(result)}"
            timestamp = datetime.now(timezone.utc).isoformat() + 'Z'
        
        result.append({
            'type': 'user' if isinstance(message, HumanMessage) else 'ai',
            'content': content,
            'id': checkpoint_id,
            'sent_at': timestamp,
            'read_at': timestamp,
            'previous_message_id': previous_message_id
        })
        
        previous_message_id = checkpoint_id
    
    return result


async def summarize_thread(thread_id: str) -> str:
    """Summarize thread history."""
    llm = get_bedrock_converse_model(model_id=config.BEDROCK_MODEL_ID_THREAD_SUMMARIZE)
    jinja_env = JinjaEnvironments.thread
    template = jinja_env.get_template("chat_history.jinja")
    message_history = await get_message_history(thread_id)
    rendered_history = template.render(history=message_history)

    messages = [
        SystemMessage(prompts.thread_summary.system),
        HumanMessage(prompts.thread_summary.user.format(history=rendered_history))
    ]

    response = await llm.ainvoke(messages)
    return response.content


@require_http_methods(["POST"])
async def summarize_thread_view(request):
    """POST /api/thread/summarize - Summarize thread history."""
    try:
        body = json.loads(request.body)
        request_data = SummarizeRequest(**body)
        
        summary = await summarize_thread(request_data.thread_id)
        
        return JsonResponse({
            "thread_id": request_data.thread_id,
            "summary": summary
        })
    except json.JSONDecodeError:
        return JsonResponse({"detail": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"detail": str(e)}, status=500)


@require_http_methods(["POST"])
async def thread_history_view(request):
    """POST /api/thread/history - Get thread message history."""
    try:
        body = json.loads(request.body)
        request_data = ThreadHistoryRequest(**body)
        
        messages = await get_thread_history_with_metadata(request_data.thread_id)

        return JsonResponse({
            'thread_id': request_data.thread_id,
            'messages': messages
        })
    except json.JSONDecodeError:
        return JsonResponse({"detail": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"detail": str(e)}, status=500)


@require_http_methods(["POST"])
async def sms_chat_view(request):
    """
    POST /api/chat/sms - Non-streaming chat for SMS channel.
    Calls LangGraph with channel=sms and returns the full AI response in one shot.
    """
    try:
        body = json.loads(request.body)
        req = SmsChatRequest(**body)
    except json.JSONDecodeError:
        return JsonResponse({"detail": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"detail": str(e)}, status=400)

    thread_id = req.thread_id.strip()
    if not thread_id:
        return JsonResponse({"detail": "thread_id cannot be empty"}, status=400)

    try:
        await graph_manager.initialize_graph()
    except Exception as e:
        return JsonResponse({"detail": f"Failed to initialize graph: {e}"}, status=500)

    graph = await get_graph()
    if not graph_manager.checkpointer_initialized():
        return JsonResponse({"detail": "Graph checkpointer not initialized."}, status=500)

    input_state = {
        "thread_id": thread_id,
        "messages": [HumanMessage(content=req.message)],
        "channel": Channel.SMS,
        "task": None,
        "data": None,
        "context": None,
    }
    graph_config = {"configurable": {"thread_id": thread_id}}

    try:
        final_state = await graph.ainvoke(input_state, config=graph_config)
    except Exception as e:
        return JsonResponse({"detail": str(e)}, status=500)

    messages = final_state.get("messages", [])
    # Collect AI message texts from the end (this turn: main response then post-script static message)
    ai_texts_from_end = []
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            text = extract_message_content(msg) or ""
            if text.strip():
                ai_texts_from_end.append(text)
    # Combine: main response + "\n" + static post-script (e.g. "Generated by AI...") when both present
    if len(ai_texts_from_end) >= 2:
        response_text = (ai_texts_from_end[1] + "\n" + ai_texts_from_end[0]).strip()
    elif len(ai_texts_from_end) == 1:
        response_text = ai_texts_from_end[0]
    else:
        response_text = ""

    return JsonResponse({"message": response_text, "thread_id": thread_id})
