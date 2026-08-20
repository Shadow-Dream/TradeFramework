import { describe, expect, test } from "bun:test"
import { handleInternalUiToolCall } from "./server"
import { UiSyncHub } from "./ui-sync-hub"

describe("internal UI tool endpoint", () => {
  test("requires the bridge token and rejects browser origins", async () => {
    const hub = new UiSyncHub()
    const unauthorized = await handleInternalUiToolCall(new Request("http://agent/api/internal/ui-tools/call", {
      method: "POST",
      body: JSON.stringify({ tool: "trade_ui_state_get", arguments: {} }),
    }), "bridge-secret", hub)
    expect(unauthorized.status).toBe(403)

    const browser = await handleInternalUiToolCall(new Request("http://agent/api/internal/ui-tools/call", {
      method: "POST",
      headers: { Authorization: "Bearer bridge-secret", Origin: "http://engine" },
      body: JSON.stringify({ tool: "trade_ui_state_get", arguments: {} }),
    }), "bridge-secret", hub)
    expect(browser.status).toBe(403)
    hub.dispose()
  })

  test("returns only the live Hub result for an exact allowlisted call", async () => {
    const hub = new UiSyncHub({ now: () => Date.parse("2026-08-17T00:00:00Z") })
    const response = await handleInternalUiToolCall(new Request("http://agent/api/internal/ui-tools/call", {
      method: "POST",
      headers: {
        Authorization: "Bearer bridge-secret",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ tool: "trade_ui_state_get", arguments: {} }),
    }), "bridge-secret", hub)
    expect(response.status).toBe(200)
    const payload = await response.json() as { result: { serverSeq: number; activeTabId: string | null } }
    expect(payload.result.serverSeq).toBe(0)
    expect(payload.result.activeTabId).toBeNull()
    hub.dispose()
  })

  test("rejects unknown arguments before dispatch", async () => {
    const hub = new UiSyncHub()
    const response = await handleInternalUiToolCall(new Request("http://agent/api/internal/ui-tools/call", {
      method: "POST",
      headers: { Authorization: "Bearer bridge-secret", "Content-Type": "application/json" },
      body: JSON.stringify({ tool: "trade_ui_state_get", arguments: { path: "/tmp/leak" } }),
    }), "bridge-secret", hub)
    expect(response.status).toBe(400)
    hub.dispose()
  })

  test("requires an explicit JSON media type", async () => {
    const hub = new UiSyncHub()
    const response = await handleInternalUiToolCall(new Request("http://agent/api/internal/ui-tools/call", {
      method: "POST",
      headers: { Authorization: "Bearer bridge-secret", "Content-Type": "text/plain" },
      body: JSON.stringify({ tool: "trade_ui_state_get", arguments: {} }),
    }), "bridge-secret", hub)
    expect(response.status).toBe(415)
    hub.dispose()
  })
})
