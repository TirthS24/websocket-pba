# ws_server (Django Channels WebSocket Server)

Production-oriented Django + Channels (ASGI) WebSocket server designed to run behind **AWS ALB + Auto Scaling Group** with a shared **Redis (ElastiCache)** backend.

## Run with Docker Compose

From the repo root:

```bash
docker compose up --build
```

## Features

- **Django + Channels (ASGI)** via Daphne (no `runserver` in production)
- **RedisChannelLayer** (not in-memory) for cross-instance messaging
- WebSocket endpoint: **`/ws/session/<session_id>/`**
- **Multiple concurrent connections** can subscribe to the same `session_id` (fan-out/broadcast)
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

### Test WebSocket

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

1) Create venv + install:

```bash
cd /Users/nileshs/Documents/ws_server
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -r requirements.txt
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

For local docker-compose, these are set in `docker-compose.yml`.

For EC2/systemd deployments, provide them via an EnvironmentFile.

- `DJANGO_SECRET_KEY`: required (production must not use a dev placeholder)
- `DJANGO_DEBUG`: `0` or `1`
- `DJANGO_ALLOWED_HOSTS`: comma-separated (e.g. `example.com,internal-alb-dns`)
- `REDIS_URL`: e.g. `redis://<elasticache-endpoint>:6379/0`
- `INSTANCE_ID`: EC2 instance id or other identifier (useful for diagnostics)
- `DATABASE_URL`: optional (service can run without a database)

## Production run command (no `runserver`)

```bash
daphne -b 0.0.0.0 -p 8000 ws_server.asgi:application
```

## Deployment

See `docs/TECHNICAL.md` for:

- ALB target group settings (health checks, timeouts)
- Security group rules (instance + Redis)
- Scaling notes and cross-instance messaging behavior

