import { useCallback, useEffect, useMemo, useRef } from "react"
import type { ResolvedChatReadAnchor } from "../../shared/types"
import type { KannaSocket } from "./socket"

/**
 * Minimum gap between `chat.setReadAnchor` writes while the user keeps
 * scrolling. Also bounds how long after they stop before the final position
 * lands (the trailing write fires at most this late).
 */
const READ_ANCHOR_WRITE_INTERVAL_MS = 1500

/**
 * Where in the message the reader was, and the column width that makes it
 * meaningful. Restoring uses the offset only at a matching width, since a
 * narrower column rewraps the message and moves everything inside it.
 */
export interface ReadAnchorLayout {
  transcriptWidth: number
  offsetFromMessage: number
}

export interface ChatReadAnchorState {
  /**
   * False until the chat's first snapshot lands. Consumers must wait for this
   * before restoring scroll, otherwise they'd land on the fallback and then
   * visibly jump when the anchor arrives.
   */
  resolved: boolean
  anchor: ResolvedChatReadAnchor | null
}

export interface ChatReadAnchorSync {
  anchorState: ChatReadAnchorState
  /**
   * Report the message currently at the top of the viewport. Safe to call on
   * every scroll event — writes are throttled and deduped.
   */
  reportReadAnchor: (messageId: string, atEnd: boolean, layout?: ReadAnchorLayout) => void
}

/**
 * Tracks a chat's server-side read position.
 *
 * The anchor arrives inline on the chat snapshot, already resolved against the
 * window the server chose — opening a chat is one round trip rather than a
 * probe followed by a re-subscription at a wider limit, which fetched the
 * transcript twice.
 *
 * Only the first snapshot per chat is consulted, so a device sitting on an
 * open chat never gets its viewport moved because another device scrolled.
 * Writes are throttled and ack-only.
 */
export function useChatReadAnchor(
  socket: KannaSocket,
  activeChatId: string | null,
  snapshotAnchor: ResolvedChatReadAnchor | null | undefined,
  chatReady: boolean
): ChatReadAnchorSync {
  // Latched per chat: later snapshots carry a moving anchor (it is recomputed
  // as entries append), and adopting those would let a background update
  // retarget a restore that already happened.
  const latchedRef = useRef<{ chatId: string | null; anchor: ResolvedChatReadAnchor | null }>({
    chatId: null,
    anchor: null,
  })
  if (latchedRef.current.chatId !== activeChatId) {
    latchedRef.current = { chatId: activeChatId, anchor: chatReady ? snapshotAnchor ?? null : null }
  } else if (!latchedRef.current.anchor && chatReady && snapshotAnchor) {
    latchedRef.current = { chatId: activeChatId, anchor: snapshotAnchor }
  }

  const anchorState = useMemo<ChatReadAnchorState>(
    () => ({ resolved: !activeChatId || chatReady, anchor: latchedRef.current.anchor }),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- latch is keyed by chat + readiness
    [activeChatId, chatReady, latchedRef.current.anchor]
  )

  const pendingRef = useRef<{
    chatId: string
    messageId: string
    atEnd: boolean
    layout?: ReadAnchorLayout
  } | null>(null)
  const timerRef = useRef<number | null>(null)
  const lastWriteAtRef = useRef(0)
  const lastWrittenKeyRef = useRef<string | null>(null)

  const flush = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current)
      timerRef.current = null
    }
    const pending = pendingRef.current
    pendingRef.current = null
    if (!pending) return

    // While parked at the bottom the top-visible message churns with every
    // streamed chunk, but restore only needs to know "was at the end" — so all
    // of those collapse to a single write instead of one every interval.
    // Scrolling within one long message changes only the offset, so that has to
    // be part of the key or the position would stick where the message first
    // came into view.
    const key = pending.atEnd
      ? `${pending.chatId}:atEnd`
      : `${pending.chatId}:${pending.messageId}:${pending.layout?.offsetFromMessage ?? ""}`
    if (key === lastWrittenKeyRef.current) return
    lastWrittenKeyRef.current = key
    lastWriteAtRef.current = Date.now()

    void socket.command({
      type: "chat.setReadAnchor",
      chatId: pending.chatId,
      messageId: pending.messageId,
      atEnd: pending.atEnd,
      ...(pending.layout ?? {}),
    }).catch(() => {
      // Every pending command rejects with "Disconnected" on socket close even
      // though the envelope may still be delivered. Never surface this.
    })
  }, [socket])

  const reportReadAnchor = useCallback((messageId: string, atEnd: boolean, layout?: ReadAnchorLayout) => {
    if (!activeChatId) return
    pendingRef.current = { chatId: activeChatId, messageId, atEnd, layout }
    // Already scheduled — the newer position just replaces the pending one.
    if (timerRef.current !== null) return
    const delay = Math.max(0, READ_ANCHOR_WRITE_INTERVAL_MS - (Date.now() - lastWriteAtRef.current))
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null
      flush()
    }, delay)
  }, [activeChatId, flush])

  // Write anything still queued for the outgoing chat, and reset the dedupe key
  // so the new chat's first sample always writes.
  //
  // Leaving used to discard the pending sample instead. Writes are throttled to
  // `READ_ANCHOR_WRITE_INTERVAL_MS`, so the position a reader stops at is
  // almost always still pending when they navigate away — which is exactly the
  // position worth keeping. Dropping it meant returning to wherever they
  // happened to be a second and a half earlier, or to a much older sample if
  // they never paused that long. The pending entry carries its own `chatId`, so
  // flushing here writes it against the chat it was sampled from.
  useEffect(() => {
    return () => {
      flush()
      lastWrittenKeyRef.current = null
    }
  }, [activeChatId, flush])

  // Backgrounding a tab is the common way a session ends. `visibilitychange`
  // fires reliably here, unlike pagehide/beforeunload where the socket may
  // already be closing and the envelope would sit in an undrained queue.
  useEffect(() => {
    function handleVisibilityChange() {
      if (document.visibilityState === "hidden") flush()
    }
    document.addEventListener("visibilitychange", handleVisibilityChange)
    return () => document.removeEventListener("visibilitychange", handleVisibilityChange)
  }, [flush])

  return useMemo(
    () => ({ anchorState, reportReadAnchor }),
    [anchorState, reportReadAnchor]
  )
}
