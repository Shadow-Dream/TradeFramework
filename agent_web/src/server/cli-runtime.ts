import { spawnSync } from "node:child_process"
import { APP_NAME, CLI_COMMAND, LOG_PREFIX } from "../shared/branding"
import { PROD_SERVER_PORT } from "../shared/ports"

export interface CliOptions {
  port: number
  host: string
  openBrowser: boolean
  strictPort: boolean
}

export type CliRunResult =
  | { kind: "started"; stop: () => Promise<void> }
  | { kind: "exited"; code: number }

export interface CliRuntimeDeps {
  version: string
  bunVersion: string
  startServer: (options: CliOptions) => Promise<{ port: number; stop: () => Promise<void> }>
  openUrl: (url: string) => void
  log: (message: string) => void
  warn: (message: string) => void
}

function help() {
  return `${APP_NAME} — TradeEngine Agent Web\n\nUsage: ${CLI_COMMAND} [--port PORT] [--host HOST] [--no-open] [--strict-port]`
}

function parseArgs(argv: string[]): CliOptions | "help" | "version" {
  let port = PROD_SERVER_PORT
  let host = "127.0.0.1"
  let openBrowser = true
  let strictPort = false
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index]
    if (arg === "--help" || arg === "-h") return "help"
    if (arg === "--version" || arg === "-v") return "version"
    if (arg === "--no-open") { openBrowser = false; continue }
    if (arg === "--strict-port") { strictPort = true; continue }
    if (arg === "--port" || arg === "--host") {
      const value = argv[++index]
      if (!value) throw new Error(`Missing value for ${arg}`)
      if (arg === "--port") port = Number(value)
      else host = value
      continue
    }
    throw new Error(`Unsupported option: ${arg}`)
  }
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("Port must be between 1 and 65535")
  return { port, host, openBrowser, strictPort }
}

export async function runCli(argv: string[], deps: CliRuntimeDeps): Promise<CliRunResult> {
  let options: CliOptions | "help" | "version"
  try {
    options = parseArgs(argv)
  } catch (error) {
    deps.warn(`${LOG_PREFIX} ${error instanceof Error ? error.message : String(error)}`)
    return { kind: "exited", code: 2 }
  }
  if (options === "help") { deps.log(help()); return { kind: "exited", code: 0 } }
  if (options === "version") { deps.log(deps.version); return { kind: "exited", code: 0 } }
  const started = await deps.startServer(options)
  const displayHost = options.host === "0.0.0.0" ? "localhost" : options.host
  const url = `http://${displayHost}:${started.port}`
  deps.log(`${LOG_PREFIX} listening on ${url}`)
  if (options.openBrowser) deps.openUrl(url)
  return { kind: "started", stop: started.stop }
}

export function openUrl(url: string) {
  const command = process.platform === "darwin" ? "open" : process.platform === "win32" ? "cmd" : "xdg-open"
  const args = process.platform === "win32" ? ["/c", "start", "", url] : [url]
  spawnSync(command, args, { stdio: "ignore" })
}
