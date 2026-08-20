import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react"
import { Navigate, Outlet, Route, Routes, useLocation, useParams } from "react-router-dom"
import { AppDialogProvider } from "../components/ui/app-dialog"
import { TooltipProvider } from "../components/ui/tooltip"
import { APP_NAME } from "../../shared/branding"
import { useChatSoundPreferencesStore } from "../stores/chatSoundPreferencesStore"
import type { ChatSoundPreference } from "../stores/chatSoundPreferencesStore"
import { useProviderAuthStore } from "../stores/providerAuthStore"
import type { ChatTouchedFilesResult, ProviderAuthSnapshot } from "../../shared/types"
import { playChatNotificationSound, shouldPlayChatSound } from "../lib/chatSounds"
import { getBrowserWindowTitle, getChatSoundBurstCount } from "./chatNotifications"
import { KannaSidebar } from "./KannaSidebar"
import { ChatPage } from "./ChatPage"
import { LocalProjectsPage } from "./LocalProjectsPage"
import { SettingsPage } from "./SettingsPage"
import { useKannaState } from "./useKannaState"
import { useSidebarStore } from "../stores/sidebarStore"
import type { AppSettingsSnapshot } from "../../shared/types"
import type { UiTurnContextV1 } from "../../shared/ui-sync-protocol"
import { useUiContextStore } from "../stores/uiContextStore"
import {
  sanitizeTradeReturnPath,
  TradeAuthProvider,
  type TradeAccount,
  type TradeAuthContextValue,
} from "./TradeAuthContext"

const AUTH_STATUS_RETRY_DELAY_MS = 500

interface AuthStatusResponse {
  authenticated: boolean
  user: TradeAccount | null
  loginUrl: string
  tradeEngineUrl: string
  build: string
}

type AppAuthState =
  | { status: "checking" }
  | { status: "ready"; value: TradeAuthContextValue }

export function getAppAuthStateFromStatus(payload: Partial<AuthStatusResponse>): AppAuthState {
  if (payload.authenticated && payload.user && payload.tradeEngineUrl) {
    const browser = typeof window === "undefined" ? null : window
    const queryReturnPath = sanitizeTradeReturnPath(
      browser ? new URLSearchParams(browser.location.search).get("returnTo") : null,
    )
    if (browser && queryReturnPath !== "/") {
      browser.sessionStorage.setItem("trade-agent.returnTo", queryReturnPath)
    }
    const returnPath = sanitizeTradeReturnPath(
      browser?.sessionStorage.getItem("trade-agent.returnTo") ?? queryReturnPath,
    )
    return {
      status: "ready",
      value: {
        account: payload.user,
        tradeEngineUrl: payload.tradeEngineUrl,
        returnUrl: new URL(returnPath, payload.tradeEngineUrl).toString(),
        build: typeof payload.build === "string" && payload.build.trim() ? payload.build.trim() : "dev",
      },
    }
  }
  return { status: "checking" }
}

export function shouldRetryAuthStatusRequest(responseOk: boolean | null) {
  return responseOk !== true
}

function useAppAuthState() {
  const [state, setState] = useState<AppAuthState>({ status: "checking" })
  const retryTimeoutRef = useRef<number | null>(null)

  const refresh = useCallback(async () => {
    if (retryTimeoutRef.current !== null) {
      window.clearTimeout(retryTimeoutRef.current)
      retryTimeoutRef.current = null
    }

    setState((current) => current.status === "ready" ? current : { status: "checking" })

    let response: Response
    try {
      response = await fetch("/api/trade-auth/session", {
        method: "GET",
        cache: "no-store",
        headers: {
          Accept: "application/json",
        },
      })
    } catch {
      retryTimeoutRef.current = window.setTimeout(() => {
        void refresh()
      }, AUTH_STATUS_RETRY_DELAY_MS)
      return
    }

    if (shouldRetryAuthStatusRequest(response.ok)) {
      retryTimeoutRef.current = window.setTimeout(() => {
        void refresh()
      }, AUTH_STATUS_RETRY_DELAY_MS)
      return
    }

    const payload = await response.json() as Partial<AuthStatusResponse>
    if (!payload.authenticated) {
      if (typeof payload.loginUrl === "string" && payload.loginUrl.length > 0) {
        window.location.replace(payload.loginUrl)
        return
      }
      retryTimeoutRef.current = window.setTimeout(() => void refresh(), AUTH_STATUS_RETRY_DELAY_MS)
      return
    }
    setState(getAppAuthStateFromStatus(payload))
  }, [])

  useEffect(() => {
    void refresh()
    return () => {
      if (retryTimeoutRef.current !== null) {
        window.clearTimeout(retryTimeoutRef.current)
      }
    }
  }, [refresh])

  return { state }
}

