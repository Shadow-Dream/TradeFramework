import { afterEach, describe, expect, test } from "bun:test"
import { mkdtempSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import path from "node:path"
import { getMachineDisplayName, readMachineNameOverride } from "./machine-name"

let tempDir: string | null = null
const MISSING = "/nonexistent/never-here"

function tempFile(name: string, content: string) {
  tempDir ??= mkdtempSync(path.join(tmpdir(), "kanna-machine-name-"))
  const filePath = path.join(tempDir, name)
  writeFileSync(filePath, content)
  return filePath
}

afterEach(() => {
  if (tempDir) rmSync(tempDir, { recursive: true, force: true })
  tempDir = null
})

describe("machine-name override file", () => {
  test("first non-empty line wins over everything", () => {
    const override = tempFile("machine-name", "preview: main\nsecond line ignored\n")
    expect(readMachineNameOverride(override)).toBe("preview: main")
    expect(getMachineDisplayName(undefined, override)).toBe("preview: main")
  })

  test("missing or blank override files fall through", () => {
    expect(readMachineNameOverride(MISSING)).toBeNull()
    expect(readMachineNameOverride(tempFile("machine-name", "\n \n"))).toBeNull()
  })
})
