import { randomUUID } from "node:crypto"
import type { TradeIdentity } from "./trade-session"
import {
  UI_SYNC_PROTOCOL_VERSION,
  type UiCapability,
  type UiClientCommand,
  type UiClientEnvelope,
  type UiClientKind,
  type UiContextState,
  type UiDocumentState,
  type UiOperationState,
  type UiResourceChange,
  type UiResourceRef,
  type UiSelection,
  type UiServerEnvelope,
  type UiStateSnapshot,
  type UiSubscriptionTopic,
  type UiTabSnapshot,
  type UiTextPatch,
  type UiTurnContextV1,
  isUiClientEnvelope,
} from "../shared/ui-sync-protocol"

const TAB_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/
const OPERATION_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/
const MAX_LABEL = 256
const MAX_ID = 256
const MAX_ROUTE = 1024
const MAX_REFS = 32
const MAX_DOCUMENT_CONTENT = 1024 * 1024
const MAX_PATCH_TEXT = 256 * 1024
const DEFAULT_STALE_TTL_MS = 45_000
const DEFAULT_REQUEST_TIMEOUT_MS = 10_000
const MAX_RECENT_RESOURCES = 128
const MAX_RECENT_OPERATIONS = 128

const CLIENT_KINDS = new Set<UiClientKind>(["engine-spa", "jupyter"])
const CAPABILITIES = new Set<UiCapability>([
  "presence",
  "context",
  "document-read",
  "document-write",
  "resource-events",
  "operation-events",
])
const DOCUMENT_KINDS = new Set<UiDocumentState["kind"]>([
  "pipeline-draft",
  "environment-draft",
  "analysis-draft",
  "backtest-draft",
  "visualization-draft",
  "dataset-draft",
  "mining-draft",
  "jupyter-text",
  "jupyter-notebook",
])
const RESOURCE_CHANGES = new Set<UiResourceChange["change"]>([
  "changed",
  "published",
  "archived",
  "deleted",
  "validation-changed",
])
const OPERATION_STATUSES = new Set<UiOperationState["status"]>([
  "started",
  "progress",
  "waiting",
  "completed",
  "failed",
  "interrupted",
])
const FORBIDDEN_KEYS = new Set([
  "absolutePath",
  "localPath",
  "cwd",
  "workspacePath",
  "controlPath",
  "archivePath",
  "manifestPath",
  "token",
  "cookie",
  "csrfToken",
  "apiKey",
])

export interface UiSyncClientState {
  socketKind: "ui-sync"
  identity: TradeIdentity
  sessionCookie: string
  subscriptions: Map<string, UiSubscriptionTopic>
  tabId: string | null
}

export interface UiSyncSocket {
  data: UiSyncClientState
  send(payload: string): number | void
  close?(code?: number, reason?: string): void
}

interface InternalTab {
  socket: UiSyncSocket | null
  tabId: string
  clientKind: UiClientKind
  capabilities: UiCapability[]
  connected: boolean
  visible: boolean
  focused: boolean
  openedAt: number
  lastSeenAt: number
  lastInteractionAt: number
  context: UiContextState | null
  documents: Map<string, UiDocumentState>
}

interface PendingDocumentRequest {
  kind: "snapshot" | "patch"
  tabId: string
  documentId: string
  signature: string
  timer: ReturnType<typeof setTimeout>
  resolve: (value: unknown) => void
  reject: (error: UiSyncError) => void
}

export interface UiDocumentSnapshotResult {
  operationId: string
  documentId: string
  revision: number
  contentDigest: string
  content?: string
}

export interface UiDocumentPatchResult {
  operationId: string
  documentId: string
  status: "applied"
  revision: number
  savedRevision: number
  contentDigest: string
  dirty: boolean
}

export class UiSyncError extends Error {
  readonly code: string
  readonly retryable: boolean

  constructor(code: string, message: string, retryable = false) {
    super(message)
    this.name = "UiSyncError"
    this.code = code
    this.retryable = retryable
  }
}

function exactObject(value: unknown, field: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new UiSyncError("invalid_request", `${field} must be an object.`)
  }
  return value as Record<string, unknown>
}

function exactKeys(record: Record<string, unknown>, allowed: readonly string[], field: string) {
  const unknown = Object.keys(record).filter((key) => !allowed.includes(key))
  if (unknown.length) throw new UiSyncError("invalid_request", `${field} contains unknown field '${unknown[0]}'.`)
}

function boundedString(value: unknown, field: string, maxLength = MAX_ID, allowEmpty = false): string {
  if (typeof value !== "string" || (!allowEmpty && !value) || value.length > maxLength) {
    throw new UiSyncError("invalid_request", `${field} must be a bounded${allowEmpty ? "" : " non-empty"} string.`)
  }
  return value
}

