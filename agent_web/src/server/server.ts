import path from "node:path"
import { timingSafeEqual } from "node:crypto"
import { chmod, lstat, mkdir, stat } from "node:fs/promises"
import { APP_NAME } from "../shared/branding"
import type { ChatAttachment } from "../shared/types"
import { TradeSessionVerifier, type TradeIdentity } from "./trade-session"
import { EventStore } from "./event-store"
import { AgentCoordinator } from "./agent"
import { CodexAppServerManager } from "./codex-app-server"
import { NoopAnalyticsReporter } from "./analytics"
import { AppSettingsManager } from "./app-settings"
import { UsageLimitsManager } from "./usage-limits"
import { DiffStore } from "./diff-store"
import { WorktreeProbe } from "./worktree-probe"
import { TurnFileTracker } from "./worktree-snapshot"
import { KeybindingsManager } from "./keybindings"
import { applyClaudeDeepSeekModels } from "./provider-catalog"
import { createProcessAuthDeps, ProviderAuthManager } from "./provider-auth"
import { DeepSeekCredentialStore } from "./deepseek-credentials"
import { getMachineDisplayName } from "./machine-name"
import { TerminalManager } from "./terminal-manager"
import { createWsRouter, type ClientState } from "./ws-router"
import { UiSyncError, UiSyncHub, type UiSyncClientState, type UiSyncSocket } from "./ui-sync-hub"
import { deleteProjectUpload, inferAttachmentContentType, inferProjectFileContentType, persistProjectUpload } from "./uploads"
import { getProjectUploadDir } from "./paths"
import {
  parseExternalAgentProjects,
  TradeProjectCatalog,
  type ExternalAgentProject,
} from "./trade-project-catalog"
import { TradeToolGrantIssuer } from "./trade-tool-grants"

const MAX_UPLOAD_FILES = 50
const MAX_UPLOAD_SIZE_BYTES = 100 * 1024 * 1024
const STALE_EMPTY_CHAT_PRUNE_INTERVAL_MS = 60 * 1000
const STALE_CHAT_AUTO_ARCHIVE_INTERVAL_MS = 6 * 60 * 60 * 1000
const STALE_CHAT_DELETE_INTERVAL_MS = 24 * 60 * 60 * 1000
const TRADE_SESSION_REVALIDATE_INTERVAL_MS = 30 * 1000
const MAX_INTERNAL_UI_TOOL_BODY_BYTES = 320 * 1024

function constantTimeTokenMatch(actual: string, expected: string) {
  const actualBytes = Buffer.from(actual, "utf8")
  const expectedBytes = Buffer.from(expected, "utf8")
  return actualBytes.length === expectedBytes.length && timingSafeEqual(actualBytes, expectedBytes)
}

function exactRecord(value: unknown, allowed: readonly string[]) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null
  const record = value as Record<string, unknown>
  return Object.keys(record).every((key) => allowed.includes(key)) ? record : null
}

