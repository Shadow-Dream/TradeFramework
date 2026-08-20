import type { TradeContextV1 } from "./trade-context"
import type { UiTurnContextV1 } from "./ui-sync-protocol"
import type { ReviewArtifactV1 } from "./review-artifact"

export const STORE_VERSION = 4 as const
export const PROTOCOL_VERSION = 1 as const

export type AgentProvider = "claude-deepseek" | "codex-openai"
export type AppThemePreference = "light" | "dark" | "system"
export type ChatSoundPreference = "never" | "unfocused" | "always"
export type ChatSoundId = "blow" | "bottle" | "frog" | "funk" | "glass" | "ping" | "pop" | "purr" | "tink"
export type DefaultProviderPreference = AgentProvider

export type AttachmentKind = "image" | "file"

/**
 * A user-invocable skill/command surfaced by a harness, normalized across
 * providers for the composer's "/" menu:
 *   - claude: built-in commands, .claude/commands, .claude/skills, plugins
 *   - codex:  agent skills (skills/list)
 */
export type HarnessSkillSource = "builtin" | "command" | "skill" | "plugin" | "extension"

export interface HarnessSkill {
  /** Invoked as `/name` (for example a Claude plugin command). */
  name: string
  description: string
  argumentHint?: string
  source: HarnessSkillSource
}

export interface ChatSkillsSnapshot {
  provider: AgentProvider
  skills: HarnessSkill[]
  /** "live" = enumerated from the running harness; "filesystem" = Kanna's own disk scan (cold start / fallback). */
  origin: "live" | "filesystem"
}

export interface ChatAttachment {
  id: string
  kind: AttachmentKind
  displayName: string
  relativePath: string
  contentUrl: string
  mimeType: string
  size: number
}

export interface QueuedChatMessage {
  id: string
  content: string
  attachments: ChatAttachment[]
  createdAt: number
  provider?: AgentProvider
  model?: string
  modelOptions?: ModelOptions
  planMode?: boolean
  autoPlan?: boolean
  clientRequestId?: string
  inputDigest?: string
  context?: TradeContextV1
  uiContext?: UiTurnContextV1
  uiContextDigest?: string
}

export interface ProviderModelOption {
  id: string
  label: string
  supportsEffort: boolean
  aliases?: readonly string[]
  supportedReasoningEfforts?: readonly CodexReasoningEffortOption[]
  defaultReasoningEffort?: CodexReasoningEffort
  supportsFastMode?: boolean
  contextWindowOptions?: readonly ProviderContextWindowOption[]
  /**
   * Fixed context window (in tokens) for models that expose a single,
   * non-selectable window. Drives the input-footer meter directly, bypassing
   * the 200k/1m selector machinery. When set, `contextWindowOptions` should be
   * omitted (no picker).
   */
  contextWindowTokens?: number
  supportsMaxReasoningEffort?: boolean
}

export interface ProviderEffortOption {
  id: string
  label: string
  description?: string
}

export type CodexReasoningEffort = "minimal" | "low" | "medium" | "high" | "xhigh" | "max" | "ultra"

export interface CodexReasoningEffortOption extends ProviderEffortOption {
  id: CodexReasoningEffort
}

export interface ProviderContextWindowOption {
  id: ClaudeContextWindow
  label: string
}

export const CLAUDE_REASONING_OPTIONS = [
  { id: "low", label: "Low" },
  { id: "medium", label: "Medium" },
  { id: "high", label: "High" },
  { id: "max", label: "Max" },
] as const satisfies readonly ProviderEffortOption[]

export const CODEX_REASONING_OPTIONS = [
  { id: "low", label: "Low" },
  { id: "medium", label: "Medium" },
  { id: "high", label: "High" },
  { id: "xhigh", label: "Extra High" },
  { id: "max", label: "Max" },
  {
    id: "ultra",
    label: "Ultra",
    description: "Delegates to subagents more",
  },
] as const satisfies readonly CodexReasoningEffortOption[]

export type ClaudeReasoningEffort = (typeof CLAUDE_REASONING_OPTIONS)[number]["id"]
export type ClaudeContextWindow = "200k" | "1m"
export type ServiceTier = "fast"

export interface ClaudeModelOptions {
  reasoningEffort: ClaudeReasoningEffort
  contextWindow: ClaudeContextWindow
  fastMode: boolean
}

export interface CodexModelOptions {
  reasoningEffort: CodexReasoningEffort
  fastMode: boolean
}

export interface ProviderModelOptionsByProvider {
  "claude-deepseek": ClaudeModelOptions
  "codex-openai": CodexModelOptions
}

export interface ProviderPreference<TModelOptions> {
  model: string
  modelOptions: TModelOptions
  planMode: boolean
  /**
   * "Auto Plan": leave the EnterPlanMode tool in the harness's toolset so the
   * agent may decide on its own to plan first. Orthogonal to {@link planMode},
   * which forces the session to *start* in plan mode. Claude-only — every
   * other provider normalizes this to false (see provider-preferences.ts).
   */
  autoPlan: boolean
}

/**
 * The user-facing three-way mode, derived from the (planMode, autoPlan) pair.
 * Kept as a derived value rather than a stored field so the two server-side
 * concerns stay independent: planMode maps to the SDK's permissionMode (
 * swappable at runtime), autoPlan maps to the session's tool allowlist (fixed
 * at session creation).
 */
export type ChatMode = "full-access" | "plan" | "auto-plan"

export function chatModeFromFlags(planMode: boolean, autoPlan: boolean): ChatMode {
  // planMode wins: a user who explicitly asked to start in plan mode sees
  // "Plan Mode" even while autoPlan is held underneath.
  return planMode ? "plan" : autoPlan ? "auto-plan" : "full-access"
}

export function chatModeToFlags(
  mode: ChatMode,
  currentAutoPlan: boolean
): { planMode: boolean; autoPlan: boolean } {
  switch (mode) {
    case "plan":
      // Preserve autoPlan so approving a plan (which clears planMode) returns
      // an Auto Plan user to Auto Plan rather than dropping them to Full Access.
      return { planMode: true, autoPlan: currentAutoPlan }
    case "auto-plan":
      return { planMode: false, autoPlan: true }
    case "full-access":
      return { planMode: false, autoPlan: false }
  }
}

export type ChatProviderPreferences = {
  "claude-deepseek": ProviderPreference<ClaudeModelOptions>
  "codex-openai": ProviderPreference<CodexModelOptions>
}

