# Server broadcast (WebSocket)

This document describes how to **send** and **receive** **server messages**: messages that are broadcast to **all active WebSocket connections** on the server, across **all sessions**. Use this when the frontend needs app-wide announcements (e.g. maintenance, global notifications) rather than session-only chat.

---

## Connection

- **URL:** `ws://<host>/ws/session/<session_id>/` (or `wss://` in production)
- **First message (required):** Send a hello with `user_type` so the server accepts further messages:

```json
{"type":"hello","user_type":"patient"}
```

Allowed `user_type` values: `"patient"`, `"operator"`, `"ai"`.

- **Auth (if enabled):** When the server has `AUTH_API_KEY` set, send the key via:
  - Header: `X-API-KEY: <key>`, or
  - Query: `?api_key=<key>`, or
  - Subprotocol: `['x-api-key', '<key>']` (for browser `WebSocket` when you cannot set headers).

---

## Sending a server message (client → server)

To broadcast a message to **all** connected clients (all sessions), send a JSON message with `type: "server"` and either `content` or `msg`:

```json
{"type":"server","content":"Maintenance in 5 minutes"}
```

Or with a string in `msg` (same effect):

```json
{"type":"server","msg":"Maintenance in 5 minutes"}
```

- You must have sent the initial `hello` with `user_type` first; otherwise the server responds with `user_type_required` and may close the connection.
- The **sender does not receive** their own server message back (the server skips delivery to the originating connection).

---

## Receiving a server message (server → client)

When any client sends a server message, **every other** WebSocket connection receives:

```json
{
  "type":"server",
  "content":"Maintenance in 5 minutes",
  "from_session_id":"abc123",
  "from_connection_id":"<connection_id>",
  "from_user_type":"operator"
}
```

| Field               | Description                                      |
|---------------------|--------------------------------------------------|
| `type`              | Always `"server"` for server-wide broadcasts.   |
| `content`           | The broadcast payload (string or as sent).      |
| `from_session_id`   | Session ID of the connection that sent it.     |
| `from_connection_id`| Server-assigned connection ID of the sender.   |
| `from_user_type`    | `user_type` of the sender (e.g. `"operator"`).  |

---

## Frontend example

```javascript
const ws = new WebSocket('wss://your-host/ws/session/my-session-id/');

ws.onopen = () => {
  ws.send(JSON.stringify({ type: 'hello', user_type: 'patient' }));
};

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  if (msg.type === 'connected') {
    // Ready; can send server messages after this (after hello is processed).
  }
  if (msg.type === 'hello_ack') {
    // user_type accepted; safe to send "server" or "broadcast" messages.
  }
  if (msg.type === 'server') {
    console.log('Server broadcast:', msg.content, 'from', msg.from_user_type);
    // e.g. show global notification: msg.content
  }
};

// To broadcast to all sessions (all clients):
function sendServerBroadcast(text) {
  ws.send(JSON.stringify({ type: 'server', content: text }));
}
```

---

## Summary

| Direction   | Message type | Purpose                                      |
|------------|--------------|----------------------------------------------|
| Client → Server | `{"type":"server","content":"..."}` or `"msg":"..."` | Broadcast to all connections (all sessions). |
| Server → Client | `{"type":"server","content":..., "from_session_id", "from_connection_id", "from_user_type"}` | Receive server-wide broadcasts (sender does not get their own). |

For **session-only** broadcasts (only clients in the same `session_id`), use `type: "broadcast"` instead; see [TECHNICAL.md](./TECHNICAL.md) and the protocol comments in `ws_server/realtime/consumers.py`.
