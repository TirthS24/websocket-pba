from applib.config import config
from applib.graph.graph_manager import get_graph
from applib.llms import get_bedrock_converse_model
from applib.models.api import ChatRequest, EndEvent, ErrorEvent, EscalationEvent, SummarizeRequest, ThreadHistoryRequest, TokenEvent, SessionConnectRequest, SmsChatRequest, Channel
from applib.prompts.templates import JinjaEnvironments
from applib.helpers import get_utc_now, message_content_str
from applib.prompts import prompts
from applib.textcontent import static_messages
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage
from typing import AsyncGenerator, Any
from applib.graph.tracing import traced_astream_events, traced_ainvoke
import uuid
import json

router = APIRouter()


def create_state_from_chat_request(request: ChatRequest) -> dict:
    """Build initial graph state from a ChatRequest.

    Message is stored as-is (no invoice in user message) so history/summary stay clean.
    When invoice is present it is stored in state and injected into the system prompt
    at respond time only; it is never returned in chat history or included in summaries.
    """
    state = {
        "thread_id": request.thread_id,
        "messages": [HumanMessage(content=request.message, additional_kwargs={"timestamp": get_utc_now()})],
        "channel": request.channel,
    }
    if getattr(request, "invoice", None) is not None:
        state["invoice"] = request.invoice
    if getattr(request, "stripe_link", None):
        state["stripe_link"] = request.stripe_link
    if getattr(request, "webapp_link", None):
        state["webapp_link"] = request.webapp_link

    return state


def create_state_from_sms_request(request: SmsChatRequest) -> dict:
    """Build initial graph state for POST /chat/sms (channel=SMS, invoice optional)."""
    state = {
        "thread_id": request.thread_id,
        "messages": [HumanMessage(content=request.message, additional_kwargs={"timestamp": get_utc_now()})],
        "channel": Channel.SMS,
    }
    if getattr(request, "invoice", None) is not None:
        state["invoice"] = request.invoice
    return state


def get_message_post_script_for_channel(channel: Channel, webapp_link: str = "") -> str:
    """Return the message_post_script static text for the given channel (from static_messages)."""
    if channel == Channel.SMS:
        return static_messages.message_post_script.sms
    text = static_messages.message_post_script.web
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


async def get_message_history(thread_id: str) -> list[AnyMessage]:

    graph = await get_graph()
    graph_config = {'configurable': {'thread_id': thread_id}}
    history = graph.aget_state_history(graph_config)

    all_messages: list[AnyMessage] = []

    async for snapshot in history:
        messages = snapshot.values['messages']
        all_messages.extend(messages)
        break

    return all_messages


async def chat_sms_invoke(request: SmsChatRequest) -> str:
    """Run graph for SMS chat (non-streaming) and return the final AI response text."""
    graph = await get_graph()
    graph_config = {"configurable": {"thread_id": request.thread_id}}
    input_state = create_state_from_sms_request(request)
    result = await graph.ainvoke(input_state, config=graph_config)
    messages = result.get("messages") or []
    # Last message is the AI response
    for msg in reversed(messages):
        if getattr(msg, "type", None) == "ai":
            return message_content_str(msg)
    return ""


async def summarize_thread(thread_id: str) -> str:

    llm = get_bedrock_converse_model(model_id=config.BEDROCK_MODEL_ID_THREAD_SUMMARIZE)
    jinja_env = JinjaEnvironments.thread
    template = jinja_env.get_template("chat_history.jinja")
    message_history = await get_message_history(thread_id)
    rendered_history = template.render(history=[m for m, _ in message_history])

    messages = [
        SystemMessage(prompts.thread_summary.system),
        HumanMessage(prompts.thread_summary.user.format(history=rendered_history))
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
        "summary": await summarize_thread(request.thread_id)
    }

def _message_to_history_item(msg: AnyMessage, previous_id: str | None) -> dict[str, Any]:
    """Format a LangChain message to spec: type, content, id, sent_at, read_at, previous_message_id."""
    msg_type = "user" if msg.type == "human" else "ai"
    content = message_content_str(msg)
    msg_id = getattr(msg, "id", None) or str(uuid.uuid4())
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
    messages: list[dict[str, Any]] = []
    for msg in history:
        item = _message_to_history_item(msg, previous_id)
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
