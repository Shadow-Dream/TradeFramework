import type { ServerWebSocket } from "bun"
import { PROTOCOL_VERSION } from "../shared/types"
import type { ClientEnvelope, ServerEnvelope, SubscriptionTopic } from "../shared/protocol"
import { isClientEnvelope } from "../shared/protocol"
import type { AgentCoordinator } from "./agent"
import type { AnalyticsReporter } from "./analytics"
import { NoopAnalyticsReporter } from "./analytics"
import type { AppSettingsManager } from "./app-settings"
import { DiffStore } from "./diff-store"
import { EventStore } from "./event-store"
import { KeybindingsManager } from "./keybindings"
import { TerminalManager } from "./terminal-manager"
import type { WorktreeProbe } from "./worktree-probe"
import type { ProviderAuthManager } from "./provider-auth"
import type { UsageLimitsManager } from "./usage-limits"
import type { TradeProjectCatalog } from "./trade-project-catalog"
import type { UiSyncHub } from "./ui-sync-hub"
import { deriveChatSnapshot, deriveChatTouchedFiles, deriveLocalProjectsSnapshot, deriveSidebarData } from "./read-models"
import type {
  ChatSnapshot,
  UsageLimitsSnapshot,
} from "../shared/types"
import type { TradeIdentity } from "./trade-session"


/**
 * Cap on ids per `chat.getToolEntries`. The largest honest request is one tool
 * group's members; beyond that something is walking the transcript.
 */
const MAX_TOOL_ENTRY_REQUEST = 256
const CLIENT_REQUEST_ID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

/** Coalescing window for transcript pushes — roughly one animation frame. */
const CHAT_BROADCAST_INTERVAL_MS = 16

/**
 * Coalescing window for sidebar pushes driven by a running turn.
 *
 * Far slower than the transcript's because the sidebar shows titles, status
 * glyphs and relative ages — none of which a reader can follow at frame rate,
 * and all of which cost a full re-derive plus a whole-snapshot re-render to
 * deliver. See `armPendingSidebarTimer`.
 */
const SIDEBAR_BROADCAST_INTERVAL_MS = 400

export interface ClientState {
  subscriptions: Map<string, SubscriptionTopic>
  snapshotSignatures: Map<string, string>
  /**
   * Absolute transcript span last sent per chat subscription, so the next push
   * can carry only the entries past it. Reset whenever the subscription is
   * (re)created, which is also what makes reconnect safe: a fresh id has no
   * span and therefore gets a full window.
   */
  chatEntrySpans?: Map<string, { start: number; end: number }>
  protectedDraftChatIds?: Set<string>
  /** Verified TradeEngine identity; raw Cookie is only kept in memory for revalidation. */
  identity: TradeIdentity
  sessionCookie: string
}

interface CreateWsRouterArgs {
  store: EventStore
  diffStore: Pick<DiffStore, "getProjectSnapshot" | "getSnapshotVersion" | "refreshSnapshot" | "readPatch">
  worktreeProbe: Pick<WorktreeProbe, "getStates" | "getRepoLabels" | "getProjectsWithoutRepo">
  agent: AgentCoordinator
  terminals: TerminalManager
  keybindings: KeybindingsManager
  appSettings: Pick<AppSettingsManager, "getSnapshot" | "writePatch" | "onChange">
  analytics?: AnalyticsReporter
  refreshProjects: () => Promise<void>
  machineDisplayName: string
  usageLimits?: Pick<UsageLimitsManager, "getSnapshot" | "refresh" | "onChange"> | null
  providerAuth?: Pick<
    ProviderAuthManager,
    | "getSnapshot"
    | "refresh"
    | "startLogin"
    | "cancelLogin"
    | "setDeepSeekApiKey"
    | "onChange"
  > | null
  /** Server-owned logical project catalog. No browser command can add a path. */
  projectCatalog: Pick<TradeProjectCatalog, "has" | "resolve" | "resolveKnown">
  uiSyncHub: Pick<UiSyncHub, "captureTurnContext" | "onChange">
}

interface SnapshotBroadcastFilter {
  includeSidebar?: boolean
  includeLocalProjects?: boolean
  includeKeybindings?: boolean
  includeAppSettings?: boolean
  includeUsageLimits?: boolean
  includeProviderAuth?: boolean
  includeUiContext?: boolean
  chatIds?: Set<string>
  projectIds?: Set<string>
  terminalIds?: Set<string>
}

interface SnapshotComputationCache {
  sidebar?: {
    data: ReturnType<typeof deriveSidebarData>
    signature: string
  }
  /**
   * Derived chat snapshots keyed by chat, shared across sockets in one
   * broadcast.
   *
   * The derive is shared but the serialization is not: each socket is at its
   * own point in the transcript, so the body it needs differs. Deriving once
   * is the expensive half; serializing an incremental body is cheap.
   */
  chat?: Map<string, ChatSnapshot | null>
}

function send(ws: ServerWebSocket<ClientState>, message: ServerEnvelope) {
  const payload = JSON.stringify(message)
  ws.send(payload)
  return payload.length
}

const PUBLIC_PAYLOAD_BLOCKED_KEYS = new Set([
  "absolutePath",
  "localPath",
  "cwd",
  "workspacePath",
  "controlPath",
  "archivePath",
  "manifestPath",
  "debugRaw",
])

const CLIENT_FORBIDDEN_KEYS = new Set([
  "absolutePath",
  "localPath",
  "cwd",
  "workspacePath",
  "controlPath",
  "archivePath",
  "manifestPath",
])

export function containsForbiddenClientKey(value: unknown, depth = 0): boolean {
  if (depth > 24 || !value || typeof value !== "object") return false
  if (Array.isArray(value)) return value.some((item) => containsForbiddenClientKey(item, depth + 1))
  return Object.entries(value as Record<string, unknown>)
    .some(([key, item]) => CLIENT_FORBIDDEN_KEYS.has(key) || containsForbiddenClientKey(item, depth + 1))
}

