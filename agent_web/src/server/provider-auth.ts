import {
  AUTH_SERVICE_LABELS,
  AUTH_SERVICE_ORDER,
  type AuthServiceId,
  type AuthServiceSnapshot,
  type ProviderAuthSnapshot,
} from "../shared/types"

export interface ExecResult {
  code: number
  stdout: string
  stderr: string
}

export interface ProviderAuthManagerDeps {
  exec: (argv: string[], opts?: { stdin?: string; env?: Record<string, string>; timeoutMs?: number }) => Promise<ExecResult>
  codexDeviceAuth?: {
    start: (onCompleted: (result: { success: boolean; error?: string | null }) => void) => Promise<{
      verificationUrl: string
      userCode: string
    }>
    cancel: () => Promise<void>
  }
  codexAccount?: () => Promise<{ account?: { type: string; email?: string | null; planType?: string } | null } | null>
  deepSeekCredentials?: {
    getStatus: () => Promise<{ configured: boolean; source: "managed" | "setdeepseek" | null }>
    setApiKey: (apiKey: string) => Promise<unknown>
  }
  onSignedIn?: (service: AuthServiceId) => void
  allowedServices?: readonly AuthServiceId[]
}

function initialSnapshot(service: AuthServiceId): AuthServiceSnapshot {
  return {
    service,
    label: AUTH_SERVICE_LABELS[service],
    installed: false,
    version: null,
    latestVersion: null,
    updateAvailable: false,
    authStatus: "unknown",
    account: null,
    statusDetail: null,
    login: { phase: "idle" },
    installState: "idle",
    installError: null,
    checkedAt: null,
  }
}

function versionFrom(text: string) {
  return text.trim().match(/\d+(?:\.\d+){1,3}/)?.[0] ?? null
}

export class ProviderAuthManager {
  private readonly deps: ProviderAuthManagerDeps
  private readonly allowedServices: readonly AuthServiceId[]
  private readonly services = new Map<AuthServiceId, AuthServiceSnapshot>()
  private readonly listeners = new Set<(snapshot: ProviderAuthSnapshot) => void>()
  private loginActive = false
  private disposed = false

  constructor(deps: ProviderAuthManagerDeps) {
    this.deps = deps
    this.allowedServices = deps.allowedServices ?? AUTH_SERVICE_ORDER
    for (const service of this.allowedServices) this.services.set(service, initialSnapshot(service))
  }

  getSnapshot(): ProviderAuthSnapshot {
    return { services: this.allowedServices.map((service) => structuredClone(this.services.get(service)!)) }
  }