export async function handleInternalUiToolCall(
  req: Request,
  bridgeToken: string,
  uiSyncHub: UiSyncHub,
): Promise<Response> {
  if (req.method !== "POST") return new Response(null, { status: 405, headers: { Allow: "POST" } })
  if (!bridgeToken) return Response.json({ error: "UI tool bridge is unavailable.", code: "bridge_unavailable", retryable: true }, { status: 503 })
  if (req.headers.has("origin")) return Response.json({ error: "Browser requests are not accepted.", code: "forbidden", retryable: false }, { status: 403 })
  const authorization = req.headers.get("authorization") ?? ""
  const token = authorization.startsWith("Bearer ") ? authorization.slice(7) : ""
  if (!constantTimeTokenMatch(token, bridgeToken)) {
    return Response.json({ error: "Invalid bridge credential.", code: "forbidden", retryable: false }, { status: 403 })
  }
  if ((req.headers.get("content-type") ?? "").split(";", 1)[0].trim().toLowerCase() !== "application/json") {
    return Response.json({ error: "Content-Type must be application/json.", code: "invalid_request", retryable: false }, { status: 415 })
  }
  const contentLength = Number(req.headers.get("content-length") ?? "0")
  if (!Number.isSafeInteger(contentLength) || contentLength < 0 || contentLength > MAX_INTERNAL_UI_TOOL_BODY_BYTES) {
    return Response.json({ error: "Request body is too large.", code: "invalid_request", retryable: false }, { status: 413 })
  }
  let payload: unknown
  try {
    const body = await req.arrayBuffer()
    if (body.byteLength > MAX_INTERNAL_UI_TOOL_BODY_BYTES) throw new Error("too large")
    payload = JSON.parse(new TextDecoder().decode(body))
  } catch {
    return Response.json({ error: "Request must be bounded JSON.", code: "invalid_request", retryable: false }, { status: 400 })
  }
  const request = exactRecord(payload, ["tool", "arguments"])
  const args = request ? exactRecord(request.arguments, [
    "operationId", "documentId", "includeContent", "baseRevision", "baseDigest", "patch", "save",
  ]) : null
  if (!request || typeof request.tool !== "string" || !args) {
    return Response.json({ error: "Exact tool and arguments fields are required.", code: "invalid_request", retryable: false }, { status: 400 })
  }
  try {
    if (request.tool === "trade_ui_state_get") {
      if (Object.keys(args).length) throw new UiSyncError("invalid_request", "trade_ui_state_get does not accept arguments.")
      return Response.json({ result: uiSyncHub.captureContext() })
    }
    if (request.tool === "trade_ui_document_get") {
      if (!Object.keys(args).every((key) => ["operationId", "documentId", "includeContent"].includes(key))
          || typeof args.documentId !== "string"
          || (args.operationId !== undefined && typeof args.operationId !== "string")
          || (args.includeContent !== undefined && typeof args.includeContent !== "boolean")) {
        throw new UiSyncError("invalid_request", "Invalid document snapshot arguments.")
      }
      return Response.json({ result: await uiSyncHub.requestDocumentSnapshot({
        operationId: args.operationId,
        documentId: args.documentId,
        includeContent: args.includeContent,
      }) })
    }
    if (request.tool === "trade_ui_document_patch") {
      if (!Object.keys(args).every((key) => ["operationId", "documentId", "baseRevision", "baseDigest", "patch", "save"].includes(key))
          || typeof args.operationId !== "string"
          || typeof args.documentId !== "string"
          || !Number.isSafeInteger(args.baseRevision) || Number(args.baseRevision) < 0
          || typeof args.baseDigest !== "string"
          || !args.patch || typeof args.patch !== "object" || Array.isArray(args.patch)
          || (args.save !== undefined && typeof args.save !== "boolean")) {
        throw new UiSyncError("invalid_request", "Invalid document patch arguments.")
      }
      return Response.json({ result: await uiSyncHub.requestDocumentPatch({
        operationId: args.operationId,
        documentId: args.documentId,
        baseRevision: Number(args.baseRevision),
        baseDigest: args.baseDigest,
        patch: args.patch as never,
        save: args.save,
      }) })
    }
    throw new UiSyncError("tool_not_allowed", "Unknown UI tool.")
  } catch (error) {
    const resolved = error instanceof UiSyncError
      ? error
      : new UiSyncError("internal_error", "The UI tool request failed.", true)
    const status = resolved.code === "tool_not_allowed" ? 404 : resolved.code === "invalid_request" ? 400 : 409
    return Response.json({ error: resolved.message, code: resolved.code, retryable: resolved.retryable }, { status })
  }
}

async function withOriginAgentCluster(response: Response | Promise<Response> | undefined) {
  const resolved = await response
  // Chrome groups localhost ports into one site. Origin-keying reduces the
  // chance that a busy preview shares Kanna's renderer.
  resolved?.headers.set("Origin-Agent-Cluster", "?1")
  return resolved
}

export async function persistUploadedFiles(args: {
  projectId: string
  localPath: string
  files: File[]
  persistUpload?: typeof persistProjectUpload
}): Promise<ChatAttachment[]> {
  const persistUpload = args.persistUpload ?? persistProjectUpload
  const attachments: ChatAttachment[] = []

  try {
    for (const file of args.files) {
      const bytes = new Uint8Array(await file.arrayBuffer())
      const attachment = await persistUpload({
        projectId: args.projectId,
        localPath: args.localPath,
        fileName: file.name,
        bytes,
        fallbackMimeType: file.type || undefined,
      })
      attachments.push(attachment)
    }
  } catch (error) {
    await Promise.allSettled(
      attachments.map((attachment) => deleteProjectUpload({
        localPath: args.localPath,
        storedName: path.basename(attachment.relativePath),
      }))
    )
    throw error
  }

  return attachments
}

