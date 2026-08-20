import { describe, expect, test } from "bun:test"
import {
  getDataDir,
  getDataDirDisplay,
  getDataRootName,
  getKeybindingsFilePath,
  getKeybindingsFilePathDisplay,
  getRuntimeProfile,
} from "./branding"

describe("runtime profile helpers", () => {
  test("defaults to the prod profile when unset", () => {
    expect(getRuntimeProfile({})).toBe("prod")
    expect(getDataRootName({})).toBe(".trade-agent")
    expect(getDataDir("/tmp/home", {})).toBe("/tmp/home/.trade-agent/data")
    expect(getDataDirDisplay({})).toBe("~/.trade-agent/data")
    expect(getKeybindingsFilePath("/tmp/home", {})).toBe("/tmp/home/.trade-agent/keybindings.json")
    expect(getKeybindingsFilePathDisplay({})).toBe("~/.trade-agent/keybindings.json")
  })

  test("switches to dev paths for the dev profile", () => {
    const env = { TRADE_AGENT_RUNTIME_PROFILE: "dev" }

    expect(getRuntimeProfile(env)).toBe("dev")
    expect(getDataRootName(env)).toBe(".trade-agent-dev")
    expect(getDataDir("/tmp/home", env)).toBe("/tmp/home/.trade-agent-dev/data")
    expect(getDataDirDisplay(env)).toBe("~/.trade-agent-dev/data")
    expect(getKeybindingsFilePath("/tmp/home", env)).toBe("/tmp/home/.trade-agent-dev/keybindings.json")
    expect(getKeybindingsFilePathDisplay(env)).toBe("~/.trade-agent-dev/keybindings.json")
  })
})