function optionalString(value: unknown, field: string, maxLength = MAX_ID): string | undefined {
  if (value === undefined) return undefined
  return boundedString(value, field, maxLength)
}

function nonNegativeInteger(value: unknown, field: string): number {
  if (!Number.isSafeInteger(value) || Number(value) < 0) {
    throw new UiSyncError("invalid_request", `${field} must be a non-negative integer.`)
  }
  return Number(value)
}

function containsForbiddenKey(value: unknown, depth = 0): boolean {
  if (depth > 24 || !value || typeof value !== "object") return false
  if (Array.isArray(value)) return value.some((item) => containsForbiddenKey(item, depth + 1))
  return Object.entries(value as Record<string, unknown>)
    .some(([key, item]) => FORBIDDEN_KEYS.has(key) || containsForbiddenKey(item, depth + 1))
}

function sanitizeRelativePath(value: unknown): string | undefined {
  if (value === undefined) return undefined
  const relativePath = boundedString(value, "document.relativePath", 512)
  if (relativePath.startsWith("/") || relativePath.startsWith("\\")
      || relativePath.split(/[\\/]/).some((part) => part === ".." || part === "")) {
    throw new UiSyncError("invalid_request", "document.relativePath must be a normalized relative path.")
  }
  return relativePath.replaceAll("\\", "/")
}

function sanitizeResourceRef(value: unknown, index: number): UiResourceRef {
  const record = exactObject(value, `context.resourceRefs[${index}]`)
  exactKeys(record, ["kind", "id", "version", "digest", "label"], `context.resourceRefs[${index}]`)
  return {
    kind: boundedString(record.kind, `context.resourceRefs[${index}].kind`, 96),
    id: boundedString(record.id, `context.resourceRefs[${index}].id`),
    version: optionalString(record.version, `context.resourceRefs[${index}].version`, 128),
    digest: optionalString(record.digest, `context.resourceRefs[${index}].digest`, 128),
    label: optionalString(record.label, `context.resourceRefs[${index}].label`, MAX_LABEL),
  }
}

function sanitizeSelection(value: unknown): UiSelection | undefined {
  if (value === undefined) return undefined
  const record = exactObject(value, "context.selection")
  exactKeys(record, ["kind", "id", "label"], "context.selection")
  return {
    kind: boundedString(record.kind, "context.selection.kind", 96),
    id: boundedString(record.id, "context.selection.id"),
    label: optionalString(record.label, "context.selection.label", MAX_LABEL),
  }
}

export function sanitizeUiContext(value: unknown): UiContextState {
  const record = exactObject(value, "context")
  exactKeys(record, [
    "route", "view", "subview", "projectId", "resourceRefs", "selection", "documentId", "documentRevision",
  ], "context")
  if (!Array.isArray(record.resourceRefs) || record.resourceRefs.length > MAX_REFS) {
    throw new UiSyncError("invalid_request", `context.resourceRefs must contain at most ${MAX_REFS} references.`)
  }
  const route = boundedString(record.route, "context.route", MAX_ROUTE)
  if (!route.startsWith("/") || route.startsWith("//")) {
    throw new UiSyncError("invalid_request", "context.route must be a relative application URL.")
  }
  return {
    route,
    view: boundedString(record.view, "context.view", 96),
    subview: optionalString(record.subview, "context.subview", 96),
    projectId: optionalString(record.projectId, "context.projectId", 128),
    resourceRefs: record.resourceRefs.map(sanitizeResourceRef),
    selection: sanitizeSelection(record.selection),
    documentId: optionalString(record.documentId, "context.documentId"),
    documentRevision: record.documentRevision === undefined
      ? undefined
      : nonNegativeInteger(record.documentRevision, "context.documentRevision"),
  }
}

export function sanitizeUiDocument(value: unknown): UiDocumentState {
  const record = exactObject(value, "document")
  exactKeys(record, [
    "documentId", "kind", "label", "projectId", "workspaceId", "relativePath", "language",
    "revision", "savedRevision", "contentDigest", "dirty", "readOnly",
  ], "document")
  const kind = boundedString(record.kind, "document.kind", 64) as UiDocumentState["kind"]
  if (!DOCUMENT_KINDS.has(kind)) throw new UiSyncError("unsupported_document_type", `Unsupported document kind '${kind}'.`)
  if (typeof record.dirty !== "boolean" || typeof record.readOnly !== "boolean") {
    throw new UiSyncError("invalid_request", "document dirty/readOnly fields must be booleans.")
  }
  const revision = nonNegativeInteger(record.revision, "document.revision")
  const savedRevision = nonNegativeInteger(record.savedRevision, "document.savedRevision")
  if (savedRevision > revision) throw new UiSyncError("invalid_request", "document.savedRevision cannot exceed revision.")
  return {
    documentId: boundedString(record.documentId, "document.documentId"),
    kind,
    label: boundedString(record.label, "document.label", MAX_LABEL),
    projectId: optionalString(record.projectId, "document.projectId", 128),
    workspaceId: optionalString(record.workspaceId, "document.workspaceId", 128),
    relativePath: sanitizeRelativePath(record.relativePath),
    language: optionalString(record.language, "document.language", 64),
    revision,
    savedRevision,
    contentDigest: boundedString(record.contentDigest, "document.contentDigest", 128),
    dirty: record.dirty,
    readOnly: record.readOnly,
  }
}

