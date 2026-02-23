from applib.config import config
from applib.graph.graph_manager import get_graph
from applib.llms import get_bedrock_converse_model
from applib.models.api import ChatRequest, EndEvent, ErrorEvent, EscalationEvent, SummarizeRequest, ThreadHistoryRequest, TokenEvent, SessionConnectRequest, SmsChatRequest, Channel
from applib.prompts.templates import JinjaEnvironments
from applib.helpers import get_utc_now, message_content_str
from applib.prompts import prompts
from applib.state import State
from applib.textcontent import static_messages
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage
from typing import AsyncGenerator, Any
from applib.graph.tracing import traced_astream_events, traced_ainvoke
import hashlib
import json
import uuid

router = APIRouter()


def create_state_from_chat_request(request: ChatRequest) -> State:
    """Build initial graph state from a ChatRequest.

    Message is stored as-is (no invoice in user message) so history/summary stay clean.
    When invoice is present it is stored in state and injected into the system prompt
    at respond time only; it is never returned in chat history or included in summaries.
    """
    state = {
        "thread_id": request.thread_id,
        "messages": [
            HumanMessage(
                content=request.message,
                id=str(uuid.uuid4()),
                additional_kwargs={"timestamp": get_utc_now()},
            )
        ],
        "channel": request.channel,
    }
    
    optional_attributes = (
        "invoice",
        "stripe_payment_link",
        "webapp_link",
    )

    for attribute in optional_attributes:
        if (value := getattr(request, attribute, None)) is not None:
            state[attribute] = value

    return state


def create_state_from_sms_request(request: SmsChatRequest) -> State:
    """Build initial graph state for POST /chat/sms (channel=SMS, invoice optional)."""
    state = {
        "thread_id": request.thread_id,
        "messages": [
            HumanMessage(
                content=request.message,
                id=str(uuid.uuid4()),
                additional_kwargs={"timestamp": get_utc_now()},
            )
        ],
        "channel": Channel.SMS,
        "webapp_link": request.webapp_link,
    }
    return state


def get_message_post_script_for_channel(channel: Channel, webapp_link: str = "") -> str:
    """Return the message_post_script static text for the given channel (from static_messages)."""
    if channel == Channel.WEB:
        return static_messages.message_post_script.web
    text = static_messages.message_post_script.sms
    if "{LINK_TO_WEBAPP}" in text and webapp_link:
        text = text.replace("{LINK_TO_WEBAPP}", webapp_link)
    return text


def format_sse(data: dict) -> str:
    """Format data as Server-Sent Event."""
    return f"data: {json.dumps(data)}\n\n"

async def generate_stream(request: ChatRequest) -> AsyncGenerator:

    """Stream chat response with escalation detection.

        Yields SSE events:
        1. metadata - contains should_escalate flag
        2. token - streamed response content
        3. end - signals completion"""

    graph_config = {'configurable': {'thread_id': request.thread_id}}
    input_state = create_state_from_chat_request(request)

    escalation_detected: bool = False

    try:
        graph = await get_graph()

        async for event in traced_astream_events(graph, input_state, graph_config, version="v2"):

            event_type = event.get('event')
            node_name = event.get('name')

            if event_type == "on_chain_end":

                if node_name == "detect_escalation":
                    escalation_detected = event.get('data', {}).get('output', {}).get('should_escalate', False)

            if event_type == "on_chat_model_stream":
                chunk = event.get('data', {}).get('chunk')

                if chunk and hasattr(chunk, 'content') and chunk.content:
                    content = chunk.content[0]
                    if content['type'] == 'text':
                        token = TokenEvent(content=content['text'])
                        yield format_sse(token.model_dump())

            if event_type == "on_chain_stream":
                if node_name == "escalation_respond":
                    chunk = event.get('data', {}).get('chunk', {})
                    if chunk and chunk.get('messages'):
                        content = chunk['messages'][0].content[0]
                        if content['type'] == 'text':
                            token = TokenEvent(content=content['text'])
                            yield format_sse(token.model_dump())


        if escalation_detected:
            escalation_event = EscalationEvent(should_escalate=escalation_detected)
            yield format_sse(escalation_event.model_dump())

        end = EndEvent()
        yield format_sse(end.model_dump())

    except Exception as e:
        error = ErrorEvent(message=str(e))
        print(error)
        yield format_sse(error.model_dump())


def _extract_text_from_chunk(chunk: dict | Any) -> str:
    """Extract first message text from a stream chunk.
    Handles content as either a string or a list of blocks (e.g. [{"type": "text", "text": "..."}]).
    """
    if not isinstance(chunk, dict) or not chunk.get("messages"):
        return ""
    first_msg = chunk["messages"][0]
    content = getattr(first_msg, "content", None)
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip() or ""
    if not isinstance(content, list) or not content:
        return ""
    first_block = content[0]
    if isinstance(first_block, str):
        return first_block.strip() or ""
    if isinstance(first_block, dict) and first_block.get("type") == "text":
        return (first_block.get("text") or "").strip() or ""
    return ""