  onChange(listener: (snapshot: ProviderAuthSnapshot) => void) {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  private publish() {
    const snapshot = this.getSnapshot()
    for (const listener of this.listeners) listener(snapshot)
  }

  private update(service: AuthServiceId, patch: Partial<AuthServiceSnapshot>) {
    const current = this.services.get(service)
    if (!current) throw new Error("Unsupported authentication service.")
    this.services.set(service, { ...current, ...patch })
    this.publish()
  }

  async refresh(_options: { force?: boolean } = {}) {
    await Promise.all(this.allowedServices.map(async (service) => {
      if (service === "claude") {
        const [version, credentials] = await Promise.all([
          this.deps.exec(["claude", "--version"], { timeoutMs: 5_000 }).catch(() => null),
          this.deps.deepSeekCredentials?.getStatus().catch(() => null),
        ])
        this.update(service, {
          installed: version?.code === 0,
          version: versionFrom(`${version?.stdout ?? ""}\n${version?.stderr ?? ""}`),
          authStatus: credentials?.configured ? "signed_in" : "signed_out",
          account: credentials?.configured ? "DeepSeek API key" : null,
          statusDetail: version?.code === 0 ? null : "Claude Code is not installed.",
          checkedAt: Date.now(),
          login: { phase: "idle" },
        })
        return
      }
      const [version, status] = await Promise.all([
        this.deps.exec(["codex", "--version"], { timeoutMs: 5_000 }).catch(() => null),
        this.deps.codexAccount?.().catch(() => null),
      ])
      const signedIn = status?.account?.type === "chatgpt"
      this.update(service, {
        installed: version?.code === 0,
        version: versionFrom(`${version?.stdout ?? ""}\n${version?.stderr ?? ""}`),
        authStatus: signedIn ? "signed_in" : "signed_out",
        account: signedIn
          ? (status?.account?.email || status?.account?.planType || "ChatGPT account")
          : null,
        statusDetail: version?.code !== 0
          ? "Codex is not installed."
          : status?.account?.type === "apiKey"
            ? "Sign in with ChatGPT device code; API-key accounts are not used by this backend."
            : null,
        checkedAt: Date.now(),
        login: { phase: "idle" },
      })
    }))
  }

  async install(service: AuthServiceId) {
    this.update(service, { installState: "error", installError: "Backends are installed by the TradeEngine deployment." })
  }

  startLogin(service: AuthServiceId) {
    if (service !== "codex") throw new Error("Claude Code + DeepSeek uses an API key.")
    if (this.loginActive) throw new Error("Codex sign-in is already running.")
    if (!this.deps.codexDeviceAuth) throw new Error("Codex device sign-in is unavailable.")
    this.loginActive = true
    this.update("codex", { login: { phase: "starting" } })
    void this.deps.codexDeviceAuth.start(async (result) => {
      if (!this.loginActive || this.disposed) return
      this.loginActive = false
      if (result.success) {
        await this.refresh({ force: true })
        this.deps.onSignedIn?.("codex")
      } else {
        this.update("codex", { login: { phase: "error", message: result.error || "Codex sign-in failed.", hint: null } })
      }
    }).then((challenge) => {
      if (!this.loginActive || this.disposed) return
      this.update("codex", {
        login: {
          phase: "waiting_for_approval",
          verificationUrl: challenge.verificationUrl,
          userCode: challenge.userCode,
          startedAt: Date.now(),
          expiresAt: null,
        },
      })
    }).catch((error) => {
      if (!this.loginActive || this.disposed) return
      this.loginActive = false
      this.update("codex", {
        login: { phase: "error", message: error instanceof Error ? error.message : "Codex sign-in failed.", hint: null },
      })
    })
  }

  submitLoginCode(service: AuthServiceId, _code: string) {
    if (service !== "codex") throw new Error("Unsupported authentication service.")
    throw new Error("Codex device login completes in the browser.")
  }

  async cancelLogin(service: AuthServiceId) {
    if (service !== "codex") throw new Error("Unsupported authentication service.")
    this.loginActive = false
    await this.deps.codexDeviceAuth?.cancel()
    this.update("codex", { login: { phase: "idle" } })
  }

  async setDeepSeekApiKey(apiKey: string) {
    if (!this.deps.deepSeekCredentials) throw new Error("DeepSeek credential storage is unavailable.")
    await this.deps.deepSeekCredentials.setApiKey(apiKey)
    await this.refresh({ force: true })
    this.deps.onSignedIn?.("claude")
  }

  dispose() {
    this.disposed = true
    this.loginActive = false
    void this.deps.codexDeviceAuth?.cancel()
    this.listeners.clear()
  }
}

export function createProcessAuthDeps(): Pick<ProviderAuthManagerDeps, "exec"> {
  return {
    async exec(argv, opts) {
      const proc = Bun.spawn(argv, {
        stdin: opts?.stdin === undefined ? "ignore" : new TextEncoder().encode(opts.stdin),
        stdout: "pipe",
        stderr: "pipe",
        env: { ...process.env, ...opts?.env },
      })
      let timer: ReturnType<typeof setTimeout> | null = null
      if (opts?.timeoutMs) timer = setTimeout(() => proc.kill(), opts.timeoutMs)
      try {
        const [stdout, stderr, code] = await Promise.all([
          new Response(proc.stdout).text(),
          new Response(proc.stderr).text(),
          proc.exited,
        ])
        return { code, stdout, stderr }
      } finally {
        if (timer) clearTimeout(timer)
      }
    },
  }
}
