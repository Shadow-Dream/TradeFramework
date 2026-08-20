import type {
  AgentProvider,
  ClaudeModelOptions,
  CodexModelOptions,
  ClaudeContextWindow,
  ModelOptions,
  ProviderCatalogEntry,
  ProviderModelOption,
  ServiceTier,
} from "../shared/types"
import {
  CLAUDE_CONTEXT_WINDOW_OPTIONS,
  DEFAULT_CLAUDE_MODEL_OPTIONS,
  PROVIDERS,
  deriveModelLabel,
  normalizeClaudeContextWindow,
  normalizeClaudeFastMode,
  normalizeCodexReasoningEffort,
  normalizeProviderModelId,
  isClaudeReasoningEffort,
  isCodexReasoningEffort,
  modelIdFamily,
  supportsProviderFastMode,
} from "../shared/types"

export interface ClaudeSdkModelInfo {
  value: string
  /** Canonical wire id the row's value resolves to (e.g. "sonnet" → "claude-sonnet-5"). */
  resolvedModel?: string
  displayName?: string
  description?: string
  supportsEffort?: boolean
  supportedEffortLevels?: readonly string[]
  supportsAdaptiveThinking?: boolean
  supportsFastMode?: boolean
}

function createServerProviders(): ProviderCatalogEntry[] {
  return structuredClone(PROVIDERS)
}

export const SERVER_PROVIDERS: ProviderCatalogEntry[] = createServerProviders()

export function resetServerProvidersForTests() {
  SERVER_PROVIDERS.splice(0, SERVER_PROVIDERS.length, ...createServerProviders())
}

/**
 * Rebuild the Claude picker from the SDK's supportedModels() list — the Claude
 * Rows group by the family of the model they
 * resolve to, so role rows ("default" → claude-sonnet-5) and "[1m]" window
 * variants fold into their family's entry instead of appearing as their own.
 * One entry per family, keyed by the family alias (what Kanna stores and
 * spawns with) and labeled from the resolved wire id ("Sonnet 5") — the SDK's
 * display names ("Default (recommended)", versionless "Opus") are ignored.
 * Static catalog entries seed per-family metadata the SDK doesn't report
 * (context window options, max-effort support, fable's fixed 1M window).
 * Returns true when the catalog changed (callers should broadcast).
 */
export function applyClaudeSdkModels(models: readonly ClaudeSdkModelInfo[]) {
  const claudeIndex = SERVER_PROVIDERS.findIndex((provider) => provider.id === "claude-deepseek")
  const claudeProvider = SERVER_PROVIDERS[claudeIndex]
  if (!claudeProvider) return false

  const staticModels = PROVIDERS.find((provider) => provider.id === "claude-deepseek")?.models ?? []

  const familyGroups = new Map<string, { rows: ClaudeSdkModelInfo[]; has1m: boolean }>()
  for (const row of models) {
    const wireId = row.resolvedModel ?? row.value
    const family = modelIdFamily(wireId)
    const group = familyGroups.get(family) ?? { rows: [], has1m: false }
    group.rows.push(row)
    if (row.value.includes("[1m]") || wireId.includes("[1m]")) group.has1m = true
    familyGroups.set(family, group)
  }
  if (familyGroups.size === 0) return false

  // Known families keep the static catalog's order; new ones append in SDK order.
  const orderedFamilies = [
    ...staticModels.map((option) => option.id).filter((id) => familyGroups.has(id)),
    ...[...familyGroups.keys()].filter((family) => !staticModels.some((option) => option.id === family)),
  ]

  const nextModels: ProviderModelOption[] = orderedFamilies.map((family) => {
    const group = familyGroups.get(family)!
    // Prefer the row named after the family over role rows ("default").
    const row = group.rows.find((candidate) => modelIdFamily(candidate.value) === family) ?? group.rows[0]!
    const staticOption = staticModels.find((option) => option.id === family)
    const contextWindowOptions = group.has1m
      ? [...CLAUDE_CONTEXT_WINDOW_OPTIONS]
      : staticOption?.contextWindowOptions
    return {
      id: family,
      label: deriveModelLabel(row.resolvedModel ?? row.value),
      supportsEffort: row.supportsEffort ?? staticOption?.supportsEffort ?? true,
      ...(contextWindowOptions ? { contextWindowOptions: [...contextWindowOptions] } : {}),
      ...(staticOption?.contextWindowTokens ? { contextWindowTokens: staticOption.contextWindowTokens } : {}),
      ...(staticOption?.supportsMaxReasoningEffort ? { supportsMaxReasoningEffort: true } : {}),
      ...((row.supportsFastMode ?? staticOption?.supportsFastMode) !== undefined
        ? { supportsFastMode: row.supportsFastMode ?? staticOption?.supportsFastMode }
        : {}),
    }
  })

  // The "default" role row marks the harness's recommended model.
  const defaultRow = models.find((row) => row.value === "default")
  const defaultFamily = defaultRow ? modelIdFamily(defaultRow.resolvedModel ?? defaultRow.value) : undefined
  const defaultModel = defaultFamily && familyGroups.has(defaultFamily) && defaultFamily !== "default"
    ? defaultFamily
    : claudeProvider.defaultModel

  if (
    defaultModel === claudeProvider.defaultModel
    && JSON.stringify(nextModels) === JSON.stringify(claudeProvider.models)
  ) {
    return false
  }

  SERVER_PROVIDERS.splice(claudeIndex, 1, {
    ...claudeProvider,
    defaultModel,
    models: nextModels,
  })
  return true
}

