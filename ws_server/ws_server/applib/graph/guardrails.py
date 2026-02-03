from ws_server.applib.config import config
from ws_server.applib.helpers import get_postgres_conn_string
from ws_server.applib.prompts import prompts
from ws_server.applib.types import Channel
from enum import Enum
from functools import partial
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field
from typing import Annotated, Optional
from typing_extensions import TypedDict

import operator


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
    rewrite_attempts: Annotated[int, operator.add]
    max_rewrites: int
    issues: Optional[list[str]]
    channel: Channel

def entry_passthrough(state: GuardrailState) -> dict:
    """Passthrough state to serve as target for rewrite loop"""
    return {}

def _evaluate_response(state: GuardrailState, system_message: str, structured_output: BaseModel) -> dict:
    """response evaluation base function"""
    pass

evaluate_response_sms = partial(_evaluate_response, system_message="", structured_output="")
evaluate_response_web = partial(_evaluate_response, system_message="", structured_output="")

def _rewrite_response(state: GuardrailState, system_message: str) -> dict:
    """response rewrite base function"""
    # Increment rewrite_attempts counter to prevent infinite loops
    # The operator.add annotation will add this value to the current state
    return {"rewrite_attempts": 1}

rewrite_response_sms = partial(_rewrite_response, system_message="")
rewrite_response_web = partial(_rewrite_response, system_message="")

def finalize_valid(state: GuardrailState) -> dict:
    pass

def finalize_fallback(state: GuardrailState) -> dict:
    pass


def post_evaluation_router(state: GuardrailState) -> ValidationRoute:
    if state.get('is_valid', False):
        return ValidationRoute.FINALIZE_VALID

    attempts = state.get('rewrite_attempts', 0)
    max_rewrites = state.get('max_rewrites', 3)

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
