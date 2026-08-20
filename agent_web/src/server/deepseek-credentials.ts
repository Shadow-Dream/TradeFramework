import { randomUUID } from "node:crypto"
import { homedir } from "node:os"
import path from "node:path"
import { chmod, mkdir, readFile, rename, writeFile } from "node:fs/promises"

const DEFAULT_BASE_URL = "https://api.deepseek.com/anthropic"
const DEFAULT_MODEL = "deepseek-chat"

const PROFILE_KEYS = new Set([
  "ANTHROPIC_AUTH_TOKEN",
  "ANTHROPIC_API_KEY",
  "ANTHROPIC_BASE_URL",
  "ANTHROPIC_MODEL",
  "ANTHROPIC_DEFAULT_OPUS_MODEL",
  "ANTHROPIC_DEFAULT_SONNET_MODEL",
  "ANTHROPIC_DEFAULT_HAIKU_MODEL",
  "CLAUDE_CODE_SUBAGENT_MODEL",
  "CLAUDE_CODE_EFFORT_LEVEL",
  "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
  "API_TIMEOUT_MS",
])

export interface DeepSeekCredentialStatus {
  configured: boolean
  source: "managed" | "setdeepseek" | null
  defaultModel: string
  models: string[]
}

interface DeepSeekCredentials extends DeepSeekCredentialStatus {
  environment: Record<string, string>
}

