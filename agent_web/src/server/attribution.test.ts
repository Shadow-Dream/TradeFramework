import { describe, expect, test } from "bun:test"
import {
  buildKannaAgentCorrection,
  buildKannaAgentId,
  buildKannaAttributionInstructions,
} from "./attribution"

describe("TradeEngine agent boundary prompt", () => {
  test("identifies the native runtime and selected model", () => {
    expect(buildKannaAgentId("codex", "gpt-5.6-sol"))
      .toBe("TradeEngine Agent:codex:gpt-5.6-sol")
  })

  test("requires Engine resources to go through MCP authority", () => {
    const instructions = buildKannaAttributionInstructions("TradeEngine Agent:claude:deepseek-chat")
    expect(instructions).toContain("trade_engine MCP tools")
    expect(instructions).toContain("never infer or bypass")
  })

  test("model-switch correction contains only the active identity", () => {
    expect(buildKannaAgentCorrection("TradeEngine Agent:claude:deepseek-reasoner"))
      .toContain("TradeEngine Agent:claude:deepseek-reasoner")
  })
})