export interface StartKannaServerOptions {
  port?: number
  host?: string
  dataDir?: string
  /** Configured origins; never derive security/navigation URLs from Host. */
  tradeEnginePublicUrl?: string
  agentPublicUrl?: string
  /** Server-owned repository root; mainly useful for isolated integration tests. */
  tradeEngineRoot?: string
  /** Explicit server-owned external strategy repositories. */
  agentProjects?: readonly ExternalAgentProject[]
  /** Dependency injection for server integration tests. */
  tradeSessionVerifier?: TradeSessionVerifier
  strictPort?: boolean
}

export async function startKannaServer(options: StartKannaServerOptions = {}) {
  const port = options.port ?? 3210
  const hostname = options.host ?? "127.0.0.1"
  const strictPort = options.strictPort ?? false
  const tradeSession = options.tradeSessionVerifier ?? new TradeSessionVerifier({
    tradeEnginePublicUrl: options.tradeEnginePublicUrl ?? process.env.TRADE_ENGINE_PUBLIC_URL ?? "",
    agentPublicUrl: options.agentPublicUrl ?? process.env.AGENT_PUBLIC_URL ?? "",
    buildId: process.env.TRADE_AGENT_BUILD,
  })
  const store = new EventStore(options.dataDir)
  const diffStore = new DiffStore(store.dataDir)
  const machineDisplayName = getMachineDisplayName()
  await store.initialize()
  const tradeEngineRoot = options.tradeEngineRoot?.trim()
    || process.env.TRADE_ENGINE_ROOT?.trim()
    || path.resolve(import.meta.dir, "../../..")
  const externalProjects = options.agentProjects
    ?? parseExternalAgentProjects(process.env.TRADE_AGENT_PROJECTS_JSON)
  const projectCatalog = new TradeProjectCatalog(tradeEngineRoot, externalProjects)
  const bridgeToken = process.env.TRADE_AGENT_BRIDGE_TOKEN?.trim() ?? ""
  const toolGrantIssuer = bridgeToken ? new TradeToolGrantIssuer({
    engineOrigin: tradeSession.tradeEngineOrigin,
    bridgeToken,
    tradeRoot: tradeEngineRoot,
    credentialRoot: path.join(path.dirname(store.dataDir), "tool-grants"),
    pythonPath: process.env.TRADE_AGENT_PYTHON,
  }) : null
  await projectCatalog.refresh()
  for (const project of projectCatalog.listInternal()) {
    await store.registerProject({ ...project, title: project.label })
  }
  await diffStore.initialize()
  async function refreshProjects() {
    await projectCatalog.refresh()
    for (const project of projectCatalog.listInternal()) {
      await store.registerProject({ ...project, title: project.label })
    }
  }

  await refreshProjects()

  type AgentSocketState = ClientState & { socketKind: "agent" }
  type KannaSocketState = AgentSocketState | UiSyncClientState
  let server: ReturnType<typeof Bun.serve<KannaSocketState>>
  const authenticatedSockets = new Set<import("bun").ServerWebSocket<KannaSocketState>>()
  let router: ReturnType<typeof createWsRouter>
  const uiSyncHub = new UiSyncHub()
  // Feeds the sidebar's muted "relevant to uncommitted work" dot. Derived and
  // in-memory; see worktree-probe.ts for why there's no `git status` sweep.
  const worktreeProbe = new WorktreeProbe(
    () => store.state,
    () => {
      void router.broadcastSidebar()
    },
    (projectId) => projectCatalog.resolveKnown(projectId),
  )
  // Free updates: `performRefresh` already stats every dirty file, so the
  // client's active project stays current at no extra git cost — and the dot
  // clears the instant a commit goes through Kanna's git panel.
  diffStore.onWorkingTreeProbe = (projectId, probe) => {
    worktreeProbe.recordExternalProbe(projectId, probe)
  }
  // Snapshot the worktree either side of a turn and record what changed, so
  // the sidebar can ask "did this chat touch a file that's still uncommitted?"
  // instead of comparing timestamps. See worktree-snapshot.ts.
  const turnFiles = new TurnFileTracker({
    resolveChatPath: (chatId) => {
      const chat = store.state.chatsById.get(chatId)
      const project = chat ? store.state.projectsById.get(chat.projectId) : undefined
      return project ? projectCatalog.resolveKnown(project.id) : null
    },
    recordFiles: (chatId, files) => store.recordFilesTouched(chatId, files),
  })
  store.onTurnStarted = (chatId) => {
    turnFiles.beginTurn(chatId)
  }
  // A finished turn is the likeliest moment for the dirty set to have changed,
  // so probe that one project then — after recording the turn's own files, so
  // the broadcast that follows already reflects them.
  store.onTurnEnded = (chatId) => {
    void turnFiles.endTurn(chatId).finally(() => worktreeProbe.refreshForChat(chatId))
  }
  const terminals = new TerminalManager()
  const keybindings = new KeybindingsManager()
  const appSettings = new AppSettingsManager(path.join(store.dataDir, "settings.json"))
  await appSettings.initialize()
  await keybindings.initialize()
  const analytics = NoopAnalyticsReporter
  const credentialRoot = path.join(path.dirname(store.dataDir), "credentials")
  const codexHome = path.join(credentialRoot, "codex")
  await mkdir(codexHome, { recursive: true, mode: 0o700 })
  const codexHomeInfo = await lstat(codexHome)
  if (!codexHomeInfo.isDirectory() || codexHomeInfo.isSymbolicLink()) {
    throw new Error("Managed Codex credential home must be a plain directory.")
  }
  await chmod(codexHome, 0o700)
  const codexManager = new CodexAppServerManager({
    environment: { ...process.env, CODEX_HOME: codexHome },
  })
  const deepSeekCredentials = new DeepSeekCredentialStore(path.dirname(store.dataDir))
  const deepSeekStatus = await deepSeekCredentials.getStatus()
  applyClaudeDeepSeekModels(deepSeekStatus.models, deepSeekStatus.defaultModel)
  if (!deepSeekStatus.models.includes(appSettings.getSnapshot().providerDefaults["claude-deepseek"].model)) {
    await appSettings.writePatch({
      providerDefaults: { "claude-deepseek": { model: deepSeekStatus.defaultModel } },
    })
  }
  const agent = new AgentCoordinator({
    store,
    resolveProjectPath: async (projectId) => {
      const project = store.getProject(projectId)
      if (!project) throw new Error("Project not found")
      return await projectCatalog.resolve(project.id)
    },
    analytics,
    codexManager,
    claudeEnvironment: () => deepSeekCredentials.getEnvironment(),
    claudeModels: deepSeekStatus.models,
    issueToolGrant: toolGrantIssuer ? (args) => toolGrantIssuer.issue(args) : undefined,
    revokeToolGrant: toolGrantIssuer ? (turnId) => toolGrantIssuer.revoke(turnId) : undefined,
    captureUiContext: () => uiSyncHub.captureTurnContext(),
    // Session history is backend-bound. Upstream Kanna's generic title helper
    // falls through across providers; keep the optimistic local title instead
    // so a Claude prompt is never copied into Codex (or vice versa).
    generateTitle: async () => ({ title: null, usedFallback: true, failureMessage: null }),
    onStateChange: (chatId?: string, options?: { immediate?: boolean }) => {
      if (chatId) {
        if (options?.immediate) {
          void router.broadcastChatStateImmediately(chatId)
          return
        }
        router.scheduleChatStateBroadcast(chatId)
        return
      }
      router.scheduleBroadcast()
    },
  })
  const usageLimits = new UsageLimitsManager(path.join(store.dataDir, "usage-limits.json"), {
    fetchClaudeUsage: () => agent.fetchClaudeUsage(),
    fetchCodexRateLimits: () => agent.fetchCodexRateLimits(),
  })
  await usageLimits.initialize()
  agent.setClaudeRateLimitListener((info) => usageLimits.recordClaudeRateLimitPush(info))
  codexManager.setRateLimitsListener((snapshot) => usageLimits.recordCodexRateLimitPush(snapshot))

  const providerAuth = new ProviderAuthManager({
    ...createProcessAuthDeps(),
    codexDeviceAuth: {
      start: async (onCompleted) => {
        const challenge = await codexManager.startDeviceLogin(tradeEngineRoot, onCompleted)
        return { verificationUrl: challenge.verificationUrl, userCode: challenge.userCode }
      },
      cancel: () => codexManager.cancelDeviceLogin(),
    },
    codexAccount: () => codexManager.readAccount(tradeEngineRoot),
    deepSeekCredentials,
    allowedServices: ["claude", "codex"],
    onSignedIn: () => {
      // A fresh native-backend sign-in unlocks usage limits.
      void usageLimits.refresh({ force: true }).catch(() => undefined)
    },
  })

  router = createWsRouter({
    store,
    diffStore,
    worktreeProbe,
    agent,
    terminals,
    keybindings,
    appSettings,
    analytics,
    usageLimits,
    refreshProjects,
    machineDisplayName,
    providerAuth,
    projectCatalog,
    uiSyncHub,
  })

  // Chat garbage collection, three tiers measured against the user's latest
  // chat activity: empty drafts are hard-deleted after 5 idle minutes, chats
  // 30+ days behind are auto-archived, and 90+ days behind are hard-deleted.
  const runPruneStaleEmptyChats = () => {
    void router.pruneStaleEmptyChats()
      .then((prunedChatIds) => {
        if (prunedChatIds.length > 0) {
          return router.broadcastSnapshots()
        }
      })
  }
  const runAutoArchiveStaleChats = () => {
    void router.autoArchiveStaleChats()
      .then((archivedChatIds) => {
        if (archivedChatIds.length > 0) {
          return router.broadcastSnapshots()
        }
      })
  }
  const runDeleteStaleChats = () => {
    void router.deleteStaleChats()
      .then((deletedChatIds) => {
        if (deletedChatIds.length > 0) {
          return router.broadcastSnapshots()
        }
      })
  }

  // All three run once at startup — a long-idle instance gets cleaned
  // immediately, not minutes or hours later. Lifecycle order: prune empties,
  // hard-delete 90d+ (so they aren't pointlessly archived first), then
  // archive 30d+. One broadcast at the end covers all changes.
  const runStartupGc = async () => {
    const pruned = await router.pruneStaleEmptyChats().catch(() => [])
    const deleted = await router.deleteStaleChats().catch(() => [])
    const archived = await router.autoArchiveStaleChats().catch(() => [])
    if (pruned.length + deleted.length + archived.length > 0) {
      await router.broadcastSnapshots()
    }
  }
  void runStartupGc()

  // Then keep sweeping for the lifetime of the (potentially months-long)
  // process: empties every minute, deletes daily, archives every 6 hours.
  const staleEmptyChatPruneInterval = setInterval(runPruneStaleEmptyChats, STALE_EMPTY_CHAT_PRUNE_INTERVAL_MS)
  const staleChatAutoArchiveInterval = setInterval(runAutoArchiveStaleChats, STALE_CHAT_AUTO_ARCHIVE_INTERVAL_MS)
  const staleChatDeleteInterval = setInterval(runDeleteStaleChats, STALE_CHAT_DELETE_INTERVAL_MS)
  worktreeProbe.start()
  const distDir = path.join(import.meta.dir, "..", "..", "dist", "client")

  const MAX_PORT_ATTEMPTS = 20
  let actualPort = port

  for (let attempt = 0; attempt < MAX_PORT_ATTEMPTS; attempt++) {
    try {
      server = Bun.serve<KannaSocketState>({
        port: actualPort,
        hostname,
        async fetch(req, serverInstance) {
          const url = new URL(req.url)
          const upgradeWebSocket = (
            identity: TradeIdentity,
            sessionCookie: string,
            socketKind: "agent" | "ui-sync",
          ) => {
            const upgraded = serverInstance.upgrade(req, {
              data: socketKind === "agent" ? {
                socketKind,
                subscriptions: new Map(),
                snapshotSignatures: new Map(),
                identity,
                sessionCookie,
              } : {
                socketKind,
                subscriptions: new Map(),
                identity,
                sessionCookie,
                tabId: null,
              },
            })
            return upgraded ? undefined : new Response("WebSocket upgrade failed", { status: 400 })
          }

          if (url.pathname === "/health") {
            return withOriginAgentCluster(Response.json({
              ok: true,
              port: actualPort,
              build: tradeSession.buildId,
              backends: ["claude-deepseek", "codex-openai"],
            }))
          }

          if (url.pathname === "/api/internal/ui-tools/call") {
            return withOriginAgentCluster(handleInternalUiToolCall(req, bridgeToken, uiSyncHub))
          }

          if (url.pathname === "/api/trade-auth/session") {
            if (req.method !== "GET") {
              return withOriginAgentCluster(new Response(null, { status: 405, headers: { Allow: "GET" } }))
            }
            return withOriginAgentCluster(tradeSession.statusResponse(req))
          }

          if (url.pathname === "/api/trade-auth/logout") {
            if (req.method !== "POST") {
              return withOriginAgentCluster(new Response(null, { status: 405, headers: { Allow: "POST" } }))
            }
            return withOriginAgentCluster(tradeSession.logout(req))
          }

          const verifiedSession = await tradeSession.verify(req)
          if (!verifiedSession) {
            if (url.pathname === "/ws" || url.pathname === "/ws/ui" || url.pathname.startsWith("/api/")) {
              return withOriginAgentCluster(Response.json({ error: "Authentication required." }, { status: 401 }))
            }
            return withOriginAgentCluster(Response.redirect(tradeSession.loginUrl, 303))
          }

          const validOrigin = url.pathname === "/ws/ui"
            ? tradeSession.validateUiSyncOrigin(req)
            : tradeSession.validateOrigin(req)
          if (!validOrigin) {
            return withOriginAgentCluster(new Response("Forbidden", { status: 403 }))
          }

          if (url.pathname === "/ws") {
            return withOriginAgentCluster(upgradeWebSocket(
              verifiedSession.identity,
              req.headers.get("cookie") ?? "",
              "agent",
            ))
          }

          if (url.pathname === "/ws/ui") {
            return withOriginAgentCluster(upgradeWebSocket(
              verifiedSession.identity,
              req.headers.get("cookie") ?? "",
              "ui-sync",
            ))
          }

          const uploadResponse = await handleProjectUpload(req, url, store, projectCatalog)
          if (uploadResponse) {
            return withOriginAgentCluster(uploadResponse)
          }

          const deleteUploadResponse = await handleProjectUploadDelete(req, url, store, projectCatalog)
          if (deleteUploadResponse) {
            return withOriginAgentCluster(deleteUploadResponse)
          }

          const attachmentContentResponse = await handleAttachmentContent(req, url, store, projectCatalog)
          if (attachmentContentResponse) {
            return withOriginAgentCluster(attachmentContentResponse)
          }

          const projectFileContentResponse = await handleProjectFileContent(req, url, store, projectCatalog)
          if (projectFileContentResponse) {
            return withOriginAgentCluster(projectFileContentResponse)
          }

          return withOriginAgentCluster(serveStatic(distDir, url.pathname))
        },
        websocket: {
          open(ws) {
            authenticatedSockets.add(ws)
            if (ws.data.socketKind === "agent") {
              router.handleOpen(ws as unknown as import("bun").ServerWebSocket<ClientState>)
            } else {
              uiSyncHub.handleOpen(ws as unknown as UiSyncSocket)
            }
          },
          message(ws, raw) {
            if (ws.data.socketKind === "agent") {
              router.handleMessage(ws as unknown as import("bun").ServerWebSocket<ClientState>, raw)
            } else {
              uiSyncHub.handleMessage(ws as unknown as UiSyncSocket, raw)
            }
          },
          close(ws) {
            authenticatedSockets.delete(ws)
            if (ws.data.socketKind === "agent") {
              router.handleClose(ws as unknown as import("bun").ServerWebSocket<ClientState>)
            } else {
              uiSyncHub.handleClose(ws as unknown as UiSyncSocket)
            }
          },
        },
      })
      break
    } catch (err: unknown) {
      const isAddrInUse =
        err instanceof Error && "code" in err && (err as NodeJS.ErrnoException).code === "EADDRINUSE"
      if (!isAddrInUse || strictPort || attempt === MAX_PORT_ATTEMPTS - 1) {
        throw err
      }
      console.log(`Port ${actualPort} is in use, trying ${actualPort + 1}...`)
      actualPort++
    }
  }

  let sessionRevalidationRunning = false
  const sessionRevalidationInterval = setInterval(() => {
    if (sessionRevalidationRunning) return
    sessionRevalidationRunning = true
    void Promise.all([...authenticatedSockets].map(async (ws) => {
      const verified = await tradeSession.verifyCookieHeader(ws.data.sessionCookie)
      if (!verified) {
        ws.close(4401, "TradeEngine session expired")
        authenticatedSockets.delete(ws)
        return
      }
      ws.data.identity = verified.identity
    })).finally(() => {
      sessionRevalidationRunning = false
    })
  }, TRADE_SESSION_REVALIDATE_INTERVAL_MS)

  const shutdown = async () => {
    clearInterval(staleEmptyChatPruneInterval)
    clearInterval(staleChatAutoArchiveInterval)
    clearInterval(staleChatDeleteInterval)
    clearInterval(sessionRevalidationInterval)
    uiSyncHub.dispose()
    worktreeProbe.stop()
    await agent.close()
    router.dispose()
    providerAuth.dispose()
    usageLimits.dispose()
    appSettings.dispose()
    keybindings.dispose()
    terminals.closeAll()
    await store.compact()
    server.stop(true)
  }

  return {
    port: actualPort,
    store,
    diffStore,
    analytics,
    stop: shutdown,
  }
}

