#!/bin/sh
set -e
# Run ws_server (Daphne) and LLM (uvicorn) in the same container. Both must stay up.
cd /app/ws_server
daphne -b 0.0.0.0 -p 8000 ws_server.asgi:application &
cd /app/llm
python -m uvicorn index:app --host 0.0.0.0 --port 7980 &
# Wait for all background jobs; if either exits, we exit and ECS restarts the task
wait
