import { describe, expect, test } from "bun:test"
import { renderToStaticMarkup } from "react-dom/server"
import { RefreshCw } from "lucide-react"
import {
  getKeybindingsSubtitle,
  resolveSettingsSectionId,
  shouldPreviewChatSoundChange,
  SkillsSection,
} from "./SettingsPage"
import { SettingsHeaderButton } from "../components/ui/settings-header-button"

describe("resolveSettingsSectionId", () => {
  test("accepts only current TradeEngine Agent settings sections", () => {
    for (const id of ["general", "providers", "keybindings", "skills", "usage", "labs"]) {
      expect(resolveSettingsSectionId(id)).toBe(id)
    }
    for (const id of ["changelog", "github", "cloud", "updates", "nope"]) {
      expect(resolveSettingsSectionId(id)).toBeNull()
    }
  })
})

describe("SkillsSection", () => {
  test("renders the four canonical TradeEngine task skills", () => {
    const html = renderToStaticMarkup(<SkillsSection />)
    expect(html).toContain("Strategy Development")
    expect(html).toContain("Dataset Preparation")
    expect(html).toContain("Backtest Investigation")
    expect(html).toContain("Research Verification")
  })
})

describe("settings helpers", () => {
  test("does not expose the keybindings filesystem location", () => {
    expect(getKeybindingsSubtitle("~/.trade-agent-dev/keybindings.json")).toBe(
      "Edit Agent Web keyboard shortcuts."
    )
  })

  test("previews only actual sound changes", () => {
    expect(shouldPreviewChatSoundChange("always", "always")).toBe(false)
    expect(shouldPreviewChatSoundChange("always", "never")).toBe(true)
  })

  test("renders the shared settings header action", () => {
    const html = renderToStaticMarkup(
      <SettingsHeaderButton icon={<RefreshCw className="size-3.5" />}>Refresh</SettingsHeaderButton>
    )
    expect(html).toContain("Refresh")
    expect(html).toContain("lucide-refresh-cw")
  })
})
