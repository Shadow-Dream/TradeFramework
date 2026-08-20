import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useNavigate } from "react-router-dom"
import { useShallow } from "zustand/react/shallow"
import { PROVIDERS, type AgentProvider, type AppSettingsPatch, type AskUserQuestionAnswerMap, type AppSettingsSnapshot, type ChatDiffSnapshot, type KeybindingsSnapshot, type ModelOptions, type ProviderCatalogEntry, type QueuedChatMessage, type TranscriptEntry } from "../../shared/types"
import { NEW_CHAT_COMPOSER_ID, useChatPreferencesStore } from "../stores/chatPreferencesStore"
import { useChatInputStore } from "../stores/chatInputStore"
import {
  findSidebarChat,
  getSidebarProjectGroups,
  useChatExists,
  useFirstProjectGroup,
  useNavbarProjectLabel,
  useProjectIdForChat,
  useSidebarReady,
  useSidebarStore,
} from "../stores/sidebarStore"
import type { ChatSnapshot, LocalProjectsSnapshot, SidebarChatRow, SidebarData } from "../../shared/types"
import type { AskUserQuestionItem } from "../components/messages/types"
import { useAppDialog } from "../components/ui/app-dialog"
import { processTranscriptMessages } from "../lib/parseTranscript"
import { generateUUID } from "../lib/utils"
import { canCancelStatus, getLatestToolIds, isProcessingStatus } from "./derived"
import {
  getActiveChatSnapshot,
  getMostRecentlyActiveProjectId,
  getNewestRemainingChatId,
  getPreviousPrompt,
  NEW_CHAT_OPTIMISTIC_SCOPE,
  reconcileOptimisticUserPrompts,
  resolveComposeIntent,
  type OptimisticProcessingState,
  type OptimisticUserPrompt,
} from "./kannaStateHelpers"
import {
  foldChatSnapshot,
  sameDiffs,
  shouldPreserveExistingProjectDiffs,
} from "./snapshotEquality"
import {
  cachedWindowToMessages,
  createTranscriptCacheWriter,
  readCachedWindow,
  toCachedSpan,
  type CachedTranscriptWindow,
} from "./chatTranscriptCache"
import { KannaSocket, type SocketStatus } from "./socket"
import { useAppSettingsSync } from "./useAppSettingsSync"
import { useChatCommands } from "./useChatCommands"
import { useChatReadAnchor, type ChatReadAnchorState } from "./useChatReadAnchor"
import { useSendMessage } from "./useSendMessage"

export {
  applySidebarProjectOrder,
  countMatchingUserPrompts,
  getActiveChatSnapshot,
  getMostRecentlyActiveProjectId,
  getNewestRemainingChatId,
  getNextMeasuredInputHeight,
  getPreviousPrompt,
  getTranscriptPaddingBottom,
  getUserPromptSignature,
  reconcileOptimisticUserPrompts,
  resolveComposeIntent,
  shouldAutoFollowTranscript,
  shouldMarkActiveChatRead,
  TRANSCRIPT_PADDING_BOTTOM_OFFSET,
  type OptimisticUserPrompt,
} from "./kannaStateHelpers"

/** Stable identity so an empty transcript does not re-derive rows each render. */
const EMPTY_TRANSCRIPT_ENTRIES: TranscriptEntry[] = []

/**
 * How long to wait for the local transcript cache before subscribing without
 * it. Generous next to a healthy read and still short enough that a stalled
 * one is not something you sit and look at.
 */
const CACHED_WINDOW_READ_BUDGET_MS = 250

function sameOriginWsUrl() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:"
  return `${protocol}//${window.location.host}/ws`
}

async function wsUrlProvider(): Promise<string> {
  return sameOriginWsUrl()
}

function useKannaSocket() {
  const socketRef = useRef<KannaSocket | null>(null)
  if (!socketRef.current) {
    socketRef.current = new KannaSocket(wsUrlProvider)
  }

  useEffect(() => {
    const socket = socketRef.current
    socket?.start()
    return () => {
      socket?.dispose()
    }
  }, [])

  return socketRef.current as KannaSocket
}

