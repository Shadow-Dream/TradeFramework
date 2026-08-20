import { describe, expect, test } from "bun:test"
import { TradeSessionConfigError, TradeSessionVerifier } from "./trade-session"

const COOKIE = "trade_session=session; trade_csrf=csrf"

function request(path = "/", init: RequestInit = {}) {
  return new Request(`http://10.130.130.66:30810${path}`, {
    ...init,
    headers: { Cookie: COOKIE, ...(init.headers ?? {}) },
  })
}

function verifier(fetchImpl: (input: string | URL | Request, init?: RequestInit) => Promise<Response>) {
  return new TradeSessionVerifier({
    tradeEnginePublicUrl: "http://10.130.130.66:30809",
    agentPublicUrl: "http://10.130.130.66:30810",
    fetchImpl,
  })
}

describe("TradeSessionVerifier", () => {
  test("projects only the safe TradeEngine identity fields", async () => {
    const calls: Array<[string, RequestInit | undefined]> = []
    const auth = verifier((async (url, init) => {
      calls.push([String(url), init])
      return Response.json({
        authenticated: true,
        user: { userId: "u1", email: "u@example.test", role: "admin", status: "active", secret: "no" },
        csrfToken: "csrf-token",
        expiresAt: new Date(Date.now() + 60_000).toISOString(),
        ignored: "value",
      })
    }))
    const verified = await auth.verify(request())
    expect(verified?.identity.userId).toBe("u1")
    expect(verified?.identity).not.toHaveProperty("status")
    expect(calls[0]?.[0]).toBe("http://10.130.130.66:30809/auth/session")
    expect((calls[0]?.[1]?.headers as Record<string, string>).Cookie).toBe(COOKIE)
  })

  test("normalizes TradeEngine's numeric session expiry without changing its API", async () => {
    const expiresAt = Math.floor(Date.now() / 1_000) + 60
    const auth = verifier(async () => Response.json({
      authenticated: true,
      user: { userId: "u1", email: "u@example.test", role: "admin" },
      csrfToken: "csrf-token",
      expiresAt,
    }))
    const verified = await auth.verify(request())
    expect(verified?.identity.expiresAt).toBe(new Date(expiresAt * 1_000).toISOString())
  })

  test("fails closed for missing, rejected, malformed, or expired sessions", async () => {
    const rejected = verifier(async () => new Response("no", { status: 401 }))
    expect(await rejected.verify(new Request("http://10.130.130.66:30810/"))).toBeNull()
    expect(await rejected.verify(request())).toBeNull()

    const malformed = verifier(async () => Response.json({ authenticated: true }))
    expect(await malformed.verify(request())).toBeNull()

    const expired = verifier((async () => Response.json({
      authenticated: true,
      user: { userId: "u1", email: "u@example.test", role: "user" },
      csrfToken: "csrf",
      expiresAt: "2000-01-01T00:00:00Z",
    })))
    expect(await expired.verify(request())).toBeNull()
  })

  test("allows only the configured Agent origin", () => {
    const auth = verifier(fetch)
    expect(auth.validateOrigin(request("/", { headers: { Origin: "http://10.130.130.66:30810" } }))).toBe(true)
    expect(auth.validateOrigin(request("/", { headers: { Origin: "http://evil.test" } }))).toBe(false)
  })

  test("allows Engine and Agent origins only for the shared UI socket", () => {
    const auth = verifier(fetch)
    expect(auth.validateUiSyncOrigin(request("/ws/ui", { headers: { Origin: "http://10.130.130.66:30810" } }))).toBe(true)
    expect(auth.validateUiSyncOrigin(request("/ws/ui", { headers: { Origin: "http://10.130.130.66:30809" } }))).toBe(true)
    expect(auth.validateUiSyncOrigin(request("/ws/ui", { headers: { Origin: "http://evil.test" } }))).toBe(false)
  })

  test("forwards logout with TradeEngine CSRF without returning it", async () => {
    const calls: RequestInit[] = []
    const auth = verifier((async (_url, init) => {
      calls.push(init ?? {})
      if (calls.length === 1) {
        return Response.json({
          authenticated: true,
          user: { userId: "u1", email: "u@example.test", role: "admin" },
          csrfToken: "secret-csrf",
          expiresAt: new Date(Date.now() + 60_000).toISOString(),
        })
      }
      return Response.json({ accepted: true }, { headers: { "Set-Cookie": "trade_session=; Path=/; Max-Age=0" } })
    }))
    const response = await auth.logout(request("/api/trade-auth/logout", {
      method: "POST",
      headers: { Origin: "http://10.130.130.66:30810" },
    }))
    expect(response.status).toBe(200)
    expect(JSON.stringify(await response.json())).not.toContain("secret-csrf")
    expect((calls[1]?.headers as Record<string, string>)["X-CSRF-Token"]).toBe("secret-csrf")
  })

  test("rejects mismatched hostnames and non-origin URLs", () => {
    expect(() => new TradeSessionVerifier({
      tradeEnginePublicUrl: "http://10.130.130.66:30809",
      agentPublicUrl: "http://localhost:30810",
    })).toThrow(TradeSessionConfigError)
    expect(() => new TradeSessionVerifier({
      tradeEnginePublicUrl: "http://10.130.130.66:30809/path",
      agentPublicUrl: "http://10.130.130.66:30810",
    })).toThrow(TradeSessionConfigError)
  })
})
