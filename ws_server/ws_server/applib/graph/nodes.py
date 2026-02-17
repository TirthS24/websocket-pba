from ws_server.applib.config import config
from ws_server.applib.graph.structured_outputs import SmsIntentClassification, WebIntentClassification
from ws_server.applib.graph.guardrails import get_guardrail_graph, GuardrailState
from ws_server.applib.helpers import format_invoice_for_context, message_content_str
from ws_server.applib.llms import get_bedrock_converse_model
from ws_server.applib.prompts import prompts
from ws_server.applib.state import State
from ws_server.applib.textcontent import static_messages, structured_outputs
from ws_server.applib.types import Channel, SmsIntent, WebIntent
from functools import partial
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import StateGraph, START, END
import logging

logger = logging.getLogger(__name__)

async def passthrough(state: State) -> dict:
    """Passthrough serves as target node"""
    return {}

async def channel_router(state: State) -> Channel:
    return Channel(state['channel'])


def _should_run_guardrail(state: State) -> str:
    """Route after respond node: run guardrail (post_validate) only if response was AI-generated."""
    if state.get("pending_ai_message") is not None:
        return "sms_post_validate" if state["channel"] == Channel.SMS else "web_post_validate"
    return "sms_message_post_script_respond" if state["channel"] == Channel.SMS else "web_message_post_script_respond"

async def sms_intent_router(state: State) -> SmsIntent:
    try:
        llm = (
            get_bedrock_converse_model(model_id=config.BEDROCK_MODEL_ID_INTENT_DETECTION)
            .with_structured_output(SmsIntentClassification)
        )
        messages = [
            SystemMessage(content=structured_outputs.intent_router.sms.system),
            *state['messages'][-3:]
        ]
        response: SmsIntentClassification = await llm.ainvoke(messages)
        return SmsIntent(response.intent)
    except Exception as e:
        logger.warning("SMS intent router failed, falling back to out_of_scope: %s", e, exc_info=True)
        return SmsIntent.OUT_OF_SCOPE


async def web_intent_router(state: State) -> WebIntent:
    try:
        llm = (
            get_bedrock_converse_model(model_id=config.BEDROCK_MODEL_ID_INTENT_DETECTION)
            .with_structured_output(WebIntentClassification)
        )
        messages = [
            SystemMessage(content=structured_outputs.intent_router.web.system),
            *state['messages'][-3:]
        ]
        response: WebIntentClassification = await llm.ainvoke(messages)
        return WebIntent(response.intent)
    except Exception as e:
        logger.warning("Web intent router failed, falling back to out_of_scope: %s", e, exc_info=True)
        return WebIntent.OUT_OF_SCOPE


def _build_respond_system_content(base_system: str, state: State) -> str:
    """Build system message: base prompt + invoice context when present (state only; not in message history)."""
    parts = [base_system]
    invoice = state.get("invoice")
    if invoice is not None:
        parts.append("\n\n---\nInvoice context (use this to answer questions about the bill; do not make up figures):\n")
        parts.append(format_invoice_for_context(invoice))
    return "\n".join(parts)


async def sms_respond(state: State) -> dict:
    """Generate AI response and store in pending_ai_message; post_validate will append a single message."""
    try:
        llm = get_bedrock_converse_model(model_id=config.BEDROCK_MODEL_ID_SMS_RESPOND)
        system_content = _build_respond_system_content(prompts.respond.sms.system, state)
        messages = [
            SystemMessage(content=system_content),
            *state["messages"][-10:],
        ]
        response = await llm.ainvoke(messages)
        return {"pending_ai_message": response}
    except Exception as e:
        logger.warning("SMS respond failed, falling back to out_of_scope: %s", e, exc_info=True)
        fallback = static_messages.out_of_scope.sms
        return {
            "messages": [AIMessage(content=[{"type": "text", "text": fallback}])],
            "pending_ai_message": None,
        }


