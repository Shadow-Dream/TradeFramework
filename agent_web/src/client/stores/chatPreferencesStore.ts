import { create } from "zustand"
import {
  chatModeToFlags,
  type AgentProvider,
  type ChatMode,
  type ChatProviderPreferences,
  type ClaudeModelOptions,
  type CodexModelOptions,
  type DefaultProviderPreference,
  type ProviderPreference,
  type ProviderModelOptionsByProvider,
} from "../../shared/types"
import {
  createDefaultProviderDefaults,
  normalizeClaudePreference,
  normalizeCodexPreference,
  normalizeProviderDefaults,
  normalizeProviderPreference,
  type ProviderModelOptionsInput,
  type ProviderPreferenceInput,
} from "../../shared/provider-preferences"

export type { ChatProviderPreferences, DefaultProviderPreference, ProviderPreference }
// The normalizers live in shared/provider-preferences (also used by the server's
// settings-file normalization); re-exported here for existing importers/tests.
export {
  createDefaultProviderDefaults,
  normalizeClaudePreference,
  normalizeCodexPreference,
  normalizeProviderDefaults,
  normalizeProviderPreference,
}

export type ComposerState = {
  [TProvider in AgentProvider]: {
    provider: TProvider
    model: string
    modelOptions: ProviderModelOptionsByProvider[TProvider]
    planMode: boolean
    autoPlan: boolean
  }
}[AgentProvider]

export const NEW_CHAT_COMPOSER_ID = "__new__"

function composerStateForProvider(provider: AgentProvider, value?: ProviderPreferenceInput): ComposerState {
  // The normalizer record is keyed by provider, so the provider tag always matches
  // its normalized modelOptions shape; TS can't prove that across the union.
  return { provider, ...normalizeProviderPreference(provider, value) } as ComposerState
}

function logChatPreferences(message: string, details?: unknown) {
  if (details === undefined) {
    console.info(`[chat-preferences] ${message}`)
    return
  }

  console.info(`[chat-preferences] ${message}`, details)
}

function composerFromProviderDefaults(
  provider: AgentProvider,
  providerDefaults: ChatProviderPreferences
): ComposerState {
  return composerStateForProvider(provider, providerDefaults[provider])
}

function cloneComposerState(state: ComposerState): ComposerState {
  return { ...state, modelOptions: { ...state.modelOptions } } as ComposerState
}

function sameComposerState(left: ComposerState | undefined, right: ComposerState): boolean {
  if (!left || left.provider !== right.provider) return false
  if (left.model !== right.model || left.planMode !== right.planMode) return false
  if (left.autoPlan !== right.autoPlan) return false

  const leftOptions: Record<string, unknown> = { ...left.modelOptions }
  const rightOptions: Record<string, unknown> = { ...right.modelOptions }
  const keys = new Set([...Object.keys(leftOptions), ...Object.keys(rightOptions)])
  return [...keys].every((key) => leftOptions[key] === rightOptions[key])
}

function createComposerStateForNewChat(args: {
  defaultProvider: DefaultProviderPreference
  providerDefaults: ChatProviderPreferences
  sourceState?: ComposerState | null
}): ComposerState {
  if (args.sourceState) {
    return cloneComposerState(args.sourceState)
  }
  return composerFromProviderDefaults(args.defaultProvider, args.providerDefaults)
}

function getStoredComposerState(
  state: Pick<ChatPreferencesState, "chatStates" | "defaultProvider" | "providerDefaults">,
  chatId: string
): ComposerState {
  const existingState = state.chatStates[chatId]
  if (existingState) {
    return existingState
  }

  return createComposerStateForNewChat({
    defaultProvider: state.defaultProvider,
    providerDefaults: state.providerDefaults,
  })
}

function withChatComposerState(
  state: Pick<ChatPreferencesState, "chatStates" | "defaultProvider" | "providerDefaults">,
  chatId: string,
  transform: (composerState: ComposerState) => ComposerState
) {
  const currentComposerState = getStoredComposerState(state, chatId)
  return {
    chatStates: {
      ...state.chatStates,
      [chatId]: transform(currentComposerState),
    },
  }
}

interface ChatPreferencesState {
  defaultProvider: DefaultProviderPreference
  providerDefaults: ChatProviderPreferences
  chatStates: Record<string, ComposerState>
  setDefaultProvider: (provider: DefaultProviderPreference) => void
  syncProviderDefaults: (defaultProvider: DefaultProviderPreference, providerDefaults: ChatProviderPreferences) => void
  setProviderDefaultModel: (provider: AgentProvider, model: string) => void
  setProviderDefaultModelOptions: <TProvider extends AgentProvider>(
    provider: TProvider,
    modelOptions: Partial<ProviderModelOptionsByProvider[TProvider]>
  ) => void
  setProviderDefaultMode: (provider: AgentProvider, mode: ChatMode) => void
  getComposerState: (chatId: string) => ComposerState
  initializeComposerForChat: (chatId: string, options?: { sourceState?: ComposerState | null }) => void
  setComposerState: (chatId: string, composerState: ComposerState) => void
  setChatComposerProvider: (chatId: string, provider: AgentProvider) => void
  setChatComposerModel: (chatId: string, model: string) => void
  setChatComposerModelOptions: (
    chatId: string,
    modelOptions: Partial<ClaudeModelOptions> | Partial<CodexModelOptions>
  ) => void
  setChatComposerMode: (chatId: string, mode: ChatMode) => void
  /**
   * Clears plan mode while leaving `autoPlan` untouched — used when a plan is
   * approved, so an Auto Plan user returns to Auto Plan rather than dropping
   * to Full Access.
   */
  clearChatComposerPlanMode: (chatId: string) => void
  resetChatComposerFromProvider: (chatId: string, provider: AgentProvider) => void
}

