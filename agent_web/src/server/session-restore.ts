import type { AgentProvider, TranscriptEntry } from "../shared/types"
import { getProviderCatalog } from "../shared/types"

/**
 * Bounded transcript context used only when the immutable backend's native
 * session artifact can no longer be resumed. The saved transcript remains the
 * source of truth and is prepended wire-only to the next prompt.
 *
 * Budgeting (approximating 1 token ≈ 4 chars):
 * - The rendered transcript is capped at ~HANDOFF_TOKEN_BUDGET tokens.
 * - Tool call inputs / results outside the most recent
 *   RECENT_VERBATIM_CHARS window are elided when large — the preamble points
 *   the harness at the full JSONL transcript, so elided content stays
 *   retrievable (same path the concurrent-agents notice already shares).
 * - When the whole transcript still doesn't fit, older turns are dropped
 *   wholesale, cutting on a user-prompt boundary so tool calls never lose
 *   their results.
 */
export const SESSION_RESTORE_TOKEN_BUDGET = 100_000
const CHARS_PER_TOKEN = 4
export const SESSION_RESTORE_CHAR_BUDGET = SESSION_RESTORE_TOKEN_BUDGET * CHARS_PER_TOKEN
/** Trailing window (chars) whose tool inputs/results are always verbatim. */
const RECENT_VERBATIM_CHARS = 100_000
/** Older tool inputs/results above this size (chars) get elided. */
const TOOL_IO_ELIDE_CHARS = 2_000
/** Error results from the harness are capped at this many chars. */
const ERROR_RESULT_MAX_CHARS = 2_000

interface RestoreBlock {
  entry: TranscriptEntry
  header: string
  body: string
  /** Tool call input / tool result content — elidable outside the recent window. */
  elidableBody: boolean
  elided: boolean
}

export interface SessionRestoreStats {
  totalEntries: number
  includedEntries: number
  elidedToolResults: number
  approxTokens: number
}

export interface SessionRestoreContext {
  text: string
  stats: SessionRestoreStats
}

function providerLabel(provider: AgentProvider) {
  try {
    return getProviderCatalog(provider).label
  } catch {
    return provider
  }
}

function compactJson(value: unknown) {
  try {
    return JSON.stringify(value) ?? String(value)
  } catch {
    return String(value)
  }
}

/**
 * Tool result contents vary by harness: Claude sends strings or arrays of
 * content blocks; Codex sends strings or structured objects. Text
 * blocks render verbatim (no JSON string escaping — that's the token sink);
 * everything else falls back to compact JSON.
 */
export function renderToolResultContent(content: unknown): string {
  if (typeof content === "string") return content
  if (Array.isArray(content)) {
    return content
      .map((block) => {
        if (typeof block === "string") return block
        if (block && typeof block === "object" && (block as { type?: unknown }).type === "text") {
          const text = (block as { text?: unknown }).text
          if (typeof text === "string") return text
        }
        return compactJson(block)
      })
      .join("\n")
  }
  if (content === null || content === undefined) return ""
  return compactJson(content)
}

function renderAttachmentLines(entry: Extract<TranscriptEntry, { kind: "user_prompt" }>) {
  if (!entry.attachments || entry.attachments.length === 0) return ""
  const lines = entry.attachments.map(
    (attachment) => `[attached: ${attachment.displayName} (${attachment.relativePath})]`
  )
  return `\n${lines.join("\n")}`
}

function blockFromEntry(entry: TranscriptEntry): Omit<RestoreBlock, "elided"> | null {
  switch (entry.kind) {
    case "user_prompt":
      return {
        entry,
        header: "--- user ---",
        body: `${entry.content}${renderAttachmentLines(entry)}`,
        elidableBody: false,
      }
    case "assistant_text":
      return {
        entry,
        header: "--- assistant ---",
        body: entry.text,
        elidableBody: false,
      }
    case "tool_call":
      return {
        entry,
        header: `--- assistant tool call: ${entry.tool.toolName} ---`,
        body: compactJson(entry.tool.rawInput ?? entry.tool.input ?? {}),
        elidableBody: true,
      }
    case "tool_result":
      return {
        entry,
        header: `--- tool result${entry.isError ? " (error)" : ""} ---`,
        body: renderToolResultContent(entry.content),
        elidableBody: true,
      }
    case "compact_summary":
      return {
        entry,
        header: "--- summary of earlier conversation (previous agent's context compaction) ---",
        body: entry.summary,
        elidableBody: false,
      }
    case "interrupted":
      return { entry, header: "--- turn interrupted by user ---", body: "", elidableBody: false }
    case "session_restored":
      return {
        entry,
        header: "--- conversation restored from saved transcript (previous native session unavailable) ---",
        body: "",
        elidableBody: false,
      }
    case "result":
      if (!entry.isError) return null
      return {
        entry,
        header: "--- turn ended with error ---",
        body: entry.result.slice(0, ERROR_RESULT_MAX_CHARS),
        elidableBody: false,
      }
    case "review_artifact":
      return {
        entry,
        header: "--- TradeEngine review artifact (display-only) ---",
        body: compactJson(entry.artifact),
        elidableBody: false,
      }
    // Harness/plumbing noise the new agent doesn't need.
    case "system_init":
    case "account_info":
    case "status":
    case "context_window_updated":
    case "compact_boundary":
    case "context_cleared":
      return null
  }
}

