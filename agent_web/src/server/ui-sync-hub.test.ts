import { describe, expect, test } from "bun:test"
import type { UiClientEnvelope, UiServerEnvelope } from "../shared/ui-sync-protocol"
import { UiSyncError, UiSyncHub, type UiSyncClientState, type UiSyncSocket } from "./ui-sync-hub"

function socket(userId = "local-user") {
  const messages: UiServerEnvelope[] = []
  let closed: { code?: number; reason?: string } | null = null
  const value: UiSyncSocket = {
    data: {
      socketKind: "ui-sync",
      identity: { userId, email: "local@example.test", role: "admin", expiresAt: "2099-01-01T00:00:00.000Z" },
      sessionCookie: "trade_session=test",
      subscriptions: new Map(),
      tabId: null,
    } satisfies UiSyncClientState,
    send(payload) {
      messages.push(JSON.parse(payload))
      return payload.length
    },
    close(code, reason) {
      closed = { code, reason }
    },
  }
  return { value, messages, get closed() { return closed } }
}

async function send(target: UiSyncSocket, hub: UiSyncHub, envelope: UiClientEnvelope) {
  await hub.handleMessage(target, JSON.stringify(envelope))
}

function command(id: string, value: Extract<UiClientEnvelope, { type: "command" }>["command"]): UiClientEnvelope {
  return { v: 1, type: "command", id, command: value }
}

async function register(hub: UiSyncHub, target: UiSyncSocket, tabId: string, kind: "engine-spa" | "jupyter") {
  await send(target, hub, command("register-1", {
    type: "ui.register",
    tabId,
    clientKind: kind,
    capabilities: ["presence", "context", "document-read", "document-write", "resource-events", "operation-events"],
  }))
}

function document(revision = 0, dirty = false) {
  return {
    documentId: "workspace-1:strategy.py",
    kind: "jupyter-text" as const,
    label: "strategy.py",
    workspaceId: "workspace-1",
    relativePath: "strategy.py",
    language: "python",
    revision,
    savedRevision: dirty ? Math.max(0, revision - 1) : revision,
    contentDigest: `digest-${revision}`,
    dirty,
    readOnly: false,
  }
}