export const useChatPreferencesStore = create<ChatPreferencesState>()(
  (set, get) => ({
    defaultProvider: "claude-deepseek",
    providerDefaults: createDefaultProviderDefaults(),
    chatStates: {},
    setDefaultProvider: (defaultProvider) => set({ defaultProvider }),
    syncProviderDefaults: (defaultProvider, providerDefaults) =>
      set((state) => {
        const oldNewChatFallback = createComposerStateForNewChat({
          defaultProvider: state.defaultProvider,
          providerDefaults: state.providerDefaults,
        })
        const nextNewChatFallback = createComposerStateForNewChat({
          defaultProvider,
          providerDefaults,
        })
        const chatStates = Object.fromEntries(
          Object.entries(state.chatStates).map(([chatId, composerState]) => [
            chatId,
            sameComposerState(composerState, oldNewChatFallback) ? nextNewChatFallback : composerState,
          ])
        )

        return {
          defaultProvider,
          providerDefaults,
          chatStates,
        }
      }),
      setProviderDefaultModel: (provider, model) =>
        set((state) => ({
          providerDefaults: {
            ...state.providerDefaults,
            [provider]: normalizeProviderPreference(provider, { ...state.providerDefaults[provider], model }),
          },
        })),
      setProviderDefaultModelOptions: (provider, modelOptions) =>
        set((state) => ({
          providerDefaults: {
            ...state.providerDefaults,
            [provider]: normalizeProviderPreference(provider, {
              ...state.providerDefaults[provider],
              modelOptions: {
                ...state.providerDefaults[provider].modelOptions,
                ...modelOptions,
              } as ProviderModelOptionsInput,
            }),
          },
        })),
      setProviderDefaultMode: (provider, mode) =>
        set((state) => ({
          providerDefaults: {
            ...state.providerDefaults,
            [provider]: {
              ...state.providerDefaults[provider],
              ...chatModeToFlags(mode, state.providerDefaults[provider].autoPlan),
            },
          },
        })),
      getComposerState: (chatId) => cloneComposerState(getStoredComposerState(get(), chatId)),
      initializeComposerForChat: (chatId, options) =>
        set((state) => {
          if (state.chatStates[chatId]) {
            return state
          }

          const composerState = createComposerStateForNewChat({
            defaultProvider: state.defaultProvider,
            providerDefaults: state.providerDefaults,
            sourceState: options?.sourceState,
          })

          logChatPreferences("initializeComposerForChat", { chatId, composerState })

          return {
            chatStates: {
              ...state.chatStates,
              [chatId]: composerState,
            },
          }
        }),
      setComposerState: (chatId, composerState) =>
        set((state) => ({
          chatStates: {
            ...state.chatStates,
            [chatId]: composerStateForProvider(composerState.provider, composerState),
          },
        })),
      setChatComposerProvider: (chatId, provider) =>
        set((state) => withChatComposerState(state, chatId, () => composerFromProviderDefaults(provider, state.providerDefaults))),
      setChatComposerModel: (chatId, model) =>
        set((state) => withChatComposerState(state, chatId, (composerState) =>
          composerStateForProvider(composerState.provider, { ...composerState, model })
        )),
      setChatComposerModelOptions: (chatId, modelOptions) =>
        set((state) => withChatComposerState(state, chatId, (composerState) =>
          composerStateForProvider(composerState.provider, {
            ...composerState,
            modelOptions: { ...composerState.modelOptions, ...modelOptions } as ProviderModelOptionsInput,
          })
        )),
      setChatComposerMode: (chatId, mode) =>
        set((state) => withChatComposerState(state, chatId, (composerState) => ({
          ...composerState,
          ...chatModeToFlags(mode, composerState.autoPlan),
        }))),
      clearChatComposerPlanMode: (chatId) =>
        set((state) => withChatComposerState(state, chatId, (composerState) => ({
          ...composerState,
          planMode: false,
        }))),
      resetChatComposerFromProvider: (chatId, provider) =>
        set((state) => ({
          chatStates: {
            ...state.chatStates,
            [chatId]: composerFromProviderDefaults(provider, state.providerDefaults),
          },
        })),
  })
)