export interface KannaState {
  socket: KannaSocket
  activeChatId: string | null
  activeProjectId: string | null
  localProjects: LocalProjectsSnapshot | null
  chatSnapshot: ChatSnapshot | null
  /** Server-stored read position for the active chat; drives restore on open. */
  readAnchorState: ChatReadAnchorState
  /** Report the message at the top of the viewport (throttled write). */
  reportReadAnchor: (messageId: string, atEnd: boolean) => void
  chatDiffSnapshot: ChatDiffSnapshot | null
  keybindings: KeybindingsSnapshot | null
  appSettings: AppSettingsSnapshot | null
  connectionStatus: SocketStatus
  sidebarReady: boolean
  localProjectsReady: boolean
  commandError: string | null
  sidebarOpen: boolean
  sidebarCollapsed: boolean
  messages: ReturnType<typeof processTranscriptMessages>
  queuedMessages: QueuedChatMessage[]
  previousPrompt: string | null
  latestToolIds: ReturnType<typeof getLatestToolIds>
  runtime: ChatSnapshot["runtime"] | null
  runtimeStatus: string | null
  availableProviders: ProviderCatalogEntry[]
  isProcessing: boolean
  canCancel: boolean
  isDraining: boolean
  navbarProjectId?: string
  /**
   * `repo/branch` for the selected logical Project, null when that
   * folder isn't in a repo (or hasn't been probed) — the composer placeholder
   * names the checkout when there is one and the path when there isn't.
   */
  navbarProjectLabel: string | null
  hasSelectedProject: boolean
  openSidebar: () => void
  closeSidebar: () => void
  collapseSidebar: () => void
  expandSidebar: () => void
  handleCreateChat: (projectId: string) => Promise<void>
  handleForkChat: (chat: SidebarChatRow) => Promise<void>
  handleReadAppSettings: () => Promise<void>
  handleWriteAppSettings: (patch: AppSettingsPatch) => Promise<void>
  handleSignOut: () => Promise<void>
  handleSend: (content: string, options?: { provider?: AgentProvider; model?: string; modelOptions?: ModelOptions; planMode?: boolean; autoPlan?: boolean }) => Promise<void>
  handleSteerQueuedMessage: (queuedMessageId: string) => Promise<void>
  handleRemoveQueuedMessage: (queuedMessageId: string) => Promise<void>
  handleCancel: () => Promise<void>
  handleRetry: () => Promise<void>
  handleStopDraining: () => Promise<void>
  handleRenameChat: (chat: SidebarChatRow) => Promise<void>
  handleRenameProject: (projectId: string, sidebarTitle: string | undefined, realTitle: string) => Promise<void>
  handleArchiveChat: (chat: SidebarChatRow) => Promise<void>
  handleOpenArchivedChat: (chatId: string) => Promise<void>
  handleRestoreChat: (chatId: string) => Promise<void>
  handleDeleteChat: (chat: SidebarChatRow) => Promise<void>
  handleReorderProjectGroups: (projectIds: string[]) => Promise<void>
  handleCompose: () => void
  handleAskUserQuestion: (
    toolUseId: string,
    questions: AskUserQuestionItem[],
    answers: AskUserQuestionAnswerMap
  ) => Promise<void>
  handleExitPlanMode: (
    toolUseId: string,
    confirmed: boolean,
    clearContext?: boolean,
    message?: string
  ) => Promise<void>
}

