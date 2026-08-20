/**
 * TradeEngine is the sole browser-session authority for Agent Web.
 *
 * This adapter never opens the TradeEngine auth database and never persists a
 * browser cookie.  It validates the exact cookie presented by the browser by
 * calling TradeEngine's public `/auth/session` contract.
 */

export type TradeRole = "admin" | "user"

export interface TradeIdentity {
  userId: string
  email: string
  role: TradeRole
  expiresAt: string
}

export interface VerifiedTradeSession {
  identity: TradeIdentity
  /** Used only for the same-request logout bridge. Never expose or persist. */
  csrfToken: string
}

export interface TradeSessionVerifierOptions {
  tradeEnginePublicUrl: string
  agentPublicUrl: string
  buildId?: string
  fetchImpl?: (input: string | URL | Request, init?: RequestInit) => Promise<Response>
}

export class TradeSessionConfigError extends Error {}

function configuredOrigin(value: string, label: string): URL {
  let parsed: URL
  try {
    parsed = new URL(value)
  } catch {
    throw new TradeSessionConfigError(`${label} must be an absolute http(s) URL.`)
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new TradeSessionConfigError(`${label} must use http or https.`)
  }
  if (parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new TradeSessionConfigError(`${label} must not include credentials, query, or fragment.`)
  }
  if (parsed.pathname !== "/" && parsed.pathname !== "") {
    throw new TradeSessionConfigError(`${label} must be an origin without a path.`)
  }
  parsed.pathname = "/"
  return parsed
}

function addDirectHost(hostname: string) {
  for (const name of ["NO_PROXY", "no_proxy"] as const) {
    const entries = (process.env[name] ?? "").split(",").map((item) => item.trim()).filter(Boolean)
    if (!entries.includes(hostname)) entries.push(hostname)
    process.env[name] = entries.join(",")
  }
}

function exactString(value: unknown, field: string, maxLength = 512): string {
  if (typeof value !== "string" || value.length === 0 || value.length > maxLength) {
    throw new Error(`TradeEngine session response has invalid ${field}.`)
  }
  return value
}

function exactExpiry(value: unknown): string {
  const milliseconds = typeof value === "number" && Number.isSafeInteger(value)
    ? value * 1_000
    : Date.parse(exactString(value, "expiresAt", 64))
  if (!Number.isFinite(milliseconds) || milliseconds <= Date.now()) {
    throw new Error("TradeEngine session is expired.")
  }
  return new Date(milliseconds).toISOString()
}

function parseIdentity(payload: unknown): VerifiedTradeSession {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("TradeEngine session response must be an object.")
  }
  const record = payload as Record<string, unknown>
  const user = record.user
  if (record.authenticated !== true || !user || typeof user !== "object" || Array.isArray(user)) {
    throw new Error("TradeEngine session response is not authenticated.")
  }
  const userRecord = user as Record<string, unknown>
  const role = exactString(userRecord.role, "user.role", 32)
  if (role !== "admin" && role !== "user") {
    throw new Error("TradeEngine session response has invalid user.role.")
  }
  const expiresAt = exactExpiry(record.expiresAt)
  return {
    identity: {
      userId: exactString(userRecord.userId, "user.userId", 128),
      email: exactString(userRecord.email, "user.email", 320),
      role,
      expiresAt,
    },
    csrfToken: exactString(record.csrfToken, "csrfToken", 512),
  }
}

function copySetCookieHeaders(from: Headers, to: Headers) {
  const headers = from as Headers & { getSetCookie?: () => string[] }
  const values = headers.getSetCookie?.() ?? []
  if (values.length > 0) {
    for (const value of values) to.append("Set-Cookie", value)
    return
  }
  const collapsed = from.get("set-cookie")
  if (collapsed) to.append("Set-Cookie", collapsed)
}

export class TradeSessionVerifier {
  readonly tradeEngineOrigin: string
  readonly agentOrigin: string
  readonly loginUrl: string
  readonly tradeEngineUrl: string
  readonly buildId: string
  private readonly fetchImpl: (input: string | URL | Request, init?: RequestInit) => Promise<Response>