function blockLength(block: RestoreBlock) {
  return block.header.length + (block.body ? block.body.length + 1 : 0) + 2
}

function renderBlock(block: RestoreBlock) {
  return block.body ? `${block.header}\n${block.body}` : block.header
}

function elideBody(block: RestoreBlock): RestoreBlock {
  const approxTokens = Math.round(block.body.length / CHARS_PER_TOKEN)
  const label = block.entry.kind === "tool_result" ? "tool result" : "tool input"
  return {
    ...block,
    body: `[${label} elided (~${approxTokens} tokens) — entry ${block.entry._id} in the transcript JSONL]`,
    elided: true,
  }
}

/**
 * Render the transcript into a budgeted handoff block for the new harness,
 * or null when there is nothing worth handing off.
 */
export function buildSessionRestoreContext(args: {
  entries: TranscriptEntry[]
  provider: AgentProvider
  transcriptPath: string
  charBudget?: number
}): SessionRestoreContext | null {
  const charBudget = args.charBudget ?? SESSION_RESTORE_CHAR_BUDGET

  let blocks: RestoreBlock[] = []
  for (const entry of args.entries) {
    if (entry.hidden) continue
    const block = blockFromEntry(entry)
    if (block) blocks.push({ ...block, elided: false })
  }
  if (blocks.length === 0 || !blocks.some((block) => block.entry.kind === "user_prompt")) {
    return null
  }
  const totalEntries = blocks.length

  // Pass 1 — find the start of the recent verbatim window.
  let recentStart = blocks.length
  for (let chars = 0; recentStart > 0; recentStart -= 1) {
    chars += blockLength(blocks[recentStart - 1]!)
    if (chars > RECENT_VERBATIM_CHARS) break
  }

  // Pass 2 — elide large tool IO outside the recent window.
  let elidedToolResults = 0
  blocks = blocks.map((block, index) => {
    if (index >= recentStart || !block.elidableBody || block.body.length <= TOOL_IO_ELIDE_CHARS) {
      return block
    }
    elidedToolResults += 1
    return elideBody(block)
  })

  // Pass 3 — apply the overall budget from the end.
  let cutIndex = blocks.length
  for (let chars = 0; cutIndex > 0; cutIndex -= 1) {
    const next = chars + blockLength(blocks[cutIndex - 1]!)
    if (next > charBudget) break
    chars = next
  }

  // Snap the cut forward to a turn boundary so a tool call never loses its
  // result. Falls back to skipping orphaned results when a single turn is
  // itself bigger than the budget.
  if (cutIndex > 0) {
    const nextPromptIndex = blocks.findIndex(
      (block, index) => index >= cutIndex && block.entry.kind === "user_prompt"
    )
    if (nextPromptIndex !== -1) {
      cutIndex = nextPromptIndex
    } else {
      while (cutIndex < blocks.length && blocks[cutIndex]!.entry.kind === "tool_result") {
        cutIndex += 1
      }
    }
  }

  const included = blocks.slice(cutIndex)
  if (!included.some((block) => block.entry.kind === "user_prompt" || block.entry.kind === "assistant_text")) {
    return null
  }
  const omitted = cutIndex

  const bodyLines: string[] = []
  if (omitted > 0) {
    bodyLines.push(`[${omitted} earlier entries omitted for length — read the full transcript JSONL at ${args.transcriptPath}]`)
  }
  bodyLines.push(...included.map(renderBlock))
  const body = bodyLines.join("\n\n")

  const intro = `Your previous ${providerLabel(args.provider)} session for this conversation could not be resumed — its native session data is no longer available. The conversation has been restored from the saved transcript; continue the same conversation.`
  const text = [
    "<system-message>",
    intro,
    "",
    "Everything inside <restored_transcript> is a read-only record of this same conversation. Treat it as your own history and respond to the user's message that follows it.",
    "",
    `Some older tool inputs/results may be elided and earlier turns omitted for length. The complete conversation record (JSONL, one entry per line) is at: ${args.transcriptPath}. Read it if you need elided or omitted context.`,
    "",
    "<restored_transcript>",
    body,
    "</restored_transcript>",
    "</system-message>",
  ].join("\n")

  return {
    text,
    stats: {
      totalEntries,
      includedEntries: included.length,
      elidedToolResults,
      approxTokens: Math.round(text.length / CHARS_PER_TOKEN),
    },
  }
}

/**
 * Combine the handoff block with the user's prompt for the wire. Mirrors
 * buildSteeredMessageContent: slash invocations must stay at the very start
 * of the message (Claude Code only expands a leading "/name"), so the handoff
 * trails for them and leads otherwise (context first, task last).
 */
export function buildRestoredMessageContent(restoredText: string, content: string) {
  const trimmed = content.trim()
  if (trimmed.length === 0) return restoredText
  if (trimmed.startsWith("/")) {
    return `${content}\n\n${restoredText}`
  }
  return `${restoredText}\n\n${content}`
}
