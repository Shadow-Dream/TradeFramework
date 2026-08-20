import { describe, expect, test } from "bun:test"
import { getAppAuthStateFromStatus, shouldPlayChatNotificationSound, shouldRetryAuthStatusRequest } from "./App"
import { getBrowserWindowTitle, getChatNotificationSnapshot, getChatSoundBurstCount, getNotificationTitleCount } from "./chatNotifications"
import { DEFAULT_SIDEBAR_WIDTH, MAX_SIDEBAR_WIDTH, MIN_SIDEBAR_WIDTH, clampSidebarWidth } from "./KannaSidebar"
import { isBrowserUnfocused, shouldPlayChatSound } from "../lib/chatSounds"
import type { AppSettingsSnapshot, SidebarChatRow } from "../../shared/types"

function createProjectGroup(chats: SidebarChatRow[], title = "Project", archivedChats: SidebarChatRow[] = []) {
  return {
    groupKey: "project-1",
    realTitle: "Project",
    title,
    chats,
    previewChats: chats,
    olderChats: [],
    archivedChats,
    defaultCollapsed: false,
  }
}

describe("clampSidebarWidth", () => {
  test("keeps sidebar resizing within bounds", () => {
    expect(clampSidebarWidth(MIN_SIDEBAR_WIDTH - 1)).toBe(MIN_SIDEBAR_WIDTH)
    expect(clampSidebarWidth(MAX_SIDEBAR_WIDTH + 1)).toBe(MAX_SIDEBAR_WIDTH)
    expect(clampSidebarWidth(333.6)).toBe(334)
    expect(clampSidebarWidth(Number.NaN)).toBe(DEFAULT_SIDEBAR_WIDTH)
  })
})

describe("auth boot helpers", () => {
  test("maps an authenticated TradeEngine session to ready", () => {
    const state = getAppAuthStateFromStatus({
      authenticated: true,
      user: { userId: "u1", email: "u@example.test", role: "admin", expiresAt: "2099-01-01T00:00:00Z" },
      tradeEngineUrl: "http://trade.example/",
    })
    expect(state.status).toBe("ready")
    if (state.status === "ready") {
      expect(state.value.account.userId).toBe("u1")
      expect(state.value.returnUrl).toBe("http://trade.example/")
    }
  })

  test("does not admit an unauthenticated response", () => {
    expect(getAppAuthStateFromStatus({ authenticated: false })).toEqual({ status: "checking" })
  })

  test("retries auth status requests unless the endpoint returned ok", () => {
    expect(shouldRetryAuthStatusRequest(null)).toBe(true)
    expect(shouldRetryAuthStatusRequest(false)).toBe(true)
    expect(shouldRetryAuthStatusRequest(true)).toBe(false)
  })
})

describe("getNotificationTitleCount", () => {
  test("counts unread chats and waiting-for-user chats", () => {
    expect(getNotificationTitleCount({
      projectGroups: [createProjectGroup([
          {
            _id: "chat-1",
            _creationTime: 1,
            chatId: "chat-1",
            title: "Unread",
            status: "idle",
            unread: true,
            provider: null,
            hasAutomation: false,
          },
          {
            _id: "chat-2",
            _creationTime: 2,
            chatId: "chat-2",
            title: "Waiting",
            status: "waiting_for_user",
            unread: false,
            provider: null,
            hasAutomation: false,
          },
          {
            _id: "chat-3",
            _creationTime: 3,
            chatId: "chat-3",
            title: "Both",
            status: "waiting_for_user",
            unread: true,
            provider: null,
            hasAutomation: false,
          },
        ])],
    })).toBe(4)
  })
})

describe("getBrowserWindowTitle", () => {
  test("adds the active project and chat title to the app title", () => {
    expect(getBrowserWindowTitle({
      appName: "Kanna",
      activeProjectId: "project-1",
      activeChatId: "chat-1",
      sidebarData: {
        projectGroups: [createProjectGroup([{
          _id: "chat-1",
          _creationTime: 1,
          chatId: "chat-1",
          title: "Chat title",
          status: "idle",
          unread: false,
          provider: null,
          hasAutomation: false,
        }], "Project title")],
      },
    })).toBe("Kanna : Project title : Chat title")
  })

  test("keeps the full project title and truncates chat titles after 80 characters", () => {
    const longProjectTitle = "Project ".repeat(20).trim()
    const longChatTitle = "A".repeat(81)

    expect(getBrowserWindowTitle({
      appName: "Kanna",
      activeProjectId: "project-1",
      activeChatId: "chat-1",
      sidebarData: {
        projectGroups: [createProjectGroup([{
          _id: "chat-1",
          _creationTime: 1,
          chatId: "chat-1",
          title: longChatTitle,
          status: "idle",
          unread: false,
          provider: null,
          hasAutomation: false,
        }], longProjectTitle)],
      },
    })).toBe(`Kanna : ${longProjectTitle} : ${"A".repeat(80)}...`)
  })

  test("finds archived active chats by title", () => {
    expect(getBrowserWindowTitle({
      appName: "Kanna",
      activeProjectId: null,
      activeChatId: "chat-archived",
      sidebarData: {
        projectGroups: [createProjectGroup([], "Project title", [{
          _id: "chat-archived",
          _creationTime: 1,
          chatId: "chat-archived",
          title: "Archived chat",
          status: "idle",
          unread: false,
          provider: null,
          hasAutomation: false,
        }])],
      },
    })).toBe("Kanna : Project title : Archived chat")
  })

  test("falls back to the active chat project when the active project id is stale", () => {
    expect(getBrowserWindowTitle({
      appName: "Kanna",
      activeProjectId: "stale-project",
      activeChatId: "chat-1",
      sidebarData: {
        projectGroups: [createProjectGroup([{
          _id: "chat-1",
          _creationTime: 1,
          chatId: "chat-1",
          title: "Chat title",
          status: "idle",
          unread: false,
          provider: null,
          hasAutomation: false,
        }], "Project title")],
      },
    })).toBe("Kanna : Project title : Chat title")
  })

  test("keeps notification counts and omits the chat segment when no chat is active", () => {
    expect(getBrowserWindowTitle({
      appName: "Kanna",
      activeProjectId: "project-1",
      activeChatId: null,
      sidebarData: {
        projectGroups: [createProjectGroup([
          {
            _id: "chat-1",
            _creationTime: 1,
            chatId: "chat-1",
            title: "Unread",
            status: "idle",
            unread: true,
            provider: null,
            hasAutomation: false,
          },
          {
            _id: "chat-2",
            _creationTime: 2,
            chatId: "chat-2",
            title: "Waiting",
            status: "waiting_for_user",
            unread: false,
            provider: null,
            hasAutomation: false,
          },
        ], "Project title")],
      },
    })).toBe("[2] Kanna : Project title :")
  })
})

