/**
 * Minimal WebSocket client for this repo's Channels consumer.
 *
 * Server protocol (from `ws_server/realtime/consumers.py`):
 * - Connect:  /ws/session/<session_id>/
 * - First client message MUST include `user_type` (optionally `client_type`)
 *   e.g. {"type":"hello","user_type":"admin","client_type":"node"}
 * - Then you can:
 *   - presence:   {"type":"presence"}
 *   - broadcast:  {"type":"broadcast","msg":"hi"} or {"type":"broadcast","data":{...}}
 *
 * Usage examples:
 *   node ws_client.js --base ws://localhost:8000 --session test --user admin --client node --presence
 *   node ws_client.js --base ws://localhost:8000 --session test --user admin --broadcast-msg "hello"
 *   node ws_client.js --url ws://localhost:8000/ws/session/test/ --user admin --broadcast-json '{"foo":1}'
 *   node ws_client.js --base ws://localhost:8000 --session test --user admin --interactive
 *
 * Notes:
 * - Node 20+ usually provides global WebSocket. If not, install `ws`:
 *     npm i ws
 * - In production `AllowedHostsOriginValidator` may require an Origin header.
 *   Provide `--origin https://your-site` if you get 403/handshake failures.
 */

function parseArgs(argv) {
  const args = {};
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith("--")) continue;
    const key = a.slice(2);
    const next = argv[i + 1];
    if (!next || next.startsWith("--")) {
      args[key] = true;
    } else {
      args[key] = next;
      i++;
    }
  }
  return args;
}

function buildWsUrl({ base, url, session }) {
  if (url) return url;
  if (!base || !session) throw new Error("Provide either --url OR (--base and --session).");
  return `${String(base).replace(/\/+$/, "")}/ws/session/${session}/`;
}

function loadWebSocketImpl() {
  if (typeof WebSocket !== "undefined") return { kind: "global", WS: WebSocket };
  // Fallback for older Node versions: requires `ws` dependency.
  try {
    // eslint-disable-next-line global-require
    return { kind: "ws", WS: require("ws") };
  } catch (e) {
    throw new Error('Missing dependency: "ws". Install with: npm i ws');
  }
}

function safeJsonParse(s) {
  try {
    return JSON.parse(s);
  } catch (e) {
    throw new Error(`Invalid JSON for --broadcast-json: ${e && e.message ? e.message : String(e)}`);
  }
}

async function main() {
  const args = parseArgs(process.argv);
  const wsUrl = buildWsUrl({ base: args.base, url: args.url, session: args.session });

  if (!args.user) {
    console.error("Missing required --user (maps to server user_type).");
    process.exit(2);
  }

  const clientType = args.client || "node";
  const origin = args.origin;

  const impl = loadWebSocketImpl();
  const WS = impl.WS;
  let ws;
  if (impl.kind === "ws") {
    // `ws` supports headers (useful when server validates Origin in production).
    ws = new WS(wsUrl, origin ? { headers: { Origin: origin } } : undefined);
  } else {
    // Node's global WebSocket does NOT allow setting Origin headers.
    if (origin) {
      process.stderr.write("Warning: global WebSocket cannot set Origin header; ignoring --origin.\n");
    }
    ws = new WS(wsUrl);
  }

  function onOpen(handler) {
    if (impl.kind === "ws") return ws.on("open", handler);
    ws.addEventListener("open", handler);
  }

  function onMessage(handler) {
    if (impl.kind === "ws") return ws.on("message", handler);
    ws.addEventListener("message", (event) => handler(event && event.data));
  }

  function onClose(handler) {
    if (impl.kind === "ws") return ws.on("close", handler);
    ws.addEventListener("close", (event) => handler(event && event.code, event && event.reason));
  }

  function onError(handler) {
    if (impl.kind === "ws") return ws.on("error", handler);
    ws.addEventListener("error", (event) => handler(event));
  }

  onOpen(() => {
    // REQUIRED first message
    const hello = { type: "hello", user_type: args.user, client_type: clientType };
    ws.send(JSON.stringify(hello));
    process.stdout.write(`> ${JSON.stringify(hello)}\n`);

    if (args.presence) {
      const msg = { type: "presence" };
      ws.send(JSON.stringify(msg));
      process.stdout.write(`> ${JSON.stringify(msg)}\n`);
      return;
    }

    if (typeof args["broadcast-msg"] === "string") {
      const msg = { type: "broadcast", msg: args["broadcast-msg"] };
      ws.send(JSON.stringify(msg));
      process.stdout.write(`> ${JSON.stringify(msg)}\n`);
      return;
    }

    if (typeof args["broadcast-json"] === "string") {
      const data = safeJsonParse(args["broadcast-json"]);
      const msg = { type: "broadcast", data };
      ws.send(JSON.stringify(msg));
      process.stdout.write(`> ${JSON.stringify(msg)}\n`);
      return;
    }

    if (args.interactive) {
      process.stderr.write("Interactive mode. Type a line and press Enter to broadcast. Ctrl+C to quit.\n");
      // Lazy import to avoid ESM/CJS issues.
      // eslint-disable-next-line global-require
      const readline = require("readline");
      const rl = readline.createInterface({ input: process.stdin, output: process.stdout, terminal: false });
      rl.on("line", (line) => {
        const trimmed = String(line || "").trimEnd();
        if (!trimmed) return;
        const out = { type: "broadcast", msg: trimmed };
        ws.send(JSON.stringify(out));
        process.stdout.write(`> ${JSON.stringify(out)}\n`);
      });
    }
  });

  onMessage((data) => {
    const text =
      typeof data === "string"
        ? data
        : data && typeof data.toString === "function"
          ? data.toString("utf8")
          : String(data);
    process.stdout.write(`< ${text}\n`);
    // For one-shot modes, exit after we receive the expected response.
    if (args.presence || typeof args["broadcast-msg"] === "string" || typeof args["broadcast-json"] === "string") {
      // Give the server a moment to flush any final frames.
      setTimeout(() => process.exit(0), 50);
    }
  });

  onClose((code, reason) => {
    const r = reason ? reason.toString() : "";
    process.stderr.write(`WS closed: code=${code}${r ? ` reason=${r}` : ""}\n`);
    process.exit(code === 1000 ? 0 : 1);
  });

  onError((err) => {
    const msg = err && err.message ? err.message : String(err);
    process.stderr.write(`WS error: ${msg}\n`);
  });
}

main().catch((e) => {
  console.error(e && e.stack ? e.stack : String(e));
  process.exit(1);
});

