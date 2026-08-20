export const UI_SYNC_PROTOCOL_VERSION = 1 as const

export type UiClientKind = "engine-spa" | "jupyter"

export type UiCapability =
  | "presence"
  | "context"
  | "document-read"
  | "document-write"
  | "resource-events"
  | "operation-events"

export interface UiResourceRef {
  kind: string
  id: string
  version?: string
  digest?: string
  label?: string
}

export interface UiSelection {
  kind: string
  id: string
  label?: string
}

export interface UiContextState {
  route: string
  view: string
  subview?: string
  projectId?: string
  resourceRefs: UiResourceRef[]
  selection?: UiSelection
  documentId?: string
  documentRevision?: number
}

export interface UiDocumentState {
  documentId: string
  kind:
    | "pipeline-draft"
    | "environment-draft"
    | "analysis-draft"
    | "backtest-draft"
    | "visualization-draft"
    | "dataset-draft"
    | "mining-draft"
    | "jupyter-text"
    | "jupyter-notebook"
  label: string
  projectId?: string
  workspaceId?: string
  relativePath?: string
  language?: string
  revision: number
  savedRevision: number
  contentDigest: string
  dirty: boolean
  readOnly: boolean
}

export interface UiTabSnapshot {
  tabId: string
  clientKind: UiClientKind
  capabilities: UiCapability[]
  connected: boolean
  visible: boolean
  focused: boolean
  openedAt: string
  lastSeenAt: string
  lastInteractionAt: string
  context: UiContextState | null
  documents: UiDocumentState[]
}

export interface UiResourceChange {
  eventId: string
  kind: string
  id: string
  change: "changed" | "published" | "archived" | "deleted" | "validation-changed"
  version?: string
  digest?: string
  occurredAt: string
}

export interface UiOperationState {
  operationId: string
  kind: string
  resourceId?: string
  status: "started" | "progress" | "waiting" | "completed" | "failed" | "interrupted"
  progress?: number
  message?: string
  errorCode?: string
  updatedAt: string
}

export interface UiStateSnapshot {
  serverSeq: number
  generatedAt: string
  tabs: UiTabSnapshot[]
  activeTabId: string | null
  activeContext: UiContextState | null
  activeContextAmbiguous: boolean
  resources: UiResourceChange[]
  operations: UiOperationState[]
}

/** Immutable, bounded snapshot persisted with one Agent Turn. Live operation
 * and resource feeds are deliberately omitted; a running Agent can query the
 * Hub again when it explicitly needs newer UI state. */
export interface UiTurnContextV1 {
  schemaVersion: "1"
  capturedAt: string
  serverSeq: number
  activeTabId: string | null
  activeContext: UiContextState | null
  activeContextAmbiguous: boolean
  tabs: UiTabSnapshot[]
}

export type UiSubscriptionTopic =
  | { type: "ui-state" }
  | { type: "resource-events" }
  | { type: "operation-events" }

export type UiClientCommand =
  | {
      type: "ui.register"
      tabId: string
      clientKind: UiClientKind
      capabilities: UiCapability[]
      lastServerSeq?: number
    }
  | { type: "system.ping" }
  | { type: "presence.update"; visible: boolean; focused: boolean; interacted?: boolean }
  | { type: "context.replace"; context: UiContextState }
  | { type: "document.open"; document: UiDocumentState }
  | { type: "document.update"; document: UiDocumentState; baseRevision: number }
  | { type: "document.close"; documentId: string }
  | {
      type: "document.snapshot.respond"
      operationId: string
      documentId: string
      revision: number
      contentDigest: string
      content?: string
      errorCode?: string
    }
  | {
      type: "document.patch.respond"
      operationId: string
      documentId: string
      status: "applied" | "rejected" | "unknown"
      revision?: number
      savedRevision?: number
      contentDigest?: string
      dirty?: boolean
      errorCode?: string
      message?: string
    }
  | { type: "resource.changed"; change: UiResourceChange }
  | { type: "operation.changed"; operation: UiOperationState }

export type UiClientEnvelope =
  | { v: 1; type: "subscribe"; id: string; topic: UiSubscriptionTopic }
  | { v: 1; type: "unsubscribe"; id: string }
  | { v: 1; type: "command"; id: string; command: UiClientCommand }

export type UiServerRequest =
  | { type: "document.snapshot.request"; operationId: string; documentId: string; includeContent: boolean }
  | {
      type: "document.patch.request"
      operationId: string
      documentId: string
      baseRevision: number
      baseDigest: string
      patch: UiTextPatch
      save: boolean
    }

export interface UiTextPatch {
  type: "replace"
  start: number
  end: number
  text: string
}

export type UiServerSnapshot =
  | { type: "ui-state"; data: UiStateSnapshot }
  | { type: "resource-events"; data: UiResourceChange[] }
  | { type: "operation-events"; data: UiOperationState[] }

export type UiServerEvent =
  | { type: "ui-state.changed"; serverSeq: number }
  | { type: "resource.changed"; serverSeq: number; change: UiResourceChange }
  | { type: "operation.changed"; serverSeq: number; operation: UiOperationState }

export type UiServerEnvelope =
  | { v: 1; type: "snapshot"; id: string; snapshot: UiServerSnapshot }
  | { v: 1; type: "event"; id: string; event: UiServerEvent }
  | { v: 1; type: "request"; id: string; request: UiServerRequest }
  | { v: 1; type: "ack"; id: string; result?: unknown }
  | { v: 1; type: "error"; id?: string; code: string; message: string; retryable?: boolean }

export function isUiClientEnvelope(value: unknown): value is UiClientEnvelope {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false
  const record = value as Record<string, unknown>
  if (record.v !== UI_SYNC_PROTOCOL_VERSION || typeof record.id !== "string" || !record.id) return false
  if (record.type === "unsubscribe") {
    return Object.keys(record).every((key) => ["v", "type", "id"].includes(key))
  }
  if (record.type === "subscribe") {
    const topic = record.topic
    return !!topic && typeof topic === "object" && !Array.isArray(topic)
      && ["ui-state", "resource-events", "operation-events"].includes(String((topic as Record<string, unknown>).type))
  }
  if (record.type === "command") {
    const command = record.command
    return !!command && typeof command === "object" && !Array.isArray(command)
      && typeof (command as Record<string, unknown>).type === "string"
  }
  return false
}