function describeUploadError(error: unknown) {
  if (!(error instanceof Error)) {
    return String(error)
  }
  const cause = error.cause === undefined ? "" : `\ncause: ${String(error.cause)}`
  return `${error.name}: ${error.message}${cause}\n${error.stack ?? "(no stack)"}`
}

async function handleProjectUpload(req: Request, url: URL, store: EventStore, projectCatalog: TradeProjectCatalog) {
  if (req.method !== "POST") {
    return null
  }

  const match = url.pathname.match(/^\/api\/projects\/([^/]+)\/uploads$/)
  if (!match) {
    return null
  }

  const project = store.getProject(match[1])
  if (!project) {
    return Response.json({ error: "Project not found" }, { status: 404 })
  }

  // Parsing is its own failure mode, so it gets its own report. A drag-sourced
  // File on iOS is backed by a temporary drag-session file. If the system
  // releases it before the body finishes streaming, the multipart body arrives
  // shorter than its Content-Length and parsing throws here.
  let formData: FormData
  try {
    formData = await req.formData()
  } catch (error) {
    return Response.json({
      error: "The server could not read the upload request body.",
      stage: "parse-form-data",
      detail: describeUploadError(error),
      contentType: req.headers.get("content-type"),
      contentLength: req.headers.get("content-length"),
    }, { status: 400 })
  }

  const files = formData
    .getAll("files")
    .filter((value): value is File => value instanceof File)

  if (files.length === 0) {
    return Response.json({
      error: "No files uploaded",
      stage: "read-files",
      detail: `The request parsed, but it carried no file parts.\nform fields: ${[...new Set(formData.keys())].join(", ") || "(none)"}`,
      contentLength: req.headers.get("content-length"),
    }, { status: 400 })
  }

  // The client reports each file's size before sending it. A shortfall here
  // means the body was truncated in transit, which the parser cannot see.
  const clientSizes = formData.getAll("clientFileSizes")
  if (clientSizes.length === files.length) {
    const truncated = files
      .map((file, index) => ({ file, expected: Number(clientSizes[index]) }))
      .filter((entry) => Number.isFinite(entry.expected) && entry.expected !== entry.file.size)
    if (truncated.length > 0) {
      return Response.json({
        error: "The uploaded file arrived incomplete.",
        stage: "size-mismatch",
        detail: truncated
          .map((entry) => `${entry.file.name}: client sent ${entry.expected} bytes, server received ${entry.file.size} bytes`)
          .join("\n"),
        contentLength: req.headers.get("content-length"),
      }, { status: 400 })
    }
  }

  if (files.length > MAX_UPLOAD_FILES) {
    return Response.json({ error: `You can upload up to ${MAX_UPLOAD_FILES} files at a time.` }, { status: 400 })
  }

  for (const file of files) {
    if (file.size > MAX_UPLOAD_SIZE_BYTES) {
      return Response.json(
        { error: `File "${file.name}" exceeds the ${Math.floor(MAX_UPLOAD_SIZE_BYTES / (1024 * 1024))} MB limit.` },
        { status: 413 }
      )
    }
  }

  try {
    const attachments = await persistUploadedFiles({
      projectId: project.id,
      localPath: await projectCatalog.resolve(project.id),
      files,
    })
    return Response.json({ attachments })
  } catch (error) {
    console.error("[uploads] Upload failed:", error)
    return Response.json({
      error: "The server could not save the uploaded files.",
      stage: "persist",
      detail: describeUploadError(error),
      files: files.map((file) => ({ name: file.name, size: file.size, type: file.type })),
    }, { status: 500 })
  }
}