describe("chat sound helpers", () => {
  const previous = {
    projectGroups: [createProjectGroup([{
        _id: "chat-1",
        _creationTime: 1,
        chatId: "chat-1",
        title: "Read",
        status: "idle" as const,
        unread: false,
        provider: null,
        hasAutomation: false,
      }])],
  }

  test("extracts unread and waiting notification state", () => {
    const snapshot = getChatNotificationSnapshot({
      projectGroups: [createProjectGroup([
          {
            _id: "chat-1",
            _creationTime: 1,
            chatId: "chat-1",
            title: "Unread",
            status: "idle",
            unread: true,
            provider: null,
            hasAutomation: false,
          },
          {
            _id: "chat-2",
            _creationTime: 2,
            chatId: "chat-2",
            title: "Waiting",
            status: "waiting_for_user",
            unread: false,
            provider: null,
            hasAutomation: false,
          },
        ])],
    })

    expect(snapshot.unreadCount).toBe(1)
    expect([...snapshot.waitingChatIds]).toEqual(["chat-2"])
  })

  test("does not play on initial snapshot hydration", () => {
    expect(getChatSoundBurstCount(null, previous)).toBe(0)
  })

  test("plays per unread increment and new waiting chat", () => {
    expect(getChatSoundBurstCount(previous, {
      projectGroups: [createProjectGroup([
          {
            _id: "chat-1",
            _creationTime: 1,
            chatId: "chat-1",
            title: "Unread",
            status: "idle",
            unread: true,
            provider: null,
            hasAutomation: false,
          },
          {
            _id: "chat-2",
            _creationTime: 2,
            chatId: "chat-2",
            title: "Waiting",
            status: "waiting_for_user",
            unread: true,
            provider: null,
            hasAutomation: false,
          },
        ])],
    })).toBe(3)
  })

  test("does not replay for an already-waiting chat", () => {
    const current = {
      projectGroups: [createProjectGroup([{
          _id: "chat-1",
          _creationTime: 1,
          chatId: "chat-1",
          title: "Waiting",
          status: "waiting_for_user" as const,
          unread: false,
          provider: null,
          hasAutomation: false,
        }])],
    }

    expect(getChatSoundBurstCount(current, current)).toBe(0)
  })

  test("treats hidden or blurred pages as unfocused", () => {
    expect(isBrowserUnfocused({
      visibilityState: "hidden",
      hasFocus: () => true,
    })).toBe(true)
    expect(isBrowserUnfocused({
      visibilityState: "visible",
      hasFocus: () => false,
    })).toBe(true)
    expect(isBrowserUnfocused({
      visibilityState: "visible",
      hasFocus: () => true,
    })).toBe(false)
  })

  test("applies chat sound preference gates", () => {
    const focusedDoc = { visibilityState: "visible" as const, hasFocus: () => true }
    const hiddenDoc = { visibilityState: "hidden" as const, hasFocus: () => false }

    expect(shouldPlayChatSound("never", hiddenDoc)).toBe(false)
    expect(shouldPlayChatSound("always", focusedDoc)).toBe(true)
    expect(shouldPlayChatSound("unfocused", hiddenDoc)).toBe(true)
    expect(shouldPlayChatSound("unfocused", focusedDoc)).toBe(false)
  })

  test("blocks notification sounds until app settings are hydrated", () => {
    const hiddenDoc = { visibilityState: "hidden" as const, hasFocus: () => false }

    expect(shouldPlayChatNotificationSound(null, "always", hiddenDoc)).toBe(false)
    expect(shouldPlayChatNotificationSound({} as AppSettingsSnapshot, "never", hiddenDoc)).toBe(false)
    expect(shouldPlayChatNotificationSound({} as AppSettingsSnapshot, "always", hiddenDoc)).toBe(true)
  })
})
