from ws_server.applib.code_guidance import add_guidance_to_claim_adjustments
from ws_server.applib.config import config
from ws_server.applib.graph.structured_outputs import SmsIntentClassification, WebIntentClassification
from ws_server.applib.graph.guardrails import get_guardrail_graph, GuardrailState
from ws_server.applib.llms import get_bedrock_converse_model
from ws_server.applib.prompts import prompts
from ws_server.applib.prompts.templates import JinjaEnvironments
from ws_server.applib.state import State, StateContext
from ws_server.applib.textcontent import static_messages, structured_outputs
from ws_server.applib.types import Channel, SmsIntent, WebIntent
from functools import partial
from langchain_core.messages import SystemMessage, AIMessage, HumanMessage
from langgraph.graph import StateGraph, START, END

async def passthrough(state: State) -> dict:
    """Passthrough serves as target node"""
    return {}

async def channel_router(state: State) -> Channel:
    return Channel(state['channel'])

async def sms_intent_router(state: State) -> SmsIntent:
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


async def web_intent_router(state: State) -> WebIntent:
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


async def sms_respond(state: State) -> dict:
    # Enable streaming so websocket/SSE can receive token chunks via callbacks.
    llm = get_bedrock_converse_model(model_id=config.BEDROCK_MODEL_ID_SMS_RESPOND)
    messages = [
        SystemMessage(content=prompts.respond.sms.system),
        *state['messages'][-10:]
        ]
    response = await llm.ainvoke(messages)
    return {"messages": [response]}


async def web_respond(state: State) -> dict:
    # Enable streaming so websocket/SSE can receive token chunks via callbacks.
    llm = get_bedrock_converse_model(model_id=config.BEDROCK_MODEL_ID_WEB_RESPOND)
    messages = [
        SystemMessage(content=prompts.respond.web.system),
        *state['messages'][-10:]
    ]
    response = await llm.ainvoke(messages)
    return {"messages": [response]}

async def post_validate(state: State) -> dict:
    guardrail_graph = await get_guardrail_graph()
    guardrail_state = GuardrailState(
        thread_id=state['thread_id'],
        user_query=state['messages'][-2], ## TODO: ensure this is the most recent USER message
        response_to_check=state['messages'][-1], ## TODO: ensure this is the most recent AI message
        is_valid=False,
        rewrite_attempts=0,
        max_rewrites=config.MAXIMUM_GUARDRAIL_REWRITES,
        channel=state['channel']
    )

    # Set recursion_limit as a safety net (max_rewrites * 2 + buffer for evaluation nodes)
    # This prevents infinite loops even if rewrite_attempts logic fails
    recursion_limit = (config.MAXIMUM_GUARDRAIL_REWRITES * 2) + 10
    
    # CRITICAL: The guardrail subgraph also has a checkpointer, so it needs the same config structure
    # with 'configurable.thread_id'. We construct this from the state's thread_id.
    # The config must include both the checkpointer requirements AND recursion_limit
    guardrail_config = {
        'configurable': {
            'thread_id': state['thread_id']  # Required by checkpointer
        },
        'recursion_limit': recursion_limit  # Required for loop prevention
    }
    
    result = await guardrail_graph.ainvoke(
        guardrail_state,
        config=guardrail_config
    )
    validated_response = result.get('validated_response', "") # TODO: reasonable fallback

    return {
        'messages': [AIMessage(content=[{'type': 'text', 'text': validated_response}])]
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
            SmsIntent.CHAT: 'sms_respond',
            SmsIntent.ESCALATION: 'sms_escalation_request_respond',
            SmsIntent.UNCLEAR: 'sms_out_of_scope_respond'
        }
    )

    builder.add_edge('sms_respond', 'sms_post_validate')
    builder.add_edge('sms_post_validate', 'sms_message_post_script_respond')

    builder.add_edge('sms_escalation_request_respond', END)
    builder.add_edge('sms_out_of_scope_respond', END)
    builder.add_edge('sms_message_post_script_respond', END)

    # Web Path

    builder.add_conditional_edges(
        'web_post_channel_router_passthrough',
        web_intent_router,
        {
            WebIntent.CHAT: 'web_respond',
            WebIntent.ESCALATION: 'web_escalation_request_respond',
            WebIntent.UNCLEAR: 'web_out_of_scope_respond'
        }
    )

    builder.add_edge('web_respond', 'web_post_validate')
    builder.add_edge('web_post_validate', 'web_message_post_script_respond')

    builder.add_edge('web_escalation_request_respond', END)
    builder.add_edge('web_out_of_scope_respond', END)
    builder.add_edge('web_message_post_script_respond', END)

    return builder
