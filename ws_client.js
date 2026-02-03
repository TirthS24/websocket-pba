/**
 * CLI client for this repo's Django Channels + LangGraph chatbot.
 *
 * Supports:
 * - WebSocket streaming chat:      /ws/chat/
 * - HTTP thread summary:           POST /api/thread/summarize
 * - HTTP thread message history:   POST /api/thread/history
 *
 * WebSocket protocol (`ChatConsumer`):
 * - Connect: /ws/chat/ (optionally pass ?authorization=<AUTH_API_KEY>)
 * - Client sends:
 *   {
 *     "type": "chat",
 *     "message": "...",
 *     "channel": "web" | "sms",
 *     "thread_id": "<optional; server generates if omitted>",
 *     "data": [... optional ...],
 *     "context": {... optional ...},
 *     "task": "... optional ..."
 *   }
 * - Server sends token streaming events until {"type":"end"}.
 *
 * Notes:
 * - Node 20+ usually provides global fetch + WebSocket. If WebSocket is missing, install `ws`:
 *     npm i ws
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

function rstripSlash(s) {
  return String(s || "").replace(/\/+$/, "");
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

const DEFAULT_WEB_DATA = [
  {
    external_id: "practice-001",
    name: "Demo Medical Practice",
    platform: "PatriotPay",
    email_address: "contact@demopractice.com",
    phone_number: "555-0100",
    patients: [
      {
        external_id: "patient-001",
        first_name: "John",
        last_name: "Doe",
        gender: "M",
        phone_number: "555-0101",
        email_address: "john.doe@email.com",
        dob: "1985-03-15",
        claims: [],
        patient_payments: [],
      },
    ],
  },
];

function wsChatUrl(wsBase, apiKey) {
  const base = rstripSlash(wsBase);
  const qs = apiKey ? `?authorization=${encodeURIComponent(apiKey)}` : "";
  return `${base}/ws/chat/${qs}`;
}

async function getCsrf({ httpBase, apiKey }) {
  const url = `${rstripSlash(httpBase)}/api/csrf-token/`;
  const res = await fetch(url, {
    method: "GET",
    headers: apiKey ? { Authorization: apiKey } : undefined,
  });
  const setCookie = res.headers.get("set-cookie");
  const json = await res.json();
  const token = json && json.csrf_token;
  if (!token) throw new Error(`CSRF token endpoint returned unexpected payload: ${JSON.stringify(json)}`);
  // Django typically sets "csrftoken=...; ..." — keep only the first cookie KV.
  const cookie = setCookie ? setCookie.split(",")[0].split(";")[0] : null;
  return { token, cookie };
}

async function postJson({ httpBase, apiKey, csrf, path, payload }) {
  const url = `${rstripSlash(httpBase)}${path}`;
  const headers = {
    "Content-Type": "application/json",
    "X-CSRFToken": csrf.token,
  };
  if (csrf.cookie) headers.Cookie = csrf.cookie;
  if (apiKey) headers.Authorization = apiKey;

  const res = await fetch(url, { method: "POST", headers, body: JSON.stringify(payload) });
  const text = await res.text();
  const data = text ? JSON.parse(text) : {};
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${JSON.stringify(data)}`);
  return data;
}

async function main() {
  const args = parseArgs(process.argv);
  const mode = args.mode || "stream"; // stream | summary | history
  const httpBase = args.http || "http://localhost:8000";
  const wsBase = args.ws || "ws://localhost:8000";
  const apiKey = args["api-key"];

  if (mode === "summary" || mode === "history") {
    if (!args["thread-id"]) {
      console.error("Missing required --thread-id for summary/history.");
      process.exit(2);
    }
    const csrf = await getCsrf({ httpBase, apiKey });
    const path = mode === "summary" ? "/api/thread/summarize" : "/api/thread/history";
    const data = await postJson({
      httpBase,
      apiKey,
      csrf,
      path,
      payload: { thread_id: args["thread-id"] },
    });
    process.stdout.write(`${JSON.stringify(data, null, 2)}\n`);
    return;
  }

  // stream mode
  if (!args.message) {
    console.error("Missing required --message for stream mode.");
    process.exit(2);
  }

  const impl = loadWebSocketImpl();
  const WS = impl.WS;
  const wsUrl = wsChatUrl(wsBase, apiKey);
  let ws;
  if (impl.kind === "ws") {
    // `ws` supports headers.
    const headers = {};
    if (apiKey) headers.Authorization = apiKey;
    if (args.origin) headers.Origin = args.origin;
    ws = new WS(wsUrl, Object.keys(headers).length ? { headers } : undefined);
  } else {
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
    let data = null;
    if (typeof args["data-json"] === "string") data = safeJsonParse(args["data-json"]);
    else if ((args.channel || "web") === "web") data = DEFAULT_WEB_DATA;

    const payload = {
      type: "chat",
      message: args.message,
      channel: args.channel || "web",
      ...(args["thread-id"] ? { thread_id: args["thread-id"] } : {}),
      ...(data ? { data } : {}),
      ...(typeof args["context-json"] === "string" ? { context: safeJsonParse(args["context-json"]) } : {}),
      ...(typeof args.task === "string" ? { task: args.task } : {}),
    };
    ws.send(JSON.stringify(payload));
  });

  onMessage((data) => {
    const text =
      typeof data === "string"
        ? data
        : data && typeof data.toString === "function"
          ? data.toString("utf8")
          : String(data);
    let msg;
    try {
      msg = JSON.parse(text);
    } catch (e) {
      process.stdout.write(`${text}\n`);
      return;
    }

    // Server may send either `{type: ...}` (WebSocket protocol) or `{event: ...}` (SSE-style).
    const kind = msg.type || msg.event;

    if (kind === "session_started") {
      process.stderr.write(`\n[thread_id=${msg.thread_id}]\n`);
      return;
    }
    if (kind === "token") {
      process.stdout.write(String(msg.content || ""));
      return;
    }
    if (kind === "escalation") {
      process.stderr.write(`\n[escalation should_escalate=${msg.should_escalate}]\n`);
      return;
    }
    if (kind === "error") {
      process.stderr.write(`\n[error ${msg.message}]\n`);
      process.exit(1);
    }
    if (kind === "end") {
      process.stdout.write("\n");
      setTimeout(() => process.exit(0), 25);
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

