import { afterEach, describe, expect, test } from "bun:test"
import { chmodSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import path from "node:path"
import { resolveCommandPath, spawnDetached } from "./process-utils"

let fakeHome: string | null = null

afterEach(() => {
  if (fakeHome) rmSync(fakeHome, { recursive: true, force: true })
  fakeHome = null
})

describe("resolveCommandPath", () => {
  test("finds binaries the login-shell PATH misses in well-known user bin dirs", () => {
    // e.g. the native Claude Code installer's ~/.local/bin, whose PATH line
    // goes to the interactive shell rc that `sh -lc` never reads on macOS.
    fakeHome = mkdtempSync(path.join(tmpdir(), "kanna-home-"))
    const binDir = path.join(fakeHome, ".local", "bin")
    mkdirSync(binDir, { recursive: true })
    const binary = path.join(binDir, "kanna-fake-cli")
    writeFileSync(binary, "#!/bin/sh\nexit 0\n")
    chmodSync(binary, 0o755)

    expect(resolveCommandPath("kanna-fake-cli", fakeHome)).toBe(binary)
    expect(resolveCommandPath("kanna-missing-cli", fakeHome)).toBeNull()
  })

  test("login-shell resolution still wins when it succeeds", () => {
    fakeHome = mkdtempSync(path.join(tmpdir(), "kanna-home-"))
    expect(resolveCommandPath("sh", fakeHome)).toMatch(/\/sh$/)
  })
})

describe("spawnDetached", () => {
  test("rejects when the command does not exist", async () => {
    await expect(spawnDetached("definitely-not-a-real-command-kanna", [])).rejects.toThrow("Command not found")
  })

  test("resolves when the process starts successfully", async () => {
    await expect(spawnDetached("sh", ["-c", "exit 0"])).resolves.toBeUndefined()
  })
})
