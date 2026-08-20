import { createHash, randomUUID } from "node:crypto"
import path from "node:path"
import { chmod, lstat, mkdir, unlink, writeFile } from "node:fs/promises"
import type { TradeContextV1 } from "../shared/trade-context"

export const TRADE_AGENT_TOOL_SCOPES = [
  "trade_context_get",
  "trade_catalog_find",
  "trade_dataset_inspect",
  "trade_validate",
  "trade_backtest_get",
  "trade_result_query",
  "trade_proposal_create",
  "trade_ui_state_get",
  "trade_ui_document_get",
  "trade_ui_document_patch",
] as const

export interface TradeMcpServerConfig {
  command: string
  args: string[]
  env: Record<string, string>
  timeout: number
}

function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>
    return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${stableJson(record[key])}`).join(",")}}`
  }
  return JSON.stringify(value)
}

export function tradeContextDigest(context: TradeContextV1) {
  return createHash("sha256").update(stableJson(context)).digest("hex")
}

function exactOrigin(value: string, label: string) {
  const parsed = new URL(value)
  if (!(["http:", "https:"] as const).includes(parsed.protocol as "http:" | "https:")
    || parsed.username || parsed.password || parsed.pathname !== "/" || parsed.search || parsed.hash) {
    throw new Error(`${label} must be an exact http(s) origin.`)
  }
  return parsed.origin
}

export class TradeToolGrantIssuer {
  private readonly engineOrigin: string
  private readonly bridgeToken: string
  private readonly pythonPath: string
  private readonly tradeRoot: string
  private readonly credentialRoot: string
  private readonly grantFiles = new Map<string, string>()

  constructor(args: { engineOrigin: string; bridgeToken: string; tradeRoot: string; credentialRoot: string; pythonPath?: string }) {
    this.engineOrigin = exactOrigin(args.engineOrigin, "TradeEngine tool URL")
    this.bridgeToken = args.bridgeToken
    this.tradeRoot = args.tradeRoot
    this.credentialRoot = args.credentialRoot
    this.pythonPath = args.pythonPath || "python3"
  }

  async issue(args: { ownerId: string; chatId: string; turnId: string; context: TradeContextV1 }) {
    if (!this.bridgeToken) throw new Error("TradeEngine Agent tool bridge is not configured.")
    const contextDigest = tradeContextDigest(args.context)
    const response = await fetch(`${this.engineOrigin}/api/agent-tools/grants`, {
      method: "POST",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${this.bridgeToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        ownerId: args.ownerId,
        chatId: args.chatId,
        turnId: args.turnId,
        contextDigest,
        context: args.context,
        scopes: [...TRADE_AGENT_TOOL_SCOPES],
      }),
    })
    const payload = await response.json() as { grant?: unknown; error?: unknown }
    if (!response.ok || typeof payload.grant !== "string") {
      throw new Error(typeof payload.error === "string" ? payload.error : "TradeEngine Agent tool grant failed.")
    }
    await mkdir(this.credentialRoot, { recursive: true, mode: 0o700 })
    const rootInfo = await lstat(this.credentialRoot)
    if (!rootInfo.isDirectory() || rootInfo.isSymbolicLink()) {
      throw new Error("TradeEngine Agent tool grant directory must be a plain directory.")
    }
    await chmod(this.credentialRoot, 0o700)
    const grantFile = path.join(this.credentialRoot, `${args.turnId}.${randomUUID()}.grant`)
    await writeFile(grantFile, `${payload.grant}\n`, { encoding: "ascii", mode: 0o600, flag: "wx" })
    this.grantFiles.set(args.turnId, grantFile)
    return {
      contextDigest,
      mcpServer: {
        command: this.pythonPath,
        args: ["-m", "trade_agent_tools.mcp_server"],
        env: {
          PYTHONPATH: this.tradeRoot,
          TRADE_ENGINE_TOOL_URL: this.engineOrigin,
          TRADE_ENGINE_TOOL_GRANT_FILE: grantFile,
        },
        timeout: 30_000,
      } satisfies TradeMcpServerConfig,
    }
  }

  async revoke(turnId: string) {
    const grantFile = this.grantFiles.get(turnId)
    this.grantFiles.delete(turnId)
    if (grantFile) await unlink(grantFile).catch((error: NodeJS.ErrnoException) => {
      if (error.code !== "ENOENT") throw error
    })
    const response = await fetch(`${this.engineOrigin}/api/agent-tools/grants/revoke`, {
      method: "POST",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${this.bridgeToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ turnId }),
    })
    if (!response.ok) throw new Error("TradeEngine Agent tool grant revocation failed.")
  }
}