export function shouldPlayChatNotificationSound(
  appSettings: AppSettingsSnapshot | null,
  preference: ChatSoundPreference,
  doc: Pick<Document, "visibilityState" | "hasFocus"> = document
) {
  return Boolean(appSettings) && shouldPlayChatSound(preference, doc)
}

function KannaLayout() {
  const location = useLocation()
  const params = useParams()
  const state = useKannaState(params.chatId ?? null)

  // Feed the provider-auth store for the app's lifetime: sign-in state powers
  // the settings/new-chat auth cards, the harness picker's "Sign In" pills,
  // and the blocked-switch dialog.
  useEffect(() => {
    useProviderAuthStore.getState().setSocket(state.socket)
    const unsubscribe = state.socket.subscribe<ProviderAuthSnapshot>(
      { type: "provider-auth" },
      (snapshot) => useProviderAuthStore.getState().setSnapshot(snapshot),
    )
    return () => {
      unsubscribe()
      useProviderAuthStore.getState().setSocket(null)
    }
  }, [state.socket])

  useEffect(() => {
    const unsubscribe = state.socket.subscribe<UiTurnContextV1>(
      { type: "ui-context" },
      (snapshot) => useUiContextStore.getState().setSnapshot(snapshot),
    )
    return () => {
      unsubscribe()
      useUiContextStore.getState().setSnapshot(null)
    }
  }, [state.socket])

  const chatSoundPreference = useChatSoundPreferencesStore((store) => store.chatSoundPreference)
  const chatSoundId = useChatSoundPreferencesStore((store) => store.chatSoundId)
  const showMobileOpenButton = location.pathname === "/"
  // Selected as the finished string rather than derived from the snapshot: the
  // title changes when a chat is renamed or a badge count moves, and this hook
  // should not re-render the layout for anything else the sidebar carries.
  const browserTitle = useSidebarStore((store) => getBrowserWindowTitle({
    appName: APP_NAME,
    sidebarData: store.data,
    activeProjectId: state.activeProjectId,
    activeChatId: state.activeChatId,
  }))
  const handleSidebarCreateChat = useCallback((projectId: string) => {
    void state.handleCreateChat(projectId)
  }, [state.handleCreateChat])
  const handleSidebarForkChat = useCallback((chat: Parameters<typeof state.handleForkChat>[0]) => {
    void state.handleForkChat(chat)
  }, [state.handleForkChat])
  const handleSidebarRenameChat = useCallback((chat: Parameters<typeof state.handleRenameChat>[0]) => {
    void state.handleRenameChat(chat)
  }, [state.handleRenameChat])
  const handleSidebarArchiveChat = useCallback((chat: Parameters<typeof state.handleArchiveChat>[0]) => {
    void state.handleArchiveChat(chat)
  }, [state.handleArchiveChat])
  const handleOpenArchivedChat = useCallback((chatId: string) => {
    void state.handleOpenArchivedChat(chatId)
  }, [state.handleOpenArchivedChat])
  const handleRestoreChat = useCallback((chatId: string) => {
    void state.handleRestoreChat(chatId)
  }, [state.handleRestoreChat])
  const handleSidebarDeleteChat = useCallback((chat: Parameters<typeof state.handleDeleteChat>[0]) => {
    void state.handleDeleteChat(chat)
  }, [state.handleDeleteChat])
  // Straight to the socket rather than through `useKannaState`: the result is
  // read by one hover card and belongs to no snapshot, so there's no app state
  // for it to land in.
  const handleLoadTouchedFiles = useCallback((chatId: string) => (
    state.socket.command<ChatTouchedFilesResult>({ type: "chat.touchedFiles", chatId })
  ), [state.socket])
  // Rendered inline rather than through a `useMemo`: `KannaSidebar` is memoized
  // and every prop below is now stable, so React skips it on its own. The memo
  // wrapper used to be defeated anyway — its dep list named the sidebar
  // snapshot, which moved on every streamed token.
  const sidebarElement = (
    <KannaSidebar
      activeChatId={state.activeChatId}
      connectionStatus={state.connectionStatus}
      ready={state.sidebarReady}
      open={state.sidebarOpen}
      collapsed={state.sidebarCollapsed}
      showMobileOpenButton={showMobileOpenButton}
      onOpen={state.openSidebar}
      onClose={state.closeSidebar}
      onCollapse={state.collapseSidebar}
      onExpand={state.expandSidebar}
      onCreateChat={handleSidebarCreateChat}
      onForkChat={handleSidebarForkChat}
      currentProjectId={state.activeProjectId}
      keybindings={state.keybindings}
      onRenameChat={handleSidebarRenameChat}
      onArchiveChat={handleSidebarArchiveChat}
      onOpenArchivedChat={handleOpenArchivedChat}
      onRestoreChat={handleRestoreChat}
      onDeleteChat={handleSidebarDeleteChat}
      onLoadTouchedFiles={handleLoadTouchedFiles}
    />
  )

  useLayoutEffect(() => {
    document.title = browserTitle
  }, [browserTitle, location.key])

  useEffect(() => {
    function handlePageShow() {
      document.title = browserTitle
    }

    function handlePageHide() {
      document.title = APP_NAME
    }

    window.addEventListener("pageshow", handlePageShow)
    window.addEventListener("pagehide", handlePageHide)
    return () => {
      window.removeEventListener("pageshow", handlePageShow)
      window.removeEventListener("pagehide", handlePageHide)
    }
  }, [browserTitle])

  // Driven by a store subscription rather than by a render: this compares
  // consecutive sidebar snapshots, and the layout is no longer re-rendered for
  // every one of them. The preferences are read through a ref so the
  // subscription is set up once and never torn down mid-turn (a resubscribe
  // would lose the previous snapshot and swallow the next chime).
  const soundSettingsRef = useRef({ appSettings: state.appSettings, chatSoundPreference, chatSoundId })
  useEffect(() => {
    soundSettingsRef.current = { appSettings: state.appSettings, chatSoundPreference, chatSoundId }
  })
  useEffect(() => {
    return useSidebarStore.subscribe((store, previousStore) => {
      // The first snapshot has nothing to compare against, and treating the
      // empty starting state as "previous" would chime once per unread chat on
      // every page load.
      if (!previousStore.ready) return
      const burstCount = getChatSoundBurstCount(previousStore.data, store.data)
      if (burstCount <= 0) return
      const { appSettings, chatSoundPreference: preference, chatSoundId: soundId } = soundSettingsRef.current
      if (!shouldPlayChatNotificationSound(appSettings, preference)) return
      void playChatNotificationSound(soundId, burstCount).catch(() => undefined)
    })
  }, [])

  return (
    <div className="flex h-[100dvh] min-h-[100dvh] overflow-hidden">
      {sidebarElement}
      <Outlet context={state} />
    </div>
  )
}

export function App() {
  const auth = useAppAuthState()

  if (auth.state.status === "checking") {
    return (
      <div className="flex min-h-[100dvh] items-center justify-center bg-background text-sm text-muted-foreground">
        Checking session…
      </div>
    )
  }

  return (
    <TradeAuthProvider value={auth.state.value}>
      <TooltipProvider>
        <AppDialogProvider>
          <Routes>
            <Route element={<KannaLayout />}>
              <Route path="/" element={<LocalProjectsPage />} />
              <Route path="/settings" element={<Navigate to="/settings/general" replace />} />
              <Route path="/settings/:sectionId" element={<SettingsPage />} />
              <Route path="/chat/:chatId" element={<ChatPage />} />
            </Route>
          </Routes>
        </AppDialogProvider>
      </TooltipProvider>
    </TradeAuthProvider>
  )
}