function sanitizeResourceChange(value: unknown): UiResourceChange {
  const record = exactObject(value, "change")
  exactKeys(record, ["eventId", "kind", "id", "change", "version", "digest", "occurredAt"], "change")
  const change = boundedString(record.change, "change.change", 64) as UiResourceChange["change"]
  if (!RESOURCE_CHANGES.has(change)) throw new UiSyncError("invalid_request", `Unknown resource change '${change}'.`)
  const occurredAt = boundedString(record.occurredAt, "change.occurredAt", 64)
  if (!Number.isFinite(Date.parse(occurredAt))) throw new UiSyncError("invalid_request", "change.occurredAt is invalid.")
  return {
    eventId: boundedString(record.eventId, "change.eventId", 128),
    kind: boundedString(record.kind, "change.kind", 96),
    id: boundedString(record.id, "change.id"),
    change,
    version: optionalString(record.version, "change.version", 128),
    digest: optionalString(record.digest, "change.digest", 128),
    occurredAt: new Date(occurredAt).toISOString(),
  }
}

function sanitizeOperation(value: unknown): UiOperationState {
  const record = exactObject(value, "operation")
  exactKeys(record, [
    "operationId", "kind", "resourceId", "status", "progress", "message", "errorCode", "updatedAt",
  ], "operation")
  const status = boundedString(record.status, "operation.status", 32) as UiOperationState["status"]
  if (!OPERATION_STATUSES.has(status)) throw new UiSyncError("invalid_request", `Unknown operation status '${status}'.`)
  if (record.progress !== undefined
      && (typeof record.progress !== "number" || !Number.isFinite(record.progress)
        || record.progress < 0 || record.progress > 1)) {
    throw new UiSyncError("invalid_request", "operation.progress must be between 0 and 1.")
  }
  const updatedAt = boundedString(record.updatedAt, "operation.updatedAt", 64)
  if (!Number.isFinite(Date.parse(updatedAt))) throw new UiSyncError("invalid_request", "operation.updatedAt is invalid.")
  return {
    operationId: boundedString(record.operationId, "operation.operationId", 128),
    kind: boundedString(record.kind, "operation.kind", 96),
    resourceId: optionalString(record.resourceId, "operation.resourceId"),
    status,
    progress: record.progress as number | undefined,
    message: optionalString(record.message, "operation.message", 512),
    errorCode: optionalString(record.errorCode, "operation.errorCode", 96),
    updatedAt: new Date(updatedAt).toISOString(),
  }
}

function sanitizeTextPatch(value: unknown): UiTextPatch {
  const record = exactObject(value, "patch")
  exactKeys(record, ["type", "start", "end", "text"], "patch")
  if (record.type !== "replace") throw new UiSyncError("invalid_request", "Only bounded replace patches are supported.")
  const start = nonNegativeInteger(record.start, "patch.start")
  const end = nonNegativeInteger(record.end, "patch.end")
  if (end < start) throw new UiSyncError("invalid_request", "patch.end cannot be before patch.start.")
  return {
    type: "replace",
    start,
    end,
    text: boundedString(record.text, "patch.text", MAX_PATCH_TEXT, true),
  }
}

function iso(milliseconds: number): string {
  return new Date(milliseconds).toISOString()
}

function publicTab(tab: InternalTab): UiTabSnapshot {
  return {
    tabId: tab.tabId,
    clientKind: tab.clientKind,
    capabilities: [...tab.capabilities],
    connected: tab.connected,
    visible: tab.visible,
    focused: tab.focused,
    openedAt: iso(tab.openedAt),
    lastSeenAt: iso(tab.lastSeenAt),
    lastInteractionAt: iso(tab.lastInteractionAt),
    context: tab.context ? structuredClone(tab.context) : null,
    documents: [...tab.documents.values()].map((document) => structuredClone(document)),
  }
}

function send(socket: UiSyncSocket, envelope: UiServerEnvelope) {
  socket.send(JSON.stringify(envelope))
}

