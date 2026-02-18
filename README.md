# ws_server (Django Channels WebSocket Server)

Production-oriented Django + Channels (ASGI) WebSocket server designed to run behind **AWS ALB + Auto Scaling Group**.

## Installing dependencies using uv package manager
echo 'export PATH="$HOME/.cargo/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc

```bash
uv --version
```

```bash
uv pip install -r requirements.txt
```

## Run with Docker Compose

From the repo root:

```bash
docker compose up --build
```

## Features

- **Django + Channels (ASGI)** via Daphne (no `runserver` in production)
- **RedisChannelLayer** (not in-memory) for cross-instance messaging
- **LangGraph chatbot** with streaming WebSocket support
- WebSocket endpoints:
  - **`/ws/chat/`** - Chat streaming with LangGraph
  - **`/ws/session/<session_id>/`** - Multi-subscriber session streams
- HTTP endpoints:
  - **`POST /api/thread/summarize`** - Summarize thread history
  - **`POST /api/thread/history`** - Get thread message history
  - **`GET /api/csrf-token/`** - Get CSRF token for API requests
- **CSRF protection** for all API endpoints
- **Session management**: `session_id` and `thread_id` are synchronized (same value)
- **Presence**: query how many connections are connected to a `session_id` + their `user_type` and `client_type`
- Health check endpoint: **`/health/`** (for ALB target groups)
- Dockerized local run (app + Redis)

## Repository layout

- `ws_server/`:
  - `manage.py`
  - `ws_server/` (Django project): `settings.py`, `asgi.py`, `urls.py`, `health.py`, `routing.py`
  - `realtime/` (Django app): `consumers.py`, `routing.py`
- `deploy/`: systemd unit example for Daphne (production)
- `docs/TECHNICAL.md`: architecture & deployment notes

## Quickstart (Docker, recommended)

From the repo root:

```bash
cd /Users/nileshs/Documents/ws_server
docker compose up --build
```

### Test HTTP health check

```bash
curl http://127.0.0.1:8000/health/
```

### Test WebSocket Chat

Connect to chat endpoint:

- `ws://127.0.0.1:8000/ws/chat/`

#### Test via browser

Open `docs/ws_test.html` in your browser, set the URL to `ws://127.0.0.1:8000/ws/chat/` and click **Connect** → **Send**.

- If `AUTH_API_KEY` is enabled on the server, append `?authorization=<API_KEY>` to the URL.
- If you pick `channel=web`, the server requires the `data` field (the test page includes a default demo payload).

Send chat message (thread_id is optional, will be generated if not provided):

```json
{
  "type": "chat",
  "message": "Hello, how can you help?",
  "thread_id": "optional-thread-id",
  "channel": "web",
  "data": []
}
```

The server will respond with:
- `{"type": "session_started", "session_id": "...", "thread_id": "...", "connection_id": "..."}`
- `{"type": "token", "content": "..."}` - Streaming tokens
- `{"type": "escalation", "should_escalate": true}` - If escalation detected
- `{"type": "end"}` - When streaming completes
- When escalation is detected, the server sends the escalation message and `{"type": "end"}`, then closes the WebSocket gracefully (code `1000`, reason `"escalation"`) so the conversation ends; the client can handle the close and e.g. show "conversation ended" or reconnect.

### Test Session WebSocket

Connect:

- `ws://127.0.0.1:8000/ws/session/11/`

First message must include `user_type` (mandatory):

```json
{"type":"hello","user_type":"admin","client_type":"web"}
```

Broadcast to everyone connected on the same `session_id` (after `hello`):

```json
{"type":"broadcast","msg":"hello"}
```

Get how many connections are on this `session_id` (and their types):

```json
{"type":"presence"}
```

Expected receive on all listeners for broadcast:

```json
{"type":"session_message","user_type":"admin","client_type":"web","msg":"hello","data":null}
```

### Test HTTP Endpoints

Get CSRF token first:

```bash
curl http://127.0.0.1:8000/api/csrf-token/
```