async function handleAttachmentContent(req: Request, url: URL, store: EventStore, projectCatalog: TradeProjectCatalog) {
  const match = url.pathname.match(/^\/api\/projects\/([^/]+)\/uploads\/([^/]+)\/content$/)
  if (!match) {
    return null
  }

  if (req.method !== "GET") {
    return new Response(null, {
      status: 405,
      headers: {
        Allow: "GET",
      },
    })
  }

  const project = store.getProject(match[1])
  if (!project) {
    return Response.json({ error: "Project not found" }, { status: 404 })
  }

  const storedName = decodeURIComponent(match[2])
  if (!storedName || storedName.includes("/") || storedName.includes("\\") || storedName === "." || storedName === "..") {
    return Response.json({ error: "Invalid attachment path" }, { status: 400 })
  }

  const filePath = path.join(getProjectUploadDir(await projectCatalog.resolve(project.id)), storedName)
  const file = Bun.file(filePath)
  try {
    const info = await stat(filePath)
    if (!info.isFile()) {
      return Response.json({ error: "Attachment not found" }, { status: 404 })
    }
  } catch {
    return Response.json({ error: "Attachment not found" }, { status: 404 })
  }

  return new Response(file, {
    headers: {
      "Content-Type": inferAttachmentContentType(storedName, file.type),
    },
  })
}

