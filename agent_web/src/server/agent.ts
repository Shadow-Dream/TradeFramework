import { query, type CanUseTool, type McpStdioServerConfig, type PermissionResult, type Query, type SDKUserMessage, type SlashCommand } from "@anthropic-ai/claude-agent-sdk"
import { homedir } from "node:os"
import { createHash } from "node:crypto"
import path from "node:path"
import type {
  AgentProvider,
  ChatAttachment,
  ChatSkillsSnapshot,
  CodexReasoningEffort,
  ContextWindowUsageSnapshot,
  HarnessSkill,
  ModelOptions,
  NormalizedToolCall,
  PendingToolSnapshot,
  KannaStatus,
  QueuedChatMessage,
  TranscriptEntry,
} from "../shared/types"
import { normalizeToolCall } from "../shared/tools"
import type { ClientCommand } from "../shared/protocol"
import { AsyncQueue } from "./async-queue"
import { EventStore } from "./event-store"
import type { AnalyticsReporter } from "./analytics"
import { NoopAnalyticsReporter } from "./analytics"
import { CodexAppServerManager } from "./codex-app-server"
import { type GenerateChatTitleResult, generateTitleForChatDetailed } from "./generate-title"
import type { ClaudeRateLimitInfoRaw, ClaudeUsageRaw } from "./usage-limits"
import type { HarnessEvent, HarnessToolRequest, HarnessTurn } from "./harness-types"
import {
  appendSystemMessageBlock,
  findSkillByName,
  parseSkillInvocation,
  scanClaudeSkills,
  scanCodexSkills,
  TRADE_TASK_SKILL_NAMES,
} from "./harness-skills"
import {
  buildKannaAgentCorrection,
  buildKannaAgentId,
  buildKannaAttributionInstructions,
} from "./attribution"
import {
  applyClaudeSdkModels,
  type ClaudeSdkModelInfo,
  getServerProviderCatalog,
  normalizeClaudeModelOptions,
  normalizeCodexModelOptions,
  normalizeServerModel,
  serviceTierFromModelOptions,
} from "./provider-catalog"
import { resolveClaudeApiModelId } from "../shared/types"
import { fallbackTitleFromMessage } from "./generate-title"
import { asNumber, asRecord } from "../shared/json"
import { buildRestoredMessageContent, buildSessionRestoreContext, type SessionRestoreContext } from "./session-restore"
import { checkSessionArtifact, type SessionArtifactStatus } from "./session-artifacts"
import { timestamped } from "./transcript"
import { emptyTradeContext, normalizeTradeContext, type TradeContextV1 } from "../shared/trade-context"
import type { UiTurnContextV1 } from "../shared/ui-sync-protocol"
import type { TradeMcpServerConfig } from "./trade-tool-grants"
import { findReviewArtifact } from "../shared/review-artifact"

/**
 * Tools every Claude session gets. `EnterPlanMode` is deliberately absent — it
 * is added only in "Auto Plan" (see {@link claudeToolset}); without it Claude
 * cannot put itself into plan mode unprompted.
 *
 * `ExitPlanMode` stays in the base set even in Full Access: the user can flip a
 * live session into plan mode via `setPermissionMode` without a restart, and
 * without the exit tool they would be stranded there.
 */
const CLAUDE_BASE_TOOLSET = [
  "Skill",
  "WebFetch",
  "WebSearch",
  "Task",
  "TaskOutput",
  "Bash",
  "Glob",
  "Grep",
  "Read",
  "Edit",
  "Write",
  "TodoWrite",
  "KillShell",
  "AskUserQuestion",
  "ExitPlanMode",
] as const

/**
 * The SDK's `tools` allowlist is fixed at `query()` time (there is no runtime
 * tool-swap), so a change to `autoPlan` forces a session restart — see the
 * restart condition in {@link AgentCoordinator.startClaudeTurn}.
 */
export function claudeToolset(autoPlan: boolean): string[] {
  return autoPlan ? [...CLAUDE_BASE_TOOLSET, "EnterPlanMode"] : [...CLAUDE_BASE_TOOLSET]
}

interface PendingToolRequest {
  toolUseId: string
  tool: NormalizedToolCall & { toolKind: "ask_user_question" | "exit_plan_mode" }
  resolve: (result: unknown) => void
}

interface ActiveTurn {
  chatId: string
  turnId: string
  provider: AgentProvider
  /** Prevents a closing Claude stream from mutating a replacement session. */
  claudeSessionId?: string
  turn: HarnessTurn
  claudePromptSeq?: number
  model: string
  effort?: string
  serviceTier?: "fast"
  planMode: boolean
  autoPlan: boolean
  context: TradeContextV1
  uiContext: UiTurnContextV1
  uiContextDigest: string
  status: KannaStatus
  pendingTool: PendingToolRequest | null
  postToolFollowUp: { content: string; planMode: boolean } | null
  hasFinalResult: boolean
  cancelRequested: boolean
  cancelRecorded: boolean
}

interface ClaudeSessionHandle {
  provider: "claude-deepseek"
  stream: AsyncIterable<HarnessEvent>
  getAccountInfo?: () => Promise<any>
  getUsage?: () => Promise<ClaudeUsageRaw | null>
  interrupt: () => Promise<void>
  close: () => void
  sendPrompt: (content: string) => Promise<void>
  setModel: (model: string) => Promise<void>
  setPermissionMode: (planMode: boolean) => Promise<void>
  setFastMode?: (fastMode: boolean) => Promise<void>
  supportedModels?: () => Promise<ClaudeSdkModelInfo[]>
  supportedCommands?: () => Promise<SlashCommand[]>
  setTradeMcpServer?: (server: TradeMcpServerConfig) => Promise<void>
}

interface ClaudeSessionState {
  id: string
  chatId: string
  session: ClaudeSessionHandle
  localPath: string
  model: string
  /**
   * The agent id baked into this session's system-prompt append. Frozen at
   * query() time — unlike `model`, which setModel() updates in place — so a
   * mismatch against the turn's model is exactly the drift the per-turn
   * correction exists to cover.
   */
  promptAgentId: string
  effort?: string
  serviceTier?: "fast"
  planMode: boolean
  autoPlan: boolean
  sessionToken: string | null
  accountInfoLoaded: boolean
  nextPromptSeq: number
  pendingPromptSeqs: number[]
  /**
   * Set while a cancel is settling so in-flight stream entries (emitted
   * between cancel() and the interrupt landing) don't re-register an
   * active turn via resumeBackgroundTurn. Cleared on the next result or
   * interrupted entry, and whenever a new prompt is sent.
   */
  suppressResume: boolean
  /**
   * Prompt seqs whose turn was cancelled by the user (escape or steer).
   * The SDK reports an interrupt as an error result (subtype
   * error_during_execution, usually no text); results attributed to these
   * seqs are dropped instead of persisted, since cancel already appended an
   * "interrupted" entry. Unlike suppressResume, this survives a new prompt
   * being sent immediately after the cancel (the steer path).
   */
  cancelledPromptSeqs: Set<number>
}

interface AgentCoordinatorArgs {
  store: EventStore
  /** Resolves and revalidates a server-approved project cwd. */
  resolveProjectPath?: (projectId: string) => Promise<string>
  onStateChange: (chatId?: string, options?: { immediate?: boolean }) => void
  analytics?: AnalyticsReporter
  codexManager?: CodexAppServerManager
  generateTitle?: (messageContent: string, cwd: string) => Promise<GenerateChatTitleResult>
  startClaudeSession?: (args: {
    localPath: string
    model: string
    effort?: string
    serviceTier?: "fast"
    planMode: boolean
    autoPlan: boolean
    sessionToken: string | null
    forkSession: boolean
    onToolRequest: (request: HarnessToolRequest) => Promise<unknown>
    onRateLimitEvent?: (info: ClaudeRateLimitInfoRaw) => void
    mcpServer?: TradeMcpServerConfig
  }) => Promise<ClaudeSessionHandle>
  /** Provider-scoped environment. DeepSeek credentials must never be placed
   *  on the Kanna process where Codex and unrelated subprocesses inherit it. */
  claudeEnvironment?: () => Promise<Record<string, string>>
  /** Exact DeepSeek model ids allowed by the product profile. */
  claudeModels?: readonly string[]
  issueToolGrant?: (args: {
    ownerId: string
    chatId: string
    turnId: string
    context: TradeContextV1
  }) => Promise<{ contextDigest: string; mcpServer: TradeMcpServerConfig }>
  revokeToolGrant?: (turnId: string) => Promise<void>
  /** Captured by the server at Send/Enqueue time. Browsers cannot submit or
   * override this snapshot. */
  captureUiContext?: () => UiTurnContextV1
  /**
   * Probe whether a provider's native session artifact still exists on disk.
   * Injectable so tests can force a "missing" session without touching the
   * filesystem. Defaults to the real {@link checkSessionArtifact}.
   */
  checkSessionArtifact?: (
    provider: AgentProvider,
    query: { cwd: string; sessionToken: string | null | undefined }
  ) => SessionArtifactStatus
}

interface TurnFailureClassification {
  errorCode: string
  retryable: boolean
}

/**
 * Runtime adapters expose different prose, but the persisted session contract
 * needs stable failure semantics. Keep this deliberately conservative: only
 * unmistakable credential failures become `reauth_required`; everything else
 * remains a retryable runtime failure unless it is a permanent model/config
 * rejection.
 */
export function classifyTurnFailure(message: string): TurnFailureClassification {
  const normalized = message.toLowerCase()
  if (
    /(?:\b401\b|\b403\b|unauthori[sz]ed|authentication required|not authenticated|not logged in|login required|sign[ -]?in required|invalid api key|api key.*(?:missing|expired|invalid)|credential.*(?:expired|invalid))/.test(normalized)
  ) {
    return { errorCode: "reauth_required", retryable: false }
  }
  if (
    /(?:unknown model|model.*not (?:found|available|supported)|unsupported model|invalid model)/.test(normalized)
  ) {
    return { errorCode: "model_unavailable", retryable: false }
  }
  if (
    /(?:econnrefused|econnreset|connection (?:closed|refused|reset)|broken pipe|runtime.*(?:exited|crashed|unavailable)|app-server.*(?:exited|closed)|spawn .*enoent)/.test(normalized)
  ) {
    return { errorCode: "runtime_unavailable", retryable: true }
  }
  return { errorCode: "runtime_failed", retryable: true }
}


function isClaudeSteerLoggingEnabled() {
  return process.env.TRADE_AGENT_LOG_CLAUDE_STEER === "1"
}

function logClaudeSteer(stage: string, details?: Record<string, unknown>) {
  if (!isClaudeSteerLoggingEnabled()) return
  console.log("[trade-agent/claude-steer]", JSON.stringify({
    stage,
    ...details,
  }))
}

const STEERED_MESSAGE_PREFIX = `<system-message>
The user would like to inform you of something while you continue to work. Acknowledge receipt immediately with a text response, then continue with the task at hand, incorporating the user's feedback if needed.
</system-message>`

interface SendMessageOptions {
  provider?: AgentProvider
  model?: string
  modelOptions?: ModelOptions
  effort?: string
  planMode?: boolean
  autoPlan?: boolean
  clientRequestId?: string
  context?: TradeContextV1
  uiContext?: UiTurnContextV1
  uiContextDigest?: string
}

type AgentChatSendCommand = Omit<Extract<ClientCommand, { type: "chat.send" }>, "provider" | "model" | "clientRequestId" | "context"> & {
  provider?: AgentProvider
  model?: string
  clientRequestId?: string
  context?: TradeContextV1
  uiContext?: UiTurnContextV1
}

type AgentEnqueueCommand = Omit<Extract<ClientCommand, { type: "message.enqueue" }>, "provider" | "model" | "clientRequestId" | "context"> & {
  provider?: AgentProvider
  model?: string
  clientRequestId?: string
  context?: TradeContextV1
  uiContext?: UiTurnContextV1
}