function errorEnvelope(id: string | undefined, error: unknown): UiServerEnvelope {
  const resolved = error instanceof UiSyncError
    ? error
    : new UiSyncError("internal_error", error instanceof Error ? error.message : String(error))
  return {
    v: UI_SYNC_PROTOCOL_VERSION,
    type: "error",
    id,
    code: resolved.code,
    message: resolved.message,
    retryable: resolved.retryable,
  }
}

export interface UiSyncHubOptions {
  staleTtlMs?: number
  requestTimeoutMs?: number
  now?: () => number
}

export class UiSyncHub {
  private readonly sockets = new Set<UiSyncSocket>()
  private readonly tabs = new Map<string, InternalTab>()
  private readonly pending = new Map<string, PendingDocumentRequest>()
  private readonly completedOperations = new Map<string, { signature: string; result: unknown }>()
  private readonly recentResources = new Map<string, UiResourceChange>()
  private readonly operations = new Map<string, UiOperationState>()
  private readonly changeListeners = new Set<() => void>()
  private readonly staleTtlMs: number
  private readonly requestTimeoutMs: number
  private readonly now: () => number
  private readonly cleanupTimer: ReturnType<typeof setInterval>
  private serverSeq = 0

  constructor(options: UiSyncHubOptions = {}) {
    this.staleTtlMs = options.staleTtlMs ?? DEFAULT_STALE_TTL_MS
    this.requestTimeoutMs = options.requestTimeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS
    this.now = options.now ?? Date.now
    this.cleanupTimer = setInterval(() => this.pruneStaleTabs(), Math.min(this.staleTtlMs, 5_000))
    this.cleanupTimer.unref?.()
  }

  handleOpen(socket: UiSyncSocket) {
    this.sockets.add(socket)
  }

  handleClose(socket: UiSyncSocket) {
    this.sockets.delete(socket)
    const tabId = socket.data.tabId
    if (!tabId) return
    const tab = this.tabs.get(tabId)
    if (!tab || tab.socket !== socket) return
    tab.socket = null
    tab.connected = false
    tab.focused = false
    tab.visible = false
    tab.lastSeenAt = this.now()
    this.bumpAndBroadcast()
  }

  async handleMessage(socket: UiSyncSocket, raw: string | Buffer | ArrayBuffer | Uint8Array) {
    let parsed: unknown
    try {
      parsed = JSON.parse(String(raw))
    } catch {
      send(socket, errorEnvelope(undefined, new UiSyncError("invalid_json", "Invalid JSON.")))
      return
    }
    if (containsForbiddenKey(parsed)) {
      send(socket, errorEnvelope(undefined, new UiSyncError("invalid_request", "Paths and credentials are not accepted by ui-sync.")))
      return
    }
    if (!isUiClientEnvelope(parsed)) {
      send(socket, errorEnvelope(undefined, new UiSyncError("invalid_envelope", "Invalid ui-sync envelope.")))
      return
    }
    const envelope = parsed as UiClientEnvelope
    try {
      if (envelope.type === "subscribe") {
        this.requireRegistered(socket)
        socket.data.subscriptions.set(envelope.id, envelope.topic)
        this.pushSubscription(socket, envelope.id, envelope.topic)
        return
      }
      if (envelope.type === "unsubscribe") {
        socket.data.subscriptions.delete(envelope.id)
        send(socket, { v: 1, type: "ack", id: envelope.id })
        return
      }
      await this.handleCommand(socket, envelope.id, envelope.command)
    } catch (error) {
      send(socket, errorEnvelope(envelope.id, error))
    }
  }

  getStateSnapshot(): UiStateSnapshot {
    const tabs = [...this.tabs.values()]
      .sort((left, right) => right.lastInteractionAt - left.lastInteractionAt)
    // Disconnected tabs remain in memory briefly so an ordinary WebSocket
    // reconnect can restore their state. They are not current UI and must
    // never become the active Agent context after their browser window closes.
    const candidates = tabs.filter((tab) => tab.connected && tab.context)
    const active = candidates[0] ?? null
    const equallyRecent = active
      ? candidates.filter((candidate) => Math.abs(candidate.lastInteractionAt - active.lastInteractionAt) < 250)
      : []
    return {
      serverSeq: this.serverSeq,
      generatedAt: iso(this.now()),
      tabs: tabs.map(publicTab),
      activeTabId: active?.tabId ?? null,
      activeContext: active?.context ? structuredClone(active.context) : null,
      activeContextAmbiguous: equallyRecent.length > 1,
      resources: [...this.recentResources.values()].slice(-MAX_RECENT_RESOURCES).map((row) => structuredClone(row)),
      operations: [...this.operations.values()].slice(-MAX_RECENT_OPERATIONS).map((row) => structuredClone(row)),
    }
  }

