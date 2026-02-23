from applib.config import config
from applib.graph.structured_outputs import GuardrailEvaluation
from applib.helpers import get_postgres_conn_string, message_content_str
from applib.llms import get_bedrock_converse_model
from applib.prompts import prompts
from applib.types import Channel
from enum import Enum
from functools import partial
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from typing import Annotated, Optional
from typing_extensions import TypedDict
import logging
import operator

logger = logging.getLogger(__name__)

class ValidationRoute(Enum):
    REWRITE = 'rewrite_response'
    FINALIZE_VALID = 'finalize_valid'
    FINALIZE_FALLBACK = 'finalize_fallback'

class GuardrailState(TypedDict):
    thread_id: str
    user_query: str
    response_to_check: str
    validated_response: Optional[str]
    rewrite_attempts: Annotated[int, operator.add]
    max_rewrites: int
    channel: Channel
    # Metrics from structured output (evaluate node sets these)
    no_markdown: Optional[bool]
    is_concise: Optional[bool]
    no_pii: Optional[bool]
    no_payment_promises: Optional[bool]
    is_appropriate: Optional[bool]

def entry_passthrough(state: GuardrailState) -> dict:
    """Passthrough state to serve as target for rewrite loop"""
    return {}


_METRIC_LABELS: list[tuple[str, str]] = [
    ("no_markdown", "Uses markdown"),
    ("is_concise", "Not concise"),
    ("no_pii", "Contains PII"),
    ("no_payment_promises", "Makes payment plan promise"),
    ("is_appropriate", "Not appropriate"),
]


def _issues_from_state(state: GuardrailState) -> list[str]:
    """Build issues list from failed metrics in state (for rewrite step)."""
    return [
        label
        for key, label in _METRIC_LABELS
        if state.get(key) is False
    ]


def _all_metrics_passed_from_state(state: GuardrailState) -> bool:
    """True only when every metric in state is true."""
    return all(state.get(key, True) for key, _ in _METRIC_LABELS)


async def _evaluate_response(state: GuardrailState, channel_suffix: str) -> dict:
    """Evaluate whether the assistant response is appropriate, needs change, or is acceptable."""

    channel_prompts = getattr(prompts.guardrails.evaluate_response, channel_suffix)
    system_content = channel_prompts.system
    structured_output_node = getattr(channel_prompts, "structured_output", None)
    if structured_output_node is not None and hasattr(structured_output_node, "system"):
        system_content = system_content + "\n\n" + structured_output_node.system
    user_template = channel_prompts.user
    user_content = user_template.format(
        user_query=state["user_query"],
        response_to_check=state["response_to_check"],
    )

    model_id = (
        config.BEDROCK_MODEL_ID_SMS_RESPOND
        if channel_suffix == "sms"
        else config.BEDROCK_MODEL_ID_WEB_RESPOND
    )

    llm = (
        get_bedrock_converse_model(model_id=model_id)
        .with_structured_output(GuardrailEvaluation)
    )

    messages = [SystemMessage(content=system_content), HumanMessage(content=user_content)]

    result: GuardrailEvaluation = await llm.ainvoke(messages)
    logger.info(f"Guardrail evaluation result: {result}")

    # result_flags: GuardrailState = {k: getattr(result, k) for k, _ in _METRIC_LABELS}
    # if not _all_metrics_passed_from_state(result_flags):
    #     failed = _issues_from_state(result_flags)
    #     logger.info(
    #         "Guardrail: response needs rewrite (%s metric(s) failed): %s",
    #         len(failed),
    #         failed[:5] if len(failed) > 5 else failed,
    #     )

    return {
        "no_markdown": result.no_markdown,
        "is_concise": result.is_concise,
        "no_pii": result.no_pii,
        "no_payment_promises": result.no_payment_promises,
        "is_appropriate": result.is_appropriate,
    }


evaluate_response_sms = partial(_evaluate_response, channel_suffix="sms")
evaluate_response_web = partial(_evaluate_response, channel_suffix="web")

async def _rewrite_response(state: GuardrailState, channel_suffix: str) -> dict:
    """Rewrite the response using the LLM; the corrected response is re-evaluated on the next loop."""
    channel_prompts = getattr(prompts.guardrails.rewrite_response, channel_suffix)
    system_content = channel_prompts.system
    structured_output_node = getattr(channel_prompts, "structured_output", None)
    if structured_output_node is not None and hasattr(structured_output_node, "system"):
        system_content = system_content + "\n\n" + structured_output_node.system
    issues_str = "\n".join(f"- {i}" for i in _issues_from_state(state))
    user_content = channel_prompts.user.format(
        user_query=state["user_query"],
        response_to_check=state["response_to_check"],
        issues=issues_str,
    )
    model_id = config.BEDROCK_MODEL_ID_SMS_RESPOND if channel_suffix == "sms" else config.BEDROCK_MODEL_ID_WEB_RESPOND
    llm = get_bedrock_converse_model(model_id=model_id)
    messages = [SystemMessage(content=system_content), HumanMessage(content=user_content)]
    response = await llm.ainvoke(messages)
    rewritten = message_content_str(response, list_separator="")
    if rewritten:
        logger.info(
            "Guardrail: applied LLM rewrite (%s chars); will re-evaluate.",
            len(rewritten),
        )
    return {
        "response_to_check": rewritten,
        "validated_response": rewritten,
        "rewrite_attempts": 1,
    }