/** Seed Claude Code's picker from the configured DeepSeek aliases before the
 * first native session exists. The SDK refresh later keeps using the same
 * provider slot and can enrich these rows without exposing Anthropic models. */
export function applyClaudeDeepSeekModels(models: readonly string[], defaultModel: string) {
  const claudeIndex = SERVER_PROVIDERS.findIndex((provider) => provider.id === "claude-deepseek")
  const claudeProvider = SERVER_PROVIDERS[claudeIndex]
  if (!claudeProvider) return false
  const uniqueModels = [...new Set([defaultModel, ...models].map((model) => model.trim()).filter(Boolean))]
  if (uniqueModels.length === 0) return false
  const nextModels: ProviderModelOption[] = uniqueModels.map((model) => ({
    id: model,
    label: deriveModelLabel(model),
    supportsEffort: false,
    ...(model.includes("[1m]") ? { contextWindowTokens: 1_000_000 } : {}),
  }))
  if (claudeProvider.defaultModel === defaultModel && JSON.stringify(claudeProvider.models) === JSON.stringify(nextModels)) {
    return false
  }
  SERVER_PROVIDERS.splice(claudeIndex, 1, {
    ...claudeProvider,
    label: "Claude Code + DeepSeek",
    defaultModel,
    defaultEffort: undefined,
    models: nextModels,
  })
  return true
}

export function getServerProviderCatalog(provider: AgentProvider): ProviderCatalogEntry {
  const entry = SERVER_PROVIDERS.find((candidate) => candidate.id === provider)
  if (!entry) {
    throw new Error(`Unknown provider: ${provider}`)
  }
  return entry
}

export function normalizeServerModel(provider: AgentProvider, model?: string): string {
  const catalog = getServerProviderCatalog(provider)
  const normalizedModel = normalizeProviderModelId(provider, model, catalog.defaultModel)
  if (provider === "claude-deepseek") {
    return normalizedModel
  }
  if (catalog.models.some((candidate) => candidate.id === normalizedModel)) {
    return normalizedModel
  }
  throw new Error(`Unsupported ${catalog.label} model: ${normalizedModel}`)
}

export function normalizeClaudeModelOptions(
  model: string,
  modelOptions?: ModelOptions,
  legacyEffort?: string
): ClaudeModelOptions {
  const reasoningEffort = modelOptions?.claude?.reasoningEffort
  return {
    reasoningEffort: isClaudeReasoningEffort(reasoningEffort)
      ? reasoningEffort
      : isClaudeReasoningEffort(legacyEffort)
        ? legacyEffort
        : DEFAULT_CLAUDE_MODEL_OPTIONS.reasoningEffort,
    contextWindow: normalizeClaudeContextWindow(model, modelOptions?.claude?.contextWindow as ClaudeContextWindow | undefined),
    fastMode: normalizeClaudeFastMode(model, modelOptions?.claude?.fastMode),
  }
}

export function normalizeCodexModelOptions(
  model: string,
  modelOptions?: ModelOptions,
  legacyEffort?: string,
): CodexModelOptions {
  const reasoningEffort = modelOptions?.codex?.reasoningEffort
  return {
    reasoningEffort: normalizeCodexReasoningEffort(
      model,
      isCodexReasoningEffort(reasoningEffort) ? reasoningEffort : legacyEffort,
    ),
    // Spawn-time gating: fast mode only reaches models that support it
    // (per Codex docs: GPT-5.6/5.5/5.4 — not 5.3 Codex or Spark).
    fastMode: supportsProviderFastMode("codex-openai", model) && modelOptions?.codex?.fastMode === true,
  }
}

// Claude and Codex both express fast mode as a "fast" service tier at spawn time.
export function serviceTierFromModelOptions(modelOptions: { fastMode: boolean }): ServiceTier | undefined {
  return modelOptions.fastMode ? "fast" : undefined
}
