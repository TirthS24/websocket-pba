from ws_server.applib.code_guidance import add_guidance_to_claim_adjustments
from ws_server.applib.config import config
from ws_server.applib.llms import get_bedrock_converse_model
from ws_server.applib.models.claim import Claim
from ws_server.applib.models.patient import Patient
from ws_server.applib.models.payment import Payment
from ws_server.applib.models.practice import Practice
from ws_server.applib.prompts import chat as chat_prompts
from ws_server.applib.prompts.templates import JinjaEnvironments
from ws_server.applib.state import StateContext, State
from ws_server.applib.textcontent import static_messages


from functools import partial
from langchain_core.messages import SystemMessage, AIMessage, HumanMessage
from langgraph.graph import StateGraph, START, END
from pydantic import BaseModel, Field

class EscalationDetection(BaseModel):
    """Classification result for escalation detection"""
    should_escalate: bool = Field("True if the user wants to speak with a human agent")

class IntentDetection(BaseModel):
    """Classification result for non-escalation intent"""
    intent: IntentRoute = Field("""The intent of the user's query; one of: ('claim_chat', 'payments_chat', 'summarize_claim', 'summarize_payment', 'unclear')""")

escalation_llm = (
    get_bedrock_converse_model(model_id=config.BEDROCK_MODEL_ID_ESCALATION_DETECTION)
    .with_structured_output(EscalationDetection)
)

intent_detection_llm = (
    get_bedrock_converse_model(model_id=config.BEDROCK_MODEL_ID_INTENT_DETECTION)
    .with_structured_output(IntentDetection)
)

claim_summarization_llm = get_bedrock_converse_model(model_id=config.BEDROCK_MODEL_ID_THREAD_SUMMARIZE)
response_llm = get_bedrock_converse_model(model_id=config.BEDROCK_MODEL_ID_CLAIM_AGENT)


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

escalation_request_static_respond_sms = partial(_static_respond, static_message=static_messages.escalation_request.sms)
escalation_request_static_respond_web = partial(_static_respond, static_message=static_messages.escalation_request.web)

out_of_scope_static_respond_sms = partial(_static_respond, static_message=static_messages.out_of_scope.sms)
out_of_scope_static_respond_web = partial(_static_respond, static_message=static_messages.out_of_scope.web)

message_post_script_static_respond_sms = partial(_static_respond, static_message=static_messages.message_post_script.sms)
message_post_script_static_respond_web = partial(_static_respond, static_message=static_messages.message_post_script.web)


async def detect_escalation(state: State) -> dict:
    """Detect if the user wants to escalate to a human agent."""
    messages = [
        SystemMessage(content=chat_prompts.ESCALATION_DETECTION_SYSTEM_PROMPT),
        *state['messages']
    ]
    result: EscalationDetection = await escalation_llm.ainvoke(messages)
    return {"should_escalate": result.should_escalate}


async def intent_router(state: State) -> dict:
    task = state.get('task') # Check for explicit FE request for task

    if task == 'summarize_claim':
        state['intent_route'] = 'claim'
    elif task == 'summarize_payments':
        state['intent_route'] = 'payments'

    else: # No explicit FE request for task
        messages = [
            SystemMessage(content=chat_prompts.INTENT_DETECTION_SYSTEM_PROMPT),
            *state['messages']
        ]
        result: IntentDetection = await intent_detection_llm.ainvoke(messages)

        # General claim or payment query
        if result.intent == "claim_chat":
            state['intent_route'] = 'claim'
        if result.intent == "payments_chat":
            state['intent_route'] = 'payments'

        # User indicates they want a summary of claim or payments without explicit signal from FE
        if result.intent == "summarize_claim":
            state['intent_route'] = 'claim'
            state['task'] = 'summarize_claim'
        if result.intent == "summarize_payments":
            state['intent_route'] = 'payments'
            state['task'] = 'summarize_payments'

        # Unclear intent or unsupported intent response
        if result.intent == "unclear":
            state['intent_route'] = 'unclear'

        else:
            state['intent_route'] = 'unclear'

    return state


