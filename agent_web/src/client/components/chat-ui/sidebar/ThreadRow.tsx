import { memo, type ReactNode } from "react"
import { Archive, RotateCcw, Split } from "lucide-react"
import { getThreadDetailLabel, type ThreadDetailScope } from "../../../lib/thread-detail-label"
import type { SidebarThread } from "../../../lib/thread-sections"
import { cn, normalizeChatId } from "../../../lib/utils"
import { Button } from "../../ui/button"
import { useChatHasDraft, useChatInputStore } from "../../../stores/chatInputStore"
import { ThreadRowContent } from "../ThreadRowContent"
import { ChatRowMenu } from "./Menus"

interface ThreadRowProps {
  thread: SidebarThread
  isActive: boolean
  /** Archived rows swap Fork/Archive for Restore and get the archived menu. */
  archived?: boolean
  /**
   * Which question the trailing slot answers — see `getThreadDetailLabel`. The
   * row resolves it itself rather than taking a finished node, so its props stay
   * comparable and `memo` can skip a row whose chat did not move. A node prop
   * would be a new element on every render and would defeat that outright.
   */
  detailScope: ThreadDetailScope
  /** Anchor for the age label; the sidebar advances it on a slow interval. */
  nowMs: number
  /** Transient chrome that replaces the slot — the number-jump keycap. */
  detailLabelOverride?: ReactNode
  /**
   * Fade idle/read titles (see `ThreadRowContent`). The Projects tab keeps it —
   * a project's chat list is a long backlog where read rows should recede. The
   * Chats tab turns it off: its rows are already filtered into
   * In Progress / Review / recent-day sections, so the *section* carries the
   * emphasis and dimming inside one would just fight it.
   */
  dimIdleTitles?: boolean
  onSelect: (chatId: string) => void
  onCreateChat: (projectId: string) => void
  onRenameChat: (chat: SidebarThread["row"]) => void
  onForkChat: (chat: SidebarThread["row"]) => void
  onArchiveChat: (chat: SidebarThread["row"]) => void
  onRestoreChat: (chatId: string) => void
  onDeleteChat: (chat: SidebarThread["row"]) => void
}

/**
 * The canonical sidebar chat row: right-click menu, click target, status glyph /
 * harness icon, title, and hover-revealed Fork/Archive. Used by both sidebar
 * tabs; each passes the `detailScope` its list calls for — the Chats tab spans
 * projects, the Projects tab is already inside one.
 *
 * A div rather than a button so the hover-action Buttons can nest inside it.
 *
 * The row carries no hover card of its own: the sidebar keeps one card for the
 * whole list and finds the row under the pointer by `data-chat-id`. See
 * `SidebarChatHoverCard`.
 *
 * Memoized, and worth keeping that way. There is one of these per chat, each
 * carrying a context menu, and the sidebar snapshot is pushed throughout every
 * turn. Without the memo a single chat streaming a reply re-rendered every row
 * in the app.
 */
function ThreadRowImpl({
  thread,
  isActive,
  archived = false,
  detailScope,
  nowMs,
  detailLabelOverride,
  dimIdleTitles = true,
  onSelect,
  onCreateChat,
  onRenameChat,
  onForkChat,
  onArchiveChat,
  onRestoreChat,
  onDeleteChat,
}: ThreadRowProps) {
  // Whether there *is* a draft, not what it says. The row shows a pencil and the
  // menu offers "Clear Draft"; neither needs the text, and subscribing to it
  // re-rendered this row on every keystroke in the composer. The card reads the
  // text itself, from inside its own open subtree.
  const hasDraft = useChatHasDraft(thread.row.chatId)
  const clearDraft = useChatInputStore((state) => state.clearDraft)
  const detailLabel = detailLabelOverride ?? getThreadDetailLabel(thread, detailScope, nowMs)
  const hoverActions = archived ? (
    <Button
      variant="ghost"
      size="icon"
      className="h-6 w-6 shrink-0 cursor-pointer rounded-sm hover:!bg-transparent !border-0"
      onClick={(event) => {
        event.stopPropagation()
        onRestoreChat(thread.row.chatId)
      }}
      title="Restore chat"
    >
      <RotateCcw className="size-3.5" />
    </Button>
  ) : (
    <>
      {thread.row.canFork ? (
        <Button
          variant="ghost"
          size="icon"
          className="h-6 w-6 shrink-0 cursor-pointer rounded-sm hover:!bg-transparent !border-0"
          onClick={(event) => {
            event.stopPropagation()
            onForkChat(thread.row)
          }}
          title="Fork chat"
        >
          <Split className="size-3.5" />
        </Button>
      ) : null}
      <Button
        variant="ghost"
        size="icon"
        className="h-6 w-6 shrink-0 cursor-pointer rounded-sm hover:!bg-transparent !border-0"
        onClick={(event) => {
          event.stopPropagation()
          onArchiveChat(thread.row)
        }}
        title="Archive chat"
      >
        <Archive className="size-3.5" />
      </Button>
    </>
  )

  return (
    <ChatRowMenu
      canFork={thread.row.canFork}
      archived={archived}
      onNewChat={() => onCreateChat(thread.projectId)}
      onRestore={archived ? () => onRestoreChat(thread.row.chatId) : undefined}
      onRename={() => onRenameChat(thread.row)}
      onFork={() => onForkChat(thread.row)}
      onClearDraft={hasDraft ? () => clearDraft(thread.row.chatId) : undefined}
      onArchive={archived ? () => {} : () => onArchiveChat(thread.row)}
      onDelete={() => onDeleteChat(thread.row)}
    >
      <div
        // Two readers: the sidebar's scroll-to-active querySelector, and the
        // one hover card, which resolves the row under the pointer from it.
        // When the Chats tab renders above the project groups, its copy is
        // found first and the sidebar scrolls up to it.
        data-chat-id={normalizeChatId(thread.chatId)}
        className={cn(
          "group flex w-full cursor-pointer select-none items-center gap-2.5 rounded-lg border px-2 py-1.5 max-md:py-1.5 text-left text-sm max-md:text-base active:scale-[0.985] transition-all",
          isActive
            ? "bg-muted hover:bg-muted border-border"
            : "border-border/0 hover:border-border hover:bg-muted/20 dark:hover:border-slate-400/10",
        )}
        onClick={() => onSelect(thread.chatId)}
      >
        <ThreadRowContent
          thread={thread}
          showStatus
          isActive={isActive}
          dimIdleTitles={dimIdleTitles}
          hasDraft={hasDraft}
          detailLabel={detailLabel}
          hoverActions={hoverActions}
        />
      </div>
    </ChatRowMenu>
  )
}

export const ThreadRow = memo(ThreadRowImpl)