export type ModelOptions = {
  claude?: Partial<ClaudeModelOptions>
  codex?: Partial<CodexModelOptions>
}

export const DEFAULT_CLAUDE_MODEL_OPTIONS = {
  reasoningEffort: "high",
  contextWindow: "1m",
  fastMode: false,
} as const satisfies ClaudeModelOptions

export const DEFAULT_CODEX_MODEL_OPTIONS = {
  reasoningEffort: "medium",
  fastMode: false,
} as const satisfies CodexModelOptions

export function isClaudeReasoningEffort(value: unknown): value is ClaudeReasoningEffort {
  return CLAUDE_REASONING_OPTIONS.some((option) => option.id === value)
}

export function isCodexReasoningEffort(value: unknown): value is CodexReasoningEffort {
  return value === "minimal" || CODEX_REASONING_OPTIONS.some((option) => option.id === value)
}

const LEGACY_CODEX_REASONING_OPTIONS = [
  { id: "minimal", label: "Minimal" },
  ...CODEX_REASONING_OPTIONS.filter((option) => option.id !== "max" && option.id !== "ultra"),
] as const satisfies readonly ProviderEffortOption[]

const GPT_5_6_REASONING_OPTIONS = [...CODEX_REASONING_OPTIONS]
const GPT_5_6_LUNA_REASONING_OPTIONS = CODEX_REASONING_OPTIONS.filter((option) => option.id !== "ultra")

export const CLAUDE_CONTEXT_WINDOW_OPTIONS = [
  { id: "1m", label: "1M" },
  { id: "200k", label: "200k" },
] as const satisfies readonly ProviderContextWindowOption[]

function titleCaseWord(value: string) {
  return value.length === 0 ? value : `${value[0]?.toUpperCase() ?? ""}${value.slice(1)}`
}

export function deriveClaudeModelLabel(modelId: string): string {
  const parts = modelId.replace(/^claude-/, "").split("-").filter(Boolean)
  if (parts.length === 0) return modelId
  return titleCaseWord(parts[0] ?? modelId)
}

// Well-known acronyms kept fully uppercase when deriving labels from model ids.
const MODEL_LABEL_ACRONYMS = new Set(["gpt", "glm"])

/**
 * Derive a display label from a bare model id when no catalog or fave record
 * names it. The id is stripped down (vendor prefix, a trailing context-window
 * marker like `[1m]`, a `:variant` suffix) then title-cased word by word. A
 * leading "claude" segment is dropped — the family name (Fable, Opus, Sonnet…)
 * already identifies the model — and a trailing build/date stamp (a long run of
 * digits) is dropped too. Consecutive bare integers collapse into a dotted
 * version so `4-8` reads as `4.8`. Nothing is hardcoded per-model.
 *
 *   lab/kimi-k2.5:nitro       → Kimi K2.5
 *   gpt-5.6-sol               → GPT 5.6 Sol
 *   openai/gpt-5.6            → GPT 5.6
 *   claude-fable-5            → Fable 5
 *   claude-opus-4-8[1m]       → Opus 4.8
 *   claude-opus-4-8           → Opus 4.8
 *   claude-haiku-4-5-20251001 → Haiku 4.5
 */
export function deriveModelLabel(modelId: string): string {
  const base = modelId.split("/").pop() ?? modelId
  // Drop a trailing context-window marker like "[1m]" and any ":variant" suffix.
  const withoutMarkers = (base.replace(/\[[^\]]*\]\s*$/, "").split(":")[0] ?? base)
  const words = withoutMarkers.split("-").filter(Boolean)
  // The provider column already names the family, so drop a leading "claude".
  if (words[0]?.toLowerCase() === "claude") words.shift()
  // Drop a trailing build/date stamp (a long run of digits, e.g. "20251001").
  while (words.length > 1 && /^\d{5,}$/.test(words[words.length - 1] ?? "")) words.pop()
  if (words.length === 0) return modelId
  const isVersionNumber = (word: string) => /^\d+$/.test(word)
  return words.reduce((label, word, index) => {
    const rendered = MODEL_LABEL_ACRONYMS.has(word.toLowerCase()) ? word.toUpperCase() : titleCaseWord(word)
    if (index === 0) return rendered
    // Consecutive bare integers form a dotted version: "opus-4-8" → "Opus 4.8".
    const joiner = isVersionNumber(word) && isVersionNumber(words[index - 1] ?? "") ? "." : " "
    return label + joiner + rendered
  }, "")
}

/**
 * Human-readable label for a model id: prefer the catalog entry's label
 * (matching by id or alias), otherwise derive one from the id. This is the
 * single naming transform shared by the chat input's model picker trigger and
 * the transcript's session rows.
 */
export function resolveModelLabel(models: ReadonlyArray<ProviderModelOption> | undefined, modelId: string): string {
  const catalogEntry = models?.find(
    (candidate) => candidate.id === modelId || candidate.aliases?.includes(modelId)
  )
  return catalogEntry?.label ?? deriveModelLabel(modelId)
}

export interface ProviderCatalogEntry {
  id: AgentProvider
  label: string
  defaultModel: string
  defaultEffort?: string
  supportsPlanMode: boolean
  /** Whether the provider offers the third "Auto Plan" mode. Claude only. */
  supportsAutoPlanMode: boolean
  models: ProviderModelOption[]
  efforts: ProviderEffortOption[]
}

