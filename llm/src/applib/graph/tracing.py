"""
LangSmith tracing for the LangGraph flow.

Requires env: LANGSMITH_TRACING=true, LANGSMITH_API_KEY.
Optional: LANGSMITH_PROJECT, LANGSMITH_WORKSPACE_ID.

Future evals: When adding datasets, pass run_tree or config to LangSmith client
(e.g. dataset run_id in config) without changing this module's API.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from langsmith import traceable


__all__ = ["traceable", "traced_astream_events", "traced_ainvoke"]

"""
    For non-streaming endpoints, while generating summary, we need to trace the flow.
"""
@traceable(name="chat_graph_invoke", run_type="chain")
async def traced_ainvoke(graph: Any, input_state: dict, config: dict = {}) -> Any:
    """Run graph.ainvoke under a single LangSmith trace (e.g. for sync/SSE-style endpoints)."""
    return await graph.ainvoke(input_state, config=config)

"""
    For streaming endpoints, while performig chat conversation, we need to trace the flow.
"""
@traceable(
    name="chat_graph_stream",
    run_type="chain",
)
async def traced_astream_events(
    graph: Any,
    input_state: dict,
    config: dict,
    *,
    version: str = "v2",
) -> AsyncIterator[dict]:
    """
    Stream graph events under a single LangSmith trace so the entire run is visible.

    Use in consumers: async for event in traced_astream_events(graph, input_state, config): ...
    """
    async for event in graph.astream_events(
        input_state,
        config=config,
        version=version,
    ):
        yield event