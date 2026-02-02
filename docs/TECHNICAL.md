## Technical documentation (ws_server)

### High-level architecture

- **Protocol**: HTTP (health) + WebSocket (session streams)
- **Compute**: EC2 instances in an Auto Scaling Group
- **Load balancing**: AWS ALB in front of EC2
- **App server**: **Daphne** serving Django **ASGI** application
- **Shared state/messaging**: **Redis (ElastiCache)** for Channels channel layer

This design works without **sticky sessions** because WebSocket fan-out is done using the Redis channel layer.

### Current behavior: multiple connections per `session_id`

The server currently allows **multiple concurrent WebSocket connections** to the same `session_id`.

- Route: `/ws/session/<session_id>/`
- All sockets connected with the same `session_id` join the same Channels group.
- Any client can broadcast to all other clients in that session group.

This is implemented in `ws_server/realtime/consumers.py` via:

- `group_add(group_name, channel_name)` on connect
- `group_discard(...)` on disconnect
- `group_send(...)` to broadcast messages cross-instance

### WebSocket protocol

#### Connect

- URL: `ws://<host>/ws/session/<session_id>/`
- On success the server sends:

```json
{"type":"connected","session_id":"11","connection_id":"...","user_type_required":true}
```

#### Client -> server message types

- **Hello (MANDATORY first message)**: sets `user_type` for the life of this WebSocket connection.

```json
{"type":"hello","user_type":"admin","client_type":"web"}
```

- **Presence**: returns how many are connected to this `session_id` and each connection’s metadata.

```json
{"type":"presence"}
```

- **Broadcast** (fan-out to every listener of the same `session_id`), only allowed after `hello`:

```json
{"type":"broadcast","msg":"hello"}
```

- Anything else is echoed back to the sender:

```json
{"type":"echo","data":{...}}
```

#### Server -> client message types

- Broadcast delivery (includes sender identity):

```json
{"type":"session_message","user_type":"admin","client_type":"web","msg":"hello","data":null}
```

- Presence response:

```json
{
  "type":"presence",
  "session_id":"11",
  "count":2,
  "by_type":{"web":2},
  "members":[
    {"connection_id":"...","user_type":"admin","client_type":"web","connected_at":170..., "last_seen":170...}
  ]
}
```

### Presence tracking (how many connections per session)

Channels groups do not expose membership lists. To support “how many connections are connected to the same session id”, this project stores presence in Redis:

- **Session set**: `ws:presence:session:<session_id>` → set of `connection_id`
- **Connection hash**: `ws:presence:conn:<connection_id>` → `user_type`, `client_type`, timestamps

The connection hash has a TTL so dead instances don’t leave stale entries forever, and a lightweight server-side refresh keeps active connections alive (no client heartbeat messages required).

### Channels / ASGI wiring

#### ASGI entrypoint

- File: `ws_server/ws_server/asgi.py`
- Uses `ProtocolTypeRouter`:
  - `"http"` -> Django ASGI app
  - `"websocket"` -> Channels URLRouter

#### Origin/host validation

In `asgi.py`:

- **Production (`DEBUG=0`)** uses `AllowedHostsOriginValidator` for better security.
- **Local (`DEBUG=1`)** skips it because many dev WS clients do not send an `Origin` header and would otherwise get `403 Access denied`.

### Redis channel layer configuration

File: `ws_server/ws_server/settings.py`

- `CHANNEL_LAYERS["default"]["BACKEND"] = "channels_redis.core.RedisChannelLayer"`
- `hosts = [REDIS_URL]`

**Why RedisChannelLayer**:

- In-memory layers do not work across multiple processes/instances.
- Redis allows group fan-out across your ASG behind an ALB.

### Health check endpoint

- Endpoint: `/health/`
- File: `ws_server/ws_server/health.py`
- Intentionally cheap:
  - no DB queries
  - no Redis calls

Reason: avoid cascading failures during Redis maintenance and keep ALB target registration stable.

### Local development notes

#### Docker

`docker-compose.yml` runs:

- `redis` on `6379`
- `web` on `8000` and sets:
  - `REDIS_URL=redis://redis:6379/0` (container-to-container)

#### Testing with a friend on the same Wi‑Fi

If your friend connects to `ws://127.0.0.1:8000/...` on their machine, they are connecting to themselves.

They must connect to your laptop’s LAN IP:

- `ws://<YOUR_LAN_IP>:8000/ws/session/11/`

Also ensure your OS firewall allows inbound `8000/tcp`.

### AWS ALB configuration notes

#### Recommended setup (typical)

- **TLS termination at ALB**
  - Listener: `443` (HTTPS)
  - Target group: **HTTP** to instances on port `8000`

Why:

- Simpler than end-to-end TLS
- Keeps certificates at ALB (ACM)
- Works well for WebSockets (ALB supports ws/wss)

#### Health checks

- Target group health check path: `/health/`
- Success codes: `200`

#### Idle timeout

WebSockets are long-lived; set ALB **idle timeout** high enough for your usage.

If clients may stay idle for long periods, set a higher timeout (e.g. 120s+), or ensure clients send periodic messages.

### Security notes

#### Instance security group

- Inbound: allow `8000/tcp` only **from the ALB security group**
- Admin access: prefer SSM; if using SSH, restrict `22/tcp` tightly

#### Redis (ElastiCache) security group

- Inbound: allow `6379/tcp` only from the **instance security group**
- No public access

### systemd (EC2) deployment

Example unit file:

- `deploy/ws-server-daphne.service`

Key points:

- Run as non-root user
- Use `EnvironmentFile` for secrets/config
- Run Daphne, not `runserver`

### Note about “single active connection per session_id”

Earlier versions of this project implemented a **single-owner** mapping in Redis (force disconnect old connection on reconnect).

The current version was changed per request to support **multiple listeners** per `session_id`.

If you want to reintroduce single-owner semantics later, you would:

- Maintain an owner record per `session_id` in Redis
- Force close the previous owner via channel layer messaging
- Keep a lease/TTL so dead instances don’t block reconnections

