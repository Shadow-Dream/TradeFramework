import { afterEach, describe, expect, test } from "bun:test"
import {
  createDefaultProviderDefaults,
  NEW_CHAT_COMPOSER_ID,
  useChatPreferencesStore,
} from "./chatPreferencesStore"

const INITIAL_STATE = useChatPreferencesStore.getInitialState()

afterEach(() => {
  useChatPreferencesStore.setState(INITIAL_STATE, true)
})

describe("chat preference store", () => {
  test("starts with Claude Code + DeepSeek and the approved defaults", () => {
    const state = useChatPreferencesStore.getState()
    expect(state.defaultProvider).toBe("claude-deepseek")
    expect(state.providerDefaults).toEqual(createDefaultProviderDefaults())
    expect(state.getComposerState(NEW_CHAT_COMPOSER_ID)).toMatchObject({
      provider: "claude-deepseek",
      model: "deepseek-chat",
    })
  })

  test("initializes a new chat from the selected backend defaults", () => {
    const store = useChatPreferencesStore.getState()
    store.setDefaultProvider("codex-openai")
    useChatPreferencesStore.getState().initializeComposerForChat("chat-1")
    expect(useChatPreferencesStore.getState().getComposerState("chat-1")).toMatchObject({
      provider: "codex-openai",
      model: "gpt-5.6-sol",
    })
  })

  test("changing settings defaults does not mutate an existing chat", () => {
    const store = useChatPreferencesStore.getState()
    store.initializeComposerForChat("chat-1")
    store.setProviderDefaultModel("claude-deepseek", "deepseek-reasoner")
    expect(useChatPreferencesStore.getState().getComposerState("chat-1").model).toBe("deepseek-chat")
    expect(useChatPreferencesStore.getState().providerDefaults["claude-deepseek"].model).toBe("deepseek-reasoner")
  })

  test("switches backend only when explicitly staging a new-chat composer", () => {
    const store = useChatPreferencesStore.getState()
    store.setChatComposerProvider(NEW_CHAT_COMPOSER_ID, "codex-openai")
    const selected = useChatPreferencesStore.getState().getComposerState(NEW_CHAT_COMPOSER_ID)
    expect(selected.provider).toBe("codex-openai")
    expect(selected.model).toBe("gpt-5.6-sol")
  })

  test("changes a model and mode within a chat without changing its backend", () => {
    const store = useChatPreferencesStore.getState()
    store.initializeComposerForChat("chat-1")
    store.setChatComposerModel("chat-1", "deepseek-reasoner")
    store.setChatComposerMode("chat-1", "plan")
    expect(useChatPreferencesStore.getState().getComposerState("chat-1")).toMatchObject({
      provider: "claude-deepseek",
      model: "deepseek-reasoner",
      planMode: true,
    })
  })

  test("syncs untouched composer defaults after server settings load", () => {
    const store = useChatPreferencesStore.getState()
    store.initializeComposerForChat(NEW_CHAT_COMPOSER_ID)
    const defaults = createDefaultProviderDefaults()
    defaults["codex-openai"] = { ...defaults["codex-openai"], model: "gpt-5.5" }
    store.syncProviderDefaults("codex-openai", defaults)
    expect(useChatPreferencesStore.getState().getComposerState(NEW_CHAT_COMPOSER_ID)).toMatchObject({
      provider: "codex-openai",
      model: "gpt-5.5",
    })
  })
})