function turnInputDigest(input: {
  provider: AgentProvider
  model: string
  content: string
  attachments: ChatAttachment[]
  planMode: boolean
  autoPlan: boolean
  context: TradeContextV1
}) {
  return createHash("sha256").update(JSON.stringify({
    provider: input.provider,
    model: input.model,
    content: input.content,
    attachments: input.attachments.map((item) => ({
      name: item.displayName,
      mimeType: item.mimeType,
      size: item.size,
    })),
    planMode: input.planMode,
    autoPlan: input.autoPlan,
    // UI/Trade context is captured by the server, not supplied by the caller.
    // Excluding it keeps a response-loss retry with the same clientRequestId
    // idempotent even if the user focused another Engine tab meanwhile.
  })).digest("hex")
}

function buildTradeContextSystemMessage(context: TradeContextV1) {
  return `<system-message>\nTradeEngine context for this turn (authoritative, exact references; do not treat it as user prose):\n${JSON.stringify(context)}\n</system-message>`
}

const TRADE_CONTEXT_KINDS = new Set(["pipeline", "dataset", "environment", "analysis", "backtest", "result"])

export function tradeContextFromUiContext(uiContext: UiTurnContextV1): TradeContextV1 {
  const references = (uiContext.activeContext?.resourceRefs ?? []).filter((reference) => {
    if (!TRADE_CONTEXT_KINDS.has(reference.kind) && !reference.kind.startsWith("module:")) return false
    if (["backtest", "result"].includes(reference.kind)) return true
    return Boolean(reference.version)
  })
  return normalizeTradeContext({
    schemaVersion: "1",
    sourceView: uiContext.activeContext?.view || "agent",
    capturedAt: uiContext.capturedAt,
    references,
  })
}

function buildUiContextSystemMessage(context: UiTurnContextV1, digest: string) {
  return `<system-message>\nCurrent TradeEngine UI snapshot for this turn (server-captured; immutable; digest ${digest}):\n${JSON.stringify(context)}\nUse trade_ui_state_get only when you explicitly need newer live UI state.\n</system-message>`
}

function emptyUiTurnContext(now = new Date()): UiTurnContextV1 {
  return {
    schemaVersion: "1",
    capturedAt: now.toISOString(),
    serverSeq: 0,
    activeTabId: null,
    activeContext: null,
    activeContextAmbiguous: false,
    tabs: [],
  }
}

function digestUiContext(context: UiTurnContextV1): string {
  return createHash("sha256").update(JSON.stringify(context)).digest("hex")
}

