import { useMemo } from "react"
import { create } from "zustand"
import { useShallow } from "zustand/react/shallow"
import type { SidebarData } from "../../shared/types"

/**
 * Chats with a prompt in flight — sent by this browser, not yet acknowledged in
 * a sidebar snapshot.
 *
 * This exists to close a gap the sidebar could otherwise see through. A chat you
 * have typed into is held in the Relevant section by its draft, and a chat that
 * is running is held in In Progress by its status. Pressing send clears the
 * draft immediately (the composer must empty on the keystroke, not on the round
 * trip) while the status is still idle and the chat still has no messages — so
 * for the length of the round trip the chat qualifies for nothing, and the row
 * vanishes and then reappears somewhere else.
 *
 * Recording *when* the send started, rather than a bare flag, does two jobs: it
 * places the row in In Progress at the position it will keep once the server
 * catches up (that section sorts by when you last hit send), and it makes the
 * entry self-expiring — see `isPendingSend`, which ignores an entry the chat's
 * own `lastMessageAt` has already overtaken. A dropped `clearSend` therefore
 * costs a stale map key and nothing else.
 */

interface PendingSendState {
  /** Chat id → when the send started (`Date.now()`). */
  sentAt: Record<string, number>
  markSending: (chatId: string, sentAt?: number) => void
  clearSending: (chatId: string) => void
}

export const usePendingSendStore = create<PendingSendState>()((set) => ({
  sentAt: {},

  markSending: (chatId, sentAt = Date.now()) => set((state) => ({
    sentAt: { ...state.sentAt, [chatId]: sentAt },
  })),

  clearSending: (chatId) => set((state) => {
    if (!(chatId in state.sentAt)) return state
    const { [chatId]: _cleared, ...rest } = state.sentAt
    return { sentAt: rest }
  }),
}))

export type PendingSendTimes = ReadonlyMap<string, number>

/**
 * The in-flight sends, for the sidebar's section pass. Memoized on a shallow
 * compare of the record, so the sections only recompute when a send actually
 * starts or lands — the same shape as `useDraftStartTimes`, for the same reason.
 */
export function usePendingSendTimes(): PendingSendTimes {
  const sentAt = usePendingSendStore(useShallow((state) => state.sentAt))
  return useMemo(() => new Map(Object.entries(sentAt)), [sentAt])
}

export function markSending(chatId: string, sentAt?: number) {
  usePendingSendStore.getState().markSending(chatId, sentAt)
}

export function clearSending(chatId: string) {
  usePendingSendStore.getState().clearSending(chatId)
}

/**
 * Drop entries the sidebar snapshot has caught up with, and entries for chats it
 * no longer carries at all (deleted while a send was in flight).
 *
 * Housekeeping, not correctness: `isPendingSend` already ignores an overtaken
 * entry. This just stops the map growing for the length of a session.
 */
export function prunePendingSends(projectGroups: SidebarData["projectGroups"]): void {
  const { sentAt } = usePendingSendStore.getState()
  const pendingIds = Object.keys(sentAt)
  if (pendingIds.length === 0) return

  const lastMessageAtByChatId = new Map<string, number>()
  for (const group of projectGroups) {
    for (const chat of group.chats) {
      lastMessageAtByChatId.set(chat.chatId, chat.lastMessageAt ?? 0)
    }
  }

  const settled = pendingIds.filter((chatId) => {
    const lastMessageAt = lastMessageAtByChatId.get(chatId)
    return lastMessageAt === undefined || lastMessageAt >= sentAt[chatId]!
  })
  if (settled.length === 0) return

  usePendingSendStore.setState((state) => {
    const next = { ...state.sentAt }
    for (const chatId of settled) delete next[chatId]
    return { sentAt: next }
  })
}
