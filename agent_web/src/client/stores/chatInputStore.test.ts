import { beforeEach, describe, expect, test } from "bun:test"
import { useChatInputStore } from "./chatInputStore"

describe("chatInputStore", () => {
  beforeEach(() => {
    useChatInputStore.setState({
      drafts: {},
      draftStartedAt: {},
      attachmentDrafts: {},
    })
  })

  test("stamps a draft when it appears, and leaves it alone as you keep typing", () => {
    // The sidebar sorts Relevant by this, so it has to hold still mid-sentence
    // — a row that climbs a place per keystroke is unreadable to type beside.
    const store = useChatInputStore.getState()
    store.setDraft("chat-1", "h")
    const startedAt = useChatInputStore.getState().draftStartedAt["chat-1"]

    expect(startedAt).toBeGreaterThan(0)
    store.setDraft("chat-1", "half a thought")
    expect(useChatInputStore.getState().draftStartedAt["chat-1"]).toBe(startedAt)
  })

  test("a draft cleared and started again is a new draft", () => {
    const store = useChatInputStore.getState()
    store.setDraft("chat-1", "first")
    const firstStartedAt = useChatInputStore.getState().draftStartedAt["chat-1"]

    store.clearDraft("chat-1")
    expect(useChatInputStore.getState().draftStartedAt["chat-1"]).toBeUndefined()

    store.setDraft("chat-1", "second")
    expect(useChatInputStore.getState().draftStartedAt["chat-1"]).toBeGreaterThanOrEqual(firstStartedAt)
  })

  test("emptying a draft drops its stamp too", () => {
    const store = useChatInputStore.getState()
    store.setDraft("chat-1", "typed")
    store.setDraft("chat-1", "")

    expect(useChatInputStore.getState().drafts["chat-1"]).toBeUndefined()
    expect(useChatInputStore.getState().draftStartedAt["chat-1"]).toBeUndefined()
  })

  test("stores attachment drafts per chat", () => {
    useChatInputStore.getState().setAttachmentDrafts("chat-1", [{
      id: "attachment-1",
      kind: "image",
      displayName: "mock.png",
      relativePath: "./.trade-agent/uploads/mock.png",
      contentUrl: "/api/projects/project-1/uploads/mock.png/content",
      mimeType: "image/png",
      size: 512,
    }])

    expect(useChatInputStore.getState().getAttachmentDrafts("chat-1")).toHaveLength(1)
    expect(useChatInputStore.getState().getAttachmentDrafts("chat-2")).toEqual([])
  })

  test("clears attachment drafts for a chat", () => {
    useChatInputStore.getState().setAttachmentDrafts("chat-1", [{
      id: "attachment-1",
      kind: "file",
      displayName: "spec.pdf",
      relativePath: "./.trade-agent/uploads/spec.pdf",
      contentUrl: "/api/projects/project-1/uploads/spec.pdf/content",
      mimeType: "application/pdf",
      size: 1234,
    }])

    useChatInputStore.getState().clearAttachmentDrafts("chat-1")
    expect(useChatInputStore.getState().getAttachmentDrafts("chat-1")).toEqual([])
  })
})