async function handleProjectFileContent(req: Request, url: URL, store: EventStore, projectCatalog: TradeProjectCatalog) {
  const match = url.pathname.match(/^\/api\/projects\/([^/]+)\/files\/([^/]+)\/content$/)
  if (!match) {
    return null
  }

  if (req.method !== "GET") {
    return new Response(null, {
      status: 405,
      headers: {
        Allow: "GET",
      },
    })
  }

  const project = store.getProject(match[1])
  if (!project) {
    return Response.json({ error: "Project not found" }, { status: 404 })
  }

  const relativePath = path.posix.normalize(decodeURIComponent(match[2]).replaceAll("\\", "/"))
  if (!relativePath || relativePath === "." || relativePath.startsWith("../") || relativePath.includes("/../") || path.posix.isAbsolute(relativePath)) {
    return Response.json({ error: "Invalid project file path" }, { status: 400 })
  }

  const projectRoot = path.resolve(await projectCatalog.resolve(project.id))
  const filePath = path.resolve(projectRoot, relativePath)
  if (filePath !== projectRoot && !filePath.startsWith(`${projectRoot}${path.sep}`)) {
    return Response.json({ error: "Invalid project file path" }, { status: 400 })
  }

  const file = Bun.file(filePath)
  try {
    const info = await stat(filePath)
    if (!info.isFile()) {
      return Response.json({ error: "File not found" }, { status: 404 })
    }
  } catch {
    return Response.json({ error: "File not found" }, { status: 404 })
  }

  return new Response(file, {
    headers: {
      "Content-Type": inferProjectFileContentType(relativePath, file.type),
    },
  })
}