function decodeValue(raw: string): string {
  const trimmed = raw.trim()
  if (!trimmed) return ""
  if (trimmed.includes("`") || trimmed.includes("$(")) {
    throw new Error("DeepSeek profile contains executable shell syntax.")
  }
  const quote = trimmed[0]
  if (quote === "\"" || quote === "'") {
    if (trimmed.at(-1) !== quote) throw new Error("DeepSeek profile contains an unterminated quote.")
    return trimmed.slice(1, -1)
  }
  const value = trimmed.replace(/\s+#.*$/u, "").trim()
  if (/\s/u.test(value)) throw new Error("DeepSeek profile contains an unsupported unquoted value.")
  return value
}

/** Parse assignments only. The profile is data and is never sourced as shell code. */
export function parseDeepSeekProfile(text: string): Record<string, string> {
  if (Buffer.byteLength(text, "utf8") > 64 * 1024) {
    throw new Error("DeepSeek profile is too large.")
  }
  const environment: Record<string, string> = {}
  for (const [index, rawLine] of text.split(/\r?\n/u).entries()) {
    const line = rawLine.trim()
    if (!line || line.startsWith("#")) continue
    const assignment = /^(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*(.*)$/u.exec(line)
    if (!assignment) throw new Error(`Unsupported DeepSeek profile line ${index + 1}.`)
    const key = assignment[1]!
    if (!PROFILE_KEYS.has(key)) throw new Error(`Unsupported DeepSeek profile field ${key}.`)
    if (Object.hasOwn(environment, key)) throw new Error(`Duplicate DeepSeek profile field ${key}.`)
    const value = decodeValue(assignment[2] ?? "")
    if (!value || value.length > 4096 || /[\u0000-\u001f\u007f]/u.test(value)) {
      throw new Error(`Invalid DeepSeek profile value for ${key}.`)
    }
    environment[key] = value
  }
  if (!(environment.ANTHROPIC_AUTH_TOKEN || environment.ANTHROPIC_API_KEY)) {
    throw new Error("DeepSeek profile is missing an API key.")
  }
  if (!environment.ANTHROPIC_BASE_URL || !environment.ANTHROPIC_MODEL) {
    throw new Error("DeepSeek profile is missing its base URL or default model.")
  }
  return environment
}

function modelList(environment: Record<string, string>): string[] {
  return [...new Set([
    environment.ANTHROPIC_MODEL,
    environment.ANTHROPIC_DEFAULT_OPUS_MODEL,
    environment.ANTHROPIC_DEFAULT_SONNET_MODEL,
    environment.ANTHROPIC_DEFAULT_HAIKU_MODEL,
    environment.CLAUDE_CODE_SUBAGENT_MODEL,
  ].filter((model): model is string => Boolean(model?.trim())))]
}

function normalizeCredentials(
  environment: Record<string, string>,
  source: DeepSeekCredentials["source"],
): DeepSeekCredentials {
  const apiKey = environment.ANTHROPIC_AUTH_TOKEN || environment.ANTHROPIC_API_KEY || ""
  const models = modelList(environment)
  const defaultModel = environment.ANTHROPIC_MODEL || models[0] || DEFAULT_MODEL
  return {
    configured: Boolean(apiKey && environment.ANTHROPIC_BASE_URL),
    source,
    defaultModel,
    models: models.length > 0 ? models : [defaultModel],
    environment: {
      ...environment,
      ANTHROPIC_BASE_URL: environment.ANTHROPIC_BASE_URL || DEFAULT_BASE_URL,
      ANTHROPIC_MODEL: defaultModel,
      CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC:
        environment.CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC || "1",
    },
  }
}

export class DeepSeekCredentialStore {
  readonly filePath: string
  readonly importPath: string
  readonly claudeConfigDir: string

  constructor(dataRoot: string, importPath = path.join(homedir(), ".setdeepseek")) {
    this.filePath = path.join(dataRoot, "credentials", "claude-deepseek.json")
    this.importPath = importPath
    this.claudeConfigDir = path.join(dataRoot, "credentials", "claude-deepseek")
  }

  private async readSaved(): Promise<DeepSeekCredentials | null> {
    try {
      const parsed = JSON.parse(await readFile(this.filePath, "utf8")) as {
        apiKey?: unknown
        baseUrl?: unknown
        defaultModel?: unknown
        models?: unknown
      }
      const apiKey = typeof parsed.apiKey === "string" ? parsed.apiKey.trim() : ""
      if (!apiKey) return null
      const defaultModel = typeof parsed.defaultModel === "string" && parsed.defaultModel.trim()
        ? parsed.defaultModel.trim()
        : DEFAULT_MODEL
      const models = Array.isArray(parsed.models)
        ? parsed.models.filter((model): model is string => typeof model === "string" && Boolean(model.trim()))
        : []
      const modelAliases = [
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
      ]
      return normalizeCredentials({
        ANTHROPIC_AUTH_TOKEN: apiKey,
        ANTHROPIC_BASE_URL: typeof parsed.baseUrl === "string" && parsed.baseUrl.trim()
          ? parsed.baseUrl.trim()
          : DEFAULT_BASE_URL,
        ANTHROPIC_MODEL: defaultModel,
        ...Object.fromEntries(models.slice(0, modelAliases.length).map((model, index) => [modelAliases[index]!, model])),
      }, "managed")
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return null
      throw error
    }
  }

  private async readImported(): Promise<DeepSeekCredentials | null> {
    try {
      return normalizeCredentials(parseDeepSeekProfile(await readFile(this.importPath, "utf8")), "setdeepseek")
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return null
      throw error
    }
  }

  async read(): Promise<DeepSeekCredentials> {
    return await this.readSaved()
      ?? await this.readImported()
      ?? normalizeCredentials({}, null)
  }

  async getStatus(): Promise<DeepSeekCredentialStatus> {
    const { configured, source, defaultModel, models } = await this.read()
    return { configured, source, defaultModel, models }
  }

  async getEnvironment(): Promise<Record<string, string>> {
    const credentials = await this.read()
    if (!credentials.configured) throw new Error("Claude Code + DeepSeek is not connected.")
    await mkdir(this.claudeConfigDir, { recursive: true, mode: 0o700 })
    return { ...credentials.environment, CLAUDE_CONFIG_DIR: this.claudeConfigDir }
  }

  async setApiKey(apiKey: string): Promise<DeepSeekCredentialStatus> {
    const cleaned = apiKey.trim()
    if (cleaned.length < 8 || /[\r\n]/u.test(cleaned)) throw new Error("Enter a valid DeepSeek API key.")
    const current = await this.read()
    const payload = JSON.stringify({
      apiKey: cleaned,
      baseUrl: current.environment.ANTHROPIC_BASE_URL || DEFAULT_BASE_URL,
      defaultModel: current.defaultModel,
      models: current.models,
    }, null, 2) + "\n"
    const directory = path.dirname(this.filePath)
    await mkdir(directory, { recursive: true, mode: 0o700 })
    const temporary = path.join(directory, `.claude-deepseek.${randomUUID()}.tmp`)
    await writeFile(temporary, payload, { encoding: "utf8", mode: 0o600, flag: "wx" })
    await rename(temporary, this.filePath)
    await chmod(this.filePath, 0o600)
    return await this.getStatus()
  }
}
