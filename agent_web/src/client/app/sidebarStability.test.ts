import { describe, expect, test } from "bun:test"
import type { SidebarChatRow, SidebarData, SidebarProjectGroup } from "../../shared/types"
import { stabilizeSidebarData } from "./sidebarStability"

function row(overrides: Partial<SidebarChatRow> & { chatId: string }): SidebarChatRow {
  return {
    _id: overrides.chatId,
    _creationTime: 1,
    title: "Chat",
    status: "idle",
    unread: false,
    provider: "claude-deepseek",
    hasAutomation: false,
    ...overrides,
  }
}

function group(overrides: Partial<SidebarProjectGroup> & { groupKey: string }): SidebarProjectGroup {
  const chats = overrides.chats ?? [row({ chatId: `${overrides.groupKey}-a` })]
  return {
    title: "Project",
    realTitle: "Project",
    chats,
    previewChats: chats,
    olderChats: [],
    defaultCollapsed: false,
    ...overrides,
  }
}

function data(groups: SidebarProjectGroup[]): SidebarData {
  return { projectGroups: groups }
}

describe("stabilizeSidebarData", () => {
  test("returns the previous snapshot when nothing changed", () => {
    const previous = data([group({ groupKey: "p1" }), group({ groupKey: "p2" })])
    const next = data([group({ groupKey: "p1" }), group({ groupKey: "p2" })])

    expect(stabilizeSidebarData(previous, next)).toBe(previous)
  })

  test("keeps every untouched row and group when one row moves", () => {
    const previous = data([
      group({ groupKey: "p1", chats: [row({ chatId: "a" }), row({ chatId: "b" })] }),
      group({ groupKey: "p2" }),
    ])
    const next = data([
      group({
        groupKey: "p1",
        chats: [row({ chatId: "a" }), row({ chatId: "b", lastAgentMessagePreview: "hi" })],
      }),
      group({ groupKey: "p2" }),
    ])

    const result = stabilizeSidebarData(previous, next)

    expect(result).not.toBe(previous)
    // The untouched project group keeps its identity, so it never re-renders.
    expect(result.projectGroups[1]).toBe(previous.projectGroups[1])
    expect(result.projectGroups[0]).not.toBe(previous.projectGroups[0])
    expect(result.projectGroups[0]!.chats[0]).toBe(previous.projectGroups[0]!.chats[0])
    expect(result.projectGroups[0]!.chats[1]!.lastAgentMessagePreview).toBe("hi")
  })

  test("keeps a group's chat array when only its archived list changed", () => {
    const shared = [row({ chatId: "a" })]
    const previous = data([group({ groupKey: "p1", chats: shared, archivedChats: [] })])
    const next = data([
      group({ groupKey: "p1", chats: [row({ chatId: "a" })], archivedChats: [row({ chatId: "z" })] }),
    ])

    const result = stabilizeSidebarData(previous, next)

    expect(result.projectGroups[0]!.chats).toBe(previous.projectGroups[0]!.chats)
    expect(result.projectGroups[0]!.archivedChats).toHaveLength(1)
  })

  test("takes the new snapshot when a row is added or removed", () => {
    const previous = data([group({ groupKey: "p1", chats: [row({ chatId: "a" })] })])
    const next = data([
      group({ groupKey: "p1", chats: [row({ chatId: "a" }), row({ chatId: "b" })] }),
    ])

    const result = stabilizeSidebarData(previous, next)

    expect(result.projectGroups[0]!.chats).toHaveLength(2)
  })

  test("takes the new snapshot when the group count changes", () => {
    const previous = data([group({ groupKey: "p1" })])
    const next = data([group({ groupKey: "p1" }), group({ groupKey: "p2" })])

    expect(stabilizeSidebarData(previous, next)).toBe(next)
  })

  test("takes the new group when a project field changed", () => {
    const previous = data([group({ groupKey: "p1", branchName: "main" })])
    const next = data([group({ groupKey: "p1", branchName: "feature" })])

    const result = stabilizeSidebarData(previous, next)

    expect(result.projectGroups[0]!.branchName).toBe("feature")
  })

  test("passes the first snapshot through", () => {
    const next = data([group({ groupKey: "p1" })])
    expect(stabilizeSidebarData(null, next)).toBe(next)
  })
})