export const PROVIDERS: ProviderCatalogEntry[] = [
  {
    id: "claude-deepseek",
    label: "Claude Code + DeepSeek",
    defaultModel: "deepseek-chat",
    supportsPlanMode: true,
    supportsAutoPlanMode: true,
    models: [
      {
        id: "deepseek-chat",
        label: "DeepSeek Chat",
        supportsEffort: false,
      },
      {
        id: "deepseek-reasoner",
        label: "DeepSeek Reasoner",
        supportsEffort: false,
      },
    ],
    efforts: [...CLAUDE_REASONING_OPTIONS],
  },
  {
    id: "codex-openai",
    label: "Codex + GPT",
    defaultModel: "gpt-5.6-sol",
    defaultEffort: "medium",
    supportsPlanMode: true,
    supportsAutoPlanMode: false,
    models: [
      {
        id: "gpt-5.6-sol",
        label: "GPT-5.6 Sol",
        supportsEffort: true,
        aliases: ["gpt-5.6"],
        supportedReasoningEfforts: GPT_5_6_REASONING_OPTIONS,
        defaultReasoningEffort: "medium",
        supportsFastMode: true,
      },
      {
        id: "gpt-5.6-terra",
        label: "GPT-5.6 Terra",
        supportsEffort: true,
        supportedReasoningEfforts: GPT_5_6_REASONING_OPTIONS,
        defaultReasoningEffort: "medium",
        supportsFastMode: true,
      },
      {
        id: "gpt-5.6-luna",
        label: "GPT-5.6 Luna",
        supportsEffort: true,
        supportedReasoningEfforts: GPT_5_6_LUNA_REASONING_OPTIONS,
        defaultReasoningEffort: "medium",
        supportsFastMode: true,
      },
      {
        id: "gpt-5.5",
        label: "GPT-5.5",
        supportsEffort: true,
        supportedReasoningEfforts: LEGACY_CODEX_REASONING_OPTIONS,
        defaultReasoningEffort: "medium",
        supportsFastMode: true,
      },
      {
        id: "gpt-5.4",
        label: "GPT-5.4",
        supportsEffort: true,
        supportedReasoningEfforts: LEGACY_CODEX_REASONING_OPTIONS,
        defaultReasoningEffort: "medium",
        supportsFastMode: true,
      },
      {
        id: "gpt-5.3-codex",
        label: "GPT-5.3 Codex",
        supportsEffort: true,
        aliases: ["gpt-5-codex"],
        supportedReasoningEfforts: LEGACY_CODEX_REASONING_OPTIONS,
        defaultReasoningEffort: "high",
        // Fast mode supports GPT-5.6/5.5/5.4 only (docs: /codex/speed).
        supportsFastMode: false,
      },
      {
        id: "gpt-5.3-codex-spark",
        label: "GPT-5.3 Codex Spark",
        supportsEffort: true,
        supportedReasoningEfforts: LEGACY_CODEX_REASONING_OPTIONS,
        defaultReasoningEffort: "high",
        supportsFastMode: false,
      },
    ],
    efforts: [...CODEX_REASONING_OPTIONS],
  },
]

export function getProviderCatalog(provider: AgentProvider): ProviderCatalogEntry {
  const entry = PROVIDERS.find((candidate) => candidate.id === provider)
  if (!entry) {
    throw new Error(`Unknown provider: ${provider}`)
  }
  return entry
}

function getProviderModelMatch(provider: AgentProvider, modelId?: string): ProviderModelOption | undefined {
  if (!modelId) return undefined

  return getProviderCatalog(provider).models.find((candidate) =>
    candidate.id === modelId || candidate.aliases?.includes(modelId)
  )
}

/**
 * The family word of a Claude-style model id: "claude-opus-4-8[1m]" → "opus",
 * "sonnet" → "sonnet". Used to fold version-pinned ids into the alias-keyed
 * catalog (migration of persisted ids) and to match SDK model rows to catalog
 * entries (server provider-catalog).
 */
