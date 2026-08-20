import { useMemo } from "react"
import { create } from "zustand"
import { persist } from "zustand/middleware"
import { useShallow } from "zustand/react/shallow"
import type { ChatAttachment } from "../../shared/types"

interface ChatInputState {
  drafts: Record<string, string>
  /**
   * When each draft *appeared* — the moment the composer went from empty to
   * holding something, not the last keystroke. That's what the sidebar sorts
   * by, so a chat keeps the place it took when you started writing rather than
   * climbing a row at a time as you type. Persisted alongside the text so the
   * order survives a reload; absent for drafts written before this existed,
   * which fall back to chat activity.
   */
  draftStartedAt: Record<string, number>
  attachmentDrafts: Record<string, ChatAttachment[]>
  setDraft: (chatId: string, value: string) => void
  clearDraft: (chatId: string) => void
  getDraft: (chatId: string) => string
  setAttachmentDrafts: (chatId: string, attachments: ChatAttachment[]) => void
  clearAttachmentDrafts: (chatId: string) => void
  getAttachmentDrafts: (chatId: string) => ChatAttachment[]
}

export const useChatInputStore = create<ChatInputState>()(
  persist(
    (set, get) => ({
      drafts: {},
      draftStartedAt: {},
      attachmentDrafts: {},

      setDraft: (chatId, value) =>
        set((state) => {
          if (!value) {
            const { [chatId]: _, ...rest } = state.drafts
            const { [chatId]: __, ...restStartedAt } = state.draftStartedAt
            return { drafts: rest, draftStartedAt: restStartedAt }
          }
          // Stamped on the clean → dirty flip only. Every later keystroke
          // leaves it alone, so the object identity holds still while you type
          // and the sidebar doesn't re-sort under the cursor.
          const startedAt = state.draftStartedAt[chatId]
          return {
            drafts: { ...state.drafts, [chatId]: value },
            draftStartedAt: startedAt == null
              ? { ...state.draftStartedAt, [chatId]: Date.now() }
              : state.draftStartedAt,
          }
        }),

      clearDraft: (chatId) =>
        set((state) => {
          const { [chatId]: _, ...rest } = state.drafts
          const { [chatId]: __, ...restStartedAt } = state.draftStartedAt
          return { drafts: rest, draftStartedAt: restStartedAt }
        }),

      getDraft: (chatId) => get().drafts[chatId] ?? "",

      setAttachmentDrafts: (chatId, attachments) =>
        set((state) => {
          if (attachments.length === 0) {
            const { [chatId]: _, ...rest } = state.attachmentDrafts
            return { attachmentDrafts: rest }
          }
          return {
            attachmentDrafts: {
              ...state.attachmentDrafts,
              [chatId]: attachments,
            },
          }
        }),

      clearAttachmentDrafts: (chatId) =>
        set((state) => {
          const { [chatId]: _, ...rest } = state.attachmentDrafts
          return { attachmentDrafts: rest }
        }),

      getAttachmentDrafts: (chatId) => get().attachmentDrafts[chatId] ?? [],
    }),
    {
      name: "chat-input-drafts",
    }
  )
)

/**
 * One chat's unsent draft, trimmed, `""` when it has none — whitespace alone is
 * not something to advertise.
 *
 * Subscribes to that chat's string alone, so a row re-renders only while *its*
 * draft is being typed rather than on every keystroke anywhere. Drafts live in
 * the browser, never on the server, so anything reading this is per-device.
 *
 * Read this as close to the thing that displays the text as possible, and pass
 * the value into the presentational piece rather than reading it there. Two
 * reasons, and they pull the same way: those pieces stay renderable (and
 * testable) without a live store, which under zustand v5 they would not be —
 * its server snapshot is the *initial* state, so a store read inside one
 * silently renders as empty — and a subscription in an always-mounted component
 * re-renders it on every keystroke. A sidebar row wants `useChatHasDraft`; only
 * the open hover card wants the text.
 */
export function useChatDraft(chatId: string): string {
  return useChatInputStore((state) => (state.drafts[chatId] ?? "").trim())
}

/**
 * Whether a chat holds an unsent draft, without subscribing to its text.
 *
 * The text changes on every keystroke; whether there *is* text changes twice per
 * draft. Surfaces that only show a pencil icon or enable a "Clear Draft" item
 * want the second question — asking the first re-rendered a sidebar row (and its
 * menu and hover card) on every character typed into the composer.
 */
export function useChatHasDraft(chatId: string): boolean {
  return useChatInputStore((state) => (state.drafts[chatId] ?? "").trim().length > 0)
}

/**
 * Every chat holding an unsent draft, mapped to when that draft appeared.
 * Drives the sidebar's Relevant section: membership keeps those chats out of
 * the date buckets, and the value is what they sort by.
 *
 * `0` means "draft with no known start time" — written before the timestamp
 * existed. Callers fall back to the chat's own activity there rather than
 * sinking it to the bottom of the list.
 *
 * Compared shallowly, and the underlying stamps only move on the clean → dirty
 * flip, so the identity changes when a chat gains or loses a draft and at no
 * other time — never mid-sentence, which would re-sort the list you are
 * looking at.
 */
export function useDraftStartTimes(): ReadonlyMap<string, number> {
  const startTimes = useChatInputStore(
    useShallow((state) => {
      const times: Record<string, number> = {}
      for (const [chatId, draft] of Object.entries(state.drafts)) {
        if (!draft.trim()) continue
        times[chatId] = state.draftStartedAt[chatId] ?? 0
      }
      return times
    })
  )
  return useMemo(() => new Map(Object.entries(startTimes)), [startTimes])
}