  captureContext(): UiStateSnapshot {
    return structuredClone(this.getStateSnapshot())
  }

  captureTurnContext(): UiTurnContextV1 {
    const snapshot = this.getStateSnapshot()
    const connectedTabs = snapshot.tabs.filter((tab) => tab.connected)
    return {
      schemaVersion: "1",
      capturedAt: snapshot.generatedAt,
      serverSeq: snapshot.serverSeq,
      activeTabId: snapshot.activeTabId,
      activeContext: snapshot.activeContext,
      activeContextAmbiguous: snapshot.activeContextAmbiguous,
      // A Turn describes what is open now. The retained reconnect cache is an
      // implementation detail and is deliberately excluded from persistence
      // and the Agent-facing debug subscription.
      tabs: connectedTabs.slice(0, 32).map((tab) => ({
        ...tab,
        documents: tab.documents.slice(0, 16),
      })),
    }
  }

  onChange(listener: () => void) {
    this.changeListeners.add(listener)
    return () => this.changeListeners.delete(listener)
  }

  async requestDocumentSnapshot(args: {
    operationId?: string
    documentId: string
    includeContent?: boolean
  }): Promise<UiDocumentSnapshotResult> {
    const operationId = this.requireOperationId(args.operationId ?? randomUUID())
    const signature = JSON.stringify({ type: "snapshot", documentId: args.documentId, includeContent: args.includeContent ?? true })
    const completed = this.completedOperation(operationId, signature)
    if (completed) return completed as UiDocumentSnapshotResult
    const target = this.resolveDocumentTarget(args.documentId, "document-read")
    return await this.dispatchDocumentRequest<UiDocumentSnapshotResult>({
      kind: "snapshot",
      operationId,
      signature,
      target,
      request: {
        type: "document.snapshot.request",
        operationId,
        documentId: args.documentId,
        includeContent: args.includeContent ?? true,
      },
    })
  }

  async requestDocumentPatch(args: {
    operationId?: string
    documentId: string
    baseRevision: number
    baseDigest: string
    patch: UiTextPatch
    save?: boolean
  }): Promise<UiDocumentPatchResult> {
    const operationId = this.requireOperationId(args.operationId ?? randomUUID())
    const patch = sanitizeTextPatch(args.patch)
    const signature = JSON.stringify({
      type: "patch",
      documentId: args.documentId,
      baseRevision: args.baseRevision,
      baseDigest: args.baseDigest,
      patch,
      save: args.save ?? true,
    })
    const completed = this.completedOperation(operationId, signature)
    if (completed) return completed as UiDocumentPatchResult
    const target = this.resolveDocumentTarget(args.documentId, "document-write")
    const document = target.documents.get(args.documentId)!
    if (document.readOnly) throw new UiSyncError("read_only", "The selected document is read-only.")
    if (document.revision !== args.baseRevision || document.contentDigest !== args.baseDigest) {
      throw new UiSyncError("revision_conflict", "The selected document changed; read it again before applying a patch.")
    }
    return await this.dispatchDocumentRequest<UiDocumentPatchResult>({
      kind: "patch",
      operationId,
      signature,
      target,
      request: {
        type: "document.patch.request",
        operationId,
        documentId: args.documentId,
        baseRevision: args.baseRevision,
        baseDigest: boundedString(args.baseDigest, "baseDigest", 128),
        patch,
        save: args.save ?? true,
      },
    })
  }