async def generate_stream_events(request: ChatRequest) -> AsyncGenerator[dict[str, Any], None]:
    """
    Yield events for WebSocket: token (message content), optional static (post_script only for in_scope),
    escalation flag (should_escalate true/false), then end.

    - in_scope: token (AI + guardrail) + static (post_script) + {type: "escalation", should_escalate: false} + end
    - out_of_scope: token (out_of_scope static message) + {type: "escalation", should_escalate: false} + end (no post_script)
    - escalation: token (escalation static message) + {type: "escalation", should_escalate: true} + end (no post_script)
    """
    graph_config = {"configurable": {"thread_id": request.thread_id}}
    input_state = create_state_from_chat_request(request)
    token_parts: list[str] = []
    response_path: str = "in_scope"  # "in_scope" | "escalation" | "out_of_scope"

    try:
        graph = await get_graph()
        async for event in traced_astream_events(graph, input_state, graph_config, version="v2"):
            event_type = event.get("event")
            node_name = event.get("name")

            if event_type != "on_chain_stream":
                continue

            chunk = event.get("data", {}).get("chunk", {})
            text = _extract_text_from_chunk(chunk)
            if not text:
                continue

            if node_name in ("web_post_validate", "sms_post_validate"):
                token_parts.append(text)
                response_path = "in_scope"
            elif node_name in ("web_escalation_request_respond", "sms_escalation_request_respond"):
                token_parts.append(text)
                response_path = "escalation"
            elif node_name in ("web_out_of_scope_respond", "sms_out_of_scope_respond"):
                token_parts.append(text)
                response_path = "out_of_scope"

        full_content = "".join(token_parts)
        yield {"type": "token", "content": full_content}

        if response_path == "in_scope":
            static_text = get_message_post_script_for_channel(
                request.channel,
                getattr(request, "webapp_link", "") or "",
            )
            yield {"type": "static", "content": static_text}

        yield {"type": "escalation", "should_escalate": response_path == "escalation"}
        yield {"type": "end", "content": ""}
    except Exception as e:
        yield {"type": "error", "content": str(e)}
        yield {"type": "escalation", "should_escalate": False}
        yield {"type": "end", "content": ""}


async def get_message_history(
    thread_id: str,
) -> list[tuple[AnyMessage, str | None]]:
    """Load message history for a thread with checkpoint_id per message.

    Iterates state history (newest snapshot first). Messages that first appear in a
    given snapshot are assigned that snapshot's configurable['checkpoint_id'].
    Returns list of (message, checkpoint_id) in chronological order (oldest first).
    checkpoint_id may be None for a snapshot if not present (fallback used in caller).
    """
    graph = await get_graph()
    graph_config = {"configurable": {"thread_id": thread_id}}
    history = graph.aget_state_history(graph_config)

    snapshots: list[tuple[str | None, list[AnyMessage]]] = []
    async for snapshot in history:
        configurable = (snapshot.config or {}).get("configurable") or {}
        checkpoint_id: str | None = configurable.get("checkpoint_id")
        messages = snapshot.values.get("messages") or []
        snapshots.append((checkpoint_id, messages))

    if not snapshots:
        return []

    # Newest snapshot first. Messages added at step i are snapshot[i].messages[len(snapshot[i+1].messages):].
    # Use checkpoint_id for the first message at each step; use None for others so fallback gives unique ids.
    ordered: list[tuple[AnyMessage, str | None]] = []
    for i in range(len(snapshots)):
        cid, msgs = snapshots[i]
        prev_len = len(snapshots[i + 1][1]) if i + 1 < len(snapshots) else 0
        for k, j in enumerate(range(prev_len, len(msgs))):
            # One message per step gets checkpoint_id; rest get None to avoid duplicate ids
            use_cid = cid if k == 0 else None
            ordered.append((msgs[j], use_cid))
    # Reverse so chronological (oldest first)
    ordered.reverse()
    return ordered


async def chat_sms_invoke(request: SmsChatRequest) -> str:
    """Run graph for SMS chat via stream events and return the full response text.

    Reuses the same streaming logic as generate_stream_events: collect content from
    on_chain_stream for SMS nodes. The graph substitutes {LINK_TO_WEBAPP} in
    static messages at execution time. Returns the entire AI message plus static
    post_script in one string for the REST response.
    """
    graph_config = {"configurable": {"thread_id": request.thread_id}}
    input_state = create_state_from_sms_request(request)
    token_parts: list[str] = []

    try:
        graph = await get_graph()
        async for event in traced_astream_events(graph, input_state, graph_config, version="v2"):
            event_type = event.get("event")
            node_name = event.get("name")

            if event_type != "on_chain_stream":
                continue

            chunk = event.get("data", {}).get("chunk", {})
            text = _extract_text_from_chunk(chunk)
            if not text:
                continue

            if node_name in ("sms_post_validate", "sms_append_ai_no_guardrail"):
                token_parts.append(text)
            elif node_name == "sms_escalation_request_respond":
                token_parts.append(text)
            elif node_name in ("sms_out_of_scope_respond", "sms_respond"):
                token_parts.append(text)
            elif node_name == "sms_message_post_script_respond":
                token_parts.append(text)

        return "\n\n".join(p for p in token_parts if p)
    except Exception:
        raise


