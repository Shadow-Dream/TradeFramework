import { describe, expect, test } from "bun:test"
import { renderToStaticMarkup } from "react-dom/server"
import { PROVIDERS } from "../../../shared/types"
import { ChatPreferenceControls } from "./ChatPreferenceControls"

describe("ChatPreferenceControls", () => {
  test("renders Codex model, reasoning and fast-mode controls", () => {
    const html = renderToStaticMarkup(
      <ChatPreferenceControls
        availableProviders={PROVIDERS}
        selectedProvider="codex-openai"
        model="gpt-5.5"
        modelOptions={{ reasoningEffort: "xhigh", fastMode: true }}
        onProviderChange={() => {}}
        onModelChange={() => {}}
        onModelOptionChange={() => {}}
        includeMode={false}
      />
    )
    expect(html).toContain("Codex + GPT")
    expect(html).toContain("GPT-5.5")
    expect(html).toContain("Extra High")
    expect(html).toContain("Fast Mode")
    expect(html).not.toContain("Plan Mode")
  })

  test("hides fast mode where the Codex catalog does not support it", () => {
    const html = renderToStaticMarkup(
      <ChatPreferenceControls
        availableProviders={PROVIDERS}
        selectedProvider="codex-openai"
        model="gpt-5.3-codex"
        modelOptions={{ reasoningEffort: "xhigh", fastMode: false }}
        onModelChange={() => {}}
        onModelOptionChange={() => {}}
        includeMode={false}
      />
    )
    expect(html).toContain("GPT-5.3 Codex")
    expect(html).not.toContain("Fast Mode")
  })

  test("renders all supported GPT-5.6 reasoning levels", () => {
    const html = renderToStaticMarkup(
      <ChatPreferenceControls
        availableProviders={PROVIDERS}
        selectedProvider="codex-openai"
        model="gpt-5.6-sol"
        modelOptions={{ reasoningEffort: "ultra", fastMode: false }}
        onModelChange={() => {}}
        onModelOptionChange={() => {}}
        includeMode={false}
      />
    )
    expect(html).toContain("GPT-5.6 Sol")
    expect(html).toContain("Ultra")
  })

  test("renders DeepSeek models and modes without Anthropic-only tuning", () => {
    const html = renderToStaticMarkup(
      <ChatPreferenceControls
        availableProviders={PROVIDERS}
        selectedProvider="claude-deepseek"
        model="deepseek-reasoner"
        modelOptions={{ reasoningEffort: "high", contextWindow: "1m", fastMode: false }}
        onModelChange={() => {}}
        onModelOptionChange={() => {}}
        mode="plan"
        onModeChange={() => {}}
        includeMode
      />
    )
    expect(html).toContain("Claude Code + DeepSeek")
    expect(html).toContain("DeepSeek Reasoner")
    expect(html).toContain("Plan Mode")
    expect(html).not.toContain("1M")
    expect(html).not.toContain("Fast Mode")
    expect(html).not.toContain("Max")
    expect(html).not.toContain("Fable")
  })
})
