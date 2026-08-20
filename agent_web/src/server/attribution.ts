import { APP_NAME } from "../shared/branding"

type NativeRuntime = "claude" | "codex"

/** Stable runtime/model marker used to detect a live Claude model switch. */
export function buildKannaAgentId(runtime: NativeRuntime, model: string) {
  return `${APP_NAME}:${runtime}:${model}`
}

/** Product boundary appended to each native agent's own system prompt. */
export function buildKannaAttributionInstructions(agentId: string) {
  return [
    `You are running as ${agentId} inside TradeEngine Agent Web.`,
    "Continue to behave as a coding agent: inspect, edit, run tools, report progress, and ask the user when input is required.",
    "TradeEngine resources must be accessed through the provided trade_engine MCP tools; never infer or bypass their authority from filesystem paths.",
  ].join("\n")
}

/** Re-states the identity after the native session changes model in place. */
export function buildKannaAgentCorrection(agentId: string) {
  return `<system-message>The active TradeEngine Agent identity is now ${agentId}.</system-message>`
}