/** Browser projection for provider/tool payloads and error prose. */
function redactBrowserPayload(value: unknown, projectRoot?: string, depth = 0): unknown {
  if (depth > 24) return "<truncated>"
  if (typeof value === "string") {
    let result = projectRoot ? value.replaceAll(projectRoot, "<project>") : value
    result = result.replace(/\/(?:file\/share|home|root|tmp|var\/lib|run)\/[^\s"'<>]*/g, "<internal-path>")
    return result
  }
  if (Array.isArray(value)) return value.slice(0, 512).map((item) => redactBrowserPayload(item, projectRoot, depth + 1))
  if (!value || typeof value !== "object") return value
  return Object.fromEntries(Object.entries(value as Record<string, unknown>)
    .filter(([key]) => !PUBLIC_PAYLOAD_BLOCKED_KEYS.has(key))
    .slice(0, 512)
    .map(([key, item]) => [key, redactBrowserPayload(item, projectRoot, depth + 1)]))
}

/**
 * Send a snapshot whose body was already serialized once for this broadcast,
 * so N subscribers cost one JSON.stringify instead of N.
 */
function sendSerializedSnapshot(ws: ServerWebSocket<ClientState>, id: string, snapshotJson: string) {
  ws.send(`{"v":${PROTOCOL_VERSION},"type":"snapshot","id":${JSON.stringify(id)},"snapshot":${snapshotJson}}`)
}

function ensureChatEntrySpans(ws: ServerWebSocket<ClientState>) {
  if (!ws.data.chatEntrySpans) {
    ws.data.chatEntrySpans = new Map()
  }

  return ws.data.chatEntrySpans
}

function ensureSnapshotSignatures(ws: ServerWebSocket<ClientState>) {
  if (!ws.data.snapshotSignatures) {
    ws.data.snapshotSignatures = new Map()
  }

  return ws.data.snapshotSignatures
}

export function createWsRouter({
  store,
  diffStore,
  worktreeProbe,
  agent,
  terminals,
  keybindings,
  appSettings,
  analytics,
  refreshProjects,
  machineDisplayName,
  usageLimits,
  providerAuth,
  projectCatalog,
  uiSyncHub,
}: CreateWsRouterArgs) {
  const sockets = new Set<ServerWebSocket<ClientState>>()
  const terminalOwners = new Map<string, string>()
  let pendingBroadcastTimer: ReturnType<typeof setTimeout> | null = null
  let pendingBroadcastAll = false
  const pendingBroadcastChatIds = new Set<string>()
  let pendingSidebarTimer: ReturnType<typeof setTimeout> | null = null
  const resolvedAnalytics = analytics ?? NoopAnalyticsReporter
  function requireFixedProject(projectId: string) {
    const project = store.getProject(projectId)
    if (!project) throw new Error("Project not found")
    if (!projectCatalog.has(project.id) || project.workspaceKey !== project.id) {
      throw new Error("Project is not in the TradeEngine catalog.")
    }
    return project
  }

  function knownProjectPath(projectId: string) {
    return projectCatalog.resolveKnown(projectId)
  }

  function requireFixedChat(chatId: string) {
    const chat = store.getChat(chatId)
    if (!chat) throw new Error("Chat not found")
    requireFixedProject(chat.projectId)
    return chat
  }

  function requireOwnedChat(ws: ServerWebSocket<ClientState>, chatId: string) {
    const chat = requireFixedChat(chatId)
    if (chat.ownerId !== ws.data.identity.userId) throw new Error("Chat not found")
    return chat
  }

  function requireAdmin(ws: ServerWebSocket<ClientState>) {
    if (ws.data.identity.role !== "admin") {
      throw new Error("Administrator access is required for backend credentials.")
    }
  }

  function requireClientRequestId(value: string | undefined) {
    if (!value || !CLIENT_REQUEST_ID_RE.test(value)) {
      throw new Error("clientRequestId must be a UUID.")
    }
    return value
  }

  function getProtectedChatIds() {
    const activeStatuses = agent.getActiveStatuses()
    const drainingChatIds = typeof agent.getDrainingChatIds === "function"
      ? agent.getDrainingChatIds()
      : new Set<string>()
    return new Set([
      ...activeStatuses.keys(),
      ...drainingChatIds.values(),
    ])
  }

  function getProtectedDraftChatIds(extraSockets?: Iterable<ServerWebSocket<ClientState>>) {
    const protectedChatIds = new Set<string>()

    for (const socket of sockets) {
      for (const chatId of socket.data.protectedDraftChatIds ?? []) {
        protectedChatIds.add(chatId)
      }
    }

    for (const socket of extraSockets ?? []) {
      for (const chatId of socket.data.protectedDraftChatIds ?? []) {
        protectedChatIds.add(chatId)
      }
    }

    return protectedChatIds
  }

  async function maybePruneStaleEmptyChats(extraSockets?: Iterable<ServerWebSocket<ClientState>>) {
    const activeChatIds = getProtectedChatIds()
    const protectedDraftChatIds = getProtectedDraftChatIds(extraSockets)
    return await store.pruneStaleEmptyChats({
      activeChatIds,
      protectedChatIds: protectedDraftChatIds,
    })
  }

  async function maybeAutoArchiveStaleChats(extraSockets?: Iterable<ServerWebSocket<ClientState>>) {
    const activeChatIds = getProtectedChatIds()
    const protectedDraftChatIds = getProtectedDraftChatIds(extraSockets)
    return await store.autoArchiveStaleChats({
      activeChatIds,
      protectedChatIds: protectedDraftChatIds,
    })
  }

  async function maybeDeleteStaleChats(extraSockets?: Iterable<ServerWebSocket<ClientState>>) {
    const activeChatIds = getProtectedChatIds()
    const protectedDraftChatIds = getProtectedDraftChatIds(extraSockets)
    return await store.deleteStaleChats({
      activeChatIds,
      protectedChatIds: protectedDraftChatIds,
    })
  }

  function shouldIncludeTopic(topic: SubscriptionTopic, filter?: SnapshotBroadcastFilter) {
    if (!filter) {
      return true
    }

    if (topic.type === "sidebar") {
      return Boolean(filter.includeSidebar)
    }
    if (topic.type === "local-projects") {
      return Boolean(filter.includeLocalProjects)
    }
    if (topic.type === "keybindings") {
      return Boolean(filter.includeKeybindings)
    }
    if (topic.type === "app-settings") {
      return Boolean(filter.includeAppSettings)
    }
    if (topic.type === "usage-limits") {
      return Boolean(filter.includeUsageLimits)
    }
    if (topic.type === "provider-auth") {
      return Boolean(filter.includeProviderAuth)
    }

    if (topic.type === "ui-context") return Boolean(filter.includeUiContext)
    if (topic.type === "chat") {
      return filter.chatIds?.has(topic.chatId) ?? false
    }
    if (topic.type === "project-git") {
      return filter.projectIds?.has(topic.projectId) ?? false
    }
    if (topic.type === "terminal") {
      return filter.terminalIds?.has(topic.terminalId) ?? false
    }

    return true
  }

  function getSidebarSnapshotCacheEntry(ownerId: string, cache?: SnapshotComputationCache) {
    if (cache?.sidebar) {
      return cache.sidebar
    }

    const activeStatuses = agent.getActiveStatuses()
    const pendingToolKinds = new Map<string, string>()
    for (const [chatId, status] of activeStatuses) {
      if (status !== "waiting_for_user") continue
      const pendingTool = agent.getPendingTool(chatId)
      if (pendingTool) pendingToolKinds.set(chatId, pendingTool.toolKind)
    }
    const data = deriveSidebarData(store.state, activeStatuses, {
      ownerId,
      sidebarProjectOrder: store.getSidebarProjectOrder(),
      drainingChatIds: agent.getDrainingChatIds(),
      pendingToolKinds,
      workingTrees: worktreeProbe.getStates(),
      repoLabels: worktreeProbe.getRepoLabels(),
      projectsWithoutRepo: worktreeProbe.getProjectsWithoutRepo(),
    })

    const sidebar = {
      data,
      signature: JSON.stringify({
        type: "sidebar" as const,
        data,
      }),
    }

    if (cache) {
      cache.sidebar = sidebar
    }

    return sidebar
  }

  function getProjectGitSignature(projectId: string): string {
    return store.getProject(projectId)
      ? `project-git:${projectId}:v${diffStore.getSnapshotVersion(projectId)}`
      : `project-git:${projectId}:none`
  }

  function createEnvelope(ws: ServerWebSocket<ClientState>, id: string, topic: SubscriptionTopic, cache?: SnapshotComputationCache): ServerEnvelope {
    if (topic.type === "sidebar") {
      const sidebar = getSidebarSnapshotCacheEntry(ws.data.identity.userId, cache)
      return {
        v: PROTOCOL_VERSION,
        type: "snapshot",
        id,
        snapshot: {
          type: "sidebar",
          data: sidebar.data,
        },
      }
    }

    if (topic.type === "local-projects") {
      const data = deriveLocalProjectsSnapshot(store.state, machineDisplayName, ws.data.identity.userId)

      return {
        v: PROTOCOL_VERSION,
        type: "snapshot",
        id,
        snapshot: {
          type: "local-projects",
          data,
        },
      }
    }

    if (topic.type === "keybindings") {
      return {
        v: PROTOCOL_VERSION,
        type: "snapshot",
        id,
        snapshot: {
          type: "keybindings",
          data: keybindings.getSnapshot(),
        },
      }
    }

    if (topic.type === "app-settings") {
      return {
        v: PROTOCOL_VERSION,
        type: "snapshot",
        id,
        snapshot: {
          type: "app-settings",
          data: appSettings.getSnapshot(),
        },
      }
    }

    if (topic.type === "usage-limits") {
      const data: UsageLimitsSnapshot = usageLimits?.getSnapshot() ?? { providers: [] }
      return {
        v: PROTOCOL_VERSION,
        type: "snapshot",
        id,
        snapshot: {
          type: "usage-limits",
          data,
        },
      }
    }

    if (topic.type === "provider-auth") {
      return {
        v: PROTOCOL_VERSION,
        type: "snapshot",
        id,
        snapshot: {
          type: "provider-auth",
          data: providerAuth?.getSnapshot() ?? { services: [] },
        },
      }
    }

    if (topic.type === "ui-context") {
      return {
        v: PROTOCOL_VERSION,
        type: "snapshot",
        id,
        snapshot: {
          type: "ui-context",
          data: uiSyncHub.captureTurnContext(),
        },
      }
    }

    if (topic.type === "terminal") {
      if (terminalOwners.get(topic.terminalId) !== ws.data.identity.userId) {
        throw new Error("Terminal not found")
      }
      return {
        v: PROTOCOL_VERSION,
        type: "snapshot",
        id,
        snapshot: {
          type: "terminal",
          data: terminals.getSnapshot(topic.terminalId),
        },
      }
    }

    if (topic.type === "project-git") {
      return {
        v: PROTOCOL_VERSION,
        type: "snapshot",
        id,
        snapshot: {
          type: "project-git",
          data: store.getProject(topic.projectId)
            ? diffStore.getProjectSnapshot(topic.projectId)
            : null,
        },
      }
    }

    return {
      v: PROTOCOL_VERSION,
      type: "snapshot",
      id,
      snapshot: {
        type: "chat",
        data: getChatSnapshotData(ws.data.identity.userId, topic.chatId, cache),
      },
    }
  }

  function getChatSnapshotData(ownerId: string, chatId: string, cache?: SnapshotComputationCache) {
    const key = chatId
    const existing = cache?.chat?.get(key)
    if (existing !== undefined) {
      return existing
    }
    const derived = deriveChatSnapshot(
      store.state,
      agent.getActiveStatuses(),
      agent.getDrainingChatIds(),
      chatId,
      (id) => store.getClientTranscript(id)
      , ownerId
    )
    const data = derived
      ? redactBrowserPayload(derived, projectCatalog.resolveKnown(derived.runtime.projectId)) as ChatSnapshot
      : null
    if (cache) {
      (cache.chat ??= new Map()).set(key, data)
    }
    return data
  }

  /**
   * Narrow a chat snapshot to the entries a socket has not seen.
   *
   * Only contiguous forward movement qualifies. If the window slid backwards
   * (a widened read-anchor window) or forwards past the socket's position (a
   * missed push), the client would end up with a hole it cannot detect, so the
   * full window is sent instead.
   */
  function toSocketChatSnapshot(data: ChatSnapshot | null, previous: { start: number; end: number } | undefined) {
    if (!data || !previous) return data
    const end = data.startIndex + data.messages.length
    const isContiguous = data.startIndex >= previous.start
      && data.startIndex <= previous.end
      && previous.end <= end
    if (!isContiguous) return data
    return {
      ...data,
      messages: data.messages.slice(previous.end - data.startIndex),
      startIndex: previous.end,
      incremental: true,
    }
  }

  /**
   * Adopt a client's cached transcript position so its first push is
   * incremental.
   *
   * Honoured only when the entry at the boundary still matches what this
   * machine has — a cache from another machine, or from before a transcript
   * was rewritten, would otherwise be spliced onto unrelated history. Any
   * doubt (unverifiable index, mismatched id) falls through to a full window,
   * which is always correct and only costs bytes.
   */
  function seedChatEntrySpanFromClient(
    ws: ServerWebSocket<ClientState>,
    subscriptionId: string,
    topic: SubscriptionTopic
  ) {
    if (topic.type !== "chat") return
    const span = topic.cachedSpan
    if (!span || span.end <= 0 || span.start < 0 || span.start > span.end) return
    // A client can hold a cache for a chat this machine has since pruned.
    // Reading it would throw, and this runs outside the command try/catch on a
    // handler nobody awaits — so an unhandled rejection rather than the empty
    // snapshot the client is meant to get.
    if (!store.getChat(topic.chatId)) return
    // Populates the transcript cache as a side effect, which is what makes the
    // boundary entry visible to `getEntryIdAt`.
    store.getClientTranscript(topic.chatId)
    if (store.getEntryIdAt(topic.chatId, span.end - 1) !== span.endEntryId) return
    ensureChatEntrySpans(ws).set(subscriptionId, { start: span.start, end: span.end })
  }

  async function pushSnapshots(
    ws: ServerWebSocket<ClientState>,
    options?: {
      skipPrune?: boolean
      filter?: SnapshotBroadcastFilter
      cache?: SnapshotComputationCache
      /** Answer exactly one subscription — see the `subscribe` handler. */
      onlySubscriptionId?: string
    }
  ) {
    if (!options?.skipPrune) {
      await maybePruneStaleEmptyChats([ws])
    }
    const snapshotSignatures = ensureSnapshotSignatures(ws)
    for (const [id, topic] of ws.data.subscriptions.entries()) {
      if (options?.onlySubscriptionId !== undefined && id !== options.onlySubscriptionId) {
        continue
      }
      if (!shouldIncludeTopic(topic, options?.filter)) {
        continue
      }
      // Sidebar and chat snapshots are serialized once per broadcast (shared
      // via the cache) and that serialization doubles as the dedupe signature,
      // so unchanged snapshots cost neither a derive nor a stringify per
      // socket, and changed ones are stringified exactly once.
      if (topic.type === "sidebar") {
        const sidebar = getSidebarSnapshotCacheEntry(ws.data.identity.userId, options?.cache)
        if (snapshotSignatures.get(id) === sidebar.signature) {
          continue
        }
        snapshotSignatures.set(id, sidebar.signature)
        sendSerializedSnapshot(ws, id, sidebar.signature)
        continue
      }
      if (topic.type === "chat") {
        const data = getChatSnapshotData(ws.data.identity.userId, topic.chatId, options?.cache)
        const spans = ensureChatEntrySpans(ws)
        const snapshotJson = JSON.stringify({ type: "chat", data: toSocketChatSnapshot(data, spans.get(id)) })
        if (snapshotSignatures.get(id) === snapshotJson) {
          continue
        }
        snapshotSignatures.set(id, snapshotJson)
        // Record the full span, not the slice that went out — it is what this
        // socket now holds, and what the next push measures against.
        if (data) {
          spans.set(id, { start: data.startIndex, end: data.startIndex + data.messages.length })
        } else {
          spans.delete(id)
        }
        sendSerializedSnapshot(ws, id, snapshotJson)
        continue
      }
      // project-git has a cheap version-counter signature, so an unchanged
      // snapshot (e.g. thousands of diff files) skips payload building entirely.
      const precomputedSignature = topic.type === "project-git"
        ? getProjectGitSignature(topic.projectId)
        : null
      if (precomputedSignature !== null && snapshotSignatures.get(id) === precomputedSignature) {
        continue
      }
      const envelope = createEnvelope(ws, id, topic, options?.cache)
      if (envelope.type !== "snapshot") continue
      const signature = precomputedSignature ?? JSON.stringify(envelope.snapshot)
      if (snapshotSignatures.get(id) === signature) {
        continue
      }
      snapshotSignatures.set(id, signature)
      send(ws, envelope)
    }
  }

  async function broadcastSnapshots() {
    for (const ws of sockets) {
      await pushSnapshots(ws, { skipPrune: true, cache: {} })
    }
  }

  async function broadcastFilteredSnapshots(filter: SnapshotBroadcastFilter) {
    for (const ws of sockets) {
      await pushSnapshots(ws, { skipPrune: true, filter, cache: {} })
    }
  }

  function flushPendingBroadcast() {
    pendingBroadcastTimer = null
    const shouldBroadcastAll = pendingBroadcastAll
    const chatIds = new Set(pendingBroadcastChatIds)
    pendingBroadcastAll = false
    pendingBroadcastChatIds.clear()
    if (shouldBroadcastAll) {
      void broadcastSnapshots()
      return
    }
    if (chatIds.size > 0) {
      void broadcastFilteredSnapshots({ chatIds })
    }
  }

  function flushPendingSidebarBroadcast() {
    pendingSidebarTimer = null
    void broadcastFilteredSnapshots({ includeSidebar: true })
  }

  function armPendingBroadcastTimer() {
    if (pendingBroadcastTimer) {
      return
    }
    pendingBroadcastTimer = setTimeout(flushPendingBroadcast, CHAT_BROADCAST_INTERVAL_MS)
  }

  /**
   * The sidebar rides its own, much slower timer.
   *
   * A running turn appends entries several times a second, and each one moves a
   * sidebar field (`lastAgentMessageAt`, the reply preview, `pendingToolKind`),
   * so the signature dedupe never catches. Sharing the chat timer meant
   * re-deriving every project group, re-serializing the whole snapshot, and
   * re-rendering every sidebar row at the transcript's frame rate. Nothing in
   * the sidebar is read that fast — it is a list of titles and status glyphs.
   *
   * Commands that change sidebar *membership* (create, delete, archive, rename)
   * still call `broadcastFilteredSnapshots` directly and land immediately; only
   * the streaming hot path is throttled.
   */
  function armPendingSidebarTimer() {
    if (pendingSidebarTimer) {
      return
    }
    pendingSidebarTimer = setTimeout(flushPendingSidebarBroadcast, SIDEBAR_BROADCAST_INTERVAL_MS)
  }

  function scheduleBroadcast() {
    pendingBroadcastAll = true
    pendingBroadcastChatIds.clear()
    armPendingBroadcastTimer()
  }

  function scheduleChatStateBroadcast(chatId: string) {
    if (!pendingBroadcastAll) {
      pendingBroadcastChatIds.add(chatId)
    }
    armPendingBroadcastTimer()
    armPendingSidebarTimer()
  }

  async function broadcastChatAndSidebar(chatId: string) {
    await broadcastFilteredSnapshots({
      includeSidebar: true,
      chatIds: new Set([chatId]),
    })
  }

  async function broadcastChatStateImmediately(chatId: string) {
    await broadcastChatAndSidebar(chatId)
  }

  function broadcastError(message: string) {
    for (const ws of sockets) {
      send(ws, {
        v: PROTOCOL_VERSION,
        type: "error",
        message,
      })
    }
  }

  /**
   * `force` skips the dedupe compare (the new signature is still recorded).
   * Needed on explicit close: a pane that subscribed before its session
   * existed has signature "null" from that first push, and the post-close
   * snapshot is "null" again — without force the "session gone" push would
   * be swallowed and the pane would never recreate.
   */
  function pushTerminalSnapshot(terminalId: string, options?: { force?: boolean }) {
    for (const ws of sockets) {
      const snapshotSignatures = ensureSnapshotSignatures(ws)
      for (const [id, topic] of ws.data.subscriptions.entries()) {
        if (topic.type !== "terminal" || topic.terminalId !== terminalId) continue
        const envelope = createEnvelope(ws, id, topic)
        if (envelope.type !== "snapshot") continue
        const signature = JSON.stringify(envelope.snapshot)
        if (!options?.force && snapshotSignatures.get(id) === signature) continue
        snapshotSignatures.set(id, signature)
        send(ws, envelope)
      }
    }
  }

  function pushTerminalEvent(terminalId: string, event: Extract<ServerEnvelope, { type: "event" }>["event"]) {
    for (const ws of sockets) {
      for (const [id, topic] of ws.data.subscriptions.entries()) {
        if (topic.type !== "terminal" || topic.terminalId !== terminalId) continue
        send(ws, {
          v: PROTOCOL_VERSION,
          type: "event",
          id,
          event,
        })
      }
    }
  }

  const disposeTerminalEvents = terminals.onEvent((event) => {
    pushTerminalEvent(event.terminalId, event)
  })

  const disposeKeybindingEvents = keybindings.onChange(() => {
    for (const ws of sockets) {
      const snapshotSignatures = ensureSnapshotSignatures(ws)
      for (const [id, topic] of ws.data.subscriptions.entries()) {
        if (topic.type !== "keybindings") continue
        const envelope = createEnvelope(ws, id, topic)
        if (envelope.type !== "snapshot") continue
        const signature = JSON.stringify(envelope.snapshot)
        if (snapshotSignatures.get(id) === signature) continue
        snapshotSignatures.set(id, signature)
        send(ws, envelope)
      }
    }
  })

  const disposeAppSettingsEvents = appSettings.onChange(() => {
    for (const ws of sockets) {
      const snapshotSignatures = ensureSnapshotSignatures(ws)
      for (const [id, topic] of ws.data.subscriptions.entries()) {
        if (topic.type !== "app-settings") continue
        const envelope = createEnvelope(ws, id, topic)
        if (envelope.type !== "snapshot") continue
        const signature = JSON.stringify(envelope.snapshot)
        if (snapshotSignatures.get(id) === signature) continue
        snapshotSignatures.set(id, signature)
        send(ws, envelope)
      }
    }
  })

  const disposeUsageLimitsEvents = usageLimits?.onChange(() => {
    for (const ws of sockets) {
      const snapshotSignatures = ensureSnapshotSignatures(ws)
      for (const [id, topic] of ws.data.subscriptions.entries()) {
        if (topic.type !== "usage-limits") continue
        const envelope = createEnvelope(ws, id, topic)
        if (envelope.type !== "snapshot") continue
        const signature = JSON.stringify(envelope.snapshot)
        if (snapshotSignatures.get(id) === signature) continue
        snapshotSignatures.set(id, signature)
        send(ws, envelope)
      }
    }
  }) ?? (() => {})

  const disposeProviderAuthEvents = providerAuth?.onChange(() => {
    for (const ws of sockets) {
      const snapshotSignatures = ensureSnapshotSignatures(ws)
      for (const [id, topic] of ws.data.subscriptions.entries()) {
        if (topic.type !== "provider-auth") continue
        const envelope = createEnvelope(ws, id, topic)
        if (envelope.type !== "snapshot") continue
        const signature = JSON.stringify(envelope.snapshot)
        if (snapshotSignatures.get(id) === signature) continue
        snapshotSignatures.set(id, signature)
        send(ws, envelope)
      }
    }
  }) ?? (() => {})

  const disposeUiSyncEvents = uiSyncHub.onChange(() => {
    void broadcastFilteredSnapshots({ includeUiContext: true })
  })

  agent.setBackgroundErrorReporter?.(broadcastError)

  function resolveChatProject(chatId: string) {
    const chat = store.getChat(chatId)
    if (!chat) throw new Error("Chat not found")
    const project = store.getProject(chat.projectId)
    if (!project) throw new Error("Project not found")
    return { chat, project }
  }

  /**
   * Shared shape for the chat-scoped git commands: resolve the chat's project,
   * run the diff-store operation, ack (with the result when one is produced),
   * and fire-and-forget a full snapshot broadcast when the operation reports
   * the git snapshot changed.
   */
  async function handleChatGitCommand(
    ws: ServerWebSocket<ClientState>,
    id: string,
    chatId: string,
    run: (project: ReturnType<typeof resolveChatProject>["project"]) => Promise<{ result?: unknown; changed?: boolean }>,
  ) {
    const { project } = resolveChatProject(chatId)
    await runProjectGitCommand(ws, id, project, run)
  }

  /**
   * The same shape for git commands the client addresses by project — the ones
   * driven by the diff panel's own file selection, which must land on the
   * project that produced that selection even if the active chat has moved on.
   */
  async function runProjectGitCommand(
    ws: ServerWebSocket<ClientState>,
    id: string,
    project: ReturnType<typeof resolveChatProject>["project"],
    run: (project: ReturnType<typeof resolveChatProject>["project"]) => Promise<{ result?: unknown; changed?: boolean }>,
  ) {
    const { result, changed } = await run(project)
    if (result === undefined) {
      send(ws, { v: PROTOCOL_VERSION, type: "ack", id })
    } else {
      send(ws, { v: PROTOCOL_VERSION, type: "ack", id, result })
    }
    if (changed) {
      void broadcastSnapshots()
    }
  }

  async function handleCommand(ws: ServerWebSocket<ClientState>, message: Extract<ClientEnvelope, { type: "command" }>) {
    const { command, id } = message
    try {
      if ("chatId" in command && typeof command.chatId === "string") {
        requireOwnedChat(ws, command.chatId)
      }
      switch (command.type) {
        case "system.ping": {
          send(ws, { v: PROTOCOL_VERSION, type: "ack", id })
          return
        }
        case "settings.readKeybindings": {
          send(ws, { v: PROTOCOL_VERSION, type: "ack", id, result: keybindings.getSnapshot() })
          return
        }
        case "settings.writeKeybindings": {
          const snapshot = await keybindings.write(command.bindings)
          send(ws, { v: PROTOCOL_VERSION, type: "ack", id, result: snapshot })
          return
        }
        case "settings.readAppSettings": {
          send(ws, { v: PROTOCOL_VERSION, type: "ack", id, result: appSettings.getSnapshot() })
          return
        }
        case "usage.refresh": {
          if (usageLimits) {
            // Auto-refresh (page/palette open) respects the read TTL; the
            // manual Refresh button forces past it.
            await usageLimits.refresh({ force: command.force ?? false })
            send(ws, { v: PROTOCOL_VERSION, type: "ack", id, result: usageLimits.getSnapshot() })
          } else {
            send(ws, { v: PROTOCOL_VERSION, type: "ack", id, result: { providers: [] } satisfies UsageLimitsSnapshot })
          }
          return
        }
        case "auth.refresh": {
          if (providerAuth) {
            await providerAuth.refresh({ force: command.force ?? false })
            send(ws, { v: PROTOCOL_VERSION, type: "ack", id, result: providerAuth.getSnapshot() })
          } else {
            send(ws, { v: PROTOCOL_VERSION, type: "ack", id, result: { services: [] } })
          }
          return
        }
        case "auth.login.start": {
          requireAdmin(ws)
          if (!providerAuth) throw new Error("Provider auth unavailable.")
          if (command.service !== "codex") {
            throw new Error("Claude Code + DeepSeek uses its API-key connection form.")
          }
          providerAuth.startLogin(command.service)
          send(ws, { v: PROTOCOL_VERSION, type: "ack", id })
          return
        }
        case "auth.login.cancel": {
          requireAdmin(ws)
          if (!providerAuth) throw new Error("Provider auth unavailable.")
          if (command.service !== "codex") throw new Error("Unsupported authentication service.")
          await providerAuth.cancelLogin(command.service)
          send(ws, { v: PROTOCOL_VERSION, type: "ack", id })
          return
        }
        case "auth.claude.deepseek.setApiKey": {
          requireAdmin(ws)
          if (!providerAuth) throw new Error("Provider auth unavailable.")
          await providerAuth.setDeepSeekApiKey(command.apiKey)
          send(ws, { v: PROTOCOL_VERSION, type: "ack", id })
          return
        }
        case "settings.writeAppSettingsPatch": {
          const snapshot = await appSettings.writePatch(command.patch)
          send(ws, { v: PROTOCOL_VERSION, type: "ack", id, result: snapshot })
          return
        }
        case "chat.listSkills": {
          const snapshot = await agent.listSkills(command)
          send(ws, { v: PROTOCOL_VERSION, type: "ack", id, result: snapshot })
          return
        }
        case "project.rename": {
          await store.renameProjectSidebarTitle(command.projectId, command.title)
          send(ws, { v: PROTOCOL_VERSION, type: "ack", id })
          await broadcastFilteredSnapshots({ includeSidebar: true })
          return
        }
        case "sidebar.reorderProjectGroups": {
          await store.setSidebarProjectOrder(command.projectIds)
          send(ws, { v: PROTOCOL_VERSION, type: "ack", id })
          await broadcastFilteredSnapshots({ includeSidebar: true })
          return
        }
        case "project.readDiffPatch": {
          const project = store.getProject(command.projectId)
          if (!project) {
            throw new Error("Project not found")
          }
          const result = await diffStore.readPatch({
            projectPath: knownProjectPath(project.id),
            path: command.path,
          })
          send(ws, { v: PROTOCOL_VERSION, type: "ack", id, result })
          return
        }
        case "chat.create": {
          const clientRequestId = requireClientRequestId(command.clientRequestId)
          if (command.provider !== "claude-deepseek" && command.provider !== "codex-openai") {
            throw new Error("Only Claude Code + DeepSeek and Codex + GPT are available.")
          }
          requireFixedProject(command.projectId)
          const chat = await store.createChat(
            command.projectId,
            ws.data.identity.userId,
            command.provider,
            clientRequestId,
          )
          send(ws, { v: PROTOCOL_VERSION, type: "ack", id, result: { chatId: chat.id } })
          resolvedAnalytics.track("chat_created")
          // Adding a chat changes local-projects too (chatCount/lastOpenedAt).
          await broadcastFilteredSnapshots({
            includeSidebar: true,
            includeLocalProjects: true,
            chatIds: new Set([chat.id]),
          })
          return
        }
        case "chat.fork": {
          const clientRequestId = requireClientRequestId(command.clientRequestId)
          requireFixedChat(command.chatId)
          const result = await agent.forkChat(command.chatId, clientRequestId)
          send(ws, { v: PROTOCOL_VERSION, type: "ack", id, result })
          await broadcastFilteredSnapshots({ includeSidebar: true, includeLocalProjects: true })
          return
        }
        case "chat.rename": {
          await store.renameChat(command.chatId, command.title)
          send(ws, { v: PROTOCOL_VERSION, type: "ack", id })
          await broadcastChatAndSidebar(command.chatId)
          return
        }
        case "chat.archive": {
          // Archiving a chat that never got a message is a hard delete — an
          // empty chat has nothing worth keeping in the Archived list.
          const chat = store.getChat(command.chatId)
          const hardDeleted = chat != null && !chat.hasMessages && !chat.lastMessageAt
          if (hardDeleted) {
            await store.deleteChat(command.chatId)
          } else {
            await store.archiveChat(command.chatId)
          }
          send(ws, { v: PROTOCOL_VERSION, type: "ack", id })
          // Archiving removes the chat from local-projects' chat counts; a hard
          // delete must also refresh the chat's own topic (to null) so a tab
          // viewing it learns it's gone.
          await broadcastFilteredSnapshots({
            includeSidebar: true,
            includeLocalProjects: true,
            ...(hardDeleted ? { chatIds: new Set([command.chatId]) } : {}),
          })
          return
        }
        case "chat.unarchive": {
          await store.unarchiveChat(command.chatId)
          // Unarchiving is the explicit "Restore" action (viewing an archived
          // chat no longer unarchives it). Mark it done so restoring alone
          // doesn't resurface it as needing review; sending a message clears
          // the done state and brings it back to running.
          await store.setChatDoneState(command.chatId, true)
          send(ws, { v: PROTOCOL_VERSION, type: "ack", id })
          await broadcastFilteredSnapshots({
            includeSidebar: true,
            includeLocalProjects: true,
            chatIds: new Set([command.chatId]),
          })
          return
        }
        case "chat.delete": {
          await agent.cancel(command.chatId)
          await agent.closeChat(command.chatId)
          await store.deleteChat(command.chatId)
          send(ws, { v: PROTOCOL_VERSION, type: "ack", id })
          resolvedAnalytics.track("chat_deleted")
          // The deleted chat's own topic must refresh (to null) so another tab
          // viewing it learns it's gone, and local-projects loses the chat.
          await broadcastFilteredSnapshots({
            includeSidebar: true,
            includeLocalProjects: true,
            chatIds: new Set([command.chatId]),
          })
          return
        }
        case "chat.touchedFiles": {
          const chat = store.getChat(command.chatId)
          if (!chat) {
            throw new Error("Chat not found")
          }
          // Ack-only: nothing about reading a list changes any snapshot, and
          // this fires on every hover.
          send(ws, {
            v: PROTOCOL_VERSION,
            type: "ack",
            id,
            result: deriveChatTouchedFiles(chat, worktreeProbe.getStates().get(chat.projectId)),
          })
          return
        }
        case "chat.markRead": {
          await store.setChatReadState(command.chatId, false)
          send(ws, { v: PROTOCOL_VERSION, type: "ack", id })
          await broadcastChatAndSidebar(command.chatId)
          return
        }
        case "chat.setDone": {
          await store.setChatDoneState(command.chatId, command.done)
          send(ws, { v: PROTOCOL_VERSION, type: "ack", id })
          await broadcastChatAndSidebar(command.chatId)
          return
        }
        case "chat.setReadAnchor": {
          // No broadcast on purpose. The anchor is not part of any snapshot,
          // so scrolling stays free of fan-out, and a device sitting on an
          // open chat never gets its viewport yanked by another device.
          await store.setChatReadAnchor(command.chatId, command.messageId, command.atEnd, {
            transcriptWidth: command.transcriptWidth,
            offsetFromMessage: command.offsetFromMessage,
          })
          send(ws, { v: PROTOCOL_VERSION, type: "ack", id })
          return
        }
        case "chat.getReadAnchor": {
          const result = store.getChatReadAnchor(command.chatId)
          send(ws, { v: PROTOCOL_VERSION, type: "ack", id, result })
          return
        }
        case "chat.getToolEntries": {
          // Bounded so a malformed client cannot ask for the whole transcript
          // one id at a time; the largest real request is one tool group.
          if (command.entryIds.length > MAX_TOOL_ENTRY_REQUEST) {
            throw new Error(`Too many entry ids (max ${MAX_TOOL_ENTRY_REQUEST})`)
          }
          const chat = store.requireChat(command.chatId)
          const projectRoot = await projectCatalog.resolve(chat.projectId)
          const result = redactBrowserPayload(
            store.getEntriesById(command.chatId, command.entryIds),
            projectRoot,
          )
          send(ws, { v: PROTOCOL_VERSION, type: "ack", id, result })
          return
        }
        case "chat.setDraftProtection": {
          // Only adjusts this socket's prune protection — no snapshot changes.
          ws.data.protectedDraftChatIds = new Set(command.chatIds)
          send(ws, { v: PROTOCOL_VERSION, type: "ack", id })
          return
        }
        case "chat.send": {
          requireClientRequestId(command.clientRequestId)
          if (command.provider !== "claude-deepseek" && command.provider !== "codex-openai") {
            throw new Error("Only Claude Code + DeepSeek and Codex + GPT are available.")
          }
          if (typeof command.model !== "string" || !command.model.trim() || command.model.length > 256) {
            throw new Error("model must be a bounded non-empty string.")
          }
          if (command.chatId) {
            requireFixedChat(command.chatId)
          } else if (command.projectId) requireFixedProject(command.projectId)
          else throw new Error("Missing projectId for new Agent session.")
          const result = await agent.send(command, ws.data.identity.userId)
          send(ws, { v: PROTOCOL_VERSION, type: "ack", id, result })
          return
        }
        case "chat.refreshDiffs": {
          // Acks without a result; broadcasts when the refresh reported a change.
          await handleChatGitCommand(ws, id, command.chatId, async (project) => ({
            changed: await diffStore.refreshSnapshot(project.id, knownProjectPath(project.id)),
          }))
          return
        }
        case "chat.cancel": {
          requireOwnedChat(ws, command.chatId)
          await agent.cancel(command.chatId)
          send(ws, { v: PROTOCOL_VERSION, type: "ack", id })
          return
        }
        case "chat.retry": {
          requireOwnedChat(ws, command.chatId)
          const clientRequestId = requireClientRequestId(command.clientRequestId)
          const result = await agent.retry(command.chatId, ws.data.identity.userId, clientRequestId)
          send(ws, { v: PROTOCOL_VERSION, type: "ack", id, result })
          await broadcastChatAndSidebar(command.chatId)
          return
        }
        case "chat.stopDraining": {
          await agent.stopDraining(command.chatId)
          send(ws, { v: PROTOCOL_VERSION, type: "ack", id })
          return
        }
        case "chat.respondTool": {
          await agent.respondTool(command)
          send(ws, { v: PROTOCOL_VERSION, type: "ack", id })
          return
        }
        case "message.enqueue": {
          requireClientRequestId(command.clientRequestId)
          if (command.provider !== "claude-deepseek" && command.provider !== "codex-openai") {
            throw new Error("Only Claude Code + DeepSeek and Codex + GPT are available.")
          }
          if (typeof command.model !== "string" || !command.model.trim() || command.model.length > 256) {
            throw new Error("model must be a bounded non-empty string.")
          }
          const result = await agent.enqueue(command)
          send(ws, { v: PROTOCOL_VERSION, type: "ack", id, result })
          await broadcastChatAndSidebar(command.chatId)
          return
        }
        case "message.steer": {
          await agent.steer(command)
          send(ws, { v: PROTOCOL_VERSION, type: "ack", id })
          await broadcastChatAndSidebar(command.chatId)
          return
        }
        case "message.dequeue": {
          await agent.dequeue(command)
          send(ws, { v: PROTOCOL_VERSION, type: "ack", id })
          await broadcastChatAndSidebar(command.chatId)
          return
        }
        case "terminal.create": {
          const existingOwner = terminalOwners.get(command.terminalId)
          if (existingOwner && existingOwner !== ws.data.identity.userId) {
            throw new Error("Terminal not found")
          }
          const project = requireFixedProject(command.projectId)
          const projectPath = await projectCatalog.resolve(project.id)
          const snapshot = terminals.createTerminal({
            projectPath,
            terminalId: command.terminalId,
            cols: command.cols,
            rows: command.rows,
            scrollback: command.scrollback,
          })
          terminalOwners.set(command.terminalId, ws.data.identity.userId)
          send(ws, { v: PROTOCOL_VERSION, type: "ack", id, result: snapshot })
          return
        }
        case "terminal.input": {
          if (terminalOwners.get(command.terminalId) !== ws.data.identity.userId) throw new Error("Terminal not found")
          terminals.write(command.terminalId, command.data)
          send(ws, { v: PROTOCOL_VERSION, type: "ack", id })
          return
        }
        case "terminal.resize": {
          if (terminalOwners.get(command.terminalId) !== ws.data.identity.userId) throw new Error("Terminal not found")
          terminals.resize(command.terminalId, command.cols, command.rows)
          send(ws, { v: PROTOCOL_VERSION, type: "ack", id })
          return
        }
        case "terminal.close": {
          if (terminalOwners.get(command.terminalId) !== ws.data.identity.userId) throw new Error("Terminal not found")
          terminals.close(command.terminalId)
          terminalOwners.delete(command.terminalId)
          send(ws, { v: PROTOCOL_VERSION, type: "ack", id })
          pushTerminalSnapshot(command.terminalId, { force: true })
          return
        }
      }
    } catch (error) {
      const messageText = String(redactBrowserPayload(error instanceof Error ? error.message : String(error)))
      console.error("[ws-router] command failed", {
        id,
        type: command.type,
        message: messageText,
      })
      send(ws, { v: PROTOCOL_VERSION, type: "error", id, message: messageText })
    }
  }

  return {
    handleOpen(ws: ServerWebSocket<ClientState>) {
      sockets.add(ws)
    },
    handleClose(ws: ServerWebSocket<ClientState>) {
      sockets.delete(ws)
    },
    broadcastSnapshots,
    broadcastChatStateImmediately,
    broadcastSidebar: () => broadcastFilteredSnapshots({ includeSidebar: true }),
    scheduleBroadcast,
    scheduleChatStateBroadcast,
    pruneStaleEmptyChats: () => maybePruneStaleEmptyChats(),
    autoArchiveStaleChats: () => maybeAutoArchiveStaleChats(),
    deleteStaleChats: () => maybeDeleteStaleChats(),
    async handleMessage(ws: ServerWebSocket<ClientState>, raw: string | Buffer | ArrayBuffer | Uint8Array) {
      let parsed: unknown
      try {
        parsed = JSON.parse(String(raw))
      } catch {
        send(ws, { v: PROTOCOL_VERSION, type: "error", message: "Invalid JSON" })
        return
      }

      if (containsForbiddenClientKey(parsed)) {
        send(ws, { v: PROTOCOL_VERSION, type: "error", message: "Filesystem paths are not accepted by the Agent Web protocol." })
        return
      }

      if (!isClientEnvelope(parsed)) {
        send(ws, { v: PROTOCOL_VERSION, type: "error", message: "Invalid envelope" })
        return
      }

      if (parsed.type === "subscribe") {
        if (parsed.topic.type === "chat") {
          try {
            requireOwnedChat(ws, parsed.topic.chatId)
          } catch {
            send(ws, { v: PROTOCOL_VERSION, type: "error", id: parsed.id, message: "Chat not found" })
            return
          }
        }
        if (parsed.topic.type === "terminal" && terminalOwners.get(parsed.topic.terminalId) !== ws.data.identity.userId) {
          send(ws, { v: PROTOCOL_VERSION, type: "error", id: parsed.id, message: "Terminal not found" })
          return
        }
        const snapshotSignatures = ensureSnapshotSignatures(ws)
        ws.data.subscriptions.set(parsed.id, parsed.topic)
        snapshotSignatures.delete(parsed.id)
        // A (re)subscribe starts from nothing, so the next push sends a full window.
        ws.data.chatEntrySpans?.delete(parsed.id)
        seedChatEntrySpanFromClient(ws, parsed.id, parsed.topic)
        if (parsed.topic.type === "local-projects") {
          void refreshProjects().then(() => {
            if (ws.data.subscriptions.has(parsed.id)) {
              void pushSnapshots(ws, { skipPrune: true, onlySubscriptionId: parsed.id })
            }
          })
          return
        }
        // Only the subscription just made. Opening a chat used to answer every
        // topic on the socket, so the transcript queued behind a full sidebar
        // derive and re-serialization — and the sidebar is iterated first,
        // because the new subscription is appended last. Nothing else asked for
        // an update, and anything that changes meanwhile is broadcast anyway.
        await pushSnapshots(ws, { skipPrune: true, onlySubscriptionId: parsed.id })
        // Kick a fresh usage read on subscribe so the page opens accurate;
        // the onChange fanout delivers the result to all subscribers.
        if (parsed.topic.type === "usage-limits" && usageLimits) {
          void usageLimits.refresh().catch(() => undefined)
        }
        // Same shape for provider auth: cached state paints instantly, the
        // TTL-respecting probe pushes fresh results to all subscribers.
        if (parsed.topic.type === "provider-auth" && providerAuth) {
          void providerAuth.refresh().catch(() => undefined)
        }
        return
      }

      if (parsed.type === "unsubscribe") {
        const snapshotSignatures = ensureSnapshotSignatures(ws)
        ws.data.subscriptions.delete(parsed.id)
        snapshotSignatures.delete(parsed.id)
        // A (re)subscribe starts from nothing, so the next push sends a full window.
        ws.data.chatEntrySpans?.delete(parsed.id)
        send(ws, { v: PROTOCOL_VERSION, type: "ack", id: parsed.id })
        return
      }

      await handleCommand(ws, parsed)
    },
    dispose() {
      if (pendingBroadcastTimer) {
        clearTimeout(pendingBroadcastTimer)
      }
      if (pendingSidebarTimer) {
        clearTimeout(pendingSidebarTimer)
      }
      agent.setBackgroundErrorReporter?.(null)
      disposeTerminalEvents()
      disposeKeybindingEvents()
      disposeAppSettingsEvents()
      disposeUsageLimitsEvents()
      disposeProviderAuthEvents()
      disposeUiSyncEvents()
    },
  }
}