export function modelIdFamily(modelId: string): string {
  const match = modelId.match(/^(?:claude-)?([a-z]+)(?:[-[]|$)/i)
  return match?.[1]?.toLowerCase() ?? modelId.toLowerCase()
}

export function normalizeProviderModelId(
  provider: AgentProvider,
  modelId?: string,
  fallbackModelId?: string
): string {
  const match = getProviderModelMatch(provider, modelId)
  if (match) return match.id
  if (provider === "claude-deepseek" && modelId) {
    // Deployment configuration is authoritative; the server applies its exact
    // allowlist before accepting a turn.
    const trimmed = modelId.trim()
    if (trimmed) return trimmed
  }
  return fallbackModelId ?? getProviderCatalog(provider).defaultModel
}

export function normalizeClaudeModelId(modelId?: string, fallbackModelId = "deepseek-chat"): string {
  return normalizeProviderModelId("claude-deepseek", modelId, fallbackModelId)
}

export function normalizeCodexModelId(modelId?: string, fallbackModelId = "gpt-5.6-sol"): string {
  return normalizeProviderModelId("codex-openai", modelId, fallbackModelId)
}


export function getProviderModelOption(provider: AgentProvider, modelId: string): ProviderModelOption | undefined {
  const normalizedModelId = normalizeProviderModelId(provider, modelId)
  return getProviderCatalog(provider).models.find((candidate) => candidate.id === normalizedModelId)
}

export function getClaudeModelOption(modelId: string): ProviderModelOption | undefined {
  return getProviderModelOption("claude-deepseek", modelId)
}

export function getCodexModelOption(modelId: string): ProviderModelOption | undefined {
  return getProviderModelOption("codex-openai", modelId)
}

export function getCodexReasoningOptions(modelId: string): readonly CodexReasoningEffortOption[] {
  return getCodexModelOption(modelId)?.supportedReasoningEfforts ?? CODEX_REASONING_OPTIONS
}

export function normalizeCodexReasoningEffort(
  modelId: string,
  effort?: unknown,
): CodexReasoningEffort {
  const normalizedModel = normalizeCodexModelId(modelId)
  const model = getCodexModelOption(normalizedModel)
  const supported = model?.supportedReasoningEfforts ?? CODEX_REASONING_OPTIONS

  if (effort === "minimal" && normalizedModel.startsWith("gpt-5.6-")) {
    return "low"
  }
  if (effort === "ultra" && normalizedModel === "gpt-5.6-luna") {
    return "max"
  }
  if (isCodexReasoningEffort(effort) && supported.some((option) => option.id === effort)) {
    return effort
  }

  return model?.defaultReasoningEffort ?? DEFAULT_CODEX_MODEL_OPTIONS.reasoningEffort
}

export function supportsClaudeMaxReasoningEffort(modelId: string): boolean {
  return Boolean(getClaudeModelOption(modelId)?.supportsMaxReasoningEffort)
}

export function supportsProviderFastMode(provider: AgentProvider, modelId: string): boolean {
  return Boolean(getProviderModelOption(provider, modelId)?.supportsFastMode)
}

export function supportsClaudeFastMode(modelId: string): boolean {
  return supportsProviderFastMode("claude-deepseek", modelId)
}

export function normalizeClaudeFastMode(modelId: string, fastMode?: unknown): boolean {
  return supportsClaudeFastMode(modelId) && fastMode === true
}

export function getClaudeContextWindowOptions(modelId: string): readonly ProviderContextWindowOption[] {
  return getClaudeModelOption(modelId)?.contextWindowOptions ?? []
}

// Preference normalization: models without a context window selector keep the
// default *preference* instead of a clamped value, so switching to a model
// that does support selection starts from the default rather than a stale
// clamp. The effective window is resolved at usage time below.
export function normalizeClaudeContextWindow(modelId: string, contextWindow?: unknown): ClaudeContextWindow {
  const options = getClaudeContextWindowOptions(modelId)
  if (options.length === 0) return DEFAULT_CLAUDE_MODEL_OPTIONS.contextWindow
  return options.some((option) => option.id === contextWindow)
    ? contextWindow as ClaudeContextWindow
    : DEFAULT_CLAUDE_MODEL_OPTIONS.contextWindow
}

// Usage-time resolution: models without a 1m option always run at the
// standard window regardless of the stored preference.
export function resolveClaudeContextWindow(modelId: string, contextWindow?: unknown): ClaudeContextWindow {
  const options = getClaudeContextWindowOptions(modelId)
  if (!options.some((option) => option.id === "1m")) return "200k"
  return normalizeClaudeContextWindow(modelId, contextWindow)
}

export function resolveClaudeApiModelId(modelId: string, contextWindow?: ClaudeContextWindow): string {
  return resolveClaudeContextWindow(modelId, contextWindow) === "1m" ? `${modelId}[1m]` : modelId
}

export function resolveClaudeContextWindowTokens(contextWindow: ClaudeContextWindow): number {
  switch (contextWindow) {
    case "1m":
      return 1_000_000
    case "200k":
    default:
      return 200_000
  }
}

// Effective context window (in tokens) for the input-footer meter. Models with
// a fixed window (e.g. fable) short-circuit the 200k/1m selector.
export function resolveClaudeContextWindowMaxTokens(modelId: string, contextWindow?: unknown): number {
  const fixed = getClaudeModelOption(modelId)?.contextWindowTokens
  if (typeof fixed === "number" && fixed > 0) return fixed
  return resolveClaudeContextWindowTokens(resolveClaudeContextWindow(modelId, contextWindow))
}

export type KannaStatus =
  | "idle"
  | "completed"
  | "starting"
  | "running"
  | "waiting_for_user"
  | "interrupted"
  | "failed"
  | "reauth_required"

export type AgentProjectKind = "trade-engine" | "strategy"

export interface AgentProject {
  projectId: string
  label: string
  kind: AgentProjectKind
}

export interface ProjectSummary {
  id: string
  title: string
  kind?: AgentProjectKind
  createdAt: number
  updatedAt: number
}

export interface SidebarChatRow {
  _id: string
  _creationTime: number
  chatId: string
  title: string
  status: KannaStatus
  unread: boolean
  /** User marked the chat done (board Done column). Cleared when a new turn starts. */
  done?: boolean
  /** When the chat was marked done. Set iff `done` is true. */
  doneAt?: number
  provider: AgentProvider | null
  /**
   * Model id the chat's most recent turn ran with. Absent on chats that have
   * never run a turn, and on turns recorded before the field existed — the
   * hover card names the harness alone in that case.
   */
  model?: string
  lastMessageAt?: number
  /** When the most recent turn started; with `lastTurnEndedAt`, how long it took. */
  lastTurnStartedAt?: number
  /** When the last turn ended (agent response received). Drives Review/In Progress ordering. */
  lastTurnEndedAt?: number
  /**
   * How many turns the chat has run in total — the size of the conversation,
   * as against `lastTurn*`, which describe only the most recent one. Absent on
   * chats whose turns all predate the counter.
   */
  turnCount?: number
  /**
   * When the agent last produced something — assistant text, a tool call, or a
   * tool result. Unlike `lastTurnEndedAt` this advances *during* a turn, so a
   * chat parked mid-turn (plan mode / a permission prompt, which end no turn)
   * still sorts by when it actually started asking for you rather than by when
   * you last hit send. Drives sidebar recency alongside `lastMessageAt`.
   */
  lastAgentMessageAt?: number
  /** One-line preview of the latest user prompt. */
  lastUserMessagePreview?: string
  /** One-line preview of the latest agent text message. */
  lastAgentMessagePreview?: string
  /**
   * When that preview was written. Distinct from `lastAgentMessageAt`, which
   * tool calls advance too: this dates the *words*, so a reader can tell a
   * reply to the latest prompt from one carried over from the turn before.
   */
  lastAgentMessagePreviewAt?: number
  /** Tool kind the chat is waiting on when status is waiting_for_user (e.g. "ask_user_question"). */
  pendingToolKind?: string
  errorCode?: string
  errorRetryable?: boolean
  errorMessage?: string
  /**
   * Best-effort hint that this chat is relevant to the project's uncommitted
   * work: its last turn ended after the working tree became dirty. Project-
   * scoped, so every chat active since the dirt appeared is flagged — not just
   * whichever one caused it. Drives the muted (non-pulsing) sidebar dot.
   */
  uncommittedWork?: boolean
  hasAutomation: boolean
  canFork?: boolean
}

/**
 * One file behind a chat's claim on your uncommitted work — a file it changed
 * that is *still* uncommitted. The hover card lists these as the evidence for
 * why the chat is in Relevant at all.
 *
 * Only live claims appear. Committing a file answers "why is this chat here?"
 * with "it isn't any more", so the row goes with it; a chat with nothing
 * outstanding has an empty list rather than a history of what it once did.
 *
 * Fetched per chat rather than carried on `SidebarChatRow`: a chat can hold
 * hundreds of touched paths, and the sidebar snapshot is serialized in full on
 * every broadcast to dedupe it.
 */
export interface ChatTouchedFile {
  /** Repo-root-relative, as git reports it. */
  path: string
  /**
   * Lines the chat wrote here across all its turns — not the file's current
   * diff against HEAD. Absent for binary files and for anything it changed
   * before turn-level counts were recorded; the server drops those rows rather
   * than list a filename with nothing to say.
   */
  additions?: number
  deletions?: number
}

export interface ChatTouchedFilesResult {
  /** Ranked and capped by the server; `totalCount` says what was left out. */
  files: ChatTouchedFile[]
  totalCount: number
}

export interface SidebarProjectGroup {
  groupKey: string
  title: string
  realTitle: string
  sidebarTitle?: string
  /**
   * Basename of the git repo root, absent when the project isn't in a repo.
   * Not always the project's folder name — a project can be a subdirectory of
   * its repo. Together with `branchName` this is the New Sidebar's `repo/branch`
   * label; best-effort, so treat "absent" as "not known yet", not "not a repo".
   */
  repoName?: string
  /**
   * Whether the project is in a git repo at all, once we've looked. Absent
   * means "not looked yet" — the thing `repoName` alone can't distinguish, and
   * the reason this exists: only a definite `false` should offer to `git init`
   * the folder.
   */
  hasGitRepo?: boolean
  /** Current branch of `repoName`'s repo; absent on a detached HEAD. */
  branchName?: string
  kind?: AgentProjectKind
  chats: SidebarChatRow[]
  previewChats: SidebarChatRow[]
  olderChats: SidebarChatRow[]
  archivedChats?: SidebarChatRow[]
  defaultCollapsed: boolean
}

export interface SidebarData {
  projectGroups: SidebarProjectGroup[]
}

export interface LocalProjectSummary {
  projectId: string
  source?: "saved" | "discovered"
  title: string
  kind?: AgentProjectKind
  lastOpenedAt?: number
  folderModifiedAt?: number
  chatCount: number
}

export interface LocalProjectsSnapshot {
  machine: {
    id: "local"
    displayName: string
    platform: NodeJS.Platform
  }
  projects: LocalProjectSummary[]
}

export interface AppSettingsSnapshot {
  theme: AppThemePreference
  chatSoundPreference: ChatSoundPreference
  chatSoundId: ChatSoundId
  terminal: {
    scrollbackLines: number
    minColumnWidth: number
    /** Labs: render the embedded terminal with xterm's WebGL renderer instead of the DOM one. */
    webglRenderer: boolean
  }
  defaultProvider: DefaultProviderPreference
  providerDefaults: ChatProviderPreferences
  /**
   * Setup-wizard progress. Persisted per machine (not per browser) so signing
   * in from a second browser — locally or through the cloud tunnel — never
   * re-runs onboarding that was already finished or dismissed here.
   */
  setupShown: boolean
  setupCompleted: boolean
  setupDismissed: boolean
  warning: string | null
}

export interface AppSettingsPatch {
  theme?: AppThemePreference
  chatSoundPreference?: ChatSoundPreference
  chatSoundId?: ChatSoundId
  setupShown?: boolean
  setupCompleted?: boolean
  setupDismissed?: boolean
  terminal?: Partial<AppSettingsSnapshot["terminal"]>
  defaultProvider?: DefaultProviderPreference
  providerDefaults?: {
    "claude-deepseek"?: Partial<Omit<ProviderPreference<ClaudeModelOptions>, "modelOptions">> & {
      modelOptions?: Partial<ClaudeModelOptions>
    }
    "codex-openai"?: Partial<Omit<ProviderPreference<CodexModelOptions>, "modelOptions">> & {
      modelOptions?: Partial<CodexModelOptions>
    }
  }
}

// ---------------------------------------------------------------------------
// Usage limits (subscription rate-limit utilization per harness)
// ---------------------------------------------------------------------------

/**
 * Where a usage number came from, so the UI can show honest staleness:
 * - `on_demand`: a fresh probe/read at page load or manual refresh
 * - `turn_push`: piggybacked on a turn event (may only cover one window)
 * - `cache`: loaded from the persisted last-known snapshot after a restart
 */
export type UsageLimitSource = "on_demand" | "turn_push" | "cache"

/** One rolling rate-limit window (e.g. Claude 5-hour, Codex weekly). */
export interface UsageLimitWindow {
  /** Stable id within a provider (e.g. "five_hour", "seven_day_opus", "codex:primary"). */
  id: string
  /** Human label for the bar (e.g. "5-hour session", "Weekly · Opus"). */
  label: string
  /** Percentage of the window consumed, 0–100, or null when unknown. */
  usedPercent: number | null
  /** ISO 8601 timestamp when this window resets, or null when unknown. */
  resetsAt: string | null
  /** When this specific window value was recorded (ISO 8601). */
  recordedAt: string
  /** Source of this window's value. */
  source: UsageLimitSource
}

/** Pay-per-use credit balance (Codex PAYG, Claude extra usage). */
export interface UsageLimitCredits {
  /** Human label (e.g. "Credits", "Extra usage"). */
  label: string
  /** Percentage consumed of a cap, 0–100, or null when there is no cap/unknown. */
  usedPercent: number | null
  /** Amount consumed in major currency units (dollars), or null when unknown. */
  usedAmount: number | null
  /** Spend cap in major currency units (dollars), or null when there is no cap. */
  limitAmount: number | null
  /** ISO 4217 currency code for the amounts (e.g. "USD"), or null when unknown. */
  currency: string | null
  /** Free-form fallback description when amounts aren't numeric (e.g. "Unlimited"). */
  detail: string | null
  recordedAt: string
  source: UsageLimitSource
}

export type UsageLimitStatus =
  // Windows present and meaningful.
  | "ok"
  // Provider auth doesn't expose limits (API key / Bedrock / Vertex, or logged out).
  | "unavailable"
  // Provider has no subscription limits by design.
  | "not_applicable"
  // We haven't fetched anything yet for this provider.
  | "unknown"

/** Per-provider usage snapshot rendered as a card on the Usage page. */
export interface ProviderUsageSnapshot {
  provider: AgentProvider
  status: UsageLimitStatus
  /** Plan / subscription label when known (e.g. "max", "pro", "Ultra"). */
  plan: string | null
  /** Rate-limit windows to render as horizontal bars. */
  windows: UsageLimitWindow[]
  /** Optional credit balance row. */
  credits: UsageLimitCredits | null
  /** Human explanation shown when status !== "ok" (e.g. "Sign in to Codex to see limits"). */
  detail: string | null
  /** Latest recordedAt across all windows/credits, ISO 8601, or null when never fetched. */
  updatedAt: string | null
}

export interface UsageLimitsSnapshot {
  providers: ProviderUsageSnapshot[]
}

// ---------------------------------------------------------------------------
// Provider auth: Claude Code + DeepSeek and Codex + GPT only.
// ---------------------------------------------------------------------------

export type AuthServiceId = "claude" | "codex"

export const AUTH_SERVICE_ORDER: AuthServiceId[] = ["claude", "codex"]

export const AUTH_SERVICE_LABELS: Record<AuthServiceId, string> = {
  claude: "Claude Code + DeepSeek",
  codex: "Codex + GPT",
}

export type AuthServiceStatus =
  // Not probed yet.
  | "unknown"
  | "not_installed"
  | "signed_out"
  | "signed_in"
  // Installed, but too old for the commands Kanna drives (e.g. a Claude Code
  // that predates `auth status --json`). Updating is the only way forward.
  | "outdated"
  // The probe itself failed (binary present but errored unexpectedly).
  | "error"

export type AuthLoginFlowState =
  | { phase: "idle" }
  | { phase: "starting" }
  // Codex: the user opens a link and enters a device code;
  // the server polls until the provider reports approval.
  | {
      phase: "waiting_for_approval"
      verificationUrl: string
      userCode: string | null
      startedAt: number
      expiresAt: number | null
    }
  // Approval/code received; finishing up (token exchange / re-probe).
  | { phase: "finishing" }
  | { phase: "error"; message: string; hint: string | null }

export interface AuthServiceSnapshot {
  service: AuthServiceId
  label: string
  installed: boolean
  version: string | null
  latestVersion: string | null
  updateAvailable: boolean
  authStatus: AuthServiceStatus
  /** Account identifier when the backend exposes one. */
  account: string | null
  /** Human-readable probe detail (e.g. why status is "error"). */
  statusDetail: string | null
  login: AuthLoginFlowState
  installState: "idle" | "installing" | "error"
  installError: string | null
  checkedAt: number | null
}

export interface ProviderAuthSnapshot {
  services: AuthServiceSnapshot[]
}

export function authServiceForProvider(provider: AgentProvider): AuthServiceId {
  return provider === "claude-deepseek" ? "claude" : "codex"
}

export type KeybindingAction =
  | "toggleEmbeddedTerminal"
  | "toggleRightSidebar"
  | "addSplitTerminal"
  | "jumpToSidebarChat"
  | "createChatInCurrentProject"
  | "openCommandPalette"
  | "toggleFocusMode"

export const DEFAULT_KEYBINDINGS: Record<KeybindingAction, string[]> = {
  toggleEmbeddedTerminal: ["cmd+j", "ctrl+`"],
  toggleRightSidebar: ["cmd+b", "ctrl+b"],
  addSplitTerminal: ["cmd+/", "ctrl+/"],
  jumpToSidebarChat: ["cmd+alt"],
  createChatInCurrentProject: ["cmd+alt+n"],
  openCommandPalette: ["cmd+k", "ctrl+k"],
  toggleFocusMode: ["cmd+shift+f", "ctrl+shift+f"],
}

export interface KeybindingsSnapshot {
  bindings: Record<KeybindingAction, string[]>
  warning: string | null
}

export interface McpServerInfo {
  name: string
  status: string
  error?: string
}

export interface AccountInfo {
  email?: string
  organization?: string
  subscriptionType?: string
  tokenSource?: string
  apiKeySource?: string
}

export interface AskUserQuestionOption {
  label: string
  description?: string
}

export interface AskUserQuestionItem {
  id?: string
  question: string
  header?: string
  options?: AskUserQuestionOption[]
  multiSelect?: boolean
}

export type AskUserQuestionAnswerMap = Record<string, string[]>

export interface TodoItem {
  content: string
  status: "pending" | "in_progress" | "completed"
  activeForm: string
}

interface TranscriptEntryBase {
  _id: string
  messageId?: string
  createdAt: number
  hidden?: boolean
  debugRaw?: string
  /**
   * Set only when this entry was reduced for the wire: its unbounded tool
   * payload fields were left on the server, to be fetched with
   * `chat.getToolEntries` if the row is opened.
   *
   * Never present on disk, in `getMessages()` results, or in export bundles —
   * those keep full fidelity. Absent also means "nothing was dropped", so a
   * reader can treat presence as "fetching will reveal more".
   */
  trimmed?: true
}

interface ToolCallBase<TKind extends string, TInput> {
  kind: "tool"
  toolKind: TKind
  toolName: string
  toolId: string
  input: TInput
  rawInput?: Record<string, unknown>
}

export interface AskUserQuestionToolCall
  extends ToolCallBase<"ask_user_question", { questions: AskUserQuestionItem[] }> { }

export interface ExitPlanModeToolCall
  extends ToolCallBase<"exit_plan_mode", { plan?: string; summary?: string }> { }

export interface TodoWriteToolCall
  extends ToolCallBase<"todo_write", { todos: TodoItem[] }> { }

export interface SkillToolCall
  extends ToolCallBase<"skill", { skill: string }> { }

export interface GlobToolCall
  extends ToolCallBase<"glob", { pattern: string }> { }

export interface GrepToolCall
  extends ToolCallBase<"grep", { pattern: string; outputMode?: string }> { }

export interface BashToolCall
  extends ToolCallBase<"bash", { command: string; description?: string; timeoutMs?: number; runInBackground?: boolean }> { }

export interface WebSearchToolCall
  extends ToolCallBase<"web_search", { query: string }> { }

export interface ReadFileToolCall
  extends ToolCallBase<"read_file", { filePath: string }> { }

export interface WriteFileToolCall
  extends ToolCallBase<"write_file", { filePath: string; content?: string }> { }

export interface EditFileToolCall
  extends ToolCallBase<"edit_file", { filePath: string; oldString?: string; newString?: string }> { }

export interface DeleteFileToolCall
  extends ToolCallBase<"delete_file", { filePath: string; content?: string }> { }

export interface SubagentTaskToolCall
  extends ToolCallBase<"subagent_task", { subagentType?: string }> { }

export interface McpGenericToolCall
  extends ToolCallBase<"mcp_generic", { server: string; tool: string; payload?: Record<string, unknown> }> { }

export interface UnknownToolCall
  extends ToolCallBase<"unknown_tool", { payload?: Record<string, unknown> }> { }

export type NormalizedToolCall =
  | AskUserQuestionToolCall
  | ExitPlanModeToolCall
  | TodoWriteToolCall
  | SkillToolCall
  | GlobToolCall
  | GrepToolCall
  | BashToolCall
  | WebSearchToolCall
  | ReadFileToolCall
  | WriteFileToolCall
  | EditFileToolCall
  | DeleteFileToolCall
  | SubagentTaskToolCall
  | McpGenericToolCall
  | UnknownToolCall

export interface ToolResultEntry extends TranscriptEntryBase {
  kind: "tool_result"
  toolId: string
  content: unknown
  isError?: boolean
  /**
   * `tool_use_result` lifted out of the provider's raw payload, present only
   * for the tool kinds that need it (`ask_user_question`, `exit_plan_mode`).
   *
   * Derived server-side when a page is built so the client never receives
   * `debugRaw` — which is the whole raw provider message and duplicates
   * `content`, accounting for ~66% of a typical chat snapshot. Not persisted;
   * `debugRaw` remains on disk and is fetched on demand by the raw JSON view.
   */
  structuredResult?: unknown
}

export interface UserPromptEntry extends TranscriptEntryBase {
  kind: "user_prompt"
  content: string
  attachments?: ChatAttachment[]
  steered?: boolean
  context?: TradeContextV1
  uiContext?: UiTurnContextV1
  uiContextDigest?: string
}

export interface SystemInitEntry extends TranscriptEntryBase {
  kind: "system_init"
  provider: AgentProvider
  model: string
  tools: string[]
  agents: string[]
  slashCommands: string[]
  mcpServers: McpServerInfo[]
}

export interface AccountInfoEntry extends TranscriptEntryBase {
  kind: "account_info"
  accountInfo: AccountInfo
}

export interface AssistantTextEntry extends TranscriptEntryBase {
  kind: "assistant_text"
  text: string
}

export interface ToolCallEntry extends TranscriptEntryBase {
  kind: "tool_call"
  tool: NormalizedToolCall
}

export interface ResultEntry extends TranscriptEntryBase {
  kind: "result"
  subtype: "success" | "error" | "cancelled"
  isError: boolean
  durationMs: number
  result: string
  costUsd?: number
}

export interface StatusEntry extends TranscriptEntryBase {
  kind: "status"
  status: string
}

export interface ContextWindowUsageSnapshot {
  usedTokens: number
  totalProcessedTokens?: number
  maxTokens?: number
  inputTokens?: number
  cachedInputTokens?: number
  outputTokens?: number
  reasoningOutputTokens?: number
  lastUsedTokens?: number
  lastInputTokens?: number
  lastCachedInputTokens?: number
  lastOutputTokens?: number
  lastReasoningOutputTokens?: number
  toolUses?: number
  durationMs?: number
  compactsAutomatically: boolean
}

export interface ChatDiffFile {
  path: string
  changeType: "added" | "deleted" | "modified" | "renamed"
  isUntracked: boolean
  additions: number
  deletions: number
  patchDigest: string
  mimeType?: string
  size?: number
}

export interface ChatDiffSnapshot {
  status: "unknown" | "ready" | "no_repo"
  branchName?: string
  files: ChatDiffFile[]
}

export interface ContextWindowUpdatedEntry extends TranscriptEntryBase {
  kind: "context_window_updated"
  usage: ContextWindowUsageSnapshot
}

export interface CompactBoundaryEntry extends TranscriptEntryBase {
  kind: "compact_boundary"
}

export interface CompactSummaryEntry extends TranscriptEntryBase {
  kind: "compact_summary"
  summary: string
}

export interface ContextClearedEntry extends TranscriptEntryBase {
  kind: "context_cleared"
}

export interface InterruptedEntry extends TranscriptEntryBase {
  kind: "interrupted"
  /** Why the turn stopped. Absent on provider-originated entries. */
  reason?: "user_cancelled" | "service_restart"
  /** Durable Kanna turn identity, used to make crash recovery idempotent. */
  turnId?: string
}

export interface ReviewArtifactEntry extends TranscriptEntryBase {
  kind: "review_artifact"
  artifact: ReviewArtifactV1
  contextDigest?: string
}

/**
 * Marks a same-provider session recovery. The chat's native session could not
 * be resumed (e.g. the coding-agent CLI garbage-collected its session file);
 * the next turn starts a fresh session on the same `provider` with the
 * conversation rebuilt from Kanna's saved transcript on the wire.
 */
export interface SessionRestoredEntry extends TranscriptEntryBase {
  kind: "session_restored"
  provider: AgentProvider
  /** Debug metadata about the restore context built for the new session. */
  stats?: {
    totalEntries: number
    includedEntries: number
    elidedToolResults: number
    approxTokens: number
  }
}

export type TranscriptEntry =
  | UserPromptEntry
  | SystemInitEntry
  | AccountInfoEntry
  | AssistantTextEntry
  | ToolCallEntry
  | ToolResultEntry
  | ResultEntry
  | StatusEntry
  | ContextWindowUpdatedEntry
  | CompactBoundaryEntry
  | CompactSummaryEntry
  | ContextClearedEntry
  | InterruptedEntry
  | ReviewArtifactEntry
  | SessionRestoredEntry

export interface HydratedToolCallBase<TKind extends string, TInput, TResult> {
  id: string
  messageId?: string
  hidden?: boolean
  kind: "tool"
  toolKind: TKind
  toolName: string
  toolId: string
  input: TInput
  result?: TResult
  rawResult?: unknown
  isError?: boolean
  /**
   * `_id` of the `tool_result` entry this row's result was hydrated from, or
   * undefined while the call is still pending.
   *
   * Transcript entries are append-only and immutable, so this plus the row's
   * own `id` (the `tool_call` entry) pins `input`/`result`/`rawResult` exactly.
   * Equality checks compare these ids instead of deep-comparing the payloads —
   * the results carry megabytes of tool output and the comparison runs per row
   * on every snapshot push.
   */
  resultEntryId?: string
  /**
   * The wire left this call's unbounded input fields behind; fetching the entry
   * by `id` reveals them. Absent means what is here is all there is.
   */
  inputTrimmed?: boolean
  /** As `inputTrimmed`, for the result body — fetch by `resultEntryId`. */
  resultTrimmed?: boolean
  timestamp: string
}

export interface AskUserQuestionToolResult {
  answers: AskUserQuestionAnswerMap
  discarded?: boolean
}

export interface ExitPlanModeToolResult {
  confirmed?: boolean
  clearContext?: boolean
  message?: string
  discarded?: boolean
}

/** Per-kind hydrated result payloads; kinds not listed hydrate with `unknown`. */
interface HydratedToolResultOverrides {
  ask_user_question: AskUserQuestionToolResult
  exit_plan_mode: ExitPlanModeToolResult
  read_file: ReadFileToolResult | string
}

/** Hydrated counterpart of the NormalizedToolCall member with kind `K`. */
export type HydratedToolCallOf<K extends NormalizedToolCall["toolKind"]> = HydratedToolCallBase<
  K,
  Extract<NormalizedToolCall, { toolKind: K }>["input"],
  K extends keyof HydratedToolResultOverrides ? HydratedToolResultOverrides[K] : unknown
>

export type HydratedAskUserQuestionToolCall = HydratedToolCallOf<"ask_user_question">
export type HydratedExitPlanModeToolCall = HydratedToolCallOf<"exit_plan_mode">
export type HydratedTodoWriteToolCall = HydratedToolCallOf<"todo_write">
export type HydratedSkillToolCall = HydratedToolCallOf<"skill">
export type HydratedGlobToolCall = HydratedToolCallOf<"glob">
export type HydratedGrepToolCall = HydratedToolCallOf<"grep">
export type HydratedBashToolCall = HydratedToolCallOf<"bash">
export type HydratedWebSearchToolCall = HydratedToolCallOf<"web_search">

export interface ReadFileTextBlock {
  type: "text"
  text: string
}

export interface ReadFileImageBlock {
  type: "image"
  data: string
  mimeType?: string
}

export interface ReadFileToolResult {
  content: string
  blocks?: Array<ReadFileTextBlock | ReadFileImageBlock>
}

export type HydratedReadFileToolCall = HydratedToolCallOf<"read_file">
export type HydratedWriteFileToolCall = HydratedToolCallOf<"write_file">
export type HydratedEditFileToolCall = HydratedToolCallOf<"edit_file">
export type HydratedDeleteFileToolCall = HydratedToolCallOf<"delete_file">
export type HydratedSubagentTaskToolCall = HydratedToolCallOf<"subagent_task">
export type HydratedMcpGenericToolCall = HydratedToolCallOf<"mcp_generic">
export type HydratedUnknownToolCall = HydratedToolCallOf<"unknown_tool">

/** Distributive union of HydratedToolCallOf over every NormalizedToolCall kind. */
export type HydratedToolCall = {
  [K in NormalizedToolCall["toolKind"]]: HydratedToolCallOf<K>
}[NormalizedToolCall["toolKind"]]

export type HydratedTranscriptMessage =
  | ({ kind: "user_prompt"; content: string; attachments?: ChatAttachment[]; steered?: boolean; context?: TradeContextV1; uiContext?: UiTurnContextV1; uiContextDigest?: string; id: string; messageId?: string; timestamp: string; hidden?: boolean })
  | ({ kind: "system_init"; model: string; tools: string[]; agents: string[]; slashCommands: string[]; mcpServers: McpServerInfo[]; provider: AgentProvider; id: string; messageId?: string; timestamp: string; hidden?: boolean; debugRaw?: string })
  | ({ kind: "account_info"; accountInfo: AccountInfo; id: string; messageId?: string; timestamp: string; hidden?: boolean })
  | ({ kind: "assistant_text"; text: string; id: string; messageId?: string; timestamp: string; hidden?: boolean })
  | ({ kind: "result"; success: boolean; cancelled?: boolean; result: string; durationMs: number; costUsd?: number; id: string; messageId?: string; timestamp: string; hidden?: boolean })
  | ({ kind: "status"; status: string; id: string; messageId?: string; timestamp: string; hidden?: boolean })
  | ({ kind: "context_window_updated"; usage: ContextWindowUsageSnapshot; id: string; messageId?: string; timestamp: string; hidden?: boolean })
  | ({ kind: "compact_boundary"; id: string; messageId?: string; timestamp: string; hidden?: boolean })
  | ({ kind: "compact_summary"; summary: string; id: string; messageId?: string; timestamp: string; hidden?: boolean })
  | ({ kind: "context_cleared"; id: string; messageId?: string; timestamp: string; hidden?: boolean })
  | ({ kind: "session_restored"; provider: AgentProvider; stats?: SessionRestoredEntry["stats"]; id: string; messageId?: string; timestamp: string; hidden?: boolean })
  | ({ kind: "interrupted"; reason?: InterruptedEntry["reason"]; turnId?: string; id: string; messageId?: string; timestamp: string; hidden?: boolean })
  | ({ kind: "review_artifact"; artifact: ReviewArtifactV1; contextDigest?: string; id: string; messageId?: string; timestamp: string; hidden?: boolean })
  | ({ kind: "unknown"; json: string; id: string; messageId?: string; timestamp: string; hidden?: boolean })
  | ({ id: string; messageId?: string; hidden?: boolean } & HydratedToolCall)

export interface ChatRuntime {
  chatId: string
  projectId: string
  title: string
  status: KannaStatus
  isDraining: boolean
  provider: AgentProvider | null
  /** Model used by the latest turn, so an explicit resume keeps its backend context. */
  model?: string
  planMode: boolean
  autoPlan: boolean
  sessionToken: string | null
  turnStartedAt?: number
  lastEventAt?: number
  currentTool?: string
  errorCode?: string
  errorRetryable?: boolean
  errorMessage?: string
}

export interface ChatSnapshot {
  runtime: ChatRuntime
  queuedMessages: QueuedChatMessage[]
  messages: TranscriptEntry[]
  /**
   * Absolute index of `messages[0]` in the transcript. Always 0 on a full
   * snapshot; non-zero on an incremental one, where it says where the slice
   * belongs.
   */
  startIndex: number
  /**
   * When true, `messages` extends what the client already holds rather than
   * replacing it — the entries before `startIndex` were sent earlier and are
   * unchanged.
   *
   * The transcript is the only part of this snapshot that grows without bound,
   * and it grows only at the end, so re-sending all of it on every streamed
   * entry was the dominant cost on the socket. Everything else here is small
   * and still ships whole.
   */
  incremental?: boolean
  availableProviders: ProviderCatalogEntry[]
  /**
   * The stored read position, resolved against the transcript.
   *
   * Carried inline so opening a chat is a single round trip rather than a
   * probe followed by a subscription. Null when nothing is stored or the
   * anchored entry no longer exists.
   */
  readAnchor: ResolvedChatReadAnchor | null
}

/**
 * A chat's stored read position, resolved against the current transcript.
 *
 * `messageId` is a `TranscriptEntry._id`. `atEnd` means the user was parked at
 * the bottom following the stream, so restoring should keep following rather
 * than pin to that message.
 */
export interface ResolvedChatReadAnchor {
  messageId: string
  atEnd: boolean
  /**
   * Transcript column width when recorded. `offsetFromMessage` only applies at
   * the same width, since a narrower column rewraps the message underneath it.
   */
  transcriptWidth?: number
  /** Distance below the anchored message's top, in pixels. */
  offsetFromMessage?: number
}

export interface PendingToolSnapshot {
  toolUseId: string
  toolKind: "ask_user_question" | "exit_plan_mode"
}