export function useKannaState(activeChatId: string | null): KannaState {
  const navigate = useNavigate()
  const socket = useKannaSocket()
  const dialog = useAppDialog()

  const [localProjects, setLocalProjects] = useState<LocalProjectsSnapshot | null>(null)
  const [chatSnapshot, setChatSnapshot] = useState<ChatSnapshot | null>(null)
  const transcriptCacheWriter = useMemo(() => createTranscriptCacheWriter(), [])
  const [projectDiffSnapshots, setProjectDiffSnapshots] = useState<Record<string, ChatDiffSnapshot | null>>({})
  const [connectionStatus, setConnectionStatus] = useState<SocketStatus>("connecting")
  const sidebarReady = useSidebarReady()
  const [localProjectsReady, setLocalProjectsReady] = useState(false)
  const [chatReady, setChatReady] = useState(false)
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [commandError, setCommandError] = useState<string | null>(null)
  const [pendingChatId, setPendingChatId] = useState<string | null>(null)
  const [optimisticUserPrompts, setOptimisticUserPrompts] = useState<OptimisticUserPrompt[]>([])
  const [optimisticProcessing, setOptimisticProcessing] = useState<OptimisticProcessingState | null>(null)
  const draftChatIds = useChatInputStore(useShallow((state) => Object.keys(state.drafts).sort()))
  const attachmentDraftChatIds = useChatInputStore(
    useShallow((state) => Object.keys(state.attachmentDrafts).sort())
  )
  const lastActiveProjectDiffRef = useRef<{ projectId: string | null; diffs: ChatDiffSnapshot | null }>({
    projectId: null,
    diffs: null,
  })

  useEffect(() => socket.onStatus(setConnectionStatus), [socket])

  // Straight into the store, never into React state: a running turn moves a
  // sidebar field several times a second, and holding the snapshot here would
  // re-render this hook's whole subtree — the chat page included — every time.
  // Consumers select the slice they paint (see stores/sidebarStore).
  useEffect(() => {
    return socket.subscribe<SidebarData>({ type: "sidebar" }, (snapshot) => {
      useSidebarStore.getState().setSnapshot(snapshot)
      setCommandError(null)
    })
  }, [socket])

  useEffect(() => {
    if (connectionStatus !== "connected") return

    const protectedChatIds = [...new Set([...draftChatIds, ...attachmentDraftChatIds])].sort()
    void socket.command({ type: "chat.setDraftProtection", chatIds: protectedChatIds }).catch((error) => {
      setCommandError(error instanceof Error ? error.message : String(error))
    })
  }, [attachmentDraftChatIds, connectionStatus, draftChatIds, socket])

  useEffect(() => {
    return socket.subscribe<LocalProjectsSnapshot>({ type: "local-projects" }, (snapshot) => {
      setLocalProjects(snapshot)
      setLocalProjectsReady(true)
      setCommandError(null)
    })
  }, [socket])

  const {
    keybindings,
    appSettings,
    handleReadAppSettings,
    handleWriteAppSettings,
  } = useAppSettingsSync({ socket, connectionStatus, setCommandError })

  useEffect(() => {
    if (!activeChatId) {
      setChatSnapshot(null)
      setChatReady(true)
      return
    }

    setChatSnapshot(null)
    setChatReady(false)

    // Narrowed once for the closures below, which lose it otherwise.
    const chatId = activeChatId
    let cancelled = false
    let unsubscribe: (() => void) | null = null
    // Base for the first incremental push: the server resumes from the cached
    // span, so its first body starts where this window ends rather than
    // repeating it.
    let base: { messages: TranscriptEntry[]; startIndex: number } | null = null

    function handleSnapshot(snapshot: ChatSnapshot | null) {
      // `foldChatSnapshot` is pure by contract — see its comment. Keep this
      // updater a bare call to it and nothing else; the last thing that folded
      // inline also cleared `base` as it went, and React re-running the updater
      // then left the transcript blank.
      setChatSnapshot((current) => foldChatSnapshot(current, base, snapshot))
      setChatReady(true)
      setCommandError(null)
    }

    let subscribed = false
    function subscribeToChat(cached: CachedTranscriptWindow | null) {
      if (cancelled || subscribed) return
      subscribed = true
      const span = toCachedSpan(cached)
      if (cached && span) base = cachedWindowToMessages(cached)
      // No `recentLimit`: the server sizes the window to reach the stored read
      // anchor and returns it inline. Passing one here would re-subscribe (and
      // re-send the whole transcript) once the anchor resolved.
      unsubscribe = socket.subscribe<ChatSnapshot | null>(
        { type: "chat", chatId, ...(span ? { cachedSpan: span } : {}) },
        handleSnapshot
      )
    }

    // The cache read only decides where the server should resume from, so it
    // must never be what the transcript is waiting on. It normally takes a few
    // milliseconds, but IndexedDB is a shared queue: a read issued just as the
    // cache writer puts a large window can sit behind it, and nothing has even
    // been asked of the server until it comes back. Past the deadline we
    // subscribe cold and take the full window — more bytes, but it arrives.
    const cacheDeadline = window.setTimeout(() => subscribeToChat(null), CACHED_WINDOW_READ_BUDGET_MS)
    void readCachedWindow(chatId).then((cached) => {
      window.clearTimeout(cacheDeadline)
      subscribeToChat(cached)
    })

    return () => {
      cancelled = true
      window.clearTimeout(cacheDeadline)
      unsubscribe?.()
      // A chat closed mid-turn never reaches a settled write, so take what is
      // pending rather than lose the window.
      transcriptCacheWriter.flush()
    }
  }, [activeChatId, socket, transcriptCacheWriter])


  // Seeded once the snapshot lands. Reads the groups off the store rather than
  // subscribing to them: the seed only has to be right the first time, and a
  // subscription here would drag every sidebar push back into this hook.
  useEffect(() => {
    if (selectedProjectId || !sidebarReady) return
    const seed = getMostRecentlyActiveProjectId(getSidebarProjectGroups())
    if (seed) {
      setSelectedProjectId(seed)
    }
  }, [selectedProjectId, sidebarReady])

  // Archived chats are viewable in place (viewing doesn't unarchive), so they
  // count as existing — only truly unknown/deleted chats bounce home.
  const activeChatExists = useChatExists(activeChatId)
  useEffect(() => {
    if (!activeChatId) return
    if (!sidebarReady || !chatReady) return
    if (activeChatExists) {
      if (pendingChatId === activeChatId) {
        setPendingChatId(null)
      }
      return
    }
    if (pendingChatId === activeChatId) {
      return
    }
    navigate("/")
  }, [activeChatExists, activeChatId, chatReady, navigate, pendingChatId, sidebarReady])

  useEffect(() => {
    if (!chatSnapshot) return
    setSelectedProjectId(chatSnapshot.runtime.projectId)
    if (pendingChatId === chatSnapshot.runtime.chatId) {
      setPendingChatId(null)
    }
  }, [chatSnapshot, pendingChatId])

  // Mark a chat read when the user navigates *away* from it, not when it opens.
  // A chat that receives new activity while it's the active chat stays unread
  // (badge visible) until the user leaves it. The outgoing chat's unread state
  // is read off the store at the moment of the switch, so this effect only runs
  // on chat switches, and chats that no longer exist are skipped (which avoids
  // spurious markRead commands).
  const previousActiveChatIdRef = useRef<string | null>(null)
  useEffect(() => {
    const previousChatId = previousActiveChatIdRef.current
    previousActiveChatIdRef.current = activeChatId ?? null
    if (!previousChatId || previousChatId === activeChatId) return
    if (!findSidebarChat(previousChatId)?.unread) return
    void socket.command({ type: "chat.markRead", chatId: previousChatId }).catch((error) => {
      setCommandError(error instanceof Error ? error.message : String(error))
    })
  }, [activeChatId, socket])

  const activeChatSnapshot = useMemo(
    () => getActiveChatSnapshot(chatSnapshot, activeChatId),
    [activeChatId, chatSnapshot]
  )

  // Reads the anchor off the snapshot (the server resolves it against the
  // window it chose) and owns the throttled write-back.
  const {
    anchorState: readAnchorState,
    reportReadAnchor,
  } = useChatReadAnchor(socket, activeChatId, activeChatSnapshot?.readAnchor, chatReady)

  const sidebarProjectIdForChat = useProjectIdForChat(activeChatId)
  const activeProjectId = activeChatSnapshot?.runtime.projectId
    ?? sidebarProjectIdForChat
    ?? selectedProjectId
  const chatDiffSnapshot = useMemo(() => {
    const currentDiffs = activeProjectId ? (projectDiffSnapshots[activeProjectId] ?? null) : null
    if (activeProjectId && currentDiffs) {
      lastActiveProjectDiffRef.current = {
        projectId: activeProjectId,
        diffs: currentDiffs,
      }
      return currentDiffs
    }

    if (activeProjectId && lastActiveProjectDiffRef.current.projectId === activeProjectId) {
      return lastActiveProjectDiffRef.current.diffs
    }

    return currentDiffs
  }, [activeProjectId, projectDiffSnapshots])

  useEffect(() => {
    if (!activeProjectId) {
      return
    }

    const unsubscribe = socket.subscribe<ChatDiffSnapshot | null>({ type: "project-git", projectId: activeProjectId }, (snapshot) => {
      setProjectDiffSnapshots((current) => {
        const nextDiffs = snapshot ?? null
        if (shouldPreserveExistingProjectDiffs(current[activeProjectId] ?? null, nextDiffs)) {
          return current
        }
        if (sameDiffs(current[activeProjectId] ?? null, nextDiffs)) {
          return current
        }
        return {
          ...current,
          [activeProjectId]: nextDiffs,
        }
      })
      setCommandError(null)
    })

    return unsubscribe
  }, [activeProjectId, socket])
  const serverTranscriptEntries = activeChatSnapshot?.messages ?? EMPTY_TRANSCRIPT_ENTRIES
  const optimisticScopeId = activeChatId ?? NEW_CHAT_OPTIMISTIC_SCOPE
  const optimisticTranscriptEntries = useMemo(
    () => optimisticUserPrompts
      .filter((prompt) => prompt.scopeId === optimisticScopeId)
      .map((prompt) => prompt.entry),
    [optimisticScopeId, optimisticUserPrompts]
  )
  const transcriptEntries = useMemo(
    () => [...serverTranscriptEntries, ...optimisticTranscriptEntries],
    [optimisticTranscriptEntries, serverTranscriptEntries]
  )
  const messages = useMemo(() => processTranscriptMessages(transcriptEntries), [transcriptEntries])
  const previousPrompt = useMemo(() => getPreviousPrompt(messages), [messages])
  const latestToolIds = useMemo(() => getLatestToolIds(messages), [messages])
  const runtime = activeChatSnapshot?.runtime ?? null
  const queuedMessages = activeChatSnapshot?.queuedMessages ?? []
  const optimisticRuntimeStatus = optimisticProcessing?.scopeId === optimisticScopeId && (!runtime || runtime.status === "idle")
    ? "starting"
    : null
  const effectiveRuntimeStatus = optimisticRuntimeStatus ?? runtime?.status ?? null
  const availableProviders = activeChatSnapshot?.availableProviders ?? PROVIDERS
  const isProcessing = isProcessingStatus(effectiveRuntimeStatus ?? undefined)

  // Written after a turn settles, not during: the window changes many times a
  // second while streaming and the server is the source of truth throughout.
  useEffect(() => {
    if (!activeChatId || !chatSnapshot) return
    transcriptCacheWriter.schedule(activeChatId, chatSnapshot, isProcessing)
  }, [activeChatId, chatSnapshot, isProcessing, transcriptCacheWriter])

  const canCancel = canCancelStatus(effectiveRuntimeStatus ?? undefined)
  const isDraining = runtime?.isDraining ?? false
  const fallbackProjectId = localProjects?.projects[0]?.projectId ?? null
  const firstProjectGroup = useFirstProjectGroup()
  const navbarProjectId =
    runtime?.projectId
    ?? fallbackProjectId
    ?? firstProjectGroup.groupKey
    ?? undefined
  // The composer uses the same logical Project label as the sidebar; the
  // browser never receives the server-side workspace path.
  const navbarProjectLabel = useNavbarProjectLabel(activeProjectId ?? navbarProjectId ?? null)
  const hasSelectedProject = Boolean(
    selectedProjectId
    ?? runtime?.projectId
    ?? firstProjectGroup.groupKey
    ?? fallbackProjectId
  )

  useEffect(() => {
    if (optimisticProcessing?.scopeId !== optimisticScopeId) {
      return
    }
    if (runtime?.status && runtime.status !== "idle") {
      setOptimisticProcessing(null)
    }
  }, [optimisticProcessing, optimisticScopeId, runtime?.status])

  useEffect(() => {
    if (!optimisticProcessing?.ackedAt || optimisticProcessing.scopeId !== optimisticScopeId) {
      return
    }
    if (runtime?.status && runtime.status !== "idle") {
      return
    }
    const timeoutId = window.setTimeout(() => {
      setOptimisticProcessing((current) => (
        current?.scopeId === optimisticScopeId && current.ackedAt === optimisticProcessing.ackedAt
          ? null
          : current
      ))
    }, 300)
    return () => window.clearTimeout(timeoutId)
  }, [optimisticProcessing, optimisticScopeId, runtime?.status])

  useEffect(() => {
    setOptimisticUserPrompts((current) => {
      const reconciled = reconcileOptimisticUserPrompts(current, optimisticScopeId, serverTranscriptEntries)
      if (reconciled.length === current.length && reconciled.every((prompt, index) => prompt === current[index])) {
        return current
      }
      return reconciled
    })
  }, [optimisticScopeId, serverTranscriptEntries])

  const createChatForProject = useCallback(async (projectId: string) => {
    const chatPreferences = useChatPreferencesStore.getState()
    const sourceComposerState = activeChatId
      ? chatPreferences.getComposerState(activeChatId)
      : chatPreferences.getComposerState(NEW_CHAT_COMPOSER_ID)
    const result = await socket.command<{ chatId: string }>({
      type: "chat.create",
      projectId,
      provider: sourceComposerState.provider,
      clientRequestId: generateUUID(),
    })
    chatPreferences.initializeComposerForChat(result.chatId, { sourceState: sourceComposerState })
    setSelectedProjectId(projectId)
    setPendingChatId(result.chatId)
    navigate(`/chat/${result.chatId}`)
    setSidebarOpen(false)
    setCommandError(null)
  }, [activeChatId, navigate, socket])

  const startChatForProject = useCallback(async (projectId: string) => {
    try {
      await createChatForProject(projectId)
    } catch (error) {
      setCommandError(error instanceof Error ? error.message : String(error))
    }
  }, [createChatForProject])

  const handleCreateChat = useCallback(async (projectId: string) => {
    await startChatForProject(projectId)
  }, [startChatForProject])

  const handleForkChat = useCallback(async (chat: SidebarChatRow) => {
    try {
      const result = await socket.command<{ chatId: string }>({
        type: "chat.fork",
        chatId: chat.chatId,
        clientRequestId: generateUUID(),
      })
      const chatPreferences = useChatPreferencesStore.getState()
      chatPreferences.initializeComposerForChat(result.chatId, {
        sourceState: chatPreferences.getComposerState(chat.chatId),
      })
      setPendingChatId(result.chatId)
      navigate(`/chat/${result.chatId}`)
      setSidebarOpen(false)
      setCommandError(null)
    } catch (error) {
      setCommandError(error instanceof Error ? error.message : String(error))
    }
  }, [navigate, socket])

  const handleSignOut = useCallback(async () => {
    try {
      const response = await fetch("/api/trade-auth/logout", {
        method: "POST",
        headers: {
          Accept: "application/json",
        },
      })

      if (!response.ok) {
        throw new Error(`Sign out failed with status ${response.status}`)
      }

      setCommandError(null)
      window.location.reload()
    } catch (error) {
      setCommandError(error instanceof Error ? error.message : String(error))
    }
  }, [])

  const handleSend = useSendMessage({
    socket,
    navigate,
    activeChatId,
    setCommandError,
    setSelectedProjectId,
    setPendingChatId,
    setOptimisticProcessing,
    setOptimisticUserPrompts,
    sendContext: {
      isProcessing,
      optimisticUserPrompts,
      serverTranscriptEntries,
      selectedProjectId,
    },
  })

  const handleDeleteChat = useCallback(async (chat: SidebarChatRow) => {
    const confirmed = await dialog.confirm({
      title: "Delete Chat",
      description: `Delete "${chat.title}"? This cannot be undone.`,
      confirmLabel: "Delete",
      confirmVariant: "destructive",
    })
    if (!confirmed) return
    try {
      await socket.command({ type: "chat.delete", chatId: chat.chatId })
      if (chat.chatId === activeChatId) {
        const nextChatId = getNewestRemainingChatId(getSidebarProjectGroups(), chat.chatId)
        navigate(nextChatId ? `/chat/${nextChatId}` : "/")
      }
    } catch (error) {
      setCommandError(error instanceof Error ? error.message : String(error))
    }
  }, [activeChatId, dialog, navigate, socket])

  const handleArchiveChat = useCallback(async (chat: SidebarChatRow) => {
    try {
      await socket.command({ type: "chat.archive", chatId: chat.chatId })
      if (chat.chatId === activeChatId) {
        const nextChatId = getNewestRemainingChatId(getSidebarProjectGroups(), chat.chatId)
        navigate(nextChatId ? `/chat/${nextChatId}` : "/")
      }
      setCommandError(null)
    } catch (error) {
      setCommandError(error instanceof Error ? error.message : String(error))
    }
  }, [activeChatId, navigate, socket])

  // Viewing an archived chat is read-only navigation — it stays archived.
  // Restoring is explicit (context menu) or implicit via sending a message
  // (the server unarchives on chat.send).
  const handleOpenArchivedChat = useCallback(async (chatId: string) => {
    navigate(`/chat/${chatId}`)
  }, [navigate])

  const handleRestoreChat = useCallback(async (chatId: string) => {
    try {
      await socket.command({ type: "chat.unarchive", chatId })
      setCommandError(null)
    } catch (error) {
      setCommandError(error instanceof Error ? error.message : String(error))
    }
  }, [socket])

  const handleReorderProjectGroups = useCallback(async (projectIds: string[]) => {
    useSidebarStore.getState().setOptimisticProjectOrder(projectIds)
    try {
      await socket.command({ type: "sidebar.reorderProjectGroups", projectIds })
      setCommandError(null)
    } catch (error) {
      useSidebarStore.getState().setOptimisticProjectOrder(null)
      setCommandError(error instanceof Error ? error.message : String(error))
    }
  }, [socket])

  const {
    handleSteerQueuedMessage,
    handleRemoveQueuedMessage,
    handleCancel,
    handleRetry,
    handleStopDraining,
    handleRenameChat,
    handleRenameProject,
    handleAskUserQuestion,
    handleExitPlanMode,
  } = useChatCommands({
    socket,
    dialog,
    activeChatId,
    setCommandError,
  })

  const handleCompose = useCallback(() => {
    const intent = resolveComposeIntent({
      selectedProjectId,
      sidebarProjectId: getMostRecentlyActiveProjectId(getSidebarProjectGroups()),
    })
    if (intent) {
      void startChatForProject(intent.projectId)
      return
    }

    navigate("/")
  }, [navigate, selectedProjectId, startChatForProject])

  const openSidebar = useCallback(() => setSidebarOpen(true), [])
  const closeSidebar = useCallback(() => setSidebarOpen(false), [])
  const collapseSidebar = useCallback(() => setSidebarCollapsed(true), [])
  const expandSidebar = useCallback(() => setSidebarCollapsed(false), [])

  return {
    socket,
    activeChatId,
    activeProjectId,
    localProjects,
    chatSnapshot,
    readAnchorState,
    reportReadAnchor,
    chatDiffSnapshot,
    keybindings,
    appSettings,
    connectionStatus,
    sidebarReady,
    localProjectsReady,
    commandError,
    sidebarOpen,
    sidebarCollapsed,
    messages,
    queuedMessages,
    previousPrompt,
    latestToolIds,
    runtime,
    runtimeStatus: effectiveRuntimeStatus,
    availableProviders,
    isProcessing,
    canCancel,
    isDraining,
    navbarProjectId,
    navbarProjectLabel,
    hasSelectedProject,
    openSidebar,
    closeSidebar,
    collapseSidebar,
    expandSidebar,
    handleCreateChat,
    handleForkChat,
    handleReadAppSettings,
    handleWriteAppSettings,
    handleSignOut,
    handleSend,
    handleSteerQueuedMessage,
    handleRemoveQueuedMessage,
    handleCancel,
    handleRetry,
    handleStopDraining,
    handleRenameChat,
    handleRenameProject,
    handleArchiveChat,
    handleOpenArchivedChat,
    handleRestoreChat,
    handleDeleteChat,
    handleReorderProjectGroups,
    handleCompose,
    handleAskUserQuestion,
    handleExitPlanMode,
  }
}
