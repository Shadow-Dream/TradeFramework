import { memo } from "react"
import { ArrowLeft, Flower, GitBranch, Menu, MoreHorizontal, PanelLeft, PanelRight, Search, SquarePen, Terminal } from "lucide-react"
import { Button } from "../ui/button"
import { CardHeader } from "../ui/card"
import { HotkeyTooltip, HotkeyTooltipContent, HotkeyTooltipTrigger } from "../ui/tooltip"
import { cn } from "../../lib/utils"
import { OPEN_COMMAND_PALETTE_EVENT } from "../command-palette/CommandPalette"
import { ContextMenu, ContextMenuContent, ContextMenuItem, ContextMenuTrigger } from "../ui/context-menu"
import { useTradeAuth } from "../../app/TradeAuthContext"

function NavbarOverflowMenu({
  showOnDesktop,
  onToggleEmbeddedTerminal,
}: {
  showOnDesktop: boolean
  onToggleEmbeddedTerminal?: () => void
}) {
  if (!onToggleEmbeddedTerminal) return null

  return (
    <ContextMenu>
      <ContextMenuTrigger asChild>
        <Button
          variant="ghost"
          size="none"
          onClick={(event) => event.currentTarget.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, clientX: event.clientX, clientY: event.clientY }))}
          title="More actions"
          className={cn(
            "border border-border/0 hover:!border-border/0 px-1.5 h-9 max-md:h-[45px] max-md:w-[42px] max-md:px-0 hover:!bg-transparent",
            showOnDesktop ? "flex" : "flex md:hidden"
          )}
        >
          <MoreHorizontal strokeWidth={2} className="h-4.5 max-md:h-5.5" />
        </Button>
      </ContextMenuTrigger>
      <ContextMenuContent>
        {onToggleEmbeddedTerminal ? (
          <ContextMenuItem
            onSelect={(event) => {
              event.preventDefault()
              onToggleEmbeddedTerminal()
            }}
          >
            <Terminal strokeWidth={2} className="h-3.5 w-3.5" />
            <span className="text-xs font-medium">Toggle Terminal</span>
          </ContextMenuItem>
        ) : null}
      </ContextMenuContent>
    </ContextMenu>
  )
}

interface Props {
  sidebarCollapsed: boolean
  onOpenSidebar: () => void
  onExpandSidebar: () => void
  onNewChat: () => void
  projectId?: string
  embeddedTerminalVisible?: boolean
  onToggleEmbeddedTerminal?: () => void
  rightPanel?: "hidden" | "git"
  onToggleGitPanel?: () => void
  terminalShortcut?: string[]
  rightSidebarShortcut?: string[]
  branchName?: string
  hasGitRepo?: boolean
  gitStatus?: "unknown" | "ready" | "no_repo"
}

/**
 * Memoized: it sits above the transcript, so it renders on every pushed chat
 * snapshot — many times a second while a turn runs — for a bar that only
 * changes when the branch, the panel or the sidebar does.
 */
