import { beforeEach, describe, expect, test } from "bun:test"
import type { SidebarChatRow, SidebarProjectGroup } from "../../shared/types"
import {
  clearSending,
  markSending,
  prunePendingSends,
  usePendingSendStore,
} from "./pendingSendStore"

function row(chatId: string, lastMessageAt?: number): SidebarChatRow {
  return {
    _id: chatId,
    _creationTime: 1,
    chatId,
    title: "Chat",
    status: "idle",
    unread: false,
    provider: "claude-deepseek",
    hasAutomation: false,
    ...(lastMessageAt == null ? {} : { lastMessageAt }),
  }
}

function groups(chats: SidebarChatRow[]): SidebarProjectGroup[] {
  return [{
    groupKey: "p1",
    title: "P",
    realTitle: "P",
    chats,
    previewChats: chats,
    olderChats: [],
    defaultCollapsed: false,
  }]
}

describe("pendingSendStore", () => {
  beforeEach(() => {
    usePendingSendStore.setState({ sentAt: {} })
  })

  test("records and clears an in-flight send", () => {
    markSending("a", 100)
    expect(usePendingSendStore.getState().sentAt).toEqual({ a: 100 })

    clearSending("a")
    expect(usePendingSendStore.getState().sentAt).toEqual({})
  })

  test("clearing an unknown chat leaves the state object alone", () => {
    const held = usePendingSendStore.getState().sentAt
    clearSending("missing")
    expect(usePendingSendStore.getState().sentAt).toBe(held)
  })

  test("pruning drops sends the snapshot has caught up with", () => {
    markSending("landed", 100)
    markSending("still-flying", 100)

    prunePendingSends(groups([row("landed", 150), row("still-flying", 50)]))

    expect(usePendingSendStore.getState().sentAt).toEqual({ "still-flying": 100 })
  })

  test("pruning drops sends for chats the snapshot no longer carries", () => {
    markSending("deleted", 100)

    prunePendingSends(groups([row("other", 150)]))

    expect(usePendingSendStore.getState().sentAt).toEqual({})
  })

  test("pruning with nothing pending does no work", () => {
    const held = usePendingSendStore.getState().sentAt
    prunePendingSends(groups([row("a", 1)]))
    expect(usePendingSendStore.getState().sentAt).toBe(held)
  })
})