  dispose() {
    clearInterval(this.cleanupTimer)
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer)
      pending.reject(new UiSyncError("operation_interrupted", "ui-sync is shutting down.", true))
    }
    this.pending.clear()
    this.sockets.clear()
    this.tabs.clear()
    this.changeListeners.clear()
  }

  private async handleCommand(socket: UiSyncSocket, id: string, command: UiClientCommand) {
    if (command.type === "ui.register") {
      this.register(socket, command)
      send(socket, { v: 1, type: "ack", id, result: this.getStateSnapshot() })
      return
    }
    const tab = this.requireRegistered(socket)
    tab.lastSeenAt = this.now()
    switch (command.type) {
      case "system.ping":
        send(socket, { v: 1, type: "ack", id, result: { serverSeq: this.serverSeq } })
        return
      case "presence.update":
        if (typeof command.visible !== "boolean" || typeof command.focused !== "boolean"
            || (command.interacted !== undefined && typeof command.interacted !== "boolean")) {
          throw new UiSyncError("invalid_request", "Presence fields must be booleans.")
        }
        tab.visible = command.visible
        tab.focused = command.focused
        if (command.interacted) tab.lastInteractionAt = this.now()
        this.bumpAndBroadcast()
        send(socket, { v: 1, type: "ack", id })
        return
      case "context.replace":
        this.requireCapability(tab, "context")
        tab.context = sanitizeUiContext(command.context)
        if (tab.focused && tab.visible) tab.lastInteractionAt = this.now()
        this.bumpAndBroadcast()
        send(socket, { v: 1, type: "ack", id, result: { serverSeq: this.serverSeq } })
        return
      case "document.open": {
        this.requireCapability(tab, "document-read")
        const document = sanitizeUiDocument(command.document)
        tab.documents.set(document.documentId, document)
        this.bumpAndBroadcast()
        send(socket, { v: 1, type: "ack", id, result: { serverSeq: this.serverSeq } })
        return
      }
      case "document.update": {
        this.requireCapability(tab, "document-read")
        const document = sanitizeUiDocument(command.document)
        const existing = tab.documents.get(document.documentId)
        if (!existing) throw new UiSyncError("document_not_open", "The document is not open in this tab.")
        const baseRevision = nonNegativeInteger(command.baseRevision, "baseRevision")
        if (existing.revision !== baseRevision || document.revision !== baseRevision + 1) {
          throw new UiSyncError("revision_conflict", "Document update does not match the current revision.")
        }
        tab.documents.set(document.documentId, document)
        if (tab.context?.documentId === document.documentId) {
          tab.context = { ...tab.context, documentRevision: document.revision }
        }
        if (tab.focused && tab.visible) tab.lastInteractionAt = this.now()
        this.bumpAndBroadcast()
        send(socket, { v: 1, type: "ack", id, result: { serverSeq: this.serverSeq } })
        return
      }
      case "document.close":
        tab.documents.delete(boundedString(command.documentId, "documentId"))
        this.bumpAndBroadcast()
        send(socket, { v: 1, type: "ack", id })
        return
      case "document.snapshot.respond":
        this.resolveSnapshotResponse(tab, command)
        send(socket, { v: 1, type: "ack", id })
        return
      case "document.patch.respond":
        this.resolvePatchResponse(tab, command)
        send(socket, { v: 1, type: "ack", id })
        return
      case "resource.changed": {
        this.requireCapability(tab, "resource-events")
        const change = sanitizeResourceChange(command.change)
        if (!this.recentResources.has(change.eventId)) {
          this.recentResources.set(change.eventId, change)
          while (this.recentResources.size > MAX_RECENT_RESOURCES) {
            this.recentResources.delete(this.recentResources.keys().next().value!)
          }
          this.bumpAndBroadcast("resource", change)
        }
        send(socket, { v: 1, type: "ack", id, result: { serverSeq: this.serverSeq } })
        return
      }
      case "operation.changed": {
        this.requireCapability(tab, "operation-events")
        const operation = sanitizeOperation(command.operation)
        const previous = this.operations.get(operation.operationId)
        if (previous && Date.parse(operation.updatedAt) < Date.parse(previous.updatedAt)) {
          throw new UiSyncError("revision_conflict", "Operation update is older than the current state.")
        }
        this.operations.set(operation.operationId, operation)
        while (this.operations.size > MAX_RECENT_OPERATIONS) {
          this.operations.delete(this.operations.keys().next().value!)
        }
        this.bumpAndBroadcast("operation", operation)
        send(socket, { v: 1, type: "ack", id, result: { serverSeq: this.serverSeq } })
        return
      }
    }
  }

  private register(socket: UiSyncSocket, command: Extract<UiClientCommand, { type: "ui.register" }>) {
    if (!TAB_ID_RE.test(command.tabId)) throw new UiSyncError("invalid_request", "tabId is invalid.")
    if (!CLIENT_KINDS.has(command.clientKind)) throw new UiSyncError("invalid_request", "clientKind is invalid.")
    if (!Array.isArray(command.capabilities) || command.capabilities.length > CAPABILITIES.size
        || command.capabilities.some((capability) => !CAPABILITIES.has(capability))) {
      throw new UiSyncError("invalid_request", "capabilities contain an unsupported value.")
    }
    const capabilities = [...new Set(command.capabilities)]
    const now = this.now()
    const existing = this.tabs.get(command.tabId)
    if (existing?.socket && existing.socket !== socket) {
      existing.socket.close?.(4000, "tab reconnected")
    }
    const tab: InternalTab = existing ?? {
      socket,
      tabId: command.tabId,
      clientKind: command.clientKind,
      capabilities,
      connected: true,
      visible: false,
      focused: false,
      openedAt: now,
      lastSeenAt: now,
      lastInteractionAt: now,
      context: null,
      documents: new Map(),
    }
    tab.socket = socket
    tab.clientKind = command.clientKind
    tab.capabilities = capabilities
    tab.connected = true
    tab.lastSeenAt = now
    socket.data.tabId = command.tabId
    this.tabs.set(command.tabId, tab)
    this.bumpAndBroadcast()
  }

  private requireRegistered(socket: UiSyncSocket): InternalTab {
    const tabId = socket.data.tabId
    const tab = tabId ? this.tabs.get(tabId) : null
    if (!tab || tab.socket !== socket || !tab.connected) {
      throw new UiSyncError("registration_required", "Register this ui-sync connection first.")
    }
    return tab
  }

  private requireCapability(tab: InternalTab, capability: UiCapability) {
    if (!tab.capabilities.includes(capability)) {
      throw new UiSyncError("capability_required", `The tab did not register '${capability}' capability.`)
    }
  }

  private requireOperationId(value: string): string {
    if (!OPERATION_ID_RE.test(value)) throw new UiSyncError("invalid_request", "operationId is invalid.")
    return value
  }

  private pushSubscription(socket: UiSyncSocket, id: string, topic: UiSubscriptionTopic) {
    if (topic.type === "ui-state") {
      send(socket, { v: 1, type: "snapshot", id, snapshot: { type: "ui-state", data: this.getStateSnapshot() } })
      return
    }
    if (topic.type === "resource-events") {
      send(socket, {
        v: 1,
        type: "snapshot",
        id,
        snapshot: { type: "resource-events", data: [...this.recentResources.values()].map((row) => structuredClone(row)) },
      })
      return
    }
    send(socket, {
      v: 1,
      type: "snapshot",
      id,
      snapshot: { type: "operation-events", data: [...this.operations.values()].map((row) => structuredClone(row)) },
    })
  }

  private bumpAndBroadcast(kind?: "resource" | "operation", value?: UiResourceChange | UiOperationState) {
    this.serverSeq += 1
    for (const socket of this.sockets) {
      if (!socket.data.tabId) continue
      for (const [id, topic] of socket.data.subscriptions) {
        if (topic.type === "ui-state") {
          this.pushSubscription(socket, id, topic)
          continue
        }
        if (kind === "resource" && topic.type === "resource-events" && value) {
          send(socket, { v: 1, type: "event", id, event: { type: "resource.changed", serverSeq: this.serverSeq, change: value as UiResourceChange } })
        }
        if (kind === "operation" && topic.type === "operation-events" && value) {
          send(socket, { v: 1, type: "event", id, event: { type: "operation.changed", serverSeq: this.serverSeq, operation: value as UiOperationState } })
        }
      }
    }
    for (const listener of this.changeListeners) listener()
  }

  private pruneStaleTabs() {
    const cutoff = this.now() - this.staleTtlMs
    let changed = false
    for (const [tabId, tab] of this.tabs) {
      if (!tab.connected && tab.lastSeenAt <= cutoff) {
        this.tabs.delete(tabId)
        changed = true
      }
    }
    if (changed) this.bumpAndBroadcast()
  }

  private resolveDocumentTarget(documentId: string, capability: UiCapability): InternalTab {
    boundedString(documentId, "documentId")
    const candidates = [...this.tabs.values()].filter((tab) => tab.connected
      && tab.socket
      && tab.capabilities.includes(capability)
      && tab.documents.has(documentId))
    if (!candidates.length) throw new UiSyncError("document_not_open", "The document is not open in a capable frontend.")
    const dirty = candidates.filter((tab) => tab.documents.get(documentId)?.dirty)
    if (dirty.length > 1) throw new UiSyncError("editor_ambiguous", "More than one editor has unsaved changes for this document.")
    if (dirty.length === 1) return dirty[0]!
    candidates.sort((left, right) => Number(right.focused) - Number(left.focused)
      || Number(right.visible) - Number(left.visible)
      || right.lastInteractionAt - left.lastInteractionAt)
    return candidates[0]!
  }

  private async dispatchDocumentRequest<TResult>(args: {
    kind: PendingDocumentRequest["kind"]
    operationId: string
    signature: string
    target: InternalTab
    request: Parameters<typeof send>[1] extends never ? never : Extract<UiServerEnvelope, { type: "request" }>["request"]
  }): Promise<TResult> {
    if (this.pending.has(args.operationId)) {
      throw new UiSyncError("operation_in_progress", "This operation is already in progress.", true)
    }
    const socket = args.target.socket
    if (!socket) throw new UiSyncError("page_gone", "The selected editor disconnected.", true)
    return await new Promise<TResult>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(args.operationId)
        reject(new UiSyncError("operation_timeout", "The selected editor did not answer in time.", true))
      }, this.requestTimeoutMs)
      this.pending.set(args.operationId, {
        kind: args.kind,
        tabId: args.target.tabId,
        documentId: args.request.documentId,
        signature: args.signature,
        timer,
        resolve: (value) => resolve(value as TResult),
        reject,
      })
      send(socket, { v: 1, type: "request", id: args.operationId, request: args.request })
    })
  }

  private resolveSnapshotResponse(tab: InternalTab, command: Extract<UiClientCommand, { type: "document.snapshot.respond" }>) {
    const pending = this.requirePendingResponse(tab, command.operationId, command.documentId, "snapshot")
    if (command.errorCode) {
      this.rejectPending(command.operationId, pending, new UiSyncError(
        boundedString(command.errorCode, "errorCode", 96),
        "The editor rejected the snapshot request.",
      ))
      return
    }
    const result: UiDocumentSnapshotResult = {
      operationId: command.operationId,
      documentId: command.documentId,
      revision: nonNegativeInteger(command.revision, "revision"),
      contentDigest: boundedString(command.contentDigest, "contentDigest", 128),
      content: command.content === undefined
        ? undefined
        : boundedString(command.content, "content", MAX_DOCUMENT_CONTENT, true),
    }
    this.completePending(command.operationId, pending, result)
  }

  private resolvePatchResponse(tab: InternalTab, command: Extract<UiClientCommand, { type: "document.patch.respond" }>) {
    const pending = this.requirePendingResponse(tab, command.operationId, command.documentId, "patch")
    if (command.status !== "applied") {
      const code = command.errorCode
        ? boundedString(command.errorCode, "errorCode", 96)
        : command.status === "unknown" ? "operation_unknown" : "revision_conflict"
      this.rejectPending(command.operationId, pending, new UiSyncError(
        code,
        optionalString(command.message, "message", 512) ?? "The editor rejected the patch.",
        command.status === "unknown",
      ))
      return
    }
    const revision = nonNegativeInteger(command.revision, "revision")
    const savedRevision = nonNegativeInteger(command.savedRevision, "savedRevision")
    const contentDigest = boundedString(command.contentDigest, "contentDigest", 128)
    if (savedRevision > revision || typeof command.dirty !== "boolean") {
      this.rejectPending(command.operationId, pending, new UiSyncError(
        "invalid_request",
        "The editor returned invalid savedRevision/dirty state.",
      ))
      return
    }
    const document = tab.documents.get(command.documentId)
    if (!document) {
      this.rejectPending(command.operationId, pending, new UiSyncError("document_not_open", "The document closed during the patch."))
      return
    }
    tab.documents.set(command.documentId, {
      ...document,
      revision,
      savedRevision,
      contentDigest,
      dirty: command.dirty,
    })
    if (tab.context?.documentId === command.documentId) {
      tab.context = { ...tab.context, documentRevision: revision }
    }
    const result: UiDocumentPatchResult = {
      operationId: command.operationId,
      documentId: command.documentId,
      status: "applied",
      revision,
      savedRevision,
      contentDigest,
      dirty: command.dirty,
    }
    this.completePending(command.operationId, pending, result)
    this.bumpAndBroadcast()
  }

  private requirePendingResponse(
    tab: InternalTab,
    operationId: string,
    documentId: string,
    kind: PendingDocumentRequest["kind"],
  ): PendingDocumentRequest {
    this.requireOperationId(operationId)
    boundedString(documentId, "documentId")
    const pending = this.pending.get(operationId)
    if (!pending) throw new UiSyncError("operation_unknown", "The document operation is not active.")
    if (pending.kind !== kind || pending.tabId !== tab.tabId || pending.documentId !== documentId) {
      throw new UiSyncError("operation_mismatch", "The document response does not match its request.")
    }
    return pending
  }

  private completePending(operationId: string, pending: PendingDocumentRequest, result: unknown) {
    clearTimeout(pending.timer)
    this.pending.delete(operationId)
    this.completedOperations.set(operationId, { signature: pending.signature, result: structuredClone(result) })
    while (this.completedOperations.size > 512) {
      this.completedOperations.delete(this.completedOperations.keys().next().value!)
    }
    pending.resolve(result)
  }

  private completedOperation(operationId: string, signature: string) {
    const completed = this.completedOperations.get(operationId)
    if (!completed) return null
    if (completed.signature !== signature) {
      throw new UiSyncError("idempotency_conflict", "operationId was already used for a different document request.")
    }
    return structuredClone(completed.result)
  }

  private rejectPending(operationId: string, pending: PendingDocumentRequest, error: UiSyncError) {
    clearTimeout(pending.timer)
    this.pending.delete(operationId)
    pending.reject(error)
  }
}
