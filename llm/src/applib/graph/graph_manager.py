from langgraph.graph.state import CompiledStateGraph

from applib.config import config
from applib.helpers import get_postgres_conn_string
from applib.graph.nodes import get_graph_builder
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

class GraphManager:
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
            graph_builder = get_graph_builder()
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

graph_manager = GraphManager()

async def get_graph() -> CompiledStateGraph:
    await graph_manager.initialize_graph()
    return graph_manager.graph