async def summarize_thread(thread_id: str, human_messages: str | None = None) -> str:
    """Generate thread summary from message history and optional patient–operator messages."""
    llm = get_bedrock_converse_model(model_id=config.BEDROCK_MODEL_ID_THREAD_SUMMARIZE)
    jinja_env = JinjaEnvironments.thread
    template = jinja_env.get_template("chat_history.jinja")
    message_history_with_ids = await get_message_history(thread_id)
    message_history = [msg for msg, _ in message_history_with_ids]
    rendered_history = template.render(history=message_history)
    human_messages_text = human_messages.strip() if human_messages else ""

    messages = [
        SystemMessage(prompts.thread_summary.system),
        HumanMessage(
            prompts.thread_summary.user.format(
                history=rendered_history,
                human_messages=human_messages_text,
            )
        ),
    ]

    response = await traced_ainvoke(llm, messages)
    # Bedrock can return content as list of blocks (reasoning_content + text); return plain text only.
    return message_content_str(response)

@router.post("/thread/connect", response_class=JSONResponse)
async def session_connect(request: SessionConnectRequest) -> dict:
    """Start LLM WebSocket client for the given thread_id (idempotent per thread). Called by ws_server when FE requests connection."""
    from applib.ws_client import start_connection

    if not request.thread_id or not request.thread_id.strip():
        raise HTTPException(status_code=400, detail="thread_id is required")
    if not config.WS_SERVER_URL:
        raise HTTPException(status_code=503, detail="WS_SERVER_URL not configured")

    started = await start_connection(request.thread_id.strip())
    return {"status": "connected", "thread_id": request.thread_id.strip()}

@router.post("/thread/summarize", response_class=JSONResponse)
async def summarize(request: SummarizeRequest) -> dict:
    return {
        "thread_id": request.thread_id,
        "summary": await summarize_thread(request.thread_id, human_messages=request.human_messages),
    }

def _stable_message_id(thread_id: str, index: int, content: str) -> str:
    """Return a deterministic id for a message that has no id (e.g. from older checkpoints).
    Same (thread_id, index, content) always yields the same id so thread history is stable across calls.
    """
    raw = f"{thread_id}:{index}:{content}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _message_to_history_item(
    msg: AnyMessage,
    previous_id: str | None,
    *,
    message_id: str | None = None,
    thread_id: str = "",
    index: int = 0,
) -> dict[str, Any]:
    """Format a LangChain message to spec: type, content, id, sent_at, read_at, previous_message_id.
    Prefers message_id (e.g. checkpoint_id from state history); else message.id; else deterministic
    id from (thread_id, index, content) so IDs are stable across repeated /thread/history calls.
    """
    msg_type = "patient" if msg.type == "human" else "ai"
    content = message_content_str(msg)
    msg_id = message_id or getattr(msg, "id", None)
    if not msg_id:
        msg_id = _stable_message_id(thread_id, index, content)
    # Prefer timestamp from message additional_kwargs (for thread history), else snapshot
    ts = getattr(msg, "additional_kwargs", None) or {}
    timestamp = (ts.get("timestamp") if isinstance(ts, dict) else None)
    return {
        "type": msg_type,
        "content": content,
        "id": msg_id,
        "sent_at": timestamp,
        "read_at": timestamp,
        "previous_message_id": previous_id,
    }


@router.post("/thread/history", response_class=JSONResponse)
async def thread_history(request: ThreadHistoryRequest) -> dict:
    history = await get_message_history(request.thread_id)
    previous_id: str | None = None
    last_timestamp: str | None = None
    messages: list[dict[str, Any]] = []
    for index, (msg, checkpoint_id) in enumerate(history):
        item = _message_to_history_item(
            msg,
            previous_id,
            message_id=checkpoint_id,
            thread_id=request.thread_id,
            index=index,
        )
        # Fallback for AI messages without timestamp (e.g. from older checkpoints): use previous message's time
        if item["type"] == "ai" and item["sent_at"] is None and last_timestamp is not None:
            item["sent_at"] = last_timestamp
            item["read_at"] = last_timestamp
        if item["sent_at"] is not None:
            last_timestamp = item["sent_at"]
        messages.append(item)
        previous_id = item["id"]
    return {"thread_id": request.thread_id, "messages": messages}


@router.post("/chat/sms", response_class=JSONResponse)
async def chat_sms(request: SmsChatRequest) -> dict:
    """SMS chat: single request/response. Request: message, thread_id, invoice optional. Response: message, thread_id."""
    response_text = await chat_sms_invoke(request)
    return {"message": response_text, "thread_id": request.thread_id}


@router.post("/chat/stream")
def stream_chat(request: ChatRequest) -> StreamingResponse:
    """Stream a chat response with escalation detection.

        Response: Server-Sent Events stream with:
        - escalation event: {event: "escalation", should_escalate: bool}
        - token events: {event: "token", content: str}
        - end event: {event: "end"}
        """
    return StreamingResponse(
        generate_stream(request),
        media_type="text/event-stream",
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'
        }
    )
