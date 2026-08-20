import { type ComponentPropsWithoutRef, memo, type RefObject, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react"
import * as PopoverPrimitive from "@radix-ui/react-popover"
import { TURN_CARD_ROW_INSET, TurnCardMessage, TurnCardMetaRow, TurnCardTimingRow } from "../../ui/turn-card"
import { GitBranch, PencilLine } from "lucide-react"
import { PROVIDERS, type ChatTouchedFilesResult, type SidebarChatRow } from "../../../../shared/types"
import { DiffFileStat } from "../git/shared"
import { formatPromptTimestamp } from "../../messages/ResultMessage"
import { PROVIDER_ICONS } from "../../provider-icons"
import { toMessagePreview } from "../../../../shared/message-preview"
import type { ChatJumpRole } from "../../../lib/chat-navigation"
import { useHasFinePointer } from "../../../lib/pointer"
import { cn, normalizeChatId } from "../../../lib/utils"
import type { SidebarThread } from "../../../lib/thread-sections"
import { useChatDraft } from "../../../stores/chatInputStore"

/**
 * Reads the chat's draft from inside the open card.
 *
 * The draft belongs on the card — it is the card's last line — but subscribing
 * to it anywhere in the always-mounted part of a row would re-render that row on
 * every keystroke in the composer. Radix only mounts this while the card is
 * open, so the subscription lives exactly as long as something is displaying it.
 * `ChatHoverCardContent` stays a pure function of its props.
 */
function ChatHoverCardBody({
  thread,
  ...rest
}: Omit<Parameters<typeof ChatHoverCardContent>[0], "draft">) {
  return <ChatHoverCardContent thread={thread} draft={useChatDraft(thread.row.chatId)} {...rest} />
}

/**
 * The card that appears beside a sidebar chat row on hover — the transcript
 * minimap's hover card, applied to the other list of turns you scan.
 *
 * A sidebar row can only afford a title and one glyph, so everything that says
 * *which* chat this is and *where it got to* has been squeezed out of it: the
 * branch, the harness, how long the turn has been running, what you asked and
 * what came back. The card is where that goes, on the one interaction that
 * costs nothing to attempt and nothing to dismiss.
 *
 * The two messages it shows are also the two places in the chat you most often
 * want to be, so both are clickable: the prompt lands you on the question, the
 * reply on the answer. It stays instant despite now being a target — the peek
 * is the point, and the cost is real but small: a cursor crossing the sidebar
 * raises cards it doesn't want, and briefly puts one over the transcript.
 *
 * Desktop only — hover is not a gesture touch has, and a tap-to-reveal card
 * would fight the row's tap.
 */

/** Harness names, from the catalog the provider picker reads. */
const PROVIDER_LABELS = new Map(PROVIDERS.map((provider) => [provider.id, provider.label]))

/**
 * The card's own surface, spelled out rather than layered over the shared
 * `HoverCardContent` styles — this card is anchored by hand (see
 * `SidebarChatHoverCard`) and no longer inherits a primitive's base class.
 *
 * `px-1.5` rather than the `px-3` this looks like: the other half lives on each
 * row (`TURN_CARD_ROW_INSET`), so text still lands 12px from the edge while a
 * row's hover fill can run wider than it.
 *
 * It enters with an animation and leaves with none. A card that fades out is a
 * card still on screen over the row you have already moved to, and down a fast
 * pointer those overlap.
 */
export const CHAT_HOVER_CARD_CONTENT_CLASSNAME =
  "z-50 w-80 rounded-lg border border-border bg-popover/95 px-1.5 py-2 text-xs text-popover-foreground shadow-xl outline-none backdrop-blur-sm animate-in fade-in-0 zoom-in-95 data-[side=right]:slide-in-from-left-2 data-[side=left]:slide-in-from-right-2 data-[state=closed]:hidden"

/**
 * A turn that has started and not yet ended. Read off timestamps rather than
 * `status` because it's the timestamps that have to agree with the duration:
 * a status of `running` with no start time can't be timed, and a start time
 * newer than the last end is a live turn whatever the status says.
 */
function getActiveTurnStartedAt(row: SidebarChatRow): number | null {
  if (row.lastTurnStartedAt == null) return null
  if (row.lastTurnEndedAt != null && row.lastTurnEndedAt >= row.lastTurnStartedAt) return null
  return row.lastTurnStartedAt
}

/**
 * The agent's last words, but only if they answer the prompt shown above them.
 *
 * A chat you just sent to still carries the *previous* turn's preview, and
 * pairing your new question with the old answer reads as though it had already
 * been answered. Once the prompt is newer than the reply, the card shows the
 * prompt alone until something comes back.
 *
 * Dated by `lastAgentMessagePreviewAt` where it exists, falling back to
 * `lastAgentMessageAt` for chats whose last text predates that field — a
 * slightly generous fallback (tool calls advance it) but never a wrong pairing
 * for anything written since.
 */
function getCurrentTurnReply(row: SidebarChatRow): string | null {
  if (!row.lastAgentMessagePreview) return null
  const repliedAt = row.lastAgentMessagePreviewAt ?? row.lastAgentMessageAt
  if (repliedAt == null) return null
  return repliedAt >= (row.lastMessageAt ?? 0) ? row.lastAgentMessagePreview : null
}

/**
 * How much conversation is in this chat — "1 turn", "24 turns".
 *
 * The size of the whole thing, not the length of its last turn: the two
 * messages above this line are already the latest turn, and a chat you're
 * deciding whether to open is better described by how far it has gone than by
 * how long its most recent run took (which the minimap reports per turn
 * anyway, where it's about a turn you can actually see).
 *
 * Null on chats whose turns all predate the counter — the line drops the fact
 * rather than claiming a chat with history has run none.
 */
function formatTurnCount(row: SidebarChatRow): string | null {
  if (!row.turnCount) return null
  return `${row.turnCount} turn${row.turnCount === 1 ? "" : "s"}`
}

/**
 * Why this chat is in Relevant: the files it changed that are still sitting
 * uncommitted, and how much of each is its doing.
 *
 * The sidebar can only assert the claim — a dot, a section — and "relevant to
 * your uncommitted work" is a conclusion you otherwise have to take on trust.
 * This is where it's shown its working, so the list is the same set the flag is
 * computed from: present exactly when the chat is in Relevant, empty the moment
 * its files are committed.
 *
 * Rows are the git panel's file rows (`DiffFileStat`, same path treatment), so
 * "a file and how much changed in it" looks the same wherever you meet it here.
 * Clicking one opens it in your editor: the file is the thing you'd go to next,
 * and the card is already under the pointer.
 */
function ChatTouchedFileList({ result }: {
  result: ChatTouchedFilesResult
}) {
  if (result.files.length === 0) return null
  const hidden = result.totalCount - result.files.length

  return (
    <>
      {/* Edge to edge: the card's padding is `px-1.5`, so the rule cancels it
          to span the full width and read as a section break rather than as
          another indented row. */}
      <div className="-mx-1.5 mt-2 border-t border-border/60" aria-hidden />
      <div className="mt-1.5">
        {result.files.map((file) => (
          <div
            key={file.path}
            className={cn(
              "flex w-full items-center gap-2 rounded text-left text-[12px] text-muted-foreground",
              TURN_CARD_ROW_INSET,
              "cursor-default",
            )}
          >
            {/* Plain truncation, as in the git panel — the same path in the two
                places you read it should break the same way. The full path is on
                the title, since this is the one row here that routinely won't
                fit. */}
            <span className="min-w-0 flex-1 truncate" title={file.path}>{file.path}</span>
            <DiffFileStat additions={file.additions} deletions={file.deletions} className="shrink-0" />
          </div>
        ))}
        {hidden > 0 ? (
          // Says what it left out rather than trailing off: a card that shows
          // eight of 291 files without saying so reads as the whole answer.
          <div className={cn("text-[12px] text-muted-foreground/70", TURN_CARD_ROW_INSET)}>
            {hidden} more file{hidden === 1 ? "" : "s"}
          </div>
        ) : null}
      </div>
    </>
  )
}

/**
 * Fetched lists, keyed by chat *and* by what would change one — its turn count,
 * when its last turn landed, and whether it still has uncommitted work. Hovering
 * back along a row you already visited is then free, while a chat that has since
 * run or had its work committed refetches rather than showing you the list from
 * before.
 */
const touchedFilesCache = new Map<string, ChatTouchedFilesResult>()
/** Enough for a long hover session; the whole point is to survive re-hovers, not to persist. */
const TOUCHED_FILES_CACHE_LIMIT = 64

function getTouchedFilesCacheKey(row: SidebarChatRow) {
  return [row.chatId, row.turnCount ?? 0, row.lastTurnEndedAt ?? 0, row.uncommittedWork ? 1 : 0].join(" ")
}

/**
 * Loads a chat's file list while its card is open, or `null` while there is
 * nothing (yet) to show.
 *
 * Only ever asks for the row under the pointer — a sidebar of 500 chats can't
 * carry this on its snapshot, and a card you never opened costs nothing. A
 * failed fetch stays `null`: the card is a peek, and an error line in it would
 * be louder than the fact it failed to load an appendix.
 */
function useChatTouchedFiles(
  row: SidebarChatRow | null,
  load?: (chatId: string) => Promise<ChatTouchedFilesResult>
): ChatTouchedFilesResult | null {
  const cacheKey = row ? getTouchedFilesCacheKey(row) : null
  const [result, setResult] = useState<ChatTouchedFilesResult | null>(
    () => (cacheKey ? touchedFilesCache.get(cacheKey) ?? null : null)
  )

  useEffect(() => {
    if (!row || !cacheKey || !load) return
    const cached = touchedFilesCache.get(cacheKey)
    if (cached) {
      setResult(cached)
      return
    }
    // Cleared rather than left showing the previous chat's files: cards are
    // reused as the pointer runs down the list, and one row's list under
    // another row's title is worse than no list at all.
    setResult(null)
    let cancelled = false
    void load(row.chatId).then((next) => {
      if (touchedFilesCache.size >= TOUCHED_FILES_CACHE_LIMIT) {
        touchedFilesCache.delete(touchedFilesCache.keys().next().value as string)
      }
      touchedFilesCache.set(cacheKey, next)
      if (!cancelled) setResult(next)
    }).catch(() => {})
    return () => {
      cancelled = true
    }
  }, [cacheKey, load, row])

  return result
}

/** Exported for tests: the card body, without the hover-card machinery around it. */
export function ChatHoverCardContent({
  thread,
  draft = "",
  touchedFiles,
  onSelectMessage,
  onSelectChat,
}: {
  thread: SidebarThread
  draft?: string
  /** What this chat changed; absent until the fetch lands (or if it fails). */
  touchedFiles?: ChatTouchedFilesResult | null
  /** Absent when the card is read-only (archived rows). */
  onSelectMessage?: (role: ChatJumpRole) => void
  /** Opens the chat without aiming at a message — what the draft does. */
  onSelectChat?: () => void
}) {
  const row = thread.row
  const label = thread.projectLabel
  const HarnessIcon = row.provider ? PROVIDER_ICONS[row.provider] : null
  const turns = formatTurnCount(row)
  const reply = getCurrentTurnReply(row)
  // When the turn landed, in the transcript's own format — "3:42 PM" today,
  // "Mon 3:42 PM" this week, the full date beyond that. Suppressed while a turn
  // is live: the only end time on hand then belongs to the *previous* turn.
  const endedAt = getActiveTurnStartedAt(row) != null || row.lastTurnEndedAt == null
    ? null
    : formatPromptTimestamp(new Date(row.lastTurnEndedAt).toISOString())
  // Both blocks are clickable whenever the surface offers the jump at all. The
  // card doesn't identify the messages and doesn't need to: it shows a chat's
  // latest prompt and latest reply by definition, and the transcript resolves
  // those from its own rows the way the minimap does.
  const canJump = Boolean(onSelectMessage)


  return (
    <>
      {/* Branch leads, repo trails. Down a list of chats it's the branch that
          differs — the repo is usually the same one over and over — so the
          varying fact reads down the left edge and the constant one anchors
          right, the same shape as the footer's harness and time. */}
      <TurnCardMetaRow>
        {/* Named even when it's `main`: the card has the room, and a branch you
            have to infer from the *absence* of a glyph is a worse answer than
            the word. Absent only on a detached HEAD, where the repo simply
            slides over. */}
        {label.currentBranch ? (
          <span className="flex min-w-0 items-center gap-1">
            <GitBranch className="size-2.5 shrink-0" strokeWidth={2.5} />
            <span className="truncate">{label.currentBranch}</span>
          </span>
        ) : null}
        {/* `owner/repo` when the origin owner is known, the bare repo otherwise;
            a renamed project has neither, and shows the name you gave it. Both
            sides may truncate rather than either being pinned: flexbox takes it
            out of the longer one first, which is nearly always the branch. */}
        <span className="ml-auto flex min-w-0 items-center gap-1 pl-2">
          {/* When the repo has a page, the name *is* the link to it — one
              click, one destination, the convention a repo path already
              carries on the web. Opening it in an app is the row's right-click
              menu's job; a second menu here would only duplicate it. */}
          <span className="truncate">{label.repoPath ?? label.name}</span>
        </span>
      </TurnCardMetaRow>
      {/* The exchange, as one block with matched margins above and below so it
          sits between the two meta lines rather than joining either. */}
      <div className="mt-1 space-y-1">
        {/* The prompt leads, as in the minimap card: it's the question the reply
            under it is the answer to. Falls back to the chat's title so a chat
            with no messages yet still says what it is. A draft displaces it —
            see below. */}
        {draft ? null : (
          <TurnCardMessage
            className="line-clamp-2 text-sm font-medium text-popover-foreground"
            label="Jump to this prompt"
            onSelect={canJump ? () => onSelectMessage?.("prompt") : undefined}
          >
            {toMessagePreview(row.lastUserMessagePreview || thread.title)}
          </TurnCardMessage>
        )}
        {/* Cut to a single line once a draft is here: what you were about to
            say outranks how the last answer began, and three lines of reply
            would push it to the bottom of a card you opened for the draft. */}
        {reply ? (
          <TurnCardMessage
            className={cn("text-sm text-muted-foreground", draft ? "line-clamp-1" : "line-clamp-3")}
            label="Jump to this reply"
            onSelect={canJump ? () => onSelectMessage?.("reply") : undefined}
          >
            {toMessagePreview(reply)}
          </TurnCardMessage>
        ) : null}
        {/* An unsent draft ends the card, in the prompt's own weight but
            italic and pencilled: it is the next thing said in this chat, not
            the last. It takes the sent prompt's place rather than sitting
            alongside it — the prompt is already answered by the reply above,
            and what you want back is the sentence you walked away from.
            The glyph is inline rather than a flex sibling, so a wrapped second
            line runs the full width instead of indenting to clear it. */}
        {draft ? (
          // Unlike the two messages, a draft has no entry to land on — it was
          // never sent. So it just opens the chat, where the composer is
          // already holding it.
          <TurnCardMessage
            className="line-clamp-2 text-sm font-medium italic text-popover-foreground"
            label="Open this chat"
            onSelect={onSelectChat}
          >
            <PencilLine className="mr-1 inline size-3 shrink-0 -translate-y-px" strokeWidth={2.5} />
            {toMessagePreview(draft)}
          </TurnCardMessage>
        ) : null}
      </div>
      {/* The harness on the left, the chat's size and when it last landed on
          the right. Turns rather than the last turn's duration, which the
          minimap already reports per turn and which said nothing about the
          chat: how much conversation is in here is the thing a sidebar row
          can't show and you'd want before opening it. */}
      <TurnCardTimingRow
        detail={turns}
        timestamp={endedAt}
        leading={row.provider ? (
          // Glyph and name are one fact, so nothing separates them.
          <>
            {HarnessIcon ? <HarnessIcon className="size-3 shrink-0" /> : null}
            <span className="truncate">{PROVIDER_LABELS.get(row.provider) ?? row.provider}</span>
          </>
        ) : null}
      />
      {/* Below the footer, not above it: the exchange and the harness line are
          what the card has always said, and the file list is an appendix to
          them — long, scannable, and the thing you drop to when the summary
          above didn't settle it. Nothing renders at all until the fetch lands,
          so the card doesn't resize under a pointer that's already reading it
          unless there's something to show. */}
      {touchedFiles ? (
        <ChatTouchedFileList result={touchedFiles} />
      ) : null}
    </>
  )
}

/**
 * The sidebar's chat hover card — one for the whole list, not one per row.
 *
 * There used to be a Radix hover card on every row, which left "only one card
 * is up" to N independent state machines racing a pointer that crosses several
 * rows in a frame. Each of them could get stuck open on its own: a hover card
 * that has seen a text selection anywhere in the page stops closing entirely
 * (Radix latches `hasSelectionRef` on the next `pointerup` and then refuses),
 * and a row's trigger also opens on `focus`, which bubbles up from the
 * Fork/Archive buttons inside the row — a card with no pointer near it. Fixing
 * those one at a time only narrows the window; the shape is what leaks.
 *
 * With one instance, "at most one card, on the row under the pointer" holds by
 * construction. It is also what a sidebar of 500 chats can afford: rows carry
 * no hover state, no trigger wrapper and no card body of their own, and the
 * file list is fetched once, for the row you are actually on.
 *
 * Hovering is read from a single delegated `pointerover` on the scroll
 * container, so an idle row costs nothing at all. Radix positions the card
 * against the row's element and dismisses it on Escape or a click; opening and
 * closing are this component's own.
 *
 * Desktop only — hover is not a gesture touch has, and a tap-to-reveal card
 * would fight the row's tap.
 */
function SidebarChatHoverCardImpl({
  containerRef,
  threads,
  onSelectChat,
  onSelectMessage,
  onOpenArchivedChat,
  onLoadTouchedFiles,
}: {
  /** The sidebar's scroll container — every chat row is somewhere beneath it. */
  containerRef: RefObject<HTMLDivElement | null>
  /** Every row the sidebar can show, from `useStableSidebarThreads`. */
  threads: SidebarThread[]
  /** Opens the chat plainly — the draft's action, and the row's. */
  onSelectChat: (chatId: string) => void
  /** Opens a chat at one end of its last exchange — the clickable previews. */
  onSelectMessage: (chatId: string, role: ChatJumpRole) => void
  /** Archived chats open by their own route; their cards offer nothing else. */
  onOpenArchivedChat: (chatId: string) => void
  /** Fetches what a chat changed. Omitted = the card shows no file list. */
  onLoadTouchedFiles?: (chatId: string) => Promise<ChatTouchedFilesResult>
}) {
  const hasFinePointer = useHasFinePointer()
  const [hoveredChatId, setHoveredChatId] = useState<string | null>(null)
  // What the pointer handlers read and write. They are registered once, so they
  // can't close over the state, and a ref keeps them off the re-render path.
  const hoveredChatIdRef = useRef<string | null>(null)
  // The row a dismissal happened on. Clicking a row closes its card while the
  // pointer is still sitting on it, and without this the next `pointerover`
  // inside that same row — one pixel of movement — would raise it again.
  const dismissedChatIdRef = useRef<string | null>(null)
  const anchorRef = useRef<HTMLElement | null>(null)
  const contentRef = useRef<HTMLDivElement | null>(null)

  // Keyed as the rows write it, so resolving a `data-chat-id` on a pointer move
  // is a map lookup and nothing else.
  const threadByRowId = useMemo(
    () => new Map(threads.map((thread) => [normalizeChatId(thread.chatId), thread])),
    [threads]
  )
  // Null once the hovered chat leaves the sidebar — archived from elsewhere,
  // filtered out by focus mode — which closes the card rather than stranding it
  // on a row that is no longer there.
  const thread = hasFinePointer && hoveredChatId ? threadByRowId.get(hoveredChatId) ?? null : null
  const archived = thread?.archived ?? false
  const chatId = thread?.chatId
  const touchedFiles = useChatTouchedFiles(thread?.row ?? null, onLoadTouchedFiles)

  const setHovered = useCallback((nextChatId: string | null) => {
    if (hoveredChatIdRef.current === nextChatId) return
    hoveredChatIdRef.current = nextChatId
    setHoveredChatId(nextChatId)
  }, [])

  /** Closes the card and holds it closed until the pointer reaches another row. */
  const dismiss = useCallback(() => {
    dismissedChatIdRef.current = hoveredChatIdRef.current
    setHovered(null)
  }, [setHovered])

  // Looked up from the DOM every render rather than kept from the pointer event
  // that opened the card: sections re-order and rows remount, and an anchor
  // holding a detached row floats the card where that row used to be. A layout
  // effect so it lands before the popper's own effect reads the ref.
  useLayoutEffect(() => {
    const container = containerRef.current
    anchorRef.current = hoveredChatId && container
      ? container.querySelector<HTMLElement>(`[data-chat-id="${CSS.escape(hoveredChatId)}"]`)
      : null
  })

  useEffect(() => {
    const container = containerRef.current
    if (!container || !hasFinePointer) return

    // One listener for the whole list. `pointerover` bubbles (`pointerenter`
    // does not), so the row is whatever the event came from that carries the
    // marker — and a move within one row settles to the same answer.
    function handlePointerOver(event: PointerEvent) {
      if (event.pointerType === "touch") return
      const target = event.target
      const row = target instanceof Element ? target.closest("[data-chat-id]") : null
      const rowChatId = row instanceof HTMLElement ? row.dataset.chatId ?? null : null
      if (rowChatId != null && rowChatId === dismissedChatIdRef.current) return
      dismissedChatIdRef.current = null
      // Null for the gaps between rows — section headers, the New Chat button —
      // which close the card rather than leave the last row's up.
      setHovered(rowChatId)
    }

    // Walking from a row to the card crosses a gap that the card itself covers
    // (see the bridge on its className), so by the time the sidebar reports the
    // pointer gone it is already inside the card.
    function handlePointerLeave(event: PointerEvent) {
      const next = event.relatedTarget
      if (next instanceof Node && contentRef.current?.contains(next)) return
      setHovered(null)
    }

    // A card left up while the window is in the background would be waiting on
    // the far side of a Cmd-Tab, over whatever you came back to read.
    function handleWindowBlur() {
      setHovered(null)
    }

    container.addEventListener("pointerover", handlePointerOver)
    container.addEventListener("pointerleave", handlePointerLeave)
    window.addEventListener("blur", handleWindowBlur)
    return () => {
      container.removeEventListener("pointerover", handlePointerOver)
      container.removeEventListener("pointerleave", handlePointerLeave)
      window.removeEventListener("blur", handleWindowBlur)
    }
  }, [containerRef, hasFinePointer, setHovered])

  const handleContentPointerLeave = useCallback((event: { relatedTarget: EventTarget | null }) => {
    const next = event.relatedTarget
    // Back onto the list: the container's `pointerover` re-anchors the card in
    // the same move, so clearing here would only flicker it.
    if (next instanceof Node && containerRef.current?.contains(next)) return
    setHovered(null)
  }, [containerRef, setHovered])

  const handleSelectChat = useCallback(() => {
    if (!chatId) return
    dismiss()
    if (archived) onOpenArchivedChat(chatId)
    else onSelectChat(chatId)
  }, [archived, chatId, dismiss, onOpenArchivedChat, onSelectChat])

  const handleSelectMessage = useCallback((role: ChatJumpRole) => {
    if (!chatId) return
    dismiss()
    onSelectMessage(chatId, role)
  }, [chatId, dismiss, onSelectMessage])

  return (
    <PopoverPrimitive.Root
      open={thread != null}
      onOpenChange={(nextOpen) => {
        // Only ever asked to close — pointing at a row is what opens it. Escape
        // and a click anywhere both arrive here, and both should leave the card
        // down until the pointer has moved on to another row.
        if (!nextOpen) dismiss()
      }}
    >
      {/* Anchored to the row's element instead of wrapping it. The card belongs
          to whichever row is under the pointer, and that changes without any of
          them re-rendering. */}
      <PopoverPrimitive.Anchor
        // Radix types the ref as always holding a measurable, but reads it on
        // every render and is happy with an empty one — which is what "no row
        // is hovered" is.
        virtualRef={anchorRef as ComponentPropsWithoutRef<typeof PopoverPrimitive.Anchor>["virtualRef"]}
      />
      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Content
          ref={contentRef}
          side="right"
          // Top-aligned with the row rather than centred on it: the card is
          // several times the row's height, so centring floated it above the
          // thing it describes and left you tracing back to find which row.
          align="start"
          // Clears the row's right edge so the card reads as beside the sidebar
          // rather than inside it.
          sideOffset={15}
          collisionPadding={12}
          // A peek, not a destination. It must never pull focus off the
          // composer on the way in, nor throw focus somewhere on the way out —
          // and it is raised and dropped constantly.
          onOpenAutoFocus={(event) => event.preventDefault()}
          onCloseAutoFocus={(event) => event.preventDefault()}
          onPointerLeave={handleContentPointerLeave}
          className={cn(
            CHAT_HOVER_CARD_CONTENT_CLASSNAME,
            // File rows carry their own `py-0.5`, so the card's full `pb-2`
            // under the last one reads as a wider gap than the one above the
            // list. Only when the list is there: without it the footer is plain
            // text that wants the full padding.
            touchedFiles?.files.length ? "pb-1.5" : null,
            // The bridge: an invisible strip of the card laid over the gap
            // between the row and the card, so the pointer never leaves the
            // card's hitbox on its way there and no close timer is needed to
            // cover the crossing.
            //
            // Wider than the 15px `sideOffset` and so overlapping the row's
            // last few pixels — a hairline of dead space from subpixel
            // placement would drop the pointer for a frame and close the card
            // mid-walk. The overlap lands in the row's own right padding, well
            // clear of its hover buttons.
            //
            // Full height rather than just the row's band: `align="start"`
            // stops holding once a card near the bottom of the screen gets
            // shifted up to fit, and those rows have to stay reachable too.
            "relative before:absolute before:inset-y-0 before:w-5 before:content-['']",
            "data-[side=right]:before:-left-5 data-[side=left]:before:-right-5",
          )}
        >
          {thread ? (
            <ChatHoverCardBody
              thread={thread}
              touchedFiles={touchedFiles}
              // An archived chat has nowhere to jump to and nothing to set up;
              // its card reads, and its one action reopens it.
              onSelectMessage={archived ? undefined : handleSelectMessage}
              onSelectChat={handleSelectChat}
            />
          ) : null}
        </PopoverPrimitive.Content>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  )
}

/**
 * Memoized because the sidebar re-renders on every snapshot push — several a
 * second through a turn — and this holds the one live card body.
 */
export const SidebarChatHoverCard = memo(SidebarChatHoverCardImpl)