Then use the token in subsequent requests:

```bash
curl -X POST http://127.0.0.1:8000/api/thread/history \
  -H "Authorization: your-api-key" \
  -H "X-CSRFToken: <token-from-previous-request>" \
  -H "Content-Type: application/json" \
  -d '{"thread_id": "test-thread"}'
```

## Local LAN testing (friend on same Wi‑Fi)

If your friend uses `ws://127.0.0.1:8000/...` on their laptop, they are connecting to **their own** machine, not yours.

1) Find your LAN IP on macOS:

```bash
ipconfig getifaddr en0
```

2) Friend connects to YOUR IP:

- `ws://<YOUR_LAN_IP>:8000/ws/session/11/`

3) Ensure your firewall allows inbound **TCP 8000** (or temporarily disable firewall).

## Running without Docker (local)

1) Install dependencies using uv:

```bash
# Install uv if not already installed
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install project dependencies
uv sync
# OR if using requirements.txt
uv pip install -r requirements.txt
```

2) Start Redis locally (pick one):

- Installed redis:

```bash
redis-server
```

- Docker redis:

```bash
docker run --rm -p 6379:6379 redis:7-alpine
```

3) Run Daphne (from the folder that contains `manage.py`):

```bash
cd /Users/nileshs/Documents/ws_server/ws_server
daphne -b 127.0.0.1 -p 8000 ws_server.asgi:application
```

## Environment variables

### Configuration Methods

1. **Local Development (Docker Compose)**: 
   - Create a `.env` file in the project root (copy from `example.env`)
   - Docker Compose automatically loads it via `env_file: - .env`
   - Variables are available to the container at runtime

2. **Production/EC2**: 
   - Set environment variables directly (systemd EnvironmentFile, EC2 launch template, etc.)
   - Do NOT commit `.env` files to version control

### Required Variables

- `DJANGO_SECRET_KEY`: required (production must not use a dev placeholder)
- `DJANGO_DEBUG`: `0` or `1`
- `DJANGO_ALLOWED_HOSTS`: comma-separated (e.g. `example.com,internal-alb-dns`)
- `REDIS_URL`: e.g. `redis://<elasticache-endpoint>:6379/0`
- `INSTANCE_ID`: EC2 instance id or other identifier (useful for diagnostics)
- `CSRF_SECRET_KEY`: CSRF token secret (optional, falls back to SECRET_KEY)
- `AUTH_API_KEY`: API key for endpoint authorization

### LangGraph/Chatbot Variables (see `example.env`)

- PostgreSQL: `PSQL_BOT_USERNAME`, `PSQL_BOT_PASSWORD`, `PSQL_HOST`, `PSQL_PORT`, `PSQL_STATE_DATABASE`, etc.
- AWS Bedrock: `AWS_BEDROCK_REGION`, `BEDROCK_MODEL_ID_*` (various model IDs)
- `MAXIMUM_GUARDRAIL_REWRITES`: Maximum guardrail rewrite attempts
- `APPDATA_FOLDER_PATH`: Path to appdata directory (defaults to `ws_server/ws_server/appdata` if not set)

### How Environment Variables Are Loaded

1. **Django settings.py**: Uses `python-dotenv` to load `.env` from project root
2. **applib/config.py**: Uses `pydantic-settings` which:
   - First checks environment variables (set by docker-compose or system)
   - Then tries to load from `.env` file in multiple locations
   - Priority: Environment variables > .env file

### .env File Location

- **Project root**: `/path/to/websocket-pba/.env` (recommended for local dev)
- Docker Compose automatically loads this file via `env_file: - .env`
- The `.env` file is NOT copied into the Docker image (for security)

## Production run command (no `runserver`)

```bash
daphne -b 0.0.0.0 -p 8000 ws_server.asgi:application
```

## Deployment

See `docs/TECHNICAL.md` for:

- ALB target group settings (health checks, timeouts)
- Security group rules (instance + Redis)
- Scaling notes and cross-instance messaging behavior

