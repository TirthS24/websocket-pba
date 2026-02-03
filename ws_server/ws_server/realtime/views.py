"""
Django async views for non-streaming HTTP endpoints.
"""

import json
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import ensure_csrf_cookie, csrf_protect
from django.utils.decorators import method_decorator
from django.middleware.csrf import get_token
from ws_server.applib.graph.graph_manager import get_graph
from ws_server.applib.llms import get_bedrock_converse_model
from ws_server.applib.prompts.templates import JinjaEnvironments
from ws_server.applib.prompts import prompts
from ws_server.applib.config import config
from langchain_core.messages import AnyMessage, SystemMessage, HumanMessage
from ws_server.realtime.serializers import SummarizeRequest, ThreadHistoryRequest


async def get_message_history(thread_id: str) -> list[AnyMessage]:
    """Get message history for a thread."""
    graph = await get_graph()
    graph_config = {'configurable': {'thread_id': thread_id}}
    history = graph.aget_state_history(graph_config)

    all_messages: list[AnyMessage] = []

    async for snapshot in history:
        messages = snapshot.values['messages']
        all_messages.extend(messages)
        break

    return all_messages


async def summarize_thread(thread_id: str) -> str:
    """Summarize thread history."""
    llm = get_bedrock_converse_model(model_id=config.BEDROCK_MODEL_ID_THREAD_SUMMARIZE)
    jinja_env = JinjaEnvironments.thread
    template = jinja_env.get_template("chat_history.jinja")
    message_history = await get_message_history(thread_id)
    rendered_history = template.render(history=message_history)

    messages = [
        SystemMessage(prompts.thread_summary.system),
        HumanMessage(prompts.thread_summary.user.format(history=rendered_history))
    ]

    response = await llm.ainvoke(messages)
    return response.content


@csrf_protect
@require_http_methods(["POST"])
async def summarize_thread_view(request):
    """POST /api/thread/summarize - Summarize thread history."""
    try:
        body = json.loads(request.body)
        request_data = SummarizeRequest(**body)
        
        summary = await summarize_thread(request_data.thread_id)
        
        return JsonResponse({
            "thread_id": request_data.thread_id,
            "summary": summary
        })
    except json.JSONDecodeError:
        return JsonResponse({"detail": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"detail": str(e)}, status=500)


@csrf_protect
@require_http_methods(["POST"])
async def thread_history_view(request):
    """POST /api/thread/history - Get thread message history."""
    try:
        body = json.loads(request.body)
        request_data = ThreadHistoryRequest(**body)
        
        history = await get_message_history(request_data.thread_id)
        messages = list(map(lambda msg: msg.model_dump(include=['content', 'type']), history))
        all_messages: list[dict[str, str]] = []
        
        for message in messages:
            if message['type'] == 'ai':
                message = {
                    'type': message['type'],
                    'content': message['content'][0]['text']
                }
            all_messages.append(message)

        return JsonResponse({
            'thread_id': request_data.thread_id,
            'messages': all_messages
        })
    except json.JSONDecodeError:
        return JsonResponse({"detail": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"detail": str(e)}, status=500)
