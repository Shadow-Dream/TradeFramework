import {
  chatModeFromFlags,
  CLAUDE_CONTEXT_WINDOW_OPTIONS,
  CLAUDE_REASONING_OPTIONS,
  getCodexReasoningOptions,
  normalizeClaudeContextWindow,
  normalizeClaudeFastMode,
  normalizeCodexModelId,
  normalizeCodexReasoningEffort,
  supportsClaudeMaxReasoningEffort,
  type AgentProvider,
  type ChatMode,
  type ChatProviderPreferences,
  type ClaudeContextWindow,
  type ProviderCatalogEntry,
  type ProviderModelOption,
} from "../../shared/types"
import { assertNever } from "../../shared/assert"
import { NEW_CHAT_COMPOSER_ID, type ComposerState } from "../stores/chatPreferencesStore"

/**
 * Canonical composer semantics — the single source of truth for what the
 * user can change about the current chat's harness/model/plan-mode and what
 * the effective selection is. ChatInput and the command palette both derive
 * from this module so their rules can never drift:
 *
 * - The backend is selectable only before Chat creation. A created Chat is
 *   permanently backend-bound; switching requires a new Chat.
 * - Models must come from the selected provider's catalog entry (which
 *   includes the server-approved runtime model catalog).
 * - Plan mode is only available when the provider supports it.
 * - Changing model normalizes dependent options (Claude context window /
 *   fast mode, Codex reasoning effort).
 */

/** Applies a model change to a composer state, normalizing dependent options. */
export function applyModelToComposerState(state: ComposerState, model: string): ComposerState {
  if (state.provider === "claude-deepseek") {
    return {
      ...state,
      model,
      modelOptions: {
        ...state.modelOptions,
        contextWindow: normalizeClaudeContextWindow(model, state.modelOptions.contextWindow),
        fastMode: normalizeClaudeFastMode(model, state.modelOptions.fastMode),
      },
    }
  } else {
    const normalizedModel = normalizeCodexModelId(model)
    return {
      ...state,
      model: normalizedModel,
      modelOptions: {
        ...state.modelOptions,
        reasoningEffort: normalizeCodexReasoningEffort(normalizedModel, state.modelOptions.reasoningEffort),
      },
    }
  }
}

/**
 * The effective composer state for a chat: when the chat's session has locked
 * a provider that differs from the stored composer state, fall back to that
 * provider's saved defaults (keeping plan mode).
 */
export function getEffectiveComposerState(
  composerState: ComposerState,
  activeProvider: AgentProvider | null,
  providerDefaults: ChatProviderPreferences
): ComposerState {
  if (!activeProvider || composerState.provider === activeProvider) {
    return composerState
  }

  switch (activeProvider) {
    case "claude-deepseek":
      return {
        provider: "claude-deepseek",
        model: providerDefaults["claude-deepseek"].model,
        modelOptions: { ...providerDefaults["claude-deepseek"].modelOptions },
        planMode: composerState.planMode,
        autoPlan: composerState.autoPlan,
      }
    case "codex-openai":
      return {
        provider: "codex-openai",
        model: providerDefaults["codex-openai"].model,
        modelOptions: { ...providerDefaults["codex-openai"].modelOptions },
        planMode: composerState.planMode,
        autoPlan: composerState.autoPlan,
      }
    default:
      return assertNever(activeProvider)
  }
}

export interface ComposerView {
  /** Chat-preferences store key: the chat id, or the shared new-chat composer. */
  composerChatId: string
  /** The provider of the chat's live/last session, when it has started. */
  activeProvider: AgentProvider | null
  /** Only new-chat composers may choose a backend. */
  canChangeProvider: boolean
  selectedProvider: AgentProvider
  /** Effective preferences — render/submit from this. */
  effectiveState: ComposerState
  /** Catalog entry for the selected provider (models incl. runtime-discovered). */
  providerConfig: ProviderCatalogEntry | undefined
  /** The only models that may be selected for this chat. */
  models: ProviderModelOption[]
  supportsPlanMode: boolean
  /** Whether the provider offers the third "Auto Plan" mode (Claude only). */
  supportsAutoPlanMode: boolean
}

export function deriveComposerView(args: {
  chatId: string | null
  activeProvider: AgentProvider | null
  availableProviders: ProviderCatalogEntry[]
  composerState: ComposerState
  providerDefaults: ChatProviderPreferences
}): ComposerView {
  const composerChatId = args.chatId ?? NEW_CHAT_COMPOSER_ID
  const effectiveState = getEffectiveComposerState(args.composerState, args.activeProvider, args.providerDefaults)
  const selectedProvider = effectiveState.provider
  const providerConfig = args.availableProviders.find((provider) => provider.id === selectedProvider)
    ?? args.availableProviders[0]

  return {
    composerChatId,
    activeProvider: args.activeProvider,
    canChangeProvider: args.activeProvider === null,
    selectedProvider,
    effectiveState,
    providerConfig,
    models: providerConfig?.models ?? [],
    supportsPlanMode: providerConfig?.supportsPlanMode ?? false,
    supportsAutoPlanMode: providerConfig?.supportsAutoPlanMode ?? false,
  }
}

/** True when the model id is selectable for this chat (present in the provider catalog). */
export function isModelSelectable(view: ComposerView, modelId: string): boolean {
  return view.models.some((model) => model.id === modelId)
}

export interface ComposerOptionChoice {
  id: string
  label: string
  description?: string
  disabled?: boolean
}

export interface ComposerOptionControls {
  /** Reasoning-effort selector, or null when the provider has none. */
  reasoning: { options: ComposerOptionChoice[]; selectedId: string | undefined } | null
  /** Claude context-window selector, or null when the model has a single window. */
  contextWindow: { options: ComposerOptionChoice[]; selectedId: ClaudeContextWindow } | null
  /** Fast-mode toggle, or null when the selected model doesn't support it. */
  fastMode: { enabled: boolean } | null
  /**
   * Mode selector, or null when the provider has no modes.
   * `options` is in display order — it is also the Shift+Tab cycle order, so
   * codex cycles between two entries and claude between three.
   */
  mode: { selected: ChatMode; options: ChatMode[] } | null
}

/** Labels/descriptions for each mode, shared by the picker and command palette. */
export const CHAT_MODE_LABELS: Record<ChatMode, { label: string; description: string }> = {
  "full-access": { label: "Full Access", description: "Execution without approval" },
  "plan": { label: "Plan Mode", description: "Review a plan before execution" },
  "auto-plan": { label: "Auto Plan", description: "The agent decides when to plan first" },
}

/**
 * Which per-model/provider option controls are available for a composer state
 * and what their current values are. This is the single availability registry
 * consumed by ChatPreferenceControls (chat input + provider defaults in
 * settings) and the command palette.
 */
export function deriveComposerOptionControls(
  state: ComposerState,
  providerConfig: ProviderCatalogEntry | undefined
): ComposerOptionControls {
  const selectedModelOption = providerConfig?.models.find((candidate) => candidate.id === state.model)
  const modelOptions = state.modelOptions as {
    reasoningEffort?: string
    contextWindow?: ClaudeContextWindow
    fastMode?: boolean
  }

  const reasoning = selectedModelOption?.supportsEffort === false ? null : {
      options: (
        state.provider === "claude-deepseek"
          ? CLAUDE_REASONING_OPTIONS.map((option) => ({
            ...option,
            disabled: option.id === "max" && !supportsClaudeMaxReasoningEffort(state.model),
          }))
          : [...getCodexReasoningOptions(state.model)]
      ) as ComposerOptionChoice[],
      selectedId: modelOptions.reasoningEffort,
    }

  const contextWindowOptions = state.provider === "claude-deepseek"
    ? (selectedModelOption?.contextWindowOptions ?? [])
    : []
  const contextWindow = contextWindowOptions.length > 1
    ? {
      options: contextWindowOptions.map((option) => ({ ...option }) as ComposerOptionChoice),
      selectedId: modelOptions.contextWindow ?? CLAUDE_CONTEXT_WINDOW_OPTIONS[0].id,
    }
    : null

  const fastMode = selectedModelOption?.supportsFastMode
    ? { enabled: Boolean(modelOptions.fastMode) }
    : null

  const modeOptions: ChatMode[] = providerConfig?.supportsAutoPlanMode
    ? ["full-access", "plan", "auto-plan"]
    : ["full-access", "plan"]
  // A composer state seeded from another harness can carry autoPlan into a
  // provider that has no Auto Plan (see getEffectiveComposerState), so clamp
  // the selection to what this provider actually offers.
  const selectedMode = chatModeFromFlags(state.planMode, state.autoPlan)
  const mode = providerConfig?.supportsPlanMode
    ? {
      selected: modeOptions.includes(selectedMode) ? selectedMode : "full-access" as ChatMode,
      options: modeOptions,
    }
    : null

  return { reasoning, contextWindow, fastMode, mode }
}
