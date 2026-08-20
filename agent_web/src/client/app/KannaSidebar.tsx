import { memo, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react"
import { ArrowLeft, Flower, Loader2, PanelLeft, X, Menu, Settings, SquarePen, UserRound } from "lucide-react"
import { useLocation, useNavigate } from "react-router-dom"
import { APP_NAME } from "../../shared/branding"
import { Button } from "../components/ui/button"
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "../components/ui/dialog"
import { buildChatJumpLocationState, type ChatJumpRole } from "../lib/chat-navigation"
import { formatSidebarAgeLabel } from "../lib/formatters"
import { getSidebarChatTimestamp } from "../lib/sidebarChats"
import { cn } from "../lib/utils"
import { FocusModePill } from "../components/chat-ui/sidebar/FocusModePill"
import { SidebarChatHoverCard } from "../components/chat-ui/sidebar/ChatHoverCard"
import { ThreadSections } from "../components/chat-ui/sidebar/ThreadSections"
import { getResolvedKeybindings } from "../lib/keybindings"
import { useIsStandalone } from "../hooks/useIsStandalone"
import type { ChatTouchedFilesResult, KeybindingsSnapshot, SidebarChatRow } from "../../shared/types"
import type { SocketStatus } from "./socket"
import {
  getSidebarJumpTargetIndex,
  getVisibleSidebarChats,
  isSidebarModifierShortcut,
} from "./sidebarNumberJump"
import { SIDEBAR_WIDTH_STORAGE_KEY } from "../lib/storageKeys"
import { useSidebarData } from "../stores/sidebarStore"
import {
  focusSidebarData,
  isFocusModeEnabled,
  resolveFocusedProjectGroup,
  setFocusMode,
  toggleFocusMode,
  useFocusModeEnabled,
} from "../stores/focusModeStore"
import { formatActionShortcut } from "../lib/keybindings"
import { useStableSidebarThreads } from "./useStableSidebarThreads"
import { useTradeAuth } from "./TradeAuthContext"

export const DEFAULT_SIDEBAR_WIDTH = 275
export const MIN_SIDEBAR_WIDTH = 220
export const MAX_SIDEBAR_WIDTH = 520

export function clampSidebarWidth(width: number) {
  if (!Number.isFinite(width)) return DEFAULT_SIDEBAR_WIDTH
  return Math.min(MAX_SIDEBAR_WIDTH, Math.max(MIN_SIDEBAR_WIDTH, Math.round(width)))
}

function readStoredSidebarWidth() {
  if (typeof window === "undefined") return DEFAULT_SIDEBAR_WIDTH
  const stored = window.localStorage.getItem(SIDEBAR_WIDTH_STORAGE_KEY)
  return stored ? clampSidebarWidth(Number(stored)) : DEFAULT_SIDEBAR_WIDTH
}

function persistSidebarWidth(width: number) {
  if (typeof window === "undefined") return
  window.localStorage.setItem(SIDEBAR_WIDTH_STORAGE_KEY, String(clampSidebarWidth(width)))
}

interface KannaSidebarProps {
  activeChatId: string | null
  connectionStatus: SocketStatus
  ready: boolean
  open: boolean
  collapsed: boolean
  showMobileOpenButton: boolean
  onOpen: () => void
  onClose: () => void
  onCollapse: () => void
  onExpand: () => void
  onCreateChat: (projectId: string) => void
  onForkChat: (chat: SidebarChatRow) => void
  currentProjectId: string | null
  keybindings: KeybindingsSnapshot | null
  onRenameChat: (chat: SidebarChatRow) => void
  onArchiveChat: (chat: SidebarChatRow) => void
  onOpenArchivedChat: (chatId: string) => void
  onRestoreChat: (chatId: string) => void
  onDeleteChat: (chat: SidebarChatRow) => void
  /** Fetches what a chat changed, for the hover card's file list. */
  onLoadTouchedFiles?: (chatId: string) => Promise<ChatTouchedFilesResult>
}

function KannaSidebarImpl({
  activeChatId,
  connectionStatus,
  ready,
  open,
  collapsed,
  showMobileOpenButton,
  onOpen,
  onClose,
  onCollapse,
  onExpand,
  onCreateChat,
  onForkChat,
  currentProjectId,
  keybindings,
  onRenameChat,
  onArchiveChat,
  onOpenArchivedChat,
  onRestoreChat,
  onDeleteChat,
  onLoadTouchedFiles,
}: KannaSidebarProps) {
  // The one place that wants the whole snapshot. Selected here rather than
  // passed down so a sidebar push re-renders this component and nothing above
  // it — the chat page and the transcript are untouched by it.
  const allProjectsData = useSidebarData()
  const focusModeEnabled = useFocusModeEnabled()
  // Focus mode narrows the sidebar to one project. Resolved and applied once,
  // here, so the Chats view, the Projects view and the number-jump indices all
  // agree on what is on screen. The focused project is whichever one is current,
  // so opening a chat elsewhere re-points focus rather than leaving it.
  const focusedProjectGroup = useMemo(
    () => resolveFocusedProjectGroup(allProjectsData.projectGroups, focusModeEnabled, currentProjectId),
    [allProjectsData.projectGroups, currentProjectId, focusModeEnabled]
  )
  const data = useMemo(
    () => focusSidebarData(allProjectsData, focusedProjectGroup),
    [allProjectsData, focusedProjectGroup]
  )
  const location = useLocation()
  const navigate = useNavigate()
  const isStandalone = useIsStandalone()
  const { account, returnUrl } = useTradeAuth()
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const resizeStartRef = useRef<{ pointerX: number; width: number } | null>(null)
  const [nowMs, setNowMs] = useState(() => Date.now())

  const [sidebarWidth, setSidebarWidth] = useState(readStoredSidebarWidth)
  const [isResizingSidebar, setIsResizingSidebar] = useState(false)
  const [archivedProjectId, setArchivedProjectId] = useState<string | null>(null)
  const resolvedKeybindings = useMemo(() => getResolvedKeybindings(keybindings), [keybindings])
  const visibleChats = useMemo(
    () => getVisibleSidebarChats(data.projectGroups, new Set(), new Set()),
    [data.projectGroups]
  )
  const visibleChatsRef = useRef(visibleChats)
  // The Projects tab renders the same `ThreadRow` as the Chats tab, which wants
  // a SidebarThread. Flattened once here and shared with the Chats tab so
  // projectId/projectTitle/archived stay correct in one place — and so both tabs
  // hand their rows the same identity-stable thread objects.
  const threads = useStableSidebarThreads(data)

  const activeVisibleCount = visibleChats.length
  const archivedProject = useMemo(
    () => allProjectsData.projectGroups.find((group) => group.groupKey === archivedProjectId) ?? null,
    [archivedProjectId, allProjectsData.projectGroups]
  )

  useEffect(() => {
    visibleChatsRef.current = visibleChats
  }, [visibleChats])

  const selectChat = useCallback((chatId: string) => {
    navigate(`/chat/${chatId}`)
    onClose()
  }, [navigate, onClose])

  // Same navigation with a landing spot attached. Always navigates, even to the
  // chat already open: the pathname wouldn't change, but the request id does,
  // which is what moves the viewport a second time.
  const selectChatMessage = useCallback((chatId: string, role: ChatJumpRole) => {
    navigate(`/chat/${chatId}`, { state: buildChatJumpLocationState(role) })
    onClose()
  }, [navigate, onClose])

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      setNowMs(Date.now())
    }, 30_000)

    return () => window.clearInterval(intervalId)
  }, [])

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (isSidebarModifierShortcut(resolvedKeybindings, "createChatInCurrentProject", event)) {
        if (!currentProjectId) {
          return
        }

        event.preventDefault()
        onCreateChat(currentProjectId)
        return
      }

      if (isSidebarModifierShortcut(resolvedKeybindings, "toggleFocusMode", event)) {
        // Turning focus on with no current project would hide every chat, so
        // the shortcut only turns it off in that state.
        if (!currentProjectId && !isFocusModeEnabled()) {
          return
        }

        event.preventDefault()
        toggleFocusMode()
        return
      }

      const targetIndex = getSidebarJumpTargetIndex(resolvedKeybindings, event)
      if (targetIndex === null) {
        return
      }

      const targetChat = visibleChatsRef.current[targetIndex - 1]?.chat
      if (!targetChat) {
        return
      }

      event.preventDefault()
      navigate(`/chat/${targetChat.chatId}`)
      onClose()
    }

    window.addEventListener("keydown", handleKeyDown)

    return () => {
      window.removeEventListener("keydown", handleKeyDown)
    }
  }, [currentProjectId, navigate, onClose, onCreateChat, resolvedKeybindings])

  useEffect(() => {
    if (!activeChatId || !scrollContainerRef.current) return

    requestAnimationFrame(() => {
      const container = scrollContainerRef.current
      const activeElement = container?.querySelector(`[data-chat-id="${activeChatId}"]`) as HTMLElement | null
      if (!activeElement || !container) return

      const elementRect = activeElement.getBoundingClientRect()
      const containerRect = container.getBoundingClientRect()

      if (elementRect.top < containerRect.top + 38) {
        const relativeTop = elementRect.top - containerRect.top + container.scrollTop
        container.scrollTo({ top: relativeTop - 38, behavior: "smooth" })
      } else if (elementRect.bottom > containerRect.bottom) {
        const elementCenter = elementRect.top + elementRect.height / 2 - containerRect.top + container.scrollTop
        const containerCenter = container.clientHeight / 2
        container.scrollTo({ top: elementCenter - containerCenter, behavior: "smooth" })
      }
    })
  }, [activeChatId])

  useEffect(() => {
    if (!isResizingSidebar) return

    const previousCursor = document.body.style.cursor
    const previousUserSelect = document.body.style.userSelect
    document.body.style.cursor = "col-resize"
    document.body.style.userSelect = "none"

    function handlePointerMove(event: PointerEvent) {
      const resizeStart = resizeStartRef.current
      if (!resizeStart) return
      setSidebarWidth(clampSidebarWidth(resizeStart.width + event.clientX - resizeStart.pointerX))
    }

    function handlePointerUp() {
      setIsResizingSidebar(false)
      resizeStartRef.current = null
      setSidebarWidth((current) => {
        const next = clampSidebarWidth(current)
        persistSidebarWidth(next)
        return next
      })
    }

    window.addEventListener("pointermove", handlePointerMove)
    window.addEventListener("pointerup", handlePointerUp, { once: true })

    return () => {
      window.removeEventListener("pointermove", handlePointerMove)
      window.removeEventListener("pointerup", handlePointerUp)
      document.body.style.cursor = previousCursor
      document.body.style.userSelect = previousUserSelect
    }
  }, [isResizingSidebar])

  const hasVisibleChats = activeVisibleCount > 0
  const isLocalProjectsActive = location.pathname === "/"
  const newSidebarEnabled = true
  const isSettingsActive = location.pathname.startsWith("/settings")
  const isUtilityPageActive = isLocalProjectsActive || isSettingsActive
  const isConnecting = connectionStatus === "connecting" || !ready
  const statusLabel = isConnecting ? "Connecting" : connectionStatus === "connected" ? "Connected" : "Disconnected"
  const statusDotClass = connectionStatus === "connected" ? "bg-emerald-500" : "bg-amber-500"
  const fixedProjectId = allProjectsData.projectGroups[0]?.groupKey ?? currentProjectId
  const createFixedSession = () => {
    if (!fixedProjectId) return
    onCreateChat(fixedProjectId)
    onClose()
  }

  return (
    <>
      {!open && showMobileOpenButton && (
        <Button
          variant="ghost"
          size="icon"
          className="fixed top-3 left-3 z-50 md:hidden"
          onClick={onOpen}
        >
          <Menu className="h-5 w-5" />
        </Button>
      )}

      {collapsed && isUtilityPageActive && (
        <div className="hidden md:flex fixed left-0 top-0 h-full z-40 items-start pt-4 pl-5 border-l border-border/0">
          <div className="flex items-center gap-1">
            <Flower className="size-6 text-logo" />
            <Button
              variant="ghost"
              size="icon"
              onClick={onExpand}
              title="Expand sidebar"
            >
              <PanelLeft className="h-5 w-5" />
            </Button>
          </div>
        </div>
      )}

      <div
        data-sidebar="open"
        className={cn(
          "fixed inset-0 z-50 bg-background dark:bg-card flex flex-col h-[100dvh] select-none",
          "md:relative md:inset-auto md:w-[var(--sidebar-width)] md:mr-0 md:h-[calc(100dvh-16px)] md:my-2 md:ml-2 md:border md:border-border md:rounded-2xl",
          open ? "flex" : "hidden md:flex",
          collapsed && "md:hidden"
        )}
        style={{ "--sidebar-width": `${sidebarWidth}px` } as CSSProperties}
      >
        <div className="px-2.5 h-[64px] md:h-auto md:py-1 border-b grid grid-cols-[84px_minmax(0,1fr)_84px] items-center md:pl-3 md:pr-1 md:flex md:justify-between">
          <div className="md:hidden grid grid-cols-2">
            <Button
              variant="ghost"
              size="icon"
              className="w-[42px] rounded-lg hover:!border-border/0 !border-0"
              onClick={onClose}
              title="Close sidebar"
            >
              <X className="h-5 w-5" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className={cn(
                "w-[42px] rounded-lg hover:!border-border/0 !border-0 -translate-x-[1px]",
                isSettingsActive ? "text-foreground" : "text-muted-foreground"
              )}
              onClick={() => {
                navigate("/settings/general")
                onClose()
              }}
              title="Settings"
            >
              <Settings className="h-5 w-5" />
            </Button>
          </div>
          <div className="flex items-center justify-self-center gap-2 md:justify-self-auto">
            <button
              type="button"
              onClick={onCollapse}
              title="Collapse sidebar"
              className="hidden md:flex group/sidebar-collapse relative items-center justify-center h-5 w-5 sm:h-6 sm:w-6"
            >
              <Flower className="absolute inset-0.5 h-4 w-4 sm:h-5 sm:w-5 text-logo transition-all duration-200 ease-out opacity-100 scale-100 group-hover/sidebar-collapse:opacity-0 group-hover/sidebar-collapse:scale-0" />
              <PanelLeft className="absolute inset-0 h-4 w-4 sm:h-6 sm:w-6 text-slate-500 dark:text-slate-400 transition-all duration-200 ease-out opacity-0 scale-0 group-hover/sidebar-collapse:opacity-100 group-hover/sidebar-collapse:scale-80 hover:opacity-50" />
            </button>
            <Flower className="h-5 w-5 sm:h-6 sm:w-6 text-logo md:hidden" />
            <span className="font-logo text-base uppercase sm:text-md text-slate-600 dark:text-slate-100">{APP_NAME}</span>
          </div>
          <div className="flex items-center justify-self-end md:justify-self-auto">
            <Button
              variant="ghost"
              size="icon"
              onClick={createFixedSession}
              disabled={!fixedProjectId}
              className="size-10 rounded-lg hover:!border-border/0 md:hidden"
              title="New Agent Session"
            >
              <SquarePen className="h-5 w-5" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={createFixedSession}
              disabled={!fixedProjectId}
              className="hidden md:inline-flex h-10 w-auto rounded-lg px-1.5 pl-2 hover:!border-border/0"
              title="New Agent Session"
            >
              <SquarePen className="size-4" />
            </Button>
          </div>
        </div>

        <div
          ref={scrollContainerRef}
          className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden scrollbar-hide"
          style={{
            WebkitOverflowScrolling: "touch",
            touchAction: "pan-y",
          }}
        >
          <div className="p-[7px]">
            {/* The focus row joins this block rather than sitting below it, so
                it inherits the same width, padding and row rhythm as the
                buttons — which is the whole of its treatment. It leads the
                block, and the block leads both views, so focus mode is the
                first thing the sidebar says either way. */}
            {newSidebarEnabled || focusedProjectGroup ? (
              <div className="flex flex-col gap-[1px] pb-2">
                {focusedProjectGroup ? (
                  <FocusModePill
                    projectTitle={focusedProjectGroup.title}
                    shortcutHint={formatActionShortcut(resolvedKeybindings, "toggleFocusMode") ?? undefined}
                    onExit={() => setFocusMode(false)}
                  />
                ) : null}
                {newSidebarEnabled ? (
                  <>
                    <button
                      type="button"
                      onClick={createFixedSession}
                      disabled={!fixedProjectId}
                      className="flex w-full items-center gap-2 rounded-lg border border-border/0 px-2 py-1.5 max-md:py-2 text-sm max-md:text-base text-muted-foreground transition-colors hover:border-border hover:bg-muted"
                    >
                      <SquarePen className="h-4 w-4 shrink-0" />
                      <span>New Agent Session</span>
                    </button>
                  </>
                ) : null}
              </div>
            ) : null}

            {!hasVisibleChats && isConnecting ? (
              <div className="space-y-5 px-1 pt-3">
                {[0, 1, 2].map((section) => (
                  <div key={section} className="space-y-2 animate-pulse">
                    <div className="h-4 w-28 rounded bg-muted" />
                    <div className="space-y-1">
                      {[0, 1, 2].map((row) => (
                        <div key={row} className="flex items-center gap-2 rounded-md px-3 py-2">
                          <div className="h-3.5 w-3.5 rounded-full bg-muted" />
                          <div
                            className={cn(
                              "h-3.5 rounded bg-muted",
                              row === 0 ? "w-32" : row === 1 ? "w-40" : "w-28"
                            )}
                          />
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            ) : null}

            {!isConnecting && (
              (!hasVisibleChats && data.projectGroups.length === 0)
              // A focused project with no chats: say so, rather than leave the
              // list blank under a pill naming the project.
              || focusedProjectGroup?.chats.length === 0
            ) ? (
              <p className="text-sm text-slate-400 p-2 mt-6 text-center">No conversations yet</p>
            ) : null}

            {newSidebarEnabled ? (
              <ThreadSections
                threads={threads}
                activeChatId={activeChatId}
                nowMs={nowMs}
                onSelectChat={selectChat}
                onOpenArchivedChat={onOpenArchivedChat}
                onRestoreChat={onRestoreChat}
                onCreateChat={onCreateChat}
                onRenameChat={onRenameChat}
                onForkChat={onForkChat}
                onArchiveChat={onArchiveChat}
                onDeleteChat={onDeleteChat}
              />
            ) : null}

          </div>
        </div>

        {/* One card for every row above, anchored to whichever is under the
            pointer — see `SidebarChatHoverCard`. It renders a portal and no
            layout, so it sits here rather than inside the scrolling list. */}
        <SidebarChatHoverCard
          containerRef={scrollContainerRef}
          threads={threads}
          onSelectChat={selectChat}
          onSelectMessage={selectChatMessage}
          onOpenArchivedChat={onOpenArchivedChat}
          onLoadTouchedFiles={onLoadTouchedFiles}
        />

        <div className="border-t border-border px-3 py-2">
          <div className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground" title={`${account.email} · ${account.role}`}>
            <UserRound className="h-4 w-4 shrink-0" />
            <span className="min-w-0 flex-1 truncate">{account.email}</span>
            <span className="rounded-full border border-border px-1.5 py-0.5 text-[10px] uppercase tracking-wide">
              {account.role}
            </span>
          </div>
        </div>
        <div className="hidden md:block border-t border-border p-2">
          <button
            type="button"
            onClick={() => {
              navigate("/settings/general")
              onClose()
            }}
            className={cn(
              "w-full rounded-xl rounded-t-md border px-3 py-2 text-left transition-colors",
              isSettingsActive
                ? "bg-muted border-border"
                : "border-border/0 hover:bg-muted hover:border-border active:bg-muted/80"
            )}
          >
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <Settings className="h-4 w-4 text-muted-foreground" />
                <span className="text-sm">Settings</span>
              </div>
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <span>{statusLabel}</span>
                {isConnecting ? (
                  <Loader2 className="h-2 w-2 animate-spin" />
                ) : (
                  <span className={cn("h-2 w-2 rounded-full", statusDotClass)} />
                )}
              </div>
            </div>
          </button>
        </div>
        <div className={cn("border-t border-border p-2", isStandalone && "pb-[55px]")}>
          <a
            href={returnUrl}
            className="flex w-full items-center gap-2 rounded-xl border border-border/0 px-3 py-2 text-sm transition-colors hover:border-border hover:bg-muted active:bg-muted/80"
            title="Back to TradeEngine"
          >
            <ArrowLeft className="h-4 w-4 shrink-0 text-muted-foreground" />
            <span>Back to TradeEngine</span>
          </a>
        </div>

        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize sidebar"
          tabIndex={0}
          title="Resize sidebar"
          className={cn(
            "hidden md:block absolute -right-1 top-3 bottom-3 z-20 w-2 cursor-col-resize rounded-full",
            "focus-visible:outline-none"
          )}
          onPointerDown={(event) => {
            event.preventDefault()
            resizeStartRef.current = {
              pointerX: event.clientX,
              width: sidebarWidth,
            }
            setIsResizingSidebar(true)
          }}
          onDoubleClick={() => {
            setSidebarWidth(DEFAULT_SIDEBAR_WIDTH)
            persistSidebarWidth(DEFAULT_SIDEBAR_WIDTH)
          }}
          onKeyDown={(event) => {
            let nextWidth: number | null = null
            if (event.key === "ArrowLeft") nextWidth = sidebarWidth - 16
            else if (event.key === "ArrowRight") nextWidth = sidebarWidth + 16
            else if (event.key === "Home") nextWidth = MIN_SIDEBAR_WIDTH
            else if (event.key === "End") nextWidth = MAX_SIDEBAR_WIDTH
            else if (event.key === "Enter") nextWidth = DEFAULT_SIDEBAR_WIDTH
            if (nextWidth === null) return
            event.preventDefault()
            const clampedWidth = clampSidebarWidth(nextWidth)
            setSidebarWidth(clampedWidth)
            persistSidebarWidth(clampedWidth)
          }}
        />
      </div>

      <Dialog
        open={Boolean(archivedProject)}
        onOpenChange={(dialogOpen) => {
          if (!dialogOpen) setArchivedProjectId(null)
        }}
      >
        <DialogContent size="md">
          <DialogHeader>
            <DialogTitle>Archived Chats</DialogTitle>
            <DialogDescription>
              {archivedProject?.title ?? ""}
            </DialogDescription>
          </DialogHeader>
          <DialogBody className="space-y-1">
            {archivedProject?.archivedChats?.length ? (
              archivedProject.archivedChats.map((chat) => (
                <button
                  key={chat.chatId}
                  type="button"
                  className="flex w-full items-center justify-between gap-3 rounded-lg border border-border/0 px-3 py-2 text-left transition-colors hover:border-border hover:bg-muted"
                  onClick={() => {
                    onOpenArchivedChat(chat.chatId)
                    setArchivedProjectId(null)
                    onClose()
                  }}
                >
                  <span className="min-w-0 truncate text-sm">{chat.title}</span>
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {formatSidebarAgeLabel(getSidebarChatTimestamp(chat), nowMs)}
                  </span>
                </button>
              ))
            ) : (
              <p className="px-1 py-3 text-sm text-muted-foreground">No archived chats</p>
            )}
          </DialogBody>
        </DialogContent>
      </Dialog>

      {open ? <div className="fixed inset-0 bg-black/40 z-40 md:hidden" onClick={onClose} /> : null}
    </>
  )
}

export const KannaSidebar = memo(KannaSidebarImpl)
