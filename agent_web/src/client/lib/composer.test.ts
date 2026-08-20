import { describe, expect, test } from "bun:test"
import { PROVIDERS } from "../../shared/types"
import { createDefaultProviderDefaults, type ComposerState } from "../stores/chatPreferencesStore"
import {
  applyModelToComposerState,
  deriveComposerOptionControls,
  deriveComposerView,
  getEffectiveComposerState,
  isModelSelectable,
} from "./composer"

const providerDefaults = createDefaultProviderDefaults()
const deepSeekCatalog = PROVIDERS.find((provider) => provider.id === "claude-deepseek")!
const codexCatalog = PROVIDERS.find((provider) => provider.id === "codex-openai")!

function deepSeekState(overrides: Partial<Extract<ComposerState, { provider: "claude-deepseek" }>> = {}): ComposerState {
  return {
    provider: "claude-deepseek",
    model: providerDefaults["claude-deepseek"].model,
    modelOptions: { ...providerDefaults["claude-deepseek"].modelOptions },
    planMode: false,
    autoPlan: false,
    ...overrides,
  } as ComposerState
}

function codexState(overrides: Partial<Extract<ComposerState, { provider: "codex-openai" }>> = {}): ComposerState {
  return {
    provider: "codex-openai",
    model: providerDefaults["codex-openai"].model,
    modelOptions: { ...providerDefaults["codex-openai"].modelOptions },
    planMode: false,
    autoPlan: false,
    ...overrides,
  } as ComposerState
}

describe("deriveComposerView", () => {
  test("a new chat can choose either approved backend", () => {
    const view = deriveComposerView({
      chatId: null,
      activeProvider: null,
      availableProviders: PROVIDERS,
      composerState: deepSeekState(),
      providerDefaults,
    })
    expect(view.composerChatId).toBe("__new__")
    expect(view.canChangeProvider).toBe(true)
    expect(view.selectedProvider).toBe("claude-deepseek")
    expect(view.models).toBe(deepSeekCatalog.models)
  })

  test("an existing chat is locked to its native backend", () => {
    const view = deriveComposerView({
      chatId: "chat-1",
      activeProvider: "codex-openai",
      availableProviders: PROVIDERS,
      composerState: deepSeekState({ planMode: true }),
      providerDefaults,
    })
    expect(view.canChangeProvider).toBe(false)
    expect(view.selectedProvider).toBe("codex-openai")
    expect(view.effectiveState.model).toBe(providerDefaults["codex-openai"].model)
    expect(view.effectiveState.planMode).toBe(true)
    expect(view.models).toBe(codexCatalog.models)
  })

  test("only models in the bound catalog are selectable", () => {
    const view = deriveComposerView({
      chatId: null,
      activeProvider: null,
      availableProviders: PROVIDERS,
      composerState: deepSeekState(),
      providerDefaults,
    })
    expect(isModelSelectable(view, "deepseek-chat")).toBe(true)
    expect(isModelSelectable(view, "gpt-5.6-sol")).toBe(false)
    expect(isModelSelectable(view, "made-up-model")).toBe(false)
  })
})

describe("model and option controls", () => {
  test("DeepSeek changes model without exposing unsupported tuning controls", () => {
    const state = deepSeekState()
    const next = applyModelToComposerState(state, "deepseek-reasoner")
    expect(next.provider).toBe("claude-deepseek")
    expect(next.model).toBe("deepseek-reasoner")
    expect(next.modelOptions).not.toBe(state.modelOptions)
    const controls = deriveComposerOptionControls(next, deepSeekCatalog)
    expect(controls.reasoning).toBeNull()
    expect(controls.contextWindow).toBeNull()
    expect(controls.fastMode).toBeNull()
    expect(controls.mode?.options).toEqual(["full-access", "plan", "auto-plan"])
  })

  test("Codex normalizes model-specific reasoning and has no context-window control", () => {
    const next = applyModelToComposerState(codexState({
      modelOptions: { reasoningEffort: "ultra", fastMode: false },
    }), "gpt-5.6-luna")
    expect(next.provider).toBe("codex-openai")
    expect(next.model).toBe("gpt-5.6-luna")
    expect(next.modelOptions.reasoningEffort).toBe("max")
    const controls = deriveComposerOptionControls(next, codexCatalog)
    expect(controls.reasoning?.options.length).toBeGreaterThan(0)
    expect(controls.contextWindow).toBeNull()
    expect(controls.mode?.options).toEqual(["full-access", "plan"])
  })
})

describe("getEffectiveComposerState", () => {
  test("keeps matching state and replaces a mismatched backend with its defaults", () => {
    const state = deepSeekState({ planMode: true })
    expect(getEffectiveComposerState(state, "claude-deepseek", providerDefaults)).toBe(state)
    const effective = getEffectiveComposerState(state, "codex-openai", providerDefaults)
    expect(effective.provider).toBe("codex-openai")
    expect(effective.model).toBe(providerDefaults["codex-openai"].model)
    expect(effective.planMode).toBe(true)
  })
})