async function handleProjectUploadDelete(req: Request, url: URL, store: EventStore, projectCatalog: TradeProjectCatalog) {
  if (req.method !== "DELETE") {
    return null
  }

  const match = url.pathname.match(/^\/api\/projects\/([^/]+)\/uploads\/([^/]+)$/)
  if (!match) {
    return null
  }

  const project = store.getProject(match[1])
  if (!project) {
    return Response.json({ error: "Project not found" }, { status: 404 })
  }

  const storedName = decodeURIComponent(match[2])
  if (!storedName || storedName.includes("/") || storedName.includes("\\") || storedName === "." || storedName === "..") {
    return Response.json({ error: "Invalid attachment path" }, { status: 400 })
  }

  const deleted = await deleteProjectUpload({
    localPath: await projectCatalog.resolve(project.id),
    storedName,
  })

  return Response.json({ ok: deleted })
}

async function serveStatic(distDir: string, pathname: string) {
  const requestedPath = pathname === "/" ? "/index.html" : pathname
  const filePath = path.join(distDir, requestedPath)
  const indexPath = path.join(distDir, "index.html")

  const file = Bun.file(filePath)
  if (await file.exists()) {
    return new Response(file, {
      headers: getStaticHeaders(requestedPath),
    })
  }

  const indexFile = Bun.file(indexPath)
  if (await indexFile.exists()) {
    return new Response(indexFile, {
      headers: {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-store",
      },
    })
  }

  return new Response(
    `${APP_NAME} client bundle not found. Run \`bun run build\` inside workbench/ first.`,
    { status: 503 }
  )
}

function getStaticHeaders(requestedPath: string) {
  if (requestedPath.endsWith(".html")) {
    return {
      "Cache-Control": "no-store",
    }
  }

  // Vite emits content-hashed filenames under /assets/ — safe to cache
  // forever. Matters most in cloud mode, where every uncached asset request
  // pays proxy + D1 + tunnel latency on top of the local read.
  if (requestedPath.startsWith("/assets/")) {
    return {
      "Cache-Control": "public, max-age=31536000, immutable",
    }
  }

  return undefined
}