function ChatNavbarImpl({
  sidebarCollapsed,
  onOpenSidebar,
  onExpandSidebar,
  onNewChat,
  projectId,
  embeddedTerminalVisible = false,
  onToggleEmbeddedTerminal,
  rightPanel = "hidden",
  onToggleGitPanel,
  terminalShortcut,
  rightSidebarShortcut,
  branchName,
  hasGitRepo = true,
  gitStatus = "unknown",
}: Props) {
  const { returnUrl } = useTradeAuth()
  // New Sidebar mode surfaces search in the sidebar, so the chat navbar only
  // keeps its search button on mobile (where the sidebar is hidden).
  const newSidebar = true
  const branchLabel = !hasGitRepo || gitStatus === "unknown"
      ? null
      : (branchName ?? "Detached HEAD")
  const rightPanelVisible = rightPanel !== "hidden"
  const handleCloseRightPanel = rightPanel === "git" ? onToggleGitPanel : undefined

  return (
    <CardHeader
      className={cn(
        "absolute top-0 left-0 right-0 z-10 md:pt-[9px] max-md:px-2 md:pl-1 md:pr-2 border-border/0 flex items-center justify-center"
      )}
    >
      {/* Both washes stop at the transcript's scrollbar gutter instead of
          running to the card edge, so the scrollbar isn't dimmed by them — a
          native scrollbar paints under any later positioned sibling and no
          z-index can lift it. The header keeps its full width so the controls
          in it stay where they were; only the backgrounds move inward, and
          they cover nothing but bare background out there anyway. */}
      <div className="absolute inset-y-0 left-0 right-[var(--transcript-scrollbar-w,0px)] z-0 bg-gradient-to-b from-background lg:from-background/0 pointer-events-none"></div>
      <div className="absolute top-0 left-0 right-[var(--transcript-scrollbar-w,0px)] z-0 h-[100px] bg-gradient-to-b from-background via-background/50 to-background/10 md:to-background/0 pointer-events-none block"></div>
      <div className="relative flex items-center gap-2 w-full">
        <div className={`md:h-[30px] flex items-center gap-0 flex-shrink-0 border border-border/0 rounded-[9px] ${sidebarCollapsed ? 'px-1.5  border-border' : ''} md:px-[2px]`}>
          <Button
            variant="ghost"
            size="icon"
            className="md:hidden h-[45px] w-[42px] hover:!border-border/0 hover:!bg-transparent"
            onClick={onOpenSidebar}
          >
            <Menu className="size-5" />
          </Button>
          {sidebarCollapsed && (
            <>
              <div className="hidden md:flex items-center justify-center w-[36px] h-[36px]">
                <Flower className="h-4 w-4 sm:h-5 sm:w-5 text-logo ml-1 hidden md:block" />
              </div>
              <Button
                variant="ghost"
                size="icon"
                className="hidden md:flex  hover:!border-border/0 hover:!bg-transparent"
                onClick={onExpandSidebar}
                title="Expand sidebar"
              >
                <PanelLeft className="size-4" />
              </Button>
            </>
          )}
          <Button
            variant="ghost"
            size="icon"
            className={cn(
              "max-md:h-[45px] max-md:w-[42px] hover:!border-border/0 hover:!bg-transparent",
              newSidebar && "md:hidden"
            )}
            onClick={() => window.dispatchEvent(new CustomEvent(OPEN_COMMAND_PALETTE_EVENT))}
            title="Search"
          >
            <Search className="size-4 max-md:size-5" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="max-md:h-[45px] max-md:w-[42px] hover:!border-border/0 hover:!bg-transparent"
            onClick={onNewChat}
            title="Compose"
          >
            <SquarePen className="size-4 max-md:size-5" />
          </Button>
        </div>

        <div className="flex-1 min-w-0" />

        <a
          href={returnUrl}
          title="Back to TradeEngine"
          aria-label="Back to TradeEngine"
          className={cn(
            "h-9 shrink-0 items-center gap-1.5 rounded-lg px-2 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground max-md:h-[45px] max-md:w-[42px] max-md:justify-center max-md:px-0",
            sidebarCollapsed ? "flex" : "flex md:hidden"
          )}
        >
          <ArrowLeft className="h-4 w-4 max-md:h-5 max-md:w-5" />
          <span className="hidden lg:inline">TradeEngine</span>
        </a>

        {projectId && (onToggleEmbeddedTerminal || onToggleGitPanel) ? (
          <div className="flex items-center gap-2 flex-shrink-0">
            {(onToggleEmbeddedTerminal || onToggleGitPanel) ? (
              <div className="flex items-center  rounded-[9px] h-[30px]">
                <NavbarOverflowMenu
                  showOnDesktop={rightPanelVisible}
                  onToggleEmbeddedTerminal={onToggleEmbeddedTerminal}
                />
                {onToggleEmbeddedTerminal ? (
                <HotkeyTooltip>
                  <HotkeyTooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="none"
                      onClick={onToggleEmbeddedTerminal}
                      className={cn(
                        rightPanelVisible ? "hidden" : "hidden md:flex",
                        "border border-border/0 hover:!border-border/0 px-1.5 h-9 hover:!bg-transparent",
                        embeddedTerminalVisible && "text-foreground"
                      )}
                    >
                      <Terminal strokeWidth={2} className="h-4" />
                    </Button>
                  </HotkeyTooltipTrigger>
                  <HotkeyTooltipContent side="bottom" shortcut={terminalShortcut} />
                </HotkeyTooltip>
              ) : null}
                {onToggleGitPanel ? (
                  <HotkeyTooltip>
                    <HotkeyTooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="none"
                        onClick={onToggleGitPanel}
                        className={cn(
                          "border flex flex-row items-center gap-1.5 h-9 max-md:h-[45px] max-md:w-[42px] max-md:px-0 border-border/0 hover:!border-border/0 hover:!bg-transparent",
                          rightPanelVisible ? "w-[38px] justify-center px-0" : "pl-1.5 pr-2"
                        )}
                      >
                        <GitBranch strokeWidth={2.25} className="h-4 max-md:h-5 max-md:w-5" />
                        {branchLabel && !rightPanelVisible ? <div className="font-[13px] max-w-[140px] truncate hidden md:block">{branchLabel}</div> : null}
                      </Button>
                    </HotkeyTooltipTrigger>
                    <HotkeyTooltipContent side="bottom" shortcut={rightSidebarShortcut} />
                  </HotkeyTooltip>
                ) : null}
                {rightPanelVisible && handleCloseRightPanel ? (
                  <Button
                    variant="ghost"
                    size="none"
                    onClick={handleCloseRightPanel}
                    title="Collapse sidebar"
                    aria-label="Collapse sidebar"
                    className="border border-border/0 hover:!border-border/0 px-1.5 h-9 max-md:h-[45px] max-md:w-[42px] max-md:px-0 hover:!bg-transparent text-foreground"
                  >
                    <PanelRight strokeWidth={2.25} className="h-4 max-md:h-5 max-md:w-5" />
                  </Button>
                ) : null}
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </CardHeader>
  )
}

export const ChatNavbar = memo(ChatNavbarImpl)