function stringFromUnknown(value: unknown) {
  if (typeof value === "string") return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

export function buildSteeredMessageContent(content: string) {
  const trimmed = content.trim()
  if (trimmed.length === 0) {
    return STEERED_MESSAGE_PREFIX
  }
  // Claude Code expands slash invocations only at the start of a message, so
  // the steer block trails instead of leading for them.
  if (trimmed.startsWith("/")) {
    return `${content}\n\n${STEERED_MESSAGE_PREFIX}`
  }
  return `${STEERED_MESSAGE_PREFIX}\n\n${content}`
}

export interface ConcurrentProjectChat {
  title: string
}

/**
 * Wire-only notice (never stored in the transcript — same pattern as the
 * Codex skill failsafe) appended to the harness-bound prompt when
 * other chats have active turns in the same project directory.
 */
export function buildConcurrentAgentsNotice(chats: ConcurrentProjectChat[]): string | null {
  if (chats.length === 0) return null
  const lines = chats.map((chat) => `- ${chat.title}`)
  return [
    "<system-message>there are other agents working in the current directory. Don't overwrite their work if builds fail, don't fix broken tests (as they may be stale while the other agent works) and expect changes between reads.",
    "",
    "Active chats:",
    ...lines,
    "</system-message>",
  ].join("\n")
}

function escapeXmlAttribute(value: string) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("\"", "&quot;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
}




export function buildAttachmentHintText(attachments: ChatAttachment[], projectPath: string) {
  if (attachments.length === 0) return ""

  const root = path.resolve(projectPath)
  const lines = attachments.map((attachment) => {
    const relative = path.posix.normalize(attachment.relativePath.replaceAll("\\", "/")).replace(/^\.\//, "")
    if (!relative.startsWith(".trade-agent/uploads/") || relative.includes("/../") || path.posix.isAbsolute(relative)) {
      throw new Error("Attachment is outside the selected Project upload area.")
    }
    const absolute = path.resolve(root, relative)
    if (!absolute.startsWith(`${root}${path.sep}`)) {
      throw new Error("Attachment is outside the selected Project.")
    }
    return `<attachment kind="${escapeXmlAttribute(attachment.kind)}" mime_type="${escapeXmlAttribute(attachment.mimeType)}" path="${escapeXmlAttribute(absolute)}" project_path="${escapeXmlAttribute(attachment.relativePath)}" size_bytes="${attachment.size}" display_name="${escapeXmlAttribute(attachment.displayName)}" />`
  })

  return [
    "<trade-agent-attachments>",
    ...lines,
    "</trade-agent-attachments>",
  ].join("\n")
}

export function buildPromptText(content: string, attachments: ChatAttachment[], projectPath: string) {
  const attachmentHint = buildAttachmentHintText(attachments, projectPath)
  if (!attachmentHint) {
    return content.trim()
  }

  const trimmed = content.trim()
  return [
    trimmed || "Please inspect the attached files.",
    attachmentHint,
  ].join("\n\n").trim()
}

function discardedToolResult(
  tool: NormalizedToolCall & { toolKind: "ask_user_question" | "exit_plan_mode" }
) {
  if (tool.toolKind === "ask_user_question") {
    return {
      discarded: true,
      answers: {},
    }
  }

  return {
    discarded: true,
  }
}

export function normalizeClaudeUsageSnapshot(
  value: unknown,
  maxTokens?: number,
): ContextWindowUsageSnapshot | null {
  const usage = asRecord(value)
  if (!usage) return null

  const directInputTokens = asNumber(usage.input_tokens) ?? asNumber(usage.inputTokens) ?? 0
  const cacheCreationInputTokens =
    asNumber(usage.cache_creation_input_tokens) ?? asNumber(usage.cacheCreationInputTokens) ?? 0
  const cacheReadInputTokens =
    asNumber(usage.cache_read_input_tokens) ?? asNumber(usage.cacheReadInputTokens) ?? 0
  const outputTokens = asNumber(usage.output_tokens) ?? asNumber(usage.outputTokens) ?? 0
  const reasoningOutputTokens =
    asNumber(usage.reasoning_output_tokens) ?? asNumber(usage.reasoningOutputTokens)
  const toolUses = asNumber(usage.tool_uses) ?? asNumber(usage.toolUses)
  const durationMs = asNumber(usage.duration_ms) ?? asNumber(usage.durationMs)

  const inputTokens = directInputTokens + cacheCreationInputTokens + cacheReadInputTokens
  const usedTokens = inputTokens + outputTokens
  if (usedTokens <= 0) {
    return null
  }

  return {
    usedTokens,
    inputTokens,
    ...(cacheReadInputTokens > 0 ? { cachedInputTokens: cacheReadInputTokens } : {}),
    ...(outputTokens > 0 ? { outputTokens } : {}),
    ...(reasoningOutputTokens !== undefined ? { reasoningOutputTokens } : {}),
    lastUsedTokens: usedTokens,
    lastInputTokens: inputTokens,
    ...(cacheReadInputTokens > 0 ? { lastCachedInputTokens: cacheReadInputTokens } : {}),
    ...(outputTokens > 0 ? { lastOutputTokens: outputTokens } : {}),
    ...(reasoningOutputTokens !== undefined ? { lastReasoningOutputTokens: reasoningOutputTokens } : {}),
    ...(toolUses !== undefined ? { toolUses } : {}),
    ...(durationMs !== undefined ? { durationMs } : {}),
    ...(typeof maxTokens === "number" && maxTokens > 0 ? { maxTokens } : {}),
    compactsAutomatically: false,
  }
}

export function maxClaudeContextWindowFromModelUsage(modelUsage: unknown): number | undefined {
  const record = asRecord(modelUsage)
  if (!record) return undefined

  let maxContextWindow: number | undefined
  for (const value of Object.values(record)) {
    const usage = asRecord(value)
    const contextWindow = asNumber(usage?.contextWindow) ?? asNumber(usage?.context_window)
    if (contextWindow === undefined) continue
    maxContextWindow = Math.max(maxContextWindow ?? 0, contextWindow)
  }
  return maxContextWindow
}

export function normalizeClaudeContextUsage(value: unknown): { usedTokens: number; maxTokens?: number } | null {
  const record = asRecord(value)
  if (!record) return null

  const usedTokens = asNumber(record.totalTokens)
  if (usedTokens === undefined || usedTokens <= 0) return null

  const maxTokens = asNumber(record.maxTokens)
  return {
    usedTokens,
    ...(maxTokens !== undefined && maxTokens > 0 ? { maxTokens } : {}),
  }
}

function getClaudeAssistantMessageUsageId(message: any): string | null {
  if (typeof message?.message?.id === "string" && message.message.id) {
    return message.message.id
  }
  if (typeof message?.uuid === "string" && message.uuid) {
    return message.uuid
  }
  return null
}

export function normalizeClaudeStreamMessage(message: any): TranscriptEntry[] {
  // Raw SDK JSON is kept only where the client actually consumes it: the
  // system_init raw view and tool_use_result extraction on tool_result
  // entries. Stamping it on every entry doubled transcript size on disk
  // and on every snapshot push — so serialize lazily, inside only the
  // branches that keep it, never on streaming deltas.
  const messageId = typeof message.uuid === "string" ? message.uuid : undefined

  if (message.type === "system" && message.subtype === "init") {
    return [
      timestamped({
        kind: "system_init",
        messageId,
        provider: "claude-deepseek",
        model: typeof message.model === "string" ? message.model : "unknown",
        tools: Array.isArray(message.tools) ? message.tools : [],
        agents: Array.isArray(message.agents) ? message.agents : [],
        slashCommands: Array.isArray(message.slash_commands)
          ? message.slash_commands.filter((entry: string) => !entry.startsWith("._"))
          : [],
        mcpServers: Array.isArray(message.mcp_servers) ? message.mcp_servers : [],
        debugRaw: JSON.stringify(message),
      }),
    ]
  }

  if (message.type === "assistant" && Array.isArray(message.message?.content)) {
    const entries: TranscriptEntry[] = []
    for (const content of message.message.content) {
      if (content.type === "text" && typeof content.text === "string") {
        entries.push(timestamped({
          kind: "assistant_text",
          messageId,
          text: content.text,
        }))
      }
      if (content.type === "tool_use" && typeof content.name === "string" && typeof content.id === "string") {
        entries.push(timestamped({
          kind: "tool_call",
          messageId,
          tool: normalizeToolCall({
            toolName: content.name,
            toolId: content.id,
            input: (content.input ?? {}) as Record<string, unknown>,
          }),
        }))
      }
    }
    return entries
  }

  if (message.type === "user" && Array.isArray(message.message?.content)) {
    const entries: TranscriptEntry[] = []
    let debugRaw: string | undefined
    for (const content of message.message.content) {
      if (content.type === "tool_result" && typeof content.tool_use_id === "string") {
        debugRaw ??= JSON.stringify(message)
        entries.push(timestamped({
          kind: "tool_result",
          messageId,
          toolId: content.tool_use_id,
          content: content.content,
          isError: Boolean(content.is_error),
          debugRaw,
        }))
      }
      if (message.message.role === "user" && typeof message.message.content === "string") {
        entries.push(timestamped({
          kind: "compact_summary",
          messageId,
          summary: message.message.content,
        }))
      }
    }
    const artifact = findReviewArtifact(message.tool_use_result)
      ?? findReviewArtifact(message.message.content)
    if (artifact) {
      const contextDigest = typeof message.tool_use_result?.contextDigest === "string"
        ? message.tool_use_result.contextDigest
        : undefined
      entries.push(timestamped({ kind: "review_artifact", artifact, contextDigest }))
    }
    return entries
  }

  if (message.type === "result") {
    if (message.subtype === "cancelled") {
      return [timestamped({ kind: "interrupted", messageId })]
    }
    return [
      timestamped({
        kind: "result",
        messageId,
        subtype: message.is_error ? "error" : "success",
        isError: Boolean(message.is_error),
        durationMs: typeof message.duration_ms === "number" ? message.duration_ms : 0,
        result: typeof message.result === "string" ? message.result : stringFromUnknown(message.result),
        costUsd: typeof message.total_cost_usd === "number" ? message.total_cost_usd : undefined,
      }),
    ]
  }

  if (message.type === "system" && message.subtype === "status" && typeof message.status === "string") {
    return [timestamped({ kind: "status", messageId, status: message.status })]
  }

  if (message.type === "system" && message.subtype === "compact_boundary") {
    return [timestamped({ kind: "compact_boundary", messageId })]
  }

  if (message.type === "system" && message.subtype === "context_cleared") {
    return [timestamped({ kind: "context_cleared", messageId })]
  }

  if (
    message.type === "user" &&
    message.message?.role === "user" &&
    typeof message.message.content === "string" &&
    message.message.content.startsWith("This session is being continued")
  ) {
    return [timestamped({ kind: "compact_summary", messageId, summary: message.message.content })]
  }

  return []
}

async function* createClaudeHarnessStream(
  q: Query,
  hooks?: {
    onCommandsChanged?: (commands: SlashCommand[]) => void
    onRateLimitEvent?: (info: ClaudeRateLimitInfoRaw) => void
  }
): AsyncGenerator<HarnessEvent> {
  let seenAssistantUsageIds = new Set<string>()
  let latestUsageSnapshot: ContextWindowUsageSnapshot | null = null
  let lastKnownContextWindow: number | undefined

  for await (const sdkMessage of q as AsyncIterable<any>) {
    const sessionToken = typeof sdkMessage.session_id === "string" ? sdkMessage.session_id : null
    if (sessionToken) {
      yield { type: "session_token", sessionToken }
    }

    // Mid-session command/skill list changes are pushed by the SDK; per its
    // docs the payload must REPLACE any cached list (a supportedCommands()
    // re-fetch would return the stale initialize-time list).
    if (sdkMessage?.type === "system" && sdkMessage.subtype === "commands_changed" && Array.isArray(sdkMessage.commands)) {
      hooks?.onCommandsChanged?.(sdkMessage.commands as SlashCommand[])
    }

    // Subscription rate-limit utilization pushed on turns (claude.ai plans).
    if (sdkMessage?.type === "rate_limit_event" && sdkMessage.rate_limit_info) {
      hooks?.onRateLimitEvent?.(sdkMessage.rate_limit_info as ClaudeRateLimitInfoRaw)
    }

    // Per-step usage lives on the nested API message (`sdkMessage.message.usage`);
    // SDKAssistantMessage has no top-level `usage`. Skip sidechain/subagent
    // messages (`parent_tool_use_id` set) — their usage reflects the subagent's
    // own context window, not the main thread's.
    if (sdkMessage?.type === "assistant" && sdkMessage.parent_tool_use_id == null) {
      const usageId = getClaudeAssistantMessageUsageId(sdkMessage)
      const usageSnapshot = normalizeClaudeUsageSnapshot(
        sdkMessage.message?.usage ?? sdkMessage.usage,
        lastKnownContextWindow,
      )
      if (usageId && usageSnapshot && !seenAssistantUsageIds.has(usageId)) {
        seenAssistantUsageIds.add(usageId)
        latestUsageSnapshot = usageSnapshot
        yield {
          type: "transcript",
          entry: timestamped({
            kind: "context_window_updated",
            usage: usageSnapshot,
          }),
        }
      }
    }

    if (sdkMessage?.type === "result") {
      const resultContextWindow = maxClaudeContextWindowFromModelUsage(sdkMessage.modelUsage)
      if (resultContextWindow !== undefined) {
        lastKnownContextWindow = resultContextWindow
      }

      // The result message's `usage` is *cumulative* across every step of the
      // query() call (each step re-counts the whole cached context), so it is
      // never the current context length. Only surface it as
      // `totalProcessedTokens`.
      const accumulatedUsage = normalizeClaudeUsageSnapshot(
        sdkMessage.usage,
        resultContextWindow ?? lastKnownContextWindow,
      )

      // Exact /context parity: ask the CLI for the authoritative breakdown of
      // the current context window. Falls back to the last main-thread
      // per-step snapshot when the control request is unavailable (old CLI,
      // closed transport, timeout).
      const contextUsage = normalizeClaudeContextUsage(
        await Promise.race([
          q.getContextUsage().catch(() => null),
          new Promise<null>((resolve) => setTimeout(() => resolve(null), 5_000)),
        ]),
      )

      const baseUsage: ContextWindowUsageSnapshot | null = contextUsage
        ? {
            ...(latestUsageSnapshot ?? { compactsAutomatically: false }),
            usedTokens: contextUsage.usedTokens,
            ...(contextUsage.maxTokens !== undefined ? { maxTokens: contextUsage.maxTokens } : {}),
          }
        : latestUsageSnapshot

      const finalUsage = baseUsage
        ? {
            ...baseUsage,
            ...(baseUsage.maxTokens === undefined
              && typeof (resultContextWindow ?? lastKnownContextWindow) === "number"
              ? { maxTokens: resultContextWindow ?? lastKnownContextWindow }
              : {}),
            ...(accumulatedUsage && accumulatedUsage.usedTokens > baseUsage.usedTokens
              ? { totalProcessedTokens: accumulatedUsage.usedTokens }
              : {}),
          }
        : null

      if (finalUsage) {
        yield {
          type: "transcript",
          entry: timestamped({
            kind: "context_window_updated",
            usage: finalUsage,
          }),
        }
      }

      seenAssistantUsageIds = new Set<string>()
      latestUsageSnapshot = null
    }

    for (const entry of normalizeClaudeStreamMessage(sdkMessage)) {
      yield { type: "transcript", entry }
    }
  }
}


async function startClaudeSession(args: {
  localPath: string
  model: string
  effort?: string
  serviceTier?: "fast"
  planMode: boolean
  autoPlan: boolean
  sessionToken: string | null
  forkSession: boolean
    onToolRequest: (request: HarnessToolRequest) => Promise<unknown>
    onRateLimitEvent?: (info: ClaudeRateLimitInfoRaw) => void
    mcpServer?: TradeMcpServerConfig
  environment?: Record<string, string>
}): Promise<ClaudeSessionHandle> {
  const canUseTool: CanUseTool = async (toolName, input, options) => {
    if (toolName !== "AskUserQuestion" && toolName !== "ExitPlanMode") {
      return {
        behavior: "allow",
        updatedInput: input,
      }
    }

    const tool = normalizeToolCall({
      toolName,
      toolId: options.toolUseID,
      input: (input ?? {}) as Record<string, unknown>,
    })

    if (tool.toolKind !== "ask_user_question" && tool.toolKind !== "exit_plan_mode") {
      return {
        behavior: "deny",
        message: "Unsupported tool request",
      }
    }

    const result = await args.onToolRequest({ tool })

    if (tool.toolKind === "ask_user_question") {
      const record = result && typeof result === "object" ? result as Record<string, unknown> : {}
      return {
        behavior: "allow",
        updatedInput: {
          ...(tool.rawInput ?? {}),
          questions: record.questions ?? tool.input.questions,
          answers: record.answers ?? result,
        },
      } satisfies PermissionResult
    }

    const record = result && typeof result === "object" ? result as Record<string, unknown> : {}
    const confirmed = Boolean(record.confirmed)
    if (confirmed) {
      return {
        behavior: "allow",
        updatedInput: {
          ...(tool.rawInput ?? {}),
          ...record,
        },
      } satisfies PermissionResult
    }

    return {
      behavior: "deny",
      message: typeof record.message === "string"
        ? `User wants to suggest edits to the plan: ${record.message}`
        : "User wants to suggest edits to the plan before approving.",
    } satisfies PermissionResult
  }

  const promptQueue = new AsyncQueue<SDKUserMessage>()
  let promptQueueClosed = false

  const q = query({
    prompt: promptQueue,
    options: {
      cwd: args.localPath,
      model: args.model,
      effort: args.effort as "low" | "medium" | "high" | "max" | undefined,
      resume: args.sessionToken ?? undefined,
      forkSession: args.forkSession,
      permissionMode: args.planMode ? "plan" : "acceptEdits",
      canUseTool,
      tools: claudeToolset(args.autoPlan),
      settingSources: ["user", "project", "local"],
      // Append-only: the claude_code preset stays intact, Kanna's git
      // attribution rides on the end of it (see attribution.ts).
      systemPrompt: {
        type: "preset",
        preset: "claude_code",
        append: buildKannaAttributionInstructions(buildKannaAgentId("claude", args.model)),
      },
      // fastMode must go through the flag-settings layer: the CLI only allows
      // fast mode in Agent SDK sessions when flagSettings.fastMode is true,
      // and an explicit false keeps a user-level settings.json from silently
      // enabling it while the UI shows "Standard".
      settings: { enableWorkflows: true, fastMode: args.serviceTier === "fast" },
      mcpServers: args.mcpServer ? { trade_engine: args.mcpServer satisfies McpStdioServerConfig } : {},
      pathToClaudeCodeExecutable: process.env.CLAUDE_EXECUTABLE?.replace(/^~(?=\/|$)/, homedir()) || undefined,
      env: (() => {
        const {
          CLAUDECODE: _,
          ANTHROPIC_AUTH_TOKEN: _authToken,
          ANTHROPIC_API_KEY: _apiKey,
          ANTHROPIC_BASE_URL: _baseUrl,
          ...baseEnvironment
        } = process.env
        return { ...baseEnvironment, ...args.environment }
      })(),
    },
  })

  // Latest command list pushed via system/commands_changed; null until the
  // first push. supportedCommands() below prefers this over a q re-fetch.
  const commandsRef: { current: SlashCommand[] | null } = { current: null }

  return {
    provider: "claude-deepseek",
    stream: createClaudeHarnessStream(q, {
      onCommandsChanged: (commands) => {
        commandsRef.current = commands
      },
      onRateLimitEvent: args.onRateLimitEvent,
    }),
    getAccountInfo: async () => {
      try {
        return await q.accountInfo()
      } catch {
        return null
      }
    },
    getUsage: async () => {
      try {
        const anyQ = q as unknown as {
          usage_EXPERIMENTAL_MAY_CHANGE_DO_NOT_RELY_ON_THIS_API_YET?: () => Promise<ClaudeUsageRaw>
        }
        if (typeof anyQ.usage_EXPERIMENTAL_MAY_CHANGE_DO_NOT_RELY_ON_THIS_API_YET !== "function") {
          return null
        }
        return await Promise.race([
          anyQ.usage_EXPERIMENTAL_MAY_CHANGE_DO_NOT_RELY_ON_THIS_API_YET(),
          new Promise<null>((resolve) => setTimeout(() => resolve(null), 10_000)),
        ])
      } catch {
        return null
      }
    },
    interrupt: async () => {
      await q.interrupt()
    },
    sendPrompt: async (content: string) => {
      if (promptQueueClosed) {
        throw new Error("Cannot push to a closed queue")
      }
      promptQueue.push({
        type: "user",
        message: {
          role: "user",
          content,
        },
        parent_tool_use_id: null,
        session_id: args.sessionToken ?? "",
      })
    },
    setModel: async (model: string) => {
      await q.setModel(model)
    },
    setPermissionMode: async (planMode: boolean) => {
      await q.setPermissionMode(planMode ? "plan" : "acceptEdits")
    },
    setFastMode: async (fastMode: boolean) => {
      await q.applyFlagSettings({ fastMode })
    },
    supportedModels: async () => await q.supportedModels(),
    supportedCommands: async () => commandsRef.current ?? await q.supportedCommands(),
    setTradeMcpServer: async (server) => {
      const result = await q.setMcpServers({ trade_engine: server })
      if (Object.keys(result.errors).length > 0) {
        throw new Error(`TradeEngine MCP failed to reconnect: ${Object.values(result.errors).join("; ")}`)
      }
    },
    close: () => {
      promptQueueClosed = true
      promptQueue.finish()
      q.close()
    },
  }
}

export class AgentCoordinator {
  private readonly store: EventStore
  private readonly resolveProjectPath: (projectId: string) => Promise<string>
  private readonly onStateChange: (chatId?: string, options?: { immediate?: boolean }) => void
  private readonly analytics: AnalyticsReporter
  private readonly codexManager: CodexAppServerManager
  private readonly generateTitle: (messageContent: string, cwd: string) => Promise<GenerateChatTitleResult>
  private readonly startClaudeSessionFn: NonNullable<AgentCoordinatorArgs["startClaudeSession"]>
  private readonly claudeModelCatalogLocked: boolean
  private readonly claudeModels: ReadonlySet<string> | null
  private readonly checkSessionArtifactFn: NonNullable<AgentCoordinatorArgs["checkSessionArtifact"]>
  private readonly issueToolGrantFn: AgentCoordinatorArgs["issueToolGrant"]
  private readonly revokeToolGrantFn: AgentCoordinatorArgs["revokeToolGrant"]
  private readonly captureUiContextFn: () => UiTurnContextV1
  private reportBackgroundError: ((message: string) => void) | null = null
  private onClaudeRateLimit: ((info: ClaudeRateLimitInfoRaw) => void) | null = null
  readonly activeTurns = new Map<string, ActiveTurn>()
  readonly drainingStreams = new Map<string, { turn: HarnessTurn }>()
  readonly claudeSessions = new Map<string, ClaudeSessionState>()

  constructor(args: AgentCoordinatorArgs) {
    this.store = args.store
    this.resolveProjectPath = args.resolveProjectPath ?? (async () => {
      throw new Error("A server-owned Project catalog resolver is required")
    })
    this.onStateChange = args.onStateChange
    this.analytics = args.analytics ?? NoopAnalyticsReporter
    this.codexManager = args.codexManager ?? new CodexAppServerManager()
    this.generateTitle = args.generateTitle ?? generateTitleForChatDetailed
    this.claudeModelCatalogLocked = Boolean(args.claudeEnvironment)
    this.claudeModels = args.claudeModels ? new Set(args.claudeModels) : null
    this.startClaudeSessionFn = args.startClaudeSession ?? (async (sessionArgs) => startClaudeSession({
      ...sessionArgs,
      environment: await args.claudeEnvironment?.(),
    }))
    this.checkSessionArtifactFn = args.checkSessionArtifact ?? checkSessionArtifact
    this.issueToolGrantFn = args.issueToolGrant
    this.revokeToolGrantFn = args.revokeToolGrant
    this.captureUiContextFn = args.captureUiContext ?? (() => emptyUiTurnContext())
  }

  private async revokeToolGrant(turnId: string) {
    try {
      await this.revokeToolGrantFn?.(turnId)
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      this.reportBackgroundError?.(`[tool-grant] failed to revoke Turn ${turnId}: ${message}`)
    }
  }

  setBackgroundErrorReporter(report: ((message: string) => void) | null) {
    this.reportBackgroundError = report
  }

  /** Register a sink for pushed Claude rate-limit events (usage page). */
  setClaudeRateLimitListener(listener: ((info: ClaudeRateLimitInfoRaw) => void) | null) {
    this.onClaudeRateLimit = listener
  }

  /**
   * Read Claude subscription usage on demand. Reuses a live session's query
   * when one exists; otherwise spawns a short-lived probe. Returns null when
   * unavailable (no method / timeout / not a subscription session).
   */
  async fetchClaudeUsage(): Promise<ClaudeUsageRaw | null> {
    for (const state of this.claudeSessions.values()) {
      if (state.session.getUsage) {
        const usage = await state.session.getUsage()
        if (usage) return usage
      }
    }
    // DeepSeek is API-key metered and has no Claude subscription window. Do
    // not start a synthetic agent session merely to render the Usage page.
    if (this.claudeModelCatalogLocked) return null
    let probe: ClaudeSessionHandle | null = null
    try {
      const cwd = await this.resolveProjectPath("trade-engine")
      probe = await this.startClaudeSessionFn({
        localPath: cwd,
        // Model choice is irrelevant for the usage read; use the catalog default.
        model: "sonnet",
        planMode: false,
        autoPlan: false,
        sessionToken: null,
        forkSession: false,
        onToolRequest: async () => ({}),
      })
      return (await probe.getUsage?.()) ?? null
    } catch {
      return null
    } finally {
      probe?.close()
    }
  }

  /** Read Codex account rate limits on demand (reuses a live app-server or probes). */
  async fetchCodexRateLimits() {
    return await this.codexManager.readAccountRateLimits(await this.resolveProjectPath("trade-engine"))
  }

  getCodexManager() {
    return this.codexManager
  }

  getActiveStatuses() {
    const statuses = new Map<string, KannaStatus>()
    for (const [chatId, turn] of this.activeTurns.entries()) {
      statuses.set(chatId, turn.status)
    }
    return statuses
  }

  getPendingTool(chatId: string): PendingToolSnapshot | null {
    const pending = this.activeTurns.get(chatId)?.pendingTool
    if (!pending) return null
    return { toolUseId: pending.toolUseId, toolKind: pending.tool.toolKind }
  }

  getDrainingChatIds(): Set<string> {
    return new Set(this.drainingStreams.keys())
  }

  private emitStateChange(chatId?: string, options?: { immediate?: boolean }) {
    this.onStateChange(chatId, options)
  }

  private async recordTurnFailure(chatId: string, turnId: string, message: string) {
    const failure = classifyTurnFailure(message)
    return await this.store.recordTurnFailed(
      chatId,
      turnId,
      message,
      failure.errorCode,
      failure.retryable,
    )
  }

  private refreshClaudeModelCatalog(session: ClaudeSessionHandle) {
    // DeepSeek's configured aliases are the product allowlist. Claude Code's
    // generic supportedModels() response may contain Anthropic aliases and
    // must not replace that catalog.
    if (this.claudeModelCatalogLocked) return
    if (!session.supportedModels) return
    void session.supportedModels()
      .then((models) => {
        if (applyClaudeSdkModels(models)) {
          this.emitStateChange(undefined, { immediate: true })
        }
      })
      .catch((error) => {
        const message = error instanceof Error ? error.message : String(error)
        this.reportBackgroundError?.(`[claude-models] failed to refresh Claude model catalog: ${message}`)
      })
  }

  async stopDraining(chatId: string) {
    const draining = this.drainingStreams.get(chatId)
    if (!draining) return
    draining.turn.close()
    this.drainingStreams.delete(chatId)
    this.emitStateChange(chatId)
  }

  async closeChat(chatId: string) {
    await this.stopDraining(chatId)
    const claudeSession = this.claudeSessions.get(chatId)
    if (claudeSession) {
      claudeSession.session.close()
      this.claudeSessions.delete(chatId)
    }
    this.emitStateChange(chatId)
  }

  /** Stop every native runtime without replaying unfinished turns. */
  async close() {
    for (const active of [...this.activeTurns.values()]) {
      active.cancelRequested = true
      active.cancelRecorded = true
      active.hasFinalResult = true
      this.activeTurns.delete(active.chatId)
      const claudeSession = this.claudeSessions.get(active.chatId)
      if (claudeSession) claudeSession.suppressResume = true
      await this.store.appendMessage(active.chatId, timestamped({
        kind: "interrupted",
        reason: "service_restart",
        turnId: active.turnId,
      }))
      await this.store.recordTurnInterrupted(active.chatId, active.turnId, "service_restart")
      await this.revokeToolGrant(active.turnId)
      try { await active.turn.interrupt() } catch { /* process shutdown is best effort */ }
      active.turn.close()
    }
    for (const draining of this.drainingStreams.values()) draining.turn.close()
    this.drainingStreams.clear()
    for (const session of this.claudeSessions.values()) session.session.close()
    this.claudeSessions.clear()
    this.codexManager.stopAll()
  }

  /** An explicit provider selects a new session's backend. Once persisted,
   * startTurnForChat enforces that backend as immutable. */
  private resolveProvider(options: SendMessageOptions, currentProvider: AgentProvider | null) {
    return options.provider ?? currentProvider ?? "claude-deepseek"
  }

  private getProviderSettings(provider: AgentProvider, options: SendMessageOptions) {
    const catalog = getServerProviderCatalog(provider)
    if (provider === "claude-deepseek") {
      const model = normalizeServerModel(provider, options.model)
      if (this.claudeModels && !this.claudeModels.has(model)) {
        throw new Error(`Unsupported Claude Code + DeepSeek model: ${model}`)
      }
      const modelOptions = normalizeClaudeModelOptions(model, options.modelOptions, options.effort)
      return {
        model: resolveClaudeApiModelId(model, modelOptions.contextWindow),
        effort: modelOptions.reasoningEffort,
        serviceTier: serviceTierFromModelOptions(modelOptions),
        planMode: catalog.supportsPlanMode ? Boolean(options.planMode) : false,
        autoPlan: catalog.supportsAutoPlanMode ? Boolean(options.autoPlan) : false,
      }
    }

    const model = normalizeServerModel(provider, options.model)
    const modelOptions = normalizeCodexModelOptions(model, options.modelOptions, options.effort)
    return {
      model,
      effort: modelOptions.reasoningEffort,
      serviceTier: serviceTierFromModelOptions(modelOptions),
      planMode: catalog.supportsPlanMode ? Boolean(options.planMode) : false,
      autoPlan: catalog.supportsAutoPlanMode ? Boolean(options.autoPlan) : false,
    }
  }

  private async enqueueMessage(chatId: string, content: string, attachments: ChatAttachment[], options?: SendMessageOptions) {
    options ??= {}
    const clientRequestId = options.clientRequestId ?? crypto.randomUUID()
    const chat = this.store.requireChat(chatId)
    const provider = this.resolveProvider(options, chat.provider)
    const settings = this.getProviderSettings(provider, options)
    const uiContext = options.uiContext ?? this.captureUiContextFn()
    const uiContextDigest = options.uiContextDigest ?? digestUiContext(uiContext)
    const context = normalizeTradeContext(options.context ?? tradeContextFromUiContext(uiContext))
    const inputDigest = turnInputDigest({
      provider,
      model: settings.model,
      content,
      attachments,
      planMode: settings.planMode,
      autoPlan: settings.autoPlan,
      context,
    })
    const existing = this.store.getQueuedMessages(chatId)
      .find((item) => item.clientRequestId === clientRequestId)
    if (existing) {
      if (existing.inputDigest !== inputDigest) {
        throw new Error("clientRequestId was already used with different turn input.")
      }
      return existing
    }
    const queued = await this.store.enqueueMessage(chatId, {
      content,
      attachments,
      provider: options?.provider,
      model: options?.model,
      modelOptions: options?.modelOptions,
      planMode: options?.planMode,
      autoPlan: options?.autoPlan,
      clientRequestId,
      inputDigest,
      context,
      uiContext,
      uiContextDigest,
    })
    this.emitStateChange(chatId)
    return queued
  }

  private async dequeueAndStartQueuedMessage(chatId: string, queuedMessage: QueuedChatMessage, options?: { steered?: boolean }) {
    await this.store.removeQueuedMessage(chatId, queuedMessage.id)
    const chat = this.store.requireChat(chatId)
    const provider = this.resolveProvider(queuedMessage, chat.provider)
    const settings = this.getProviderSettings(provider, queuedMessage)
    const uiContext = queuedMessage.uiContext ?? emptyUiTurnContext()
    await this.startTurnForChat({
      chatId,
      provider,
      content: queuedMessage.content,
      attachments: queuedMessage.attachments,
      model: settings.model,
      effort: settings.effort,
      serviceTier: settings.serviceTier,
      planMode: settings.planMode,
      autoPlan: settings.autoPlan,
      appendUserPrompt: true,
      steered: options?.steered,
      clientRequestId: queuedMessage.clientRequestId ?? crypto.randomUUID(),
      inputDigest: queuedMessage.inputDigest ?? crypto.randomUUID().replaceAll("-", ""),
      context: normalizeTradeContext(queuedMessage.context ?? emptyTradeContext()),
      uiContext,
      uiContextDigest: queuedMessage.uiContextDigest ?? digestUiContext(uiContext),
    })
  }

  private async maybeStartNextQueuedMessage(chatId: string) {
    if (this.activeTurns.has(chatId)) return false
    const nextQueuedMessage = typeof this.store.getQueuedMessages === "function"
      ? this.store.getQueuedMessages(chatId)[0]
      : undefined
    if (!nextQueuedMessage) return false
    await this.dequeueAndStartQueuedMessage(chatId, nextQueuedMessage)
    return true
  }

  /**
   * Other active chats bound to the same server-owned Project. Draining
   * streams are excluded because those turns are no longer doing new work.
   */
  private collectConcurrentProjectChats(chatId: string, projectId: string): ConcurrentProjectChat[] {
    const chats: ConcurrentProjectChat[] = []
    for (const activeChatId of this.activeTurns.keys()) {
      if (activeChatId === chatId) continue
      const chat = this.store.getChat(activeChatId)
      if (!chat) continue
      if (chat.projectId !== projectId) continue
      chats.push({ title: chat.title })
    }
    return chats
  }

  /**
   * Decide whether this chat's native provider session is gone and should be
   * rebuilt from the transcript. Only called when the provider is unchanged
   * (a switch already rebuilds context via the handoff path).
   *
   * - Claude Code: the session artifact is deterministic on disk, so we
   *   probe it directly. A session minted this process lifetime is still warm
   *   (in `claudeSessions`) and can't have been GC'd — skip the check.
   * - codex: the app-server reports a recoverable resume failure by falling
   *   back to a fresh thread, so we preflight `startSession` (which the turn's
   *   own call then reuses via its warm-session early return) and read the
   *   flag. Errors are swallowed so the turn's own startSession surfaces them
   *   with today's ordering.
   */
  private async detectLostProviderSession(args: {
    chatId: string
    provider: AgentProvider
    cwd: string
    model: string
    serviceTier?: "fast"
    sessionToken: string | null | undefined
    pendingForkSessionToken: string | null | undefined
  }): Promise<boolean> {
    switch (args.provider) {
      case "claude-deepseek": {
        if (this.claudeSessions.has(args.chatId)) return false
        const token = args.pendingForkSessionToken ?? args.sessionToken
        return this.checkSessionArtifactFn("claude-deepseek", { cwd: args.cwd, sessionToken: token }) === "missing"
      }
      case "codex-openai": {
        // No token → nothing to resume; a fork in progress must not be disturbed.
        if (!args.sessionToken || args.pendingForkSessionToken) return false
        try {
          const started = await this.codexManager.startSession({
            chatId: args.chatId,
            cwd: args.cwd,
            model: args.model,
            serviceTier: args.serviceTier,
            sessionToken: args.sessionToken,
            pendingForkSessionToken: null,
          })
          return started?.resumeFellBack === true
        } catch {
          return false
        }
      }
      default:
        return false
    }
  }

  /**
   * Recover a chat whose native session is gone: clear the stale token, mark a
   * "Conversation Restored" boundary, and rebuild the wire-only context from
   * the transcript without changing the immutable backend.
   * Nothing warm needs closing — Claude Code has no live session by
   * construction here, and codex's warm context IS the fresh replacement
   * thread, whose id the turn's session_token stream event persists.
   */
  private async prepareSessionRestore(
    chatId: string,
    provider: AgentProvider,
    entries: TranscriptEntry[],
  ): Promise<SessionRestoreContext | null> {
    await this.store.setSessionToken(chatId, null)
    await this.store.setPendingForkSessionToken(chatId, null)

    const restore = buildSessionRestoreContext({
      entries,
      provider,
      transcriptPath: this.store.getTranscriptPath(chatId),
    })
    await this.store.appendMessage(chatId, timestamped({
      kind: "session_restored",
      provider,
      ...(restore ? { stats: restore.stats } : {}),
    }))
    return restore
  }

  private async startTurnForChat(args: {
    chatId: string
    provider: AgentProvider
    content: string
    attachments: ChatAttachment[]
    model: string
    effort?: string
    serviceTier?: "fast"
    planMode: boolean
    autoPlan: boolean
    appendUserPrompt: boolean
    steered?: boolean
    clientRequestId: string
    inputDigest: string
    context: TradeContextV1
    uiContext: UiTurnContextV1
    uiContextDigest: string
  }) {

    if (!(new Set<AgentProvider>(["claude-deepseek", "codex-openai"])).has(args.provider)) {
      throw new Error("Only Claude Code + DeepSeek and Codex + GPT are available.")
    }

    // Close any lingering draining stream before starting a new turn.
    const draining = this.drainingStreams.get(args.chatId)
    if (draining) {
      draining.turn.close()
      this.drainingStreams.delete(args.chatId)
    }

    const chat = this.store.requireChat(args.chatId)
    if (this.activeTurns.has(args.chatId)) {
      throw new Error("Chat is already running")
    }

    const previousProvider = chat.provider
    if (previousProvider === null) {
      throw new Error("Session backend is missing. Create a new Agent session.")
    }
    if (previousProvider !== args.provider) {
      throw new Error("A session backend is immutable. Create a new Agent session to use another backend.")
    }
    await this.store.setPlanMode(args.chatId, args.planMode)
    await this.store.setAutoPlan(args.chatId, args.autoPlan)

    const existingMessages = this.store.getMessages(args.chatId)
    const shouldGenerateTitle = args.appendUserPrompt && chat.title === "New Chat" && existingMessages.length === 0
    const optimisticTitle = shouldGenerateTitle ? fallbackTitleFromMessage(args.content) : null

    if (optimisticTitle) {
      await this.store.renameChat(args.chatId, optimisticTitle)
    }

    const project = this.store.getProject(chat.projectId)
    if (!project) {
      throw new Error("Project not found")
    }
    const projectPath = await this.resolveProjectPath(project.id)

    // Same-provider session recovery: when
    // the provider's native session for this chat is gone (e.g. the CLI
    // garbage-collected its session file, or codex's resume fell back to a
    // fresh thread), rebuild context from our transcript exactly like a
    // handoff — clear the stale token, mark a "Conversation Restored" boundary,
    // and prepend the rebuilt context on the wire. Runs before the user prompt
    // is appended so the boundary precedes it, mirroring the handoff ordering.
    const restore = previousProvider !== null
      && await this.detectLostProviderSession({
        chatId: args.chatId,
        provider: args.provider,
        cwd: projectPath,
        model: args.model,
        serviceTier: args.serviceTier,
        sessionToken: chat.sessionToken,
        pendingForkSessionToken: chat.pendingForkSessionToken,
      })
      ? await this.prepareSessionRestore(args.chatId, args.provider, existingMessages)
      : null

    const turnId = await this.store.recordTurnStarted(
      args.chatId,
      args.model,
      args.clientRequestId,
      args.inputDigest,
    )
    if (args.appendUserPrompt) {
      const userPromptEntry = timestamped(
        {
          kind: "user_prompt",
          content: args.content,
          attachments: args.attachments,
          steered: args.steered,
          context: args.context,
          uiContext: args.uiContext,
          uiContextDigest: args.uiContextDigest,
        },
        Date.now()
      )
      await this.store.appendMessage(args.chatId, userPromptEntry)
    }
    let tradeMcpServer: TradeMcpServerConfig | undefined
    if (this.issueToolGrantFn) {
      try {
        if (!chat.ownerId) throw new Error("Chat has no TradeEngine owner.")
        const issued = await this.issueToolGrantFn({
          ownerId: chat.ownerId,
          chatId: args.chatId,
          turnId,
          context: args.context,
        })
        tradeMcpServer = issued.mcpServer
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error)
        await this.store.recordTurnFailed(args.chatId, turnId, message, "tool_grant_failed", true)
        // Issuance can fail after the Engine has persisted the grant (for
        // example while publishing the one-time credential file). Revoke by
        // turn id even when no MCP config was returned so that partial
        // issuance cannot leave a usable capability behind.
        await this.revokeToolGrant(turnId)
        throw error
      }
    }
    if (shouldGenerateTitle) {
      void this.generateTitleInBackground(args.chatId, args.content, projectPath, optimisticTitle ?? "New Chat")
    }

    const onToolRequest = async (request: HarnessToolRequest): Promise<unknown> => {
      const active = this.activeTurns.get(args.chatId)
      if (!active || active.turnId !== turnId) {
        throw new Error("Chat turn ended unexpectedly")
      }

      active.status = "waiting_for_user"
      this.emitStateChange(args.chatId)

      return await new Promise<unknown>((resolve) => {
        active.pendingTool = {
          toolUseId: request.tool.toolId,
          tool: request.tool,
          resolve,
        }
      })
    }

    // Wire-only injections. The transcript above stores the user's typed text
    // verbatim; anything Kanna adds for the harness is applied here and never
    // persisted (the `steered` flag on the entry drives the UI affordance).
    //
    // Steer: prefix the mid-turn <system-message> block — or suffix it when
    // the message is a slash invocation, since Claude Code only expands a message
    // that STARTS with "/name".
    //
    // Concurrent agents: when other chats have active turns in the same
    // project directory, suffix a <system-message> notice listing them (and
    // their transcript paths) so agents don't trample each other's work.
    let wireContent = args.steered ? buildSteeredMessageContent(args.content) : args.content
    wireContent = appendSystemMessageBlock(wireContent, buildTradeContextSystemMessage(args.context))
    wireContent = appendSystemMessageBlock(wireContent, buildUiContextSystemMessage(args.uiContext, args.uiContextDigest))
    const concurrentAgentsNotice = buildConcurrentAgentsNotice(
      this.collectConcurrentProjectChats(args.chatId, project.id)
    )
    if (concurrentAgentsNotice) {
      wireContent = appendSystemMessageBlock(wireContent, concurrentAgentsNotice)
    }

    // Session restore: lead with the rebuilt transcript so
    // the user's actual prompt is the last thing in context (trails for slash
    // invocations, which must stay at the very start of the message).
    if (restore) {
      wireContent = buildRestoredMessageContent(restore.text, wireContent)
    }

    // "/name" skill invocation, translated per provider:
    //   Claude Code — passthrough of the leading "/name".
    //   Codex       — structured skill input item + <system-message> failsafe.
    const skillInvocation = args.provider === "codex-openai"
      ? parseSkillInvocation(args.content)
      : null

    let turn: HarnessTurn
    try {
      if (args.provider === "claude-deepseek") {
        turn = await this.startClaudeTurn({
          chatId: args.chatId,
          localPath: projectPath,
          model: args.model,
          effort: args.effort,
          serviceTier: args.serviceTier,
          planMode: args.planMode,
          autoPlan: args.autoPlan,
          sessionToken: chat.pendingForkSessionToken ?? chat.sessionToken,
          forkSession: Boolean(chat.pendingForkSessionToken),
          onToolRequest,
          mcpServer: tradeMcpServer,
        })
      } else {
        const started = await this.codexManager.startSession({
          chatId: args.chatId,
          cwd: projectPath,
          model: args.model,
          serviceTier: args.serviceTier,
          sessionToken: chat.sessionToken,
          pendingForkSessionToken: chat.pendingForkSessionToken,
          mcpServer: tradeMcpServer,
        })
        if (chat.pendingForkSessionToken && started?.sessionToken) {
          await this.store.setPendingForkSessionToken(args.chatId, null)
        }
        turn = await this.codexManager.startTurn({
          chatId: args.chatId,
          content: buildPromptText(wireContent, args.attachments, projectPath),
          skill: skillInvocation
            ? await this.resolveCodexSkill(args.chatId, projectPath, skillInvocation.name)
            : undefined,
          model: args.model,
          effort: args.effort as CodexReasoningEffort | undefined,
          serviceTier: args.serviceTier,
          planMode: args.planMode,
          onToolRequest,
        })
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      await this.recordTurnFailure(args.chatId, turnId, message)
      await this.revokeToolGrant(turnId)
      throw error
    }

    const active: ActiveTurn = {
      chatId: args.chatId,
      turnId,
      provider: args.provider,
      claudeSessionId: args.provider === "claude-deepseek" ? this.claudeSessions.get(args.chatId)?.id : undefined,
      turn,
      model: args.model,
      effort: args.effort,
      serviceTier: args.serviceTier,
      planMode: args.planMode,
      autoPlan: args.autoPlan,
      context: args.context,
      uiContext: args.uiContext,
      uiContextDigest: args.uiContextDigest,
      status: args.provider === "claude-deepseek" ? "running" : "starting",
      pendingTool: null,
      postToolFollowUp: null,
      hasFinalResult: false,
      cancelRequested: false,
      cancelRecorded: false,
    }
    this.activeTurns.set(args.chatId, active)
    this.emitStateChange(args.chatId, { immediate: active.status === "starting" })

    if (turn.getAccountInfo) {
      void turn.getAccountInfo()
        .then(async (accountInfo) => {
          if (!accountInfo) return
          if (args.provider === "claude-deepseek") {
            const session = this.claudeSessions.get(args.chatId)
            if (session) {
              if (session.accountInfoLoaded) return
              session.accountInfoLoaded = true
            } else {
              return
            }
          }
          await this.store.appendMessage(args.chatId, timestamped({ kind: "account_info", accountInfo }))
          this.emitStateChange(args.chatId)
        })
        .catch(() => undefined)
    }

    if (args.provider === "claude-deepseek") {
      const session = this.claudeSessions.get(args.chatId)
      if (!session) {
        this.activeTurns.delete(args.chatId)
        await this.store.recordTurnFailed(
          args.chatId,
          active.turnId,
          "Claude session was not initialized",
          "runtime_unavailable",
          true,
        )
        await this.revokeToolGrant(active.turnId)
        this.emitStateChange(args.chatId)
        throw new Error("Claude session was not initialized")
      }
      session.suppressResume = false
      const promptSeq = session.nextPromptSeq + 1
      session.nextPromptSeq = promptSeq
      session.pendingPromptSeqs.push(promptSeq)
      active.claudePromptSeq = promptSeq
      logClaudeSteer("claude_prompt_sent", {
        chatId: args.chatId,
        sessionId: session.id,
        promptSeq,
        activeStatus: active.status,
        contentPreview: wireContent.slice(0, 160),
        pendingPromptSeqs: [...session.pendingPromptSeqs],
      })
      // setModel() swaps the model on the live session without restarting it,
      // so the agent id in the session prompt can be stale. Re-state it on the
      // turn text (wire-only — the transcript stores args.content) from the
      // drift onward.
      const claudeAgentId = buildKannaAgentId("claude", args.model)
      const claudePrompt = buildPromptText(wireContent, args.attachments, projectPath)
      try {
        await session.session.sendPrompt(
          session.promptAgentId === claudeAgentId
            ? claudePrompt
            : appendSystemMessageBlock(claudePrompt, buildKannaAgentCorrection(claudeAgentId))
        )
      } catch (error) {
        if (this.activeTurns.get(args.chatId) === active) {
          this.activeTurns.delete(args.chatId)
        }
        const message = error instanceof Error ? error.message : String(error)
        await this.recordTurnFailure(args.chatId, active.turnId, message)
        this.emitStateChange(args.chatId)
        throw error
      }
      return
    }

    void this.runTurn(active)
  }

  private async startClaudeTurn(args: {
    chatId: string
    localPath: string
    model: string
    effort?: string
    serviceTier?: "fast"
    planMode: boolean
    autoPlan: boolean
    sessionToken: string | null
    forkSession: boolean
    onToolRequest: (request: HarnessToolRequest) => Promise<unknown>
    mcpServer?: TradeMcpServerConfig
  }): Promise<HarnessTurn> {
    let session = this.claudeSessions.get(args.chatId)

    // autoPlan changes the SDK's `tools` allowlist, which is fixed at query()
    // time — unlike planMode (setPermissionMode) it can only be applied by
    // restarting the session. The restart resumes by sessionToken, so the
    // conversation carries over.
    if (
      !session
      || session.localPath !== args.localPath
      || session.effort !== args.effort
      || session.autoPlan !== args.autoPlan
      || args.forkSession
    ) {
      if (session) {
        session.session.close()
        this.claudeSessions.delete(args.chatId)
      }

      const started = await this.startClaudeSessionFn({
        localPath: args.localPath,
        model: args.model,
        effort: args.effort,
        serviceTier: args.serviceTier,
        planMode: args.planMode,
        autoPlan: args.autoPlan,
        sessionToken: args.sessionToken,
        forkSession: args.forkSession,
        onToolRequest: args.onToolRequest,
        onRateLimitEvent: (info) => this.onClaudeRateLimit?.(info),
        mcpServer: args.mcpServer,
      })
      this.refreshClaudeModelCatalog(started)

      session = {
        id: crypto.randomUUID(),
        chatId: args.chatId,
        session: started,
        localPath: args.localPath,
        model: args.model,
        promptAgentId: buildKannaAgentId("claude", args.model),
        effort: args.effort,
        serviceTier: args.serviceTier,
        planMode: args.planMode,
        autoPlan: args.autoPlan,
        sessionToken: args.sessionToken,
        accountInfoLoaded: false,
        nextPromptSeq: 0,
        pendingPromptSeqs: [],
        suppressResume: false,
        cancelledPromptSeqs: new Set(),
      }
      this.claudeSessions.set(args.chatId, session)
      void this.runClaudeSession(session)
    } else {
      if (args.mcpServer) {
        await session.session.setTradeMcpServer?.(args.mcpServer)
      }
      if (session.model !== args.model) {
        await session.session.setModel(args.model)
        session.model = args.model
      }
      if (session.planMode !== args.planMode) {
        await session.session.setPermissionMode(args.planMode)
        session.planMode = args.planMode
      }
      if (session.serviceTier !== args.serviceTier) {
        await session.session.setFastMode?.(args.serviceTier === "fast")
        session.serviceTier = args.serviceTier
      }
    }

    return {
      provider: "claude-deepseek",
      stream: {
        async *[Symbol.asyncIterator]() {},
      },
      getAccountInfo: session.session.getAccountInfo,
      interrupt: session.session.interrupt,
      close: () => {},
    }
  }

  async send(command: AgentChatSendCommand, ownerId = "local-test-owner") {
    let chatId = command.chatId
    const clientRequestId = command.clientRequestId ?? crypto.randomUUID()
    const uiContext = command.uiContext ?? this.captureUiContextFn()
    const uiContextDigest = digestUiContext(uiContext)
    const context = normalizeTradeContext(command.context ?? tradeContextFromUiContext(uiContext))

    if (!chatId) {
      if (!command.projectId) {
        throw new Error("Missing projectId for new chat")
      }
      const initialProvider = command.provider === "codex-openai" ? "codex-openai" : "claude-deepseek"
      const created = await this.store.createChat(
        command.projectId,
        ownerId,
        initialProvider,
        clientRequestId,
      )
      chatId = created.id
      this.analytics.track("chat_created")
    }

    const chat = this.store.requireChat(chatId)
    if (chat.ownerId !== ownerId) throw new Error("Chat not found")
    // Sending a message to an archived chat resurrects it (viewing alone
    // never unarchives).
    if (chat.archivedAt) {
      await this.store.unarchiveChat(chatId)
    }
    const provider = this.resolveProvider(command, chat.provider)
    const settings = this.getProviderSettings(provider, command)
    const inputDigest = turnInputDigest({
      provider,
      model: settings.model,
      content: command.content,
      attachments: command.attachments ?? [],
      planMode: settings.planMode,
      autoPlan: settings.autoPlan,
      context,
    })
    const existingRequest = this.store.findTurnRequest(chatId, clientRequestId)
    if (existingRequest) {
      if (existingRequest.inputDigest !== inputDigest) {
        throw new Error("clientRequestId was already used with different turn input.")
      }
      return { chatId, turnId: existingRequest.turnId, duplicate: true as const }
    }
    if (this.activeTurns.has(chatId)) {
      this.analytics.track("message_sent")
      const queuedMessage = await this.enqueueMessage(chatId, command.content, command.attachments ?? [], {
        provider: command.provider,
        model: command.model,
        modelOptions: command.modelOptions,
        effort: command.effort,
        planMode: command.planMode,
        autoPlan: command.autoPlan,
        clientRequestId,
        context,
        uiContext,
        uiContextDigest,
      })
      return { chatId, queuedMessageId: queuedMessage.id, queued: true as const }
    }
    this.analytics.track("message_sent")
    await this.startTurnForChat({
      chatId,
      provider,
      content: command.content,
      attachments: command.attachments ?? [],
      model: settings.model,
      effort: settings.effort,
      serviceTier: settings.serviceTier,
      planMode: settings.planMode,
      autoPlan: settings.autoPlan,
      appendUserPrompt: true,
      clientRequestId,
      inputDigest,
      context,
      uiContext,
      uiContextDigest,
    })


    return { chatId }
  }

  /** Retry the last failed/interrupted Turn with its persisted prompt and exact Context. */
  async retry(chatId: string, ownerId: string, clientRequestId: string) {
    const chat = this.store.requireChat(chatId)
    if (chat.ownerId !== ownerId) throw new Error("Chat not found")
    if (this.activeTurns.has(chatId) || chat.activeTurnId) throw new Error("Chat is already running")
    if (chat.lastTurnOutcome !== "failed" && chat.lastTurnOutcome !== "interrupted") {
      throw new Error("Only a failed or interrupted Turn can be retried")
    }
    if (chat.lastTurnOutcome === "failed" && chat.lastErrorRetryable !== true) {
      throw new Error("This Turn is not retryable")
    }
    const prompt = [...this.store.getMessages(chatId)].reverse()
      .find((entry): entry is Extract<TranscriptEntry, { kind: "user_prompt" }> => entry.kind === "user_prompt")
    if (!prompt) throw new Error("The original Turn input is unavailable")
    if (!chat.provider) throw new Error("Session backend is missing")
    const context = normalizeTradeContext(prompt.context ?? emptyTradeContext())
    const uiContext = prompt.uiContext ?? emptyUiTurnContext()
    const uiContextDigest = prompt.uiContextDigest ?? digestUiContext(uiContext)
    const settings = this.getProviderSettings(chat.provider, {
      model: chat.lastModel,
      planMode: chat.planMode,
      autoPlan: chat.autoPlan,
    })
    const inputDigest = turnInputDigest({
      provider: chat.provider,
      model: settings.model,
      content: prompt.content,
      attachments: prompt.attachments ?? [],
      planMode: settings.planMode,
      autoPlan: settings.autoPlan,
      context,
    })
    const existing = this.store.findTurnRequest(chatId, clientRequestId)
    if (existing) {
      if (existing.inputDigest !== inputDigest) throw new Error("clientRequestId was already used with different turn input.")
      return { chatId, turnId: existing.turnId, duplicate: true as const }
    }
    await this.startTurnForChat({
      chatId,
      provider: chat.provider,
      content: prompt.content,
      attachments: prompt.attachments ?? [],
      model: settings.model,
      effort: settings.effort,
      serviceTier: settings.serviceTier,
      planMode: settings.planMode,
      autoPlan: settings.autoPlan,
      appendUserPrompt: false,
      clientRequestId,
      inputDigest,
      context,
      uiContext,
      uiContextDigest,
    })
    return { chatId }
  }

  async enqueue(command: AgentEnqueueCommand) {
    this.analytics.track("message_sent")
    const uiContext = command.uiContext ?? this.captureUiContextFn()
    const queuedMessage = await this.enqueueMessage(command.chatId, command.content, command.attachments ?? [], {
      provider: command.provider,
      model: command.model,
      modelOptions: command.modelOptions,
      planMode: command.planMode,
      autoPlan: command.autoPlan,
      clientRequestId: command.clientRequestId ?? crypto.randomUUID(),
      context: normalizeTradeContext(command.context ?? tradeContextFromUiContext(uiContext)),
      uiContext,
      uiContextDigest: digestUiContext(uiContext),
    })
    return { queuedMessageId: queuedMessage.id }
  }

  async steer(command: Extract<ClientCommand, { type: "message.steer" }>) {
    const queuedMessage = this.store.getQueuedMessage(command.chatId, command.queuedMessageId)
    if (!queuedMessage) {
      throw new Error("Queued message not found")
    }

    logClaudeSteer("steer_requested", {
      chatId: command.chatId,
      queuedMessageId: command.queuedMessageId,
      activeTurn: this.activeTurns.has(command.chatId),
      queuedMessagePreview: queuedMessage.content.slice(0, 160),
    })

    if (this.activeTurns.has(command.chatId)) {
      await this.cancel(command.chatId, { hideInterrupted: true })
    }

    logClaudeSteer("steer_after_cancel", {
      chatId: command.chatId,
      stillActive: this.activeTurns.has(command.chatId),
    })

    if (this.activeTurns.has(command.chatId)) {
      throw new Error("Chat is still running")
    }

    await this.dequeueAndStartQueuedMessage(command.chatId, queuedMessage, { steered: true })
  }

  async dequeue(command: Extract<ClientCommand, { type: "message.dequeue" }>) {
    const queuedMessage = this.store.getQueuedMessage(command.chatId, command.queuedMessageId)
    if (!queuedMessage) {
      throw new Error("Queued message not found")
    }

    await this.store.removeQueuedMessage(command.chatId, command.queuedMessageId)
  }

  /**
   * Enumerate the skills/commands the selected harness can invoke, for the
   * composer's "/" menu. Prefers the live harness (authoritative — includes
   * built-ins, plugins, and enabled flags) and degrades to Kanna's filesystem
   * scan of the same discovery roots when no session is running yet.
   *
   * Adding a harness = one branch here (list) plus, if its wire protocol needs
   * more than leading-"/name" text, one translation in startTurnForChat.
   */
  async listSkills(
    command: Extract<ClientCommand, { type: "chat.listSkills" }>
  ): Promise<ChatSkillsSnapshot> {
    const cwd = await this.resolveSkillScanCwd(command)
    if (!cwd) {
      return { provider: command.provider, skills: [], origin: "filesystem" }
    }

    switch (command.provider) {
      case "claude-deepseek": {
        const skills = scanClaudeSkills({ cwd }).map(({ name, description, argumentHint, source }) => ({
          name,
          description,
          argumentHint,
          source,
        }))
        return { provider: "claude-deepseek", skills, origin: "filesystem" }
      }
      case "codex-openai": {
        const live = command.chatId
          ? await this.codexManager.listSkills({ chatId: command.chatId, cwd })
          : null
        if (live) {
          const skills: HarnessSkill[] = live.filter((skill) => TRADE_TASK_SKILL_NAMES.has(skill.name)).map((skill) => ({
            name: skill.name,
            description: skill.shortDescription || skill.description || "",
            source: "skill" as const,
          }))
          return { provider: "codex-openai", skills, origin: "live" }
        }
        const skills = scanCodexSkills({ cwd }).map(({ name, description, argumentHint, source }) => ({
          name,
          description,
          argumentHint,
          source,
        }))
        return { provider: "codex-openai", skills, origin: "filesystem" }
      }
    }
  }

  private async resolveSkillScanCwd(args: { chatId?: string; projectId?: string }): Promise<string | null> {
    if (args.chatId) {
      const chat = this.store.getChat(args.chatId)
      const project = chat ? this.store.getProject(chat.projectId) : undefined
      if (project) return await this.resolveProjectPath(project.id)
    }
    if (args.projectId) {
      const project = this.store.getProject(args.projectId)
      if (project) return await this.resolveProjectPath(project.id)
    }
    return null
  }

  /**
   * Resolve a typed `/name` to a codex skill for the structured input item.
   * Live skills/list is authoritative (paths must exact-match the server's own
   * discovery for the item to inject); the fs scan of the same roots covers
   * codex versions that predate skills/list. Unresolved names degrade to plain
   * text — codex silently ignores unknown skill items anyway.
   */
  private async resolveCodexSkill(
    chatId: string,
    cwd: string,
    name: string
  ): Promise<{ name: string; path: string } | undefined> {
    const live = await this.codexManager.listSkills({ chatId, cwd })
    if (live) {
      const match = live.find((skill) => TRADE_TASK_SKILL_NAMES.has(skill.name) && skill.name === name)
      return match ? { name: match.name, path: match.path } : undefined
    }
    const scanned = findSkillByName(scanCodexSkills({ cwd }), name)
    return scanned?.path ? { name: scanned.name, path: scanned.path } : undefined
  }

  async forkChat(chatId: string, clientRequestId: string = crypto.randomUUID()) {
    const chat = this.store.requireChat(chatId)
    if (this.activeTurns.has(chatId) || this.drainingStreams.has(chatId)) {
      throw new Error("Chat must be idle before forking")
    }
    if (!chat.provider) {
      throw new Error("Chat must have a provider before forking")
    }
    if (!chat.sessionToken && !chat.pendingForkSessionToken) {
      throw new Error("Chat has no session to fork")
    }

    const forked = await this.store.forkChat(chatId, clientRequestId)
    this.analytics.track("chat_created")
    return { chatId: forked.id }
  }

  /**
   * Re-registers an active turn for a Claude session that produced new
   * activity after its previous turn finished (e.g. a Monitor or Cron
   * wakeup continued the session). The resumed turn has no prompt seq, so
   * the next result entry (pendingPromptSeqs empty → null === null) closes
   * it through the normal completion path in runClaudeSession.
   */
  private async resumeBackgroundTurn(session: ClaudeSessionState) {
    const previousContext = [...this.store.getMessages(session.chatId)]
      .reverse()
      .find((entry) => entry.kind === "user_prompt")
    const context = previousContext?.kind === "user_prompt"
      ? normalizeTradeContext(previousContext.context ?? emptyTradeContext())
      : emptyTradeContext()
    const uiContext = previousContext?.kind === "user_prompt"
      ? previousContext.uiContext ?? emptyUiTurnContext()
      : emptyUiTurnContext()
    const uiContextDigest = previousContext?.kind === "user_prompt"
      ? previousContext.uiContextDigest ?? digestUiContext(uiContext)
      : digestUiContext(uiContext)
    const clientRequestId = crypto.randomUUID()
    const turnId = await this.store.recordTurnStarted(
      session.chatId,
      session.model,
      clientRequestId,
      turnInputDigest({
        provider: "claude-deepseek",
        model: session.model,
        content: "[background resume]",
        attachments: [],
        planMode: session.planMode,
        autoPlan: session.autoPlan,
        context,
      }),
    )
    const active: ActiveTurn = {
      chatId: session.chatId,
      turnId,
      provider: "claude-deepseek",
      claudeSessionId: session.id,
      turn: {
        provider: "claude-deepseek",
        stream: {
          async *[Symbol.asyncIterator]() {},
        },
        getAccountInfo: session.session.getAccountInfo,
        interrupt: session.session.interrupt,
        close: () => {},
      },
      model: session.model,
      effort: session.effort,
      planMode: session.planMode,
      autoPlan: session.autoPlan,
      context,
      uiContext,
      uiContextDigest,
      status: "running",
      pendingTool: null,
      postToolFollowUp: null,
      hasFinalResult: false,
      cancelRequested: false,
      cancelRecorded: false,
    }
    this.activeTurns.set(session.chatId, active)
    this.emitStateChange(session.chatId)
  }

  private async runClaudeSession(session: ClaudeSessionState) {
    try {
      for await (const event of session.session.stream) {
        if (event.type === "session_token" && event.sessionToken) {
          session.sessionToken = event.sessionToken
          await this.store.setSessionToken(session.chatId, event.sessionToken)
          this.emitStateChange(session.chatId)
          continue
        }

        if (!event.entry) continue

        // After an escape/cancel or steer, the SDK ends the cancelled turn
        // with a result of subtype error_during_execution (is_error, usually
        // no text). The cancel already appended an "interrupted" entry, so
        // persisting this would render a spurious "An unknown error
        // occurred." in the UI. Attribute the result to the prompt it
        // completes (pendingPromptSeqs[0]) rather than relying on
        // suppressResume, which a steered follow-up prompt clears before the
        // interrupt error lands.
        const completingPromptSeq = event.entry.kind === "result" || event.entry.kind === "interrupted"
          ? (session.pendingPromptSeqs[0] ?? null)
          : null
        const isCancelledPromptErrorResult =
          event.entry.kind === "result"
          && event.entry.isError
          && completingPromptSeq !== null
          && session.cancelledPromptSeqs.has(completingPromptSeq)
        if (!isCancelledPromptErrorResult) {
          await this.store.appendMessage(session.chatId, event.entry)
        }

        // Background wakeups (Monitor, Cron*, ScheduleWakeup, RemoteTrigger)
        // emit new activity after the previous turn completed. Re-register an
        // active turn so the chat reads as in-progress instead of idle.
        if (
          this.claudeSessions.get(session.chatId) === session
          && !this.activeTurns.has(session.chatId)
          && !session.suppressResume
          && (
            event.entry.kind === "assistant_text"
            || event.entry.kind === "tool_call"
            || event.entry.kind === "tool_result"
          )
        ) {
          await this.resumeBackgroundTurn(session)
        }

        if (event.entry.kind === "result" || event.entry.kind === "interrupted") {
          session.suppressResume = false
        }

        const activeCandidate = this.activeTurns.get(session.chatId)
        const active = activeCandidate?.provider === "claude-deepseek"
          && activeCandidate.claudeSessionId === session.id
          ? activeCandidate
          : undefined
        if (event.entry.kind === "system_init" && active) {
          active.status = "running"
          const chat = this.store.getChat(session.chatId)
          if (
            chat?.pendingForkSessionToken
            && session.sessionToken
            && session.sessionToken !== chat.pendingForkSessionToken
          ) {
            await this.store.setPendingForkSessionToken(session.chatId, null)
          }
          logClaudeSteer("claude_event_system_init", {
            chatId: session.chatId,
            sessionId: session.id,
            activePromptSeq: active.claudePromptSeq ?? null,
            pendingPromptSeqs: [...session.pendingPromptSeqs],
          })
        }

        const completedClaudePromptSeq = event.entry.kind === "result" || event.entry.kind === "interrupted"
          ? (session.pendingPromptSeqs.shift() ?? null)
          : null
        if (completedClaudePromptSeq !== null) {
          session.cancelledPromptSeqs.delete(completedClaudePromptSeq)
        }

        logClaudeSteer("claude_event", {
          chatId: session.chatId,
          sessionId: session.id,
          entryKind: event.entry.kind,
          activePromptSeq: active?.claudePromptSeq ?? null,
          completedPromptSeq: completedClaudePromptSeq,
          activeStatus: active?.status ?? null,
          pendingPromptSeqs: [...session.pendingPromptSeqs],
        })

        if (event.entry.kind === "result" && active && completedClaudePromptSeq === (active.claudePromptSeq ?? null)) {
          active.hasFinalResult = true
          if (event.entry.isError) {
            await this.recordTurnFailure(session.chatId, active.turnId, event.entry.result || "Turn failed")
          } else if (!active.cancelRequested) {
            await this.store.recordTurnFinished(session.chatId, active.turnId)
          }
          this.activeTurns.delete(session.chatId)
          await this.revokeToolGrant(active.turnId)
          if (!active.cancelRequested) {
            await this.maybeStartNextQueuedMessage(session.chatId)
          }
        }

        this.emitStateChange(session.chatId)
      }
    } catch (error) {
      const activeCandidate = this.activeTurns.get(session.chatId)
      const active = activeCandidate?.provider === "claude-deepseek"
        && activeCandidate.claudeSessionId === session.id
        ? activeCandidate
        : undefined
      if (active && !active.cancelRequested) {
        const message = error instanceof Error ? error.message : String(error)
        await this.store.appendMessage(
          session.chatId,
          timestamped({
            kind: "result",
            subtype: "error",
            isError: true,
            durationMs: 0,
            result: message,
          })
        )
        await this.recordTurnFailure(session.chatId, active.turnId, message)
        await this.revokeToolGrant(active.turnId)
      }
    } finally {
      // Only evict if this session is still the chat's current one — a restart
      // (Project/model options change, or a fork) closes the old session
      // and immediately registers a replacement, and the old stream's cleanup
      // lands afterwards. Deleting by chatId alone would drop the new session.
      if (this.claudeSessions.get(session.chatId) === session) {
        this.claudeSessions.delete(session.chatId)
      }
      const active = this.activeTurns.get(session.chatId)
      if (active?.provider === "claude-deepseek" && active.claudeSessionId === session.id) {
        if (active.cancelRequested && !active.cancelRecorded) {
          await this.store.recordTurnCancelled(session.chatId, active.turnId)
        }
        this.activeTurns.delete(session.chatId)
        await this.revokeToolGrant(active.turnId)
      }
      session.session.close()
      this.emitStateChange(session.chatId)
    }
  }

  private async generateTitleInBackground(chatId: string, messageContent: string, cwd: string, expectedCurrentTitle: string) {
    try {
      const result = await this.generateTitle(messageContent, cwd)
      if (result.failureMessage) {
        this.reportBackgroundError?.(
          `[title-generation] chat ${chatId} failed provider title generation: ${result.failureMessage}`
        )
      }
      if (!result.title || result.usedFallback) return

      const chat = this.store.requireChat(chatId)
      if (chat.title !== expectedCurrentTitle) return

      await this.store.renameChat(chatId, result.title)
      this.emitStateChange(chatId)
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      this.reportBackgroundError?.(
        `[title-generation] chat ${chatId} failed background title generation: ${message}`
      )
    }
  }

  private async runTurn(active: ActiveTurn) {
    try {
      for await (const event of active.turn.stream) {
        // Once cancelled, stop processing further stream events.
        // cancel() already removed us from activeTurns and notified the UI.
        if (active.cancelRequested) break

        if (event.type === "session_token" && event.sessionToken) {
          await this.store.setSessionToken(active.chatId, event.sessionToken)
          const chat = this.store.getChat(active.chatId)
          if (
            chat?.pendingForkSessionToken
            && event.sessionToken !== chat.pendingForkSessionToken
          ) {
            await this.store.setPendingForkSessionToken(active.chatId, null)
          }
          this.emitStateChange(active.chatId)
          continue
        }

        if (!event.entry) continue
        await this.store.appendMessage(active.chatId, event.entry)

        if (event.entry.kind === "system_init") {
          active.status = "running"
        }

        if (event.entry.kind === "result") {
          active.hasFinalResult = true
          if (event.entry.isError) {
            await this.recordTurnFailure(active.chatId, active.turnId, event.entry.result || "Turn failed")
          } else if (!active.cancelRequested) {
            await this.store.recordTurnFinished(active.chatId, active.turnId)
          }
          // Remove from activeTurns as soon as the result arrives so the UI
          // transitions to idle immediately. The stream may still be open
          // (e.g. background tasks), but the user should be able to send
          // new messages without having to hit stop first.
          this.activeTurns.delete(active.chatId)
          // Track the still-open stream so the UI can show a draining
          // indicator and the user can stop background tasks.
          this.drainingStreams.set(active.chatId, { turn: active.turn })
        }

        this.emitStateChange(active.chatId)
      }
    } catch (error) {
      if (!active.cancelRequested) {
        const message = error instanceof Error ? error.message : String(error)
        await this.store.appendMessage(
          active.chatId,
          timestamped({
            kind: "result",
            subtype: "error",
            isError: true,
            durationMs: 0,
            result: message,
          })
        )
        await this.recordTurnFailure(active.chatId, active.turnId, message)
      }
    } finally {
      if (active.cancelRequested && !active.cancelRecorded) {
        await this.store.recordTurnCancelled(active.chatId, active.turnId)
      }
      await this.revokeToolGrant(active.turnId)
      active.turn.close()
      // Only remove if we're still the active turn for this chat.
      // We may have already been removed by result handling or cancel(),
      // and a new turn may have started for the same chatId.
      if (this.activeTurns.get(active.chatId) === active) {
        this.activeTurns.delete(active.chatId)
      }
      // Stream has fully ended — no longer draining.
      this.drainingStreams.delete(active.chatId)
      this.emitStateChange(active.chatId)

      if (active.postToolFollowUp && !active.cancelRequested) {
        try {
          const clientRequestId = crypto.randomUUID()
          const inputDigest = turnInputDigest({
            provider: active.provider,
            model: active.model,
            content: active.postToolFollowUp.content,
            attachments: [],
            planMode: active.postToolFollowUp.planMode,
            autoPlan: active.autoPlan,
            context: active.context,
          })
          await this.startTurnForChat({
            chatId: active.chatId,
            provider: active.provider,
            content: active.postToolFollowUp.content,
            attachments: [],
            model: active.model,
            effort: active.effort,
            serviceTier: active.serviceTier,
            planMode: active.postToolFollowUp.planMode,
            // Codex-only path; carry the turn's mode through unchanged.
            autoPlan: active.autoPlan,
            appendUserPrompt: false,
            clientRequestId,
            inputDigest,
            context: active.context,
            uiContext: active.uiContext,
            uiContextDigest: active.uiContextDigest,
          })
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error)
          await this.store.appendMessage(
            active.chatId,
            timestamped({
              kind: "result",
              subtype: "error",
              isError: true,
              durationMs: 0,
              result: message,
            })
          )
          this.emitStateChange(active.chatId)
        }
      } else if (!active.cancelRequested) {
        try {
          await this.maybeStartNextQueuedMessage(active.chatId)
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error)
          await this.store.appendMessage(
            active.chatId,
            timestamped({
              kind: "result",
              subtype: "error",
              isError: true,
              durationMs: 0,
              result: message,
            })
          )
          this.emitStateChange(active.chatId)
        }
      }
    }
  }

  async cancel(chatId: string, options?: { hideInterrupted?: boolean }) {
    // Also clean up any draining stream for this chat.
    const draining = this.drainingStreams.get(chatId)
    if (draining) {
      draining.turn.close()
      this.drainingStreams.delete(chatId)
    }

    const active = this.activeTurns.get(chatId)
    if (!active) return

    logClaudeSteer("cancel_requested", {
      chatId,
      provider: active.provider,
      activePromptSeq: active.claudePromptSeq ?? null,
    })

    // Guard against concurrent cancel() calls — only the first one does work.
    if (active.cancelRequested) return
    active.cancelRequested = true

    // Keep in-flight stream entries (emitted before the interrupt lands)
    // from re-registering an active turn via resumeBackgroundTurn, and mark
    // the cancelled prompt so its interrupt error result gets dropped.
    if (active.provider === "claude-deepseek") {
      const session = this.claudeSessions.get(chatId)
      if (session) {
        session.suppressResume = true
        if (active.claudePromptSeq != null) {
          session.cancelledPromptSeqs.add(active.claudePromptSeq)
        }
      }
    }

    const pendingTool = active.pendingTool
    active.pendingTool = null

    if (pendingTool) {
      const result = discardedToolResult(pendingTool.tool)
      await this.store.appendMessage(
        chatId,
        timestamped({
          kind: "tool_result",
          toolId: pendingTool.toolUseId,
          content: result,
        })
      )
      if (active.provider === "codex-openai" && pendingTool.tool.toolKind === "exit_plan_mode") {
        pendingTool.resolve(result)
      }
    }

    await this.store.appendMessage(chatId, timestamped({
      kind: "interrupted",
      reason: "user_cancelled",
      turnId: active.turnId,
      hidden: options?.hideInterrupted,
    }))
    await this.store.recordTurnCancelled(chatId, active.turnId)
    await this.revokeToolGrant(active.turnId)
    active.cancelRecorded = true
    active.hasFinalResult = true

    // Remove from activeTurns immediately so the UI reflects the cancellation
    // right away, rather than waiting for interrupt() which may hang.
    this.activeTurns.delete(chatId)
    this.emitStateChange(chatId)
    logClaudeSteer("cancel_active_turn_deleted", {
      chatId,
      provider: active.provider,
      activePromptSeq: active.claudePromptSeq ?? null,
    })

    // Now attempt to interrupt/close the underlying stream in the background.
    // This is best-effort — the turn is already removed from active state above,
    // and runTurn()'s finally block will also call close().
    try {
      await Promise.race([
        active.turn.interrupt(),
        new Promise((resolve) => setTimeout(resolve, 5_000)),
      ])
    } catch {
      // interrupt() failed — force close
    }
    active.turn.close()
  }

  async respondTool(command: Extract<ClientCommand, { type: "chat.respondTool" }>) {
    const active = this.activeTurns.get(command.chatId)
    if (!active || !active.pendingTool) {
      throw new Error("No pending tool request")
    }

    const pending = active.pendingTool
    if (pending.toolUseId !== command.toolUseId) {
      throw new Error("Tool response does not match active request")
    }

    await this.store.appendMessage(
      command.chatId,
      timestamped({
        kind: "tool_result",
        toolId: command.toolUseId,
        content: command.result,
      })
    )

    active.pendingTool = null
    active.status = "running"

    if (pending.tool.toolKind === "exit_plan_mode") {
      const result = (command.result ?? {}) as {
        confirmed?: boolean
        clearContext?: boolean
        message?: string
      }
      if (result.confirmed && result.clearContext) {
        await this.store.setSessionToken(command.chatId, null)
        await this.store.appendMessage(command.chatId, timestamped({ kind: "context_cleared" }))
      }

      if (active.provider === "codex-openai") {
        active.postToolFollowUp = result.confirmed
          ? {
              content: result.message
                ? `Proceed with the approved plan. Additional guidance: ${result.message}`
                : "Proceed with the approved plan.",
              planMode: false,
            }
          : {
              content: result.message
                ? `Revise the plan using this feedback: ${result.message}`
                : "Revise the plan using this feedback.",
              planMode: true,
            }
      }
    }

    pending.resolve(command.result)

    this.emitStateChange(command.chatId)
  }
}
