from langgraph.graph.state import CompiledStateGraph

from ws_server.applib.config import config
from ws_server.applib.helpers import get_postgres_conn_string
from ws_server.applib.graph.nodes import get_graph_builder
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
            try:
                print(f"Connecting to PostgreSQL at {config.PSQL_HOST}:{config.PSQL_PORT}/{config.PSQL_STATE_DATABASE}...")
                self._checkpointer_context = AsyncPostgresSaver.from_conn_string(self._db_uri)
                self._checkpointer = await self._checkpointer_context.__aenter__()
                print("PostgreSQL connection established, setting up checkpointer...")
                await self._checkpointer.setup()
                print("Checkpointer setup complete, compiling graph...")
                graph_builder = get_graph_builder()
                self._graph = graph_builder.compile(checkpointer=self._checkpointer)
                print("Graph compiled successfully")
            except Exception as e:
                print(f"ERROR in graph initialization: {type(e).__name__}: {e}")
                import traceback
                traceback.print_exc()
                raise

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