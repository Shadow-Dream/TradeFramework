import {
  DEFAULT_CLAUDE_MODEL_OPTIONS,
  DEFAULT_CODEX_MODEL_OPTIONS,
  isClaudeReasoningEffort,
  isCodexReasoningEffort,
  normalizeClaudeContextWindow,
  normalizeClaudeFastMode,
  normalizeClaudeModelId,
  normalizeCodexModelId,
  normalizeCodexReasoningEffort,
  supportsClaudeMaxReasoningEffort,
  type AgentProvider,
  type AppSettingsPatch,
  type ChatProviderPreferences,
  type ClaudeModelOptions,
  type CodexModelOptions,
  type ProviderPreference,
} from "./types"

// The single home for provider-preference normalization, shared by the server
// (settings file JSON in app-settings.ts) and the client (persisted composer
// state in chatPreferencesStore.ts, optimistic patches in appSettingsStore.ts).

/**
 * Loose model-options shape accepted by the provider preference normalizers.
 * Fields are `unknown` because inputs range from typed ProviderPreference
 * values to raw JSON read off disk; each normalizer validates what it uses.
 */
export type ProviderModelOptionsInput = {
  reasoningEffort?: unknown
  contextWindow?: unknown
  fastMode?: unknown
}

/**
 * Loose provider preference shape accepted by the normalizers: current
 * ProviderPreference values, persisted composer states, legacy persisted
 * shapes (with a top-level `effort`), and untrusted settings-file JSON are
 * all assignable to it.
 */
export type ProviderPreferenceInput = {
  model?: unknown
  effort?: unknown
  modelOptions?: ProviderModelOptionsInput
  planMode?: unknown
  autoPlan?: unknown
}

function modelIdFromInput(value?: ProviderPreferenceInput): string | undefined {
  return typeof value?.model === "string" ? value.model : undefined
}

export function normalizeClaudePreference(value?: ProviderPreferenceInput): ProviderPreference<ClaudeModelOptions> {
  const reasoningEffort = value?.modelOptions?.reasoningEffort
  const normalizedEffort = isClaudeReasoningEffort(reasoningEffort)
    ? reasoningEffort
    : isClaudeReasoningEffort(value?.effort)
      ? value.effort
      : DEFAULT_CLAUDE_MODEL_OPTIONS.reasoningEffort
  const model = normalizeClaudeModelId(modelIdFromInput(value))

  return {
    model,
    modelOptions: {
      reasoningEffort: !supportsClaudeMaxReasoningEffort(model) && normalizedEffort === "max" ? "high" : normalizedEffort,
      contextWindow: normalizeClaudeContextWindow(model, value?.modelOptions?.contextWindow),
      fastMode: normalizeClaudeFastMode(model, value?.modelOptions?.fastMode),
    },
    planMode: value?.planMode === true,
    // Absent (older settings files / persisted composer state) means Full
    // Access, which is the intended default — nobody is silently left in the
    // legacy Auto Plan behaviour.
    autoPlan: value?.autoPlan === true,
  }
}

export function normalizeCodexPreference(value?: ProviderPreferenceInput): ProviderPreference<CodexModelOptions> {
  const model = normalizeCodexModelId(modelIdFromInput(value))
  const reasoningEffort = value?.modelOptions?.reasoningEffort
  return {
    model,
    modelOptions: {
      reasoningEffort: normalizeCodexReasoningEffort(
        model,
        isCodexReasoningEffort(reasoningEffort) ? reasoningEffort : value?.effort,
      ),
      fastMode: typeof value?.modelOptions?.fastMode === "boolean"
        ? value.modelOptions.fastMode
        : DEFAULT_CODEX_MODEL_OPTIONS.fastMode,
    },
    planMode: value?.planMode === true,
    autoPlan: false,
  }
}

// Exhaustive provider dispatch: the record is keyed by AgentProvider, so adding a
// provider to AgentProvider forces a new entry here instead of silently falling
// through to one provider's branch.
export const PROVIDER_NORMALIZERS: {
  [TProvider in AgentProvider]: (value?: ProviderPreferenceInput) => ChatProviderPreferences[TProvider]
} = {
  "claude-deepseek": normalizeClaudePreference,
  "codex-openai": normalizeCodexPreference,
}

export function normalizeProviderPreference<TProvider extends AgentProvider>(
  provider: TProvider,
  value?: ProviderPreferenceInput
): ChatProviderPreferences[TProvider] {
  return PROVIDER_NORMALIZERS[provider](value)
}

export function normalizeProviderDefaults(
  value?: Partial<Record<AgentProvider, ProviderPreferenceInput | undefined>>
): ChatProviderPreferences {
  return {
    "claude-deepseek": normalizeClaudePreference(value?.["claude-deepseek"]),
    "codex-openai": normalizeCodexPreference(value?.["codex-openai"]),
  }
}

export function createDefaultProviderDefaults(): ChatProviderPreferences {
  // Normalizing an empty preference yields each provider's default model/options.
  return normalizeProviderDefaults()
}

/**
 * Deep-merges a providerDefaults patch over current preferences (per provider,
 * per modelOptions field). Used by the server's settings applyPatch and the
 * client's optimistic patch so both sides merge identically.
 */
export function mergeProviderDefaultsPatch(
  current: ChatProviderPreferences,
  patch: AppSettingsPatch["providerDefaults"]
): ChatProviderPreferences {
  return {
    "claude-deepseek": {
      ...current["claude-deepseek"],
      ...patch?.["claude-deepseek"],
      modelOptions: {
        ...current["claude-deepseek"].modelOptions,
        ...patch?.["claude-deepseek"]?.modelOptions,
      },
    },
    "codex-openai": {
      ...current["codex-openai"],
      ...patch?.["codex-openai"],
      modelOptions: {
        ...current["codex-openai"].modelOptions,
        ...patch?.["codex-openai"]?.modelOptions,
      },
    },
  }
}
