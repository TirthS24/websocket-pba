from pathlib import Path
import json

from langchain_core.messages import HumanMessage

from ws_server.applib.models.api import ChatRequest


def load_json(path: str | Path) -> dict:
    with open(path) as f_in:
        j = json.load(f_in)
    return j

def get_postgres_conn_string(user: str, password: str, database_name: str, host: str = None, port: str = None, sslmode: str = 'disable'):
    host = host or 'localhost'
    port = port or '5432'

    return f"postgresql://{user}:{password}@{host}:{port}/{database_name}"


def create_state_from_chat_request(request: ChatRequest) -> dict:
    """Build initial graph state from a ChatRequest.

    Only request fields (message, thread_id, channel, invoice) are taken from the
    request. State-only fields (task, data, context) are not part of the API and
    are set to None here; the graph sets task from intent and builds context from
    data in add_data_context. To pass task/data/context from the client, add them
    as optional fields on ChatRequest and include them below.
    """
    return {
        'thread_id': request.thread_id,
        'messages': [HumanMessage(content=request.message)],
        'channel': request.channel,
        'task': None,
        'data': None,
        'context': None,
    }