async def web_respond(state: State) -> dict:
    """Generate AI response and store in pending_ai_message; post_validate will append a single message."""
    try:
        llm = get_bedrock_converse_model(model_id=config.BEDROCK_MODEL_ID_WEB_RESPOND)
        system_content = _build_respond_system_content(prompts.respond.web.system, state)
        messages = [
            SystemMessage(content=system_content),
            *state["messages"][-10:],
        ]
        response = await llm.ainvoke(messages)
        return {"pending_ai_message": response}
    except Exception as e:
        logger.warning("Web respond failed, falling back to out_of_scope: %s", e, exc_info=True)
        fallback = static_messages.out_of_scope.web
        return {
            "messages": [AIMessage(content=[{"type": "text", "text": fallback}])],
            "pending_ai_message": None,
        }

def _out_of_scope_fallback_for_channel(channel: Channel) -> str:
    """Return the out_of_scope static message for the given channel."""
    return static_messages.out_of_scope.sms if channel == Channel.SMS else static_messages.out_of_scope.web


async def post_validate(state: State) -> dict:
    messages = state["messages"]
    # Response to validate comes from pending_ai_message (set by respond node); we do not read from messages
    # so that we append only one AIMessage after validation (avoids duplicate in history).
    pending = state.get("pending_ai_message")
    user_query = message_content_str(messages[-1]) if len(messages) >= 1 else ""
    response_to_check = message_content_str(pending) if pending is not None else ""
    channel = state["channel"]

    try:
        guardrail_graph = await get_guardrail_graph()
        guardrail_state = GuardrailState(
            thread_id=state['thread_id'],
            user_query=user_query,
            response_to_check=response_to_check,
            is_valid=False,
            needs_rewrite=False,
            rewrite_attempts=0,
            max_rewrites=config.MAXIMUM_GUARDRAIL_REWRITES,
            channel=channel,
        )

        # Set recursion_limit as a safety net (max_rewrites * 2 + buffer for evaluation nodes)
        # This prevents infinite loops even if rewrite_attempts logic fails
        recursion_limit = (config.MAXIMUM_GUARDRAIL_REWRITES * 2) + 10

        # CRITICAL: The guardrail subgraph also has a checkpointer, so it needs the same config structure
        # with 'configurable.thread_id'. We construct this from the state's thread_id.
        guardrail_config = {
            'configurable': {'thread_id': state['thread_id']},
            'recursion_limit': recursion_limit,
        }

        result = await guardrail_graph.ainvoke(guardrail_state, config=guardrail_config)
        validated_response = result.get("validated_response", "")
        return {
            "messages": [AIMessage(content=[{"type": "text", "text": validated_response}])],
            "pending_ai_message": None,
        }
    except Exception as e:
        logger.warning("Post-validate (guardrail) failed, falling back to out_of_scope: %s", e, exc_info=True)
        fallback = _out_of_scope_fallback_for_channel(channel)
        return {
            "messages": [AIMessage(content=[{"type": "text", "text": fallback}])],
            "pending_ai_message": None,
        }


async def append_ai_no_guardrail(state: State) -> dict:
    """When guardrail is skipped (e.g. static path), append pending_ai_message to messages as-is and clear it."""
    pending = state.get("pending_ai_message")
    if pending is None:
        return {}
    text = message_content_str(pending)
    return {
        "messages": [AIMessage(content=[{"type": "text", "text": text}])],
        "pending_ai_message": None,
    }


async def _static_respond(state: State, static_message: str) -> dict:
    """Emit a static message as if it were coming from an LLM call.

    Args:
        state: The current graph state
        static_message: The static text to emit as an AI response

    Returns:
        State update with the static message wrapped in an AIMessage
    """
    return {
        'messages': [AIMessage(content=[{'type': 'text', 'text': static_message}])]
    }


