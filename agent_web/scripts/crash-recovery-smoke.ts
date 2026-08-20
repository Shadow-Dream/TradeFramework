type Provider = "claude-deepseek" | "codex-openai"

export {}

interface ChatSnapshot {
  runtime: {
    status: string
    sessionToken: string | null
    provider: Provider | null
    model?: string
  }
  messages: Array<Record<string, unknown>>
  startIndex: number
  incremental?: boolean
}

const [phase, chatIdArg, providerArg, modelArg, markerArg] = process.argv.slice(2)
const port = Number(process.env.TRADE_AGENT_SMOKE_PORT ?? "30810")
const cookie = process.env.TRADE_AGENT_SMOKE_COOKIE?.trim() ?? ""
const provider = providerArg as Provider | undefined
const marker = markerArg ?? "TRADE_AGENT_CRASH_RECOVERY_OK"
const origin = process.env.AGENT_PUBLIC_URL ?? `http://10.130.130.66:${port}`
const url = new URL("/ws", origin.replace(/^http/, "ws")).toString()

if (!phase || !["turn", "mcp", "start", "inspect", "continue"].includes(phase)) {
  throw new Error("usage: crash-recovery-smoke.ts <turn|mcp|start|inspect|continue> [chatId] [claude-deepseek|codex-openai] [model] [marker]")
}
if (!cookie.startsWith("trade_session=") || !cookie.includes("; trade_csrf=")) {
  throw new Error("TRADE_AGENT_SMOKE_COOKIE must contain the temporary trade_session and trade_csrf cookies.")
}

const socket = new WebSocket(url, { headers: { Cookie: cookie, Origin: origin } } as never)
const pending = new Map<string, { resolve: (value: unknown) => void; reject: (error: Error) => void }>()
const snapshots = new Map<string, ChatSnapshot>()
const listeners = new Map<string, Set<(snapshot: ChatSnapshot) => void>>()

socket.addEventListener("message", (event) => {
  const message = JSON.parse(String(event.data)) as Record<string, any>
  if (message.type === "ack" || message.type === "error") {
    const waiter = pending.get(message.id)
    if (!waiter) return
    pending.delete(message.id)
    if (message.type === "error") waiter.reject(new Error(message.message))
    else waiter.resolve(message.result)
    return
  }
  if (message.type !== "snapshot" || message.snapshot?.type !== "chat") return
  const incoming = message.snapshot.data as ChatSnapshot | null
  if (!incoming) return
  const previous = snapshots.get(message.id)
  const snapshot = incoming.incremental && previous
    ? {
        ...incoming,
        startIndex: previous.startIndex,
        messages: [
          ...previous.messages.slice(0, Math.max(0, incoming.startIndex - previous.startIndex)),
          ...incoming.messages,
        ],
      }
    : incoming
  snapshots.set(message.id, snapshot)
  for (const listener of listeners.get(message.id) ?? []) listener(snapshot)
})

await new Promise<void>((resolve, reject) => {
  const timeout = setTimeout(() => reject(new Error(`timed out connecting to ${url}`)), 10_000)
  socket.addEventListener("open", () => { clearTimeout(timeout); resolve() }, { once: true })
  socket.addEventListener("error", (event) => {
    clearTimeout(timeout)
    const detail = event instanceof ErrorEvent && event.message ? `: ${event.message}` : ""
    reject(new Error(`failed to connect to ${url}${detail}`))
  }, { once: true })
})

let nextId = 0
function sendCommand<T>(command: Record<string, unknown>) {
  const id = `command-${++nextId}`
  socket.send(JSON.stringify({ v: 1, type: "command", id, command }))
  return new Promise<T>((resolve, reject) => pending.set(id, { resolve: resolve as (value: unknown) => void, reject }))
}

function subscribe(chatId: string) {
  const id = `chat-${chatId}`
  socket.send(JSON.stringify({ v: 1, type: "subscribe", id, topic: { type: "chat", chatId } }))
  return id
}

function waitForSnapshot(subscriptionId: string, predicate: (snapshot: ChatSnapshot) => boolean, timeoutMs = 120_000) {
  return new Promise<ChatSnapshot>((resolve, reject) => {
    const latest = snapshots.get(subscriptionId)
    if (latest && predicate(latest)) return resolve(latest)
    const timeout = setTimeout(() => {
      listeners.get(subscriptionId)?.delete(onSnapshot)
      reject(new Error(`timed out waiting for chat snapshot (${subscriptionId})`))
    }, timeoutMs)
    const onSnapshot = (snapshot: ChatSnapshot) => {
      if (!predicate(snapshot)) return
      clearTimeout(timeout)
      listeners.get(subscriptionId)?.delete(onSnapshot)
      resolve(snapshot)
    }
    const current = listeners.get(subscriptionId) ?? new Set()
    current.add(onSnapshot)
    listeners.set(subscriptionId, current)
  })
}

const emptyContext = () => ({
  schemaVersion: "1",
  sourceView: "agent",
  capturedAt: new Date().toISOString(),
  references: [],
})

const isCompleted = (status: string) => status === "completed" || status === "idle"

function interruptionEntries(snapshot: ChatSnapshot) {
  return snapshot.messages.filter((message) => message.kind === "interrupted" && message.reason === "service_restart")
}

