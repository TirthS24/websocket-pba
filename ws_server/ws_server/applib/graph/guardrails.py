from ws_server.applib.config import config
from ws_server.applib.graph.structured_outputs import GuardrailEvaluation
from ws_server.applib.helpers import get_postgres_conn_string, message_content_str
from ws_server.applib.llms import get_bedrock_converse_model
from ws_server.applib.prompts import prompts
from ws_server.applib.types import Channel
from enum import Enum
from functools import partial
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from pydantic import ValidationError
from typing import Annotated, Optional
from typing_extensions import TypedDict
import json
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
    is_valid: bool
    needs_rewrite: bool
    rewrite_attempts: Annotated[int, operator.add]
    max_rewrites: int
    issues: Optional[list[str]]
    channel: Channel

def entry_passthrough(state: GuardrailState) -> dict:
    """Passthrough state to serve as target for rewrite loop"""
    return {}


def _extract_first_json_object(text: str) -> Optional[str]:
    """Find the first { ... } and return it (brace-balanced). Handles nested structures."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _parse_guardrail_evaluation(raw_text: str) -> Optional[GuardrailEvaluation]:
    """Parse GuardrailEvaluation from raw LLM text (JSON object, optionally inside markdown)."""
    if not raw_text:
        return None
    text = raw_text.strip()
    # Try direct parse first
    try:
        data = json.loads(text)
        return GuardrailEvaluation.model_validate(data)
    except (json.JSONDecodeError, ValidationError):
        pass
    # Extract first JSON object (e.g. from markdown code block or surrounding text)
    json_str = _extract_first_json_object(text)
    if json_str:
        try:
            data = json.loads(json_str)
            return GuardrailEvaluation.model_validate(data)
        except (json.JSONDecodeError, ValidationError):
            pass
    return None

async def _evaluate_response(state: GuardrailState, channel_suffix: str) -> dict:
    """Evaluate whether the assistant response is appropriate, needs change, or is acceptable."""
    channel_prompts = getattr(prompts.guardrails.evaluate_response, channel_suffix)
    system_content = channel_prompts.system
    user_template = channel_prompts.user
    user_content = user_template.format(
        user_query=state["user_query"],
        response_to_check=state["response_to_check"],
    )
    model_id = config.BEDROCK_MODEL_ID_SMS_RESPOND if channel_suffix == "sms" else config.BEDROCK_MODEL_ID_WEB_RESPOND
    llm = get_bedrock_converse_model(model_id=model_id)
    messages = [SystemMessage(content=system_content), HumanMessage(content=user_content)]
    response = await llm.ainvoke(messages)
    raw_text = message_content_str(response, list_separator="")
    result = _parse_guardrail_evaluation(raw_text)
    if result is None:
        logger.warning(
            "Guardrail evaluation returned None (model may have returned invalid structured output); treating as fail."
        )
        return {
            "is_valid": False,
            "needs_rewrite": True,
            "issues": ["Evaluation could not be completed."],
        }
    needs_rewrite = not result.passes
    if needs_rewrite:
        issues = result.issues or []
        logger.info(
            "Guardrail: response needs rewrite (%s issue(s)): %s",
            len(issues),
            issues[:5] if len(issues) > 5 else issues,
        )
    return {
        "is_valid": result.passes,
        "needs_rewrite": needs_rewrite,
        "issues": result.issues or [],
    }

evaluate_response_sms = partial(_evaluate_response, channel_suffix="sms")
evaluate_response_web = partial(_evaluate_response, channel_suffix="web")

async def _rewrite_response(state: GuardrailState, channel_suffix: str) -> dict:
    """Rewrite the response using the LLM; the corrected response is re-evaluated on the next loop."""
    channel_prompts = getattr(prompts.guardrails.rewrite_response, channel_suffix)
    system_content = channel_prompts.system
    issues_str = "\n".join(f"- {i}" for i in (state.get("issues") or []))
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
    if state.get("is_valid", False):
        return ValidationRoute.FINALIZE_VALID

    needs_rewrite = state.get("needs_rewrite", False)
    if not needs_rewrite:
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