sms_escalation_request_respond = partial(_static_respond, static_message=static_messages.escalation_request.sms)
web_escalation_request_respond = partial(_static_respond, static_message=static_messages.escalation_request.web)

sms_out_of_scope_respond = partial(_static_respond, static_message=static_messages.out_of_scope.sms)
web_out_of_scope_respond = partial(_static_respond, static_message=static_messages.out_of_scope.web)

sms_message_post_script_respond = partial(_static_respond, static_message=static_messages.message_post_script.sms)
web_message_post_script_respond = partial(_static_respond, static_message=static_messages.message_post_script.web)


def get_graph_builder() -> StateGraph:
    builder = StateGraph(State)

    ### NODES ###

    # passthroughs

    builder.add_node('sms_post_channel_router_passthrough', passthrough)
    builder.add_node('web_post_channel_router_passthrough', passthrough)

    # responders
    builder.add_node('sms_respond', sms_respond)
    builder.add_node('web_respond', web_respond)

    # guardrails
    builder.add_node('sms_post_validate', post_validate)
    builder.add_node('web_post_validate', post_validate)
    builder.add_node('sms_append_ai_no_guardrail', append_ai_no_guardrail)
    builder.add_node('web_append_ai_no_guardrail', append_ai_no_guardrail)

    # statics
    builder.add_node('sms_escalation_request_respond', sms_escalation_request_respond)
    builder.add_node('web_escalation_request_respond', web_escalation_request_respond)
    builder.add_node('sms_out_of_scope_respond', sms_out_of_scope_respond)
    builder.add_node('web_out_of_scope_respond', web_out_of_scope_respond)
    builder.add_node('sms_message_post_script_respond', sms_message_post_script_respond)
    builder.add_node('web_message_post_script_respond', web_message_post_script_respond)

    ### EDGES ###

    # determine channel path

    builder.add_conditional_edges(
        START,
        channel_router,
        {
            Channel.SMS: 'sms_post_channel_router_passthrough',
            Channel.WEB: 'web_post_channel_router_passthrough'
        }
    )




    # SMS path

    builder.add_conditional_edges(
        'sms_post_channel_router_passthrough',
        sms_intent_router,
        {
            SmsIntent.IN_SCOPE: 'sms_respond',
            SmsIntent.ESCALATION: 'sms_escalation_request_respond',
            SmsIntent.OUT_OF_SCOPE: 'sms_out_of_scope_respond'
        }
    )

    builder.add_conditional_edges(
        'sms_respond',
        _should_run_guardrail,
        {
            'sms_post_validate': 'sms_post_validate',
            'sms_message_post_script_respond': 'sms_append_ai_no_guardrail',
        },
    )
    builder.add_edge('sms_post_validate', 'sms_message_post_script_respond')
    builder.add_edge('sms_append_ai_no_guardrail', 'sms_message_post_script_respond')

    builder.add_edge('sms_escalation_request_respond', END)
    builder.add_edge('sms_out_of_scope_respond', END)
    builder.add_edge('sms_message_post_script_respond', END)

    # Web Path

    builder.add_conditional_edges(
        'web_post_channel_router_passthrough',
        web_intent_router,
        {
            WebIntent.IN_SCOPE: 'web_respond',
            WebIntent.ESCALATION: 'web_escalation_request_respond',
            WebIntent.OUT_OF_SCOPE: 'web_out_of_scope_respond'
        }
    )

    builder.add_conditional_edges(
        'web_respond',
        _should_run_guardrail,
        {
            'web_post_validate': 'web_post_validate',
            'web_message_post_script_respond': 'web_append_ai_no_guardrail',
        },
    )
    builder.add_edge('web_post_validate', 'web_message_post_script_respond')
    builder.add_edge('web_append_ai_no_guardrail', 'web_message_post_script_respond')

    builder.add_edge('web_escalation_request_respond', END)
    builder.add_edge('web_out_of_scope_respond', END)
    builder.add_edge('web_message_post_script_respond', END)

    return builder