function sleepToolCalls(snapshot: ChatSnapshot) {
  return snapshot.messages.filter((message) => {
    if (message.kind !== "tool_call") return false
    const tool = message.tool as Record<string, any> | undefined
    return tool?.toolKind === "bash" && String(tool.input?.command ?? "").includes("sleep 40")
  })
}

function tradeContextToolCalls(snapshot: ChatSnapshot) {
  return snapshot.messages.filter((message) => {
    if (message.kind !== "tool_call") return false
    const tool = message.tool as Record<string, any> | undefined
    return `${String(tool?.toolName ?? "")} ${String(tool?.name ?? "")}`.includes("trade_context_get")
  })
}

try {
  if (phase === "turn" || phase === "mcp") {
    if (!provider || !modelArg) throw new Error("turn requires provider and model")
    const sent = await sendCommand<{ chatId: string }>({
      type: "chat.send",
      projectId: "trade-engine",
      provider,
      model: modelArg,
      content: phase === "mcp"
        ? `Call the TradeEngine MCP tool trade_context_get exactly once. Then reply exactly ${marker} and add no other text.`
        : `Reply exactly ${marker}. Do not call tools and do not add other text.`,
      attachments: [],
      planMode: false,
      autoPlan: false,
      clientRequestId: crypto.randomUUID(),
      context: emptyContext(),
    })
    const snapshot = await waitForSnapshot(subscribe(sent.chatId), (candidate) => (
      isCompleted(candidate.runtime.status)
      && candidate.messages.some((message) => message.kind === "assistant_text" && String(message.text ?? "").includes(marker))
      && (phase !== "mcp" || tradeContextToolCalls(candidate).length === 1)
    ))
    console.log(JSON.stringify({ phase, chatId: sent.chatId, status: snapshot.runtime.status, provider: snapshot.runtime.provider, model: snapshot.runtime.model, sessionToken: snapshot.runtime.sessionToken, markerSeen: true, tradeContextToolCalls: tradeContextToolCalls(snapshot).length }))
  } else if (phase === "start") {
    if (!provider || !modelArg) throw new Error("start requires provider and model")
    const sent = await sendCommand<{ chatId: string }>({
      type: "chat.send",
      projectId: "trade-engine",
      provider,
      model: modelArg,
      content: `Use Bash to run \`sleep 40\`, then reply exactly ${marker}. Do not do anything else.`,
      attachments: [],
      planMode: false,
      autoPlan: false,
      clientRequestId: crypto.randomUUID(),
      context: emptyContext(),
    })
    const subscriptionId = subscribe(sent.chatId)
    const snapshot = await waitForSnapshot(subscriptionId, (candidate) => (
      candidate.runtime.status === "running" && sleepToolCalls(candidate).length === 1 && Boolean(candidate.runtime.sessionToken)
    ))
    console.log(JSON.stringify({ phase, chatId: sent.chatId, status: snapshot.runtime.status, provider: snapshot.runtime.provider, model: snapshot.runtime.model, sessionToken: snapshot.runtime.sessionToken, sleepToolCalls: sleepToolCalls(snapshot).length }))
  } else if (phase === "inspect") {
    if (!chatIdArg) throw new Error("inspect requires chatId")
    const snapshot = await waitForSnapshot(subscribe(chatIdArg), (candidate) => candidate.runtime.status === "interrupted")
    console.log(JSON.stringify({ phase, chatId: chatIdArg, status: snapshot.runtime.status, provider: snapshot.runtime.provider, model: snapshot.runtime.model, sessionToken: snapshot.runtime.sessionToken, interruptions: interruptionEntries(snapshot).length, sleepToolCalls: sleepToolCalls(snapshot).length }))
  } else {
    if (!chatIdArg || !provider || !modelArg) throw new Error("continue requires chatId, provider, and model")
    const subscriptionId = subscribe(chatIdArg)
    const before = await waitForSnapshot(subscriptionId, (candidate) => candidate.runtime.status === "interrupted")
    const originalSessionToken = before.runtime.sessionToken
    const originalSleepCalls = sleepToolCalls(before).length
    await sendCommand({
      type: "chat.send",
      chatId: chatIdArg,
      provider,
      model: modelArg,
      content: `This is a new Turn after an Agent Web restart. Do not rerun any previous command. Reply exactly ${marker}.`,
      attachments: [],
      planMode: false,
      autoPlan: false,
      clientRequestId: crypto.randomUUID(),
      context: emptyContext(),
    })
    const snapshot = await waitForSnapshot(subscriptionId, (candidate) => (
      isCompleted(candidate.runtime.status)
      && candidate.messages.some((message) => message.kind === "assistant_text" && String(message.text ?? "").includes(marker))
    ))
    console.log(JSON.stringify({ phase, chatId: chatIdArg, status: snapshot.runtime.status, provider: snapshot.runtime.provider, model: snapshot.runtime.model, sessionTokenPreserved: snapshot.runtime.sessionToken === originalSessionToken, interruptions: interruptionEntries(snapshot).length, previousPromptReplayed: sleepToolCalls(snapshot).length !== originalSleepCalls, markerSeen: true }))
  }
} finally {
  socket.close()
}