  constructor(options: TradeSessionVerifierOptions) {
    const engine = configuredOrigin(options.tradeEnginePublicUrl, "tradeEnginePublicUrl")
    const agent = configuredOrigin(options.agentPublicUrl, "agentPublicUrl")
    if (engine.hostname !== agent.hostname) {
      throw new TradeSessionConfigError(
        "TradeEngine and Agent Web must use the same configured hostname so trade_session is shared.",
      )
    }
    this.tradeEngineOrigin = engine.origin
    this.agentOrigin = agent.origin
    this.tradeEngineUrl = `${engine.origin}/`
    this.loginUrl = `${engine.origin}/login?next=${encodeURIComponent("/agent")}`
    this.buildId = options.buildId?.trim() || "dev"
    // The Engine bridge is a same-host authority boundary. Never send its
    // session cookie or short-lived MCP grants through a host HTTP proxy.
    addDirectHost(engine.hostname)
    this.fetchImpl = options.fetchImpl ?? fetch
  }

  validateOrigin(request: Request): boolean {
    const origin = request.headers.get("origin")
    return origin === null || origin === this.agentOrigin
  }

  /** Engine SPA and proxied Jupyter pages join the separate ui-sync socket.
   * They share the host-scoped TradeEngine session cookie, but are never
   * allowed onto the Agent command socket. */
  validateUiSyncOrigin(request: Request): boolean {
    const origin = request.headers.get("origin")
    return origin === null || origin === this.agentOrigin || origin === this.tradeEngineOrigin
  }

  async verify(request: Request): Promise<VerifiedTradeSession | null> {
    return await this.verifyCookieHeader(request.headers.get("cookie"))
  }

  async verifyCookieHeader(cookieHeader: string | null): Promise<VerifiedTradeSession | null> {
    if (!cookieHeader || !/(?:^|;\s*)trade_session=/.test(cookieHeader)) return null
    let response: Response
    try {
      response = await this.fetchImpl(`${this.tradeEngineOrigin}/auth/session`, {
        method: "GET",
        headers: {
          Accept: "application/json",
          Cookie: cookieHeader,
        },
        redirect: "manual",
        cache: "no-store",
      })
    } catch {
      return null
    }
    if (response.status !== 200) return null
    try {
      return parseIdentity(await response.json())
    } catch {
      return null
    }
  }

  async statusResponse(request: Request): Promise<Response> {
    const verified = await this.verify(request)
    return Response.json({
      authenticated: verified !== null,
      user: verified?.identity ?? null,
      expiresAt: verified?.identity.expiresAt ?? null,
      loginUrl: this.loginUrl,
      tradeEngineUrl: this.tradeEngineUrl,
      agentPublicUrl: this.agentOrigin,
      build: this.buildId,
    }, { headers: { "Cache-Control": "no-store" } })
  }

  async logout(request: Request): Promise<Response> {
    if (!this.validateOrigin(request)) {
      return Response.json({ error: "Forbidden" }, { status: 403 })
    }
    const cookie = request.headers.get("cookie")
    const verified = await this.verifyCookieHeader(cookie)
    if (!verified || !cookie) {
      return Response.json({ error: "Authentication required." }, { status: 401 })
    }
    let upstream: Response
    try {
      upstream = await this.fetchImpl(`${this.tradeEngineOrigin}/auth/logout`, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          Cookie: cookie,
          "X-CSRF-Token": verified.csrfToken,
        },
        body: "{}",
        redirect: "manual",
      })
    } catch {
      return Response.json({ error: "TradeEngine authentication service is unavailable." }, { status: 503 })
    }
    const headers = new Headers({
      "Cache-Control": "no-store",
      "Content-Type": upstream.headers.get("content-type") ?? "application/json",
    })
    copySetCookieHeaders(upstream.headers, headers)
    return new Response(await upstream.arrayBuffer(), { status: upstream.status, headers })
  }

  async authenticatedPost(request: Request, path: string, payload: unknown): Promise<Response> {
    if (!path.startsWith("/") || path.startsWith("//")) throw new Error("Invalid TradeEngine path.")
    if (!this.validateOrigin(request)) return Response.json({ error: "Forbidden" }, { status: 403 })
    const cookie = request.headers.get("cookie")
    const verified = await this.verifyCookieHeader(cookie)
    if (!verified || !cookie) return Response.json({ error: "Authentication required." }, { status: 401 })
    try {
      const upstream = await this.fetchImpl(`${this.tradeEngineOrigin}${path}`, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          Cookie: cookie,
          "X-CSRF-Token": verified.csrfToken,
        },
        body: JSON.stringify(payload),
        redirect: "manual",
      })
      return new Response(await upstream.arrayBuffer(), {
        status: upstream.status,
        headers: {
          "Cache-Control": "no-store",
          "Content-Type": upstream.headers.get("content-type") ?? "application/json",
        },
      })
    } catch {
      return Response.json({ error: "TradeEngine service is unavailable." }, { status: 503 })
    }
  }
}
