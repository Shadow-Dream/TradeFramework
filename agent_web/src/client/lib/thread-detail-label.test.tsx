import { describe, expect, test } from "bun:test"
import { renderToStaticMarkup } from "react-dom/server"
import type { SidebarChatRow } from "../../shared/types"
import { TooltipProvider } from "../components/ui/tooltip"
import { getThreadDetailLabel } from "./thread-detail-label"
import type { SidebarThread } from "./thread-sections"

const NOW = Date.parse("2026-07-26T12:00:00Z")

function thread(overrides: Partial<SidebarChatRow> = {}): SidebarThread {
  const row: SidebarChatRow = {
    _id: "chat-1",
    _creationTime: NOW - 60 * 60 * 1000,
    chatId: "chat-1",
    title: "Chat",
    status: "idle",
    unread: false,
    provider: "claude",
    hasAutomation: false,
    ...overrides,
  }
  return {
    chatId: row.chatId,
    title: row.title,
    projectId: "project-1",
    projectTitle: "kanna",
    projectLabel: {
      name: "kanna",
      branchName: "feat-x",
      repoPath: "jakemor/kanna",
      text: "kanna/feat-x",
    },
    archived: false,
    lastActivityAt: row.lastMessageAt ?? row._creationTime,
    row,
  }
}

function renderLabel(node: ReturnType<typeof getThreadDetailLabel>) {
  return renderToStaticMarkup(<TooltipProvider>{node}</TooltipProvider>)
}

describe("getThreadDetailLabel", () => {
  test("cross-project lists show the project, named the same way the sidebar names it", () => {
    const html = renderLabel(getThreadDetailLabel(thread(), "cross-project", NOW))

    // The repo inline; the branch is carried by the glyph and the tooltip, so
    // the slot's widest element stays the part that identifies the project.
    expect(html).toContain("kanna")
    expect(html).not.toContain("kanna/feat-x")
    expect(html).toContain("lucide-git-branch")
  })

  test("project-scoped lists show the chat's age instead", () => {
    const label = getThreadDetailLabel(
      thread({ lastMessageAt: NOW - 4 * 60 * 60 * 1000 }),
      "project-scoped",
      NOW
    )

    expect(label).toBe("4h")
  })

  test("age ignores turn-end so a chat that just finished doesn't read 'now'", () => {
    // `lastActivityAt` folds in `lastTurnEndedAt`; the age must not.
    const label = getThreadDetailLabel(
      thread({ lastMessageAt: NOW - 4 * 60 * 60 * 1000, lastTurnEndedAt: NOW }),
      "project-scoped",
      NOW
    )

    expect(label).toBe("4h")
  })

  test("falls back to creation time for a chat that has no messages yet", () => {
    expect(getThreadDetailLabel(thread(), "project-scoped", NOW)).toBe("1h")
  })
})