rewrite_response_sms = partial(_rewrite_response, channel_suffix="sms")
rewrite_response_web = partial(_rewrite_response, channel_suffix="web")

def finalize_valid(state: GuardrailState) -> dict:
    """Accept the current response as valid and use it as the final output."""
    return {"validated_response": state["response_to_check"]}

def finalize_fallback(state: GuardrailState) -> dict:
    """Max rewrites reached; return the last generated message as-is."""
    return {"validated_response": state["response_to_check"]}

def post_evaluation_router(state: GuardrailState) -> ValidationRoute:
    if _all_metrics_passed_from_state(state):
        return ValidationRoute.FINALIZE_VALID

    attempts = state.get("rewrite_attempts", 0)
    max_rewrites = state.get("max_rewrites", 2)
    if attempts >= max_rewrites:
        return ValidationRoute.FINALIZE_FALLBACK

    return ValidationRoute.REWRITE


def get_guardrail_subgraph_builder() -> StateGraph:

    builder = StateGraph(GuardrailState)

    ### NODES ###

    builder.add_node('entry_passthrough', entry_passthrough)
    builder.add_node('evaluate_response_sms', evaluate_response_sms)
    builder.add_node('evaluate_response_web', evaluate_response_web)
    builder.add_node('rewrite_response_sms', rewrite_response_sms)
    builder.add_node('rewrite_response_web', rewrite_response_web)
    builder.add_node('finalize_valid', finalize_valid)
    builder.add_node('finalize_fallback', finalize_fallback)

    ### EDGES ###

    builder.add_edge(START, 'entry_passthrough')

    builder.add_conditional_edges(
        'entry_passthrough',
        lambda state: Channel(state['channel']),
        {
            Channel.SMS: 'evaluate_response_sms',
            Channel.WEB: 'evaluate_response_web'
        }
    )

    builder.add_conditional_edges(
        'evaluate_response_sms',
        post_evaluation_router,
        {
            ValidationRoute.REWRITE: 'rewrite_response_sms',
            ValidationRoute.FINALIZE_VALID: 'finalize_valid',
            ValidationRoute.FINALIZE_FALLBACK: 'finalize_fallback'
        }
    )

    builder.add_conditional_edges(
        'evaluate_response_web',
        post_evaluation_router,
        {
            ValidationRoute.REWRITE: 'rewrite_response_web',
            ValidationRoute.FINALIZE_VALID: 'finalize_valid',
            ValidationRoute.FINALIZE_FALLBACK: 'finalize_fallback'
        }
    )

    builder.add_edge('rewrite_response_sms', 'entry_passthrough')
    builder.add_edge('rewrite_response_web', 'entry_passthrough')

    builder.add_edge('finalize_valid', END)
    builder.add_edge('finalize_fallback', END)

    return builder

class GuardrailGraphManager:
    """Manages the graph lifecycle"""
    def __init__(self):
        self._graph = None
        self._checkpointer = None
        self._checkpointer_context = None
        self._db_uri = get_postgres_conn_string(
            user=config.PSQL_BOT_USERNAME,
            password=config.PSQL_BOT_PASSWORD,
            host=config.PSQL_HOST,
            port=config.PSQL_PORT,
            database_name=config.PSQL_STATE_DATABASE
        )

    async def initialize_graph(self) -> None:
        if self._graph is None:
            self._checkpointer_context = AsyncPostgresSaver.from_conn_string(self._db_uri)
            self._checkpointer = await self._checkpointer_context.__aenter__()
            await self._checkpointer.setup()
            graph_builder = get_guardrail_subgraph_builder()
            self._graph = graph_builder.compile(checkpointer=self._checkpointer)

    async def shutdown(self) -> None:
        if self._checkpointer_context is not None:
            await self._checkpointer_context.__aexit__(None, None, None)


    @property
    def graph(self) -> CompiledStateGraph:
        if self._graph is None:
            raise RuntimeError("Graph not initialized. Call `initialize_graph()` first.")
        return self._graph

    def graph_initialized(self) -> bool:
        return self._graph is not None

    def checkpointer_initialized(self) -> bool:
        return self._checkpointer is not None

guardrail_graph_manager = GuardrailGraphManager()

async def get_guardrail_graph() -> CompiledStateGraph:
    await guardrail_graph_manager.initialize_graph()
    return guardrail_graph_manager.graph