def add_data_context(state: State) -> dict:

    data = state.get('data')
    context = state.get('context')

    def _has_context() -> bool:
        if context is None:
            return False
        return any((
            context.current_practice,
            context.current_patient,
            context.current_claims,
            context.current_payments
        ))


    if _has_context() or not data:
        return {}

    context_data = {}

    practice: Practice = data[0]
    context_data['current_practice'] = practice

    if not practice.patients:
        return {'context': StateContext.model_validate(context_data)}

    patient: Patient = practice.patients[0]
    context_data['current_patient'] = patient

    if patient.claims:
        context_data['current_claims'] = patient.claims

    if patient.patient_payments:
        context_data['current_payments'] = patient.patient_payments

    return {'context': StateContext.model_validate(context_data)}


async def summarize_claim(state: State) -> dict:

    state_context = state['context']
    claim_template = JinjaEnvironments.claim.get_template("claim.jinja")
    claim_renders: list[str] = []

    for claim in state_context.current_claims:
        add_guidance_to_claim_adjustments(claim)
        for claim_835 in claim.edi_mappings:
            render = claim_template.render(claim=claim_835, render_services=True, render_adjustments=True)
            claim_renders.append(render)

    claim_context = f"\n\n{''*50}\n\n".join(claim_renders)
    messages = [
        SystemMessage(chat_prompts.SUMMARIZE_CLAIM_SYSTEM_PROMPT),
        HumanMessage(claim_context)
    ]

    response = await response_llm.ainvoke(messages)

    return {"messages": [response]}

    # TODO: summarize_claim LLM SYSTEM PROMPT

async def claim_respond(state: State) -> dict:
    pass
    # TODO: claim_respond LLM call


async def unclear_respond(state: State) -> dict:
    pass
    # TODO: unclear_respond LLM call

async def guardrail(state: State) -> dict:
    pass
    # TODO: guardrail LLM call


async def respond(state: State) -> dict:
    """Generate a response to the user's query.

    Note: This node updates state with the AI response.
    Streaming is handled at the API layer via astream_events."""

    messages = [
        SystemMessage(content=chat_prompts.RESPOND_SYSTEM_PROMPT),
        *state['messages']
    ]

    response = await response_llm.ainvoke(messages)

    return {"messages": [response]}


def get_graph_builder() -> StateGraph:

    graph_builder = StateGraph(State)

    #### NODES ####
    # routers
    graph_builder.add_node('detect_escalation', detect_escalation)
    graph_builder.add_node('intent_router', intent_router)

    # build context for state
    graph_builder.add_node('add_data_context', add_data_context)

    # summarizers
    graph_builder.add_node('summarize_claim', summarize_claim)

    # responders
    graph_builder.add_node('escalation_respond', escalation_respond)
    graph_builder.add_node('claim_respond', claim_respond)
    graph_builder.add_node('unclear_respond', unclear_respond)

    # guardrail / copy editor
    graph_builder.add_node('guardrail', guardrail)


    #### EDGES ####

    """
    Detect escalation message
    If escalation, proceed to `escalation_respond` which will use canned escalation message and alert FE via SSE
    If no escalation, proceed to `intent_router`
    """
    graph_builder.add_edge(START, 'detect_escalation')
    graph_builder.add_conditional_edges(
        'detect_escalation',
        lambda state: state['should_escalate'],
        {True: 'escalation_respond', False: 'add_data_context'}
    )
    graph_builder.add_edge('escalation_respond', END)
    graph_builder.add_edge('add_data_context', 'intent_router')


    """
    Route non-escalation messages to appropriate node
    """
    graph_builder.add_conditional_edges(
        'intent_router',
        lambda state: state['intent_route'],
        {
            'claim': 'summarize_claim',
            'payments': 'summarize_payments',
            'unclear': 'unclear_respond'
        }
    )

    """
    If explicit summary `task` exists in state, proceed directly to guardrail
    Otherwise, proceed to a `respond` node which will use the respective summary 
    """
    graph_builder.add_conditional_edges(
        'summarize_claim',
        lambda state: state.get('task') == 'summarize_claim',
        {True: 'guardrail', False: 'claim_respond'}
    )


    """
    Hook up responders to guardrail
    Hook up guardrail to END node 
    """

    graph_builder.add_edge('claim_respond', 'guardrail')
    graph_builder.add_edge('payment_respond', 'guardrail')
    graph_builder.add_edge('unclear_respond', 'guardrail')
    graph_builder.add_edge('guardrail', END)

    return graph_builder