describe("UiSyncHub", () => {
  test("registers tabs and keeps disconnected state only as an internal reconnect cache", async () => {
    let now = Date.parse("2026-08-17T10:00:00Z")
    const hub = new UiSyncHub({ now: () => now, staleTtlMs: 60_000 })
    const engine = socket()
    hub.handleOpen(engine.value)
    await register(hub, engine.value, "engine-tab-0001", "engine-spa")
    await send(engine.value, hub, { v: 1, type: "subscribe", id: "state-sub", topic: { type: "ui-state" } })

    now += 100
    await send(engine.value, hub, command("context-1", {
      type: "context.replace",
      context: {
        route: "/pipeline?pipelineId=momentum-lab",
        view: "pipeline",
        subview: "signal",
        projectId: "momentum-lab",
        resourceRefs: [{ kind: "pipeline", id: "momentum-lab", version: "3", digest: "abc" }],
        selection: { kind: "graph-node", id: "score" },
      },
    }))

    const live = hub.captureContext()
    expect(live.activeTabId).toBe("engine-tab-0001")
    expect(live.activeContext?.selection?.id).toBe("score")
    expect(live.tabs[0]?.connected).toBe(true)

    hub.handleClose(engine.value)
    const disconnected = hub.captureContext()
    expect(disconnected.activeContext).toBeNull()
    expect(disconnected.activeTabId).toBeNull()
    expect(disconnected.tabs[0]?.connected).toBe(false)
    expect(hub.captureTurnContext().tabs).toEqual([])
    expect(engine.messages.some((message) => message.type === "snapshot")).toBe(true)
    hub.dispose()
  })

  test("rejects absolute paths and credential-shaped fields", async () => {
    const hub = new UiSyncHub()
    const engine = socket()
    hub.handleOpen(engine.value)
    await register(hub, engine.value, "engine-tab-0002", "engine-spa")
    await hub.handleMessage(engine.value, JSON.stringify(command("bad-doc-1", {
      type: "document.open",
      document: { ...document(), relativePath: "/tmp/strategy.py" },
    })))
    const error = engine.messages.at(-1)
    expect(error?.type).toBe("error")
    if (error?.type === "error") expect(error.code).toBe("invalid_request")
    hub.dispose()
  })

  test("enforces document revisions", async () => {
    const hub = new UiSyncHub()
    const editor = socket()
    hub.handleOpen(editor.value)
    await register(hub, editor.value, "jupyter-tab-001", "jupyter")
    await send(editor.value, hub, command("open-doc-1", { type: "document.open", document: document() }))
    await send(editor.value, hub, command("update-doc-1", {
      type: "document.update",
      baseRevision: 0,
      document: document(1, true),
    }))
    await send(editor.value, hub, command("update-doc-2", {
      type: "document.update",
      baseRevision: 0,
      document: document(2, true),
    }))
    const error = editor.messages.at(-1)
    expect(error?.type).toBe("error")
    if (error?.type === "error") expect(error.code).toBe("revision_conflict")
    hub.dispose()
  })

  test("routes a patch to the live editor and makes duplicate operation ids idempotent", async () => {
    const hub = new UiSyncHub({ requestTimeoutMs: 1_000 })
    const editor = socket()
    hub.handleOpen(editor.value)
    await register(hub, editor.value, "jupyter-tab-002", "jupyter")
    await send(editor.value, hub, command("open-doc-2", { type: "document.open", document: document() }))

    const pending = hub.requestDocumentPatch({
      operationId: "patch-op-0001",
      documentId: document().documentId,
      baseRevision: 0,
      baseDigest: "digest-0",
      patch: { type: "replace", start: 0, end: 0, text: "# changed\n" },
    })
    const request = editor.messages.find((message) => message.type === "request")
    expect(request?.type).toBe("request")
    await send(editor.value, hub, command("patch-response-1", {
      type: "document.patch.respond",
      operationId: "patch-op-0001",
      documentId: document().documentId,
      status: "applied",
      revision: 1,
      savedRevision: 1,
      contentDigest: "digest-1",
      dirty: false,
    }))
    expect(await pending).toEqual({
      operationId: "patch-op-0001",
      documentId: document().documentId,
      status: "applied",
      revision: 1,
      savedRevision: 1,
      contentDigest: "digest-1",
      dirty: false,
    })
    expect(await hub.requestDocumentPatch({
      operationId: "patch-op-0001",
      documentId: document().documentId,
      baseRevision: 0,
      baseDigest: "digest-0",
      patch: { type: "replace", start: 0, end: 0, text: "# changed\n" },
    })).toMatchObject({ revision: 1 })
    expect(hub.requestDocumentPatch({
      operationId: "patch-op-0001",
      documentId: document().documentId,
      baseRevision: 0,
      baseDigest: "digest-0",
      patch: { type: "replace", start: 0, end: 0, text: "different" },
    })).rejects.toMatchObject({ code: "idempotency_conflict" })
    hub.dispose()
  })

  test("refuses to choose between two dirty editors", async () => {
    const hub = new UiSyncHub()
    const first = socket()
    const second = socket()
    hub.handleOpen(first.value)
    hub.handleOpen(second.value)
    await register(hub, first.value, "jupyter-tab-003", "jupyter")
    await register(hub, second.value, "jupyter-tab-004", "jupyter")
    await send(first.value, hub, command("open-doc-3", { type: "document.open", document: document(1, true) }))
    await send(second.value, hub, command("open-doc-4", { type: "document.open", document: document(1, true) }))
    await expect(hub.requestDocumentSnapshot({ documentId: document().documentId })).rejects.toMatchObject({
      code: "editor_ambiguous",
    } satisfies Partial<UiSyncError>)
    hub.dispose()
  })
})
