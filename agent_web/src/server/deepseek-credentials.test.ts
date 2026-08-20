import { afterEach, describe, expect, test } from "bun:test"
import { mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import path from "node:path"
import { DeepSeekCredentialStore, parseDeepSeekProfile } from "./deepseek-credentials"

const tempDirs: string[] = []

afterEach(async () => {
  await Promise.all(tempDirs.splice(0).map((directory) => rm(directory, { recursive: true, force: true })))
})

async function fixture() {
  const root = await mkdtemp(path.join(tmpdir(), "kanna-deepseek-"))
  tempDirs.push(root)
  return {
    root,
    profilePath: path.join(root, ".setdeepseek"),
  }
}

const validProfile = `
export ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
export ANTHROPIC_AUTH_TOKEN=test-private-key
export ANTHROPIC_MODEL=deepseek-v4-pro[1m]
export ANTHROPIC_DEFAULT_SONNET_MODEL=deepseek-v4-pro[1m]
export ANTHROPIC_DEFAULT_HAIKU_MODEL=deepseek-v4-flash
export CLAUDE_CODE_SUBAGENT_MODEL=deepseek-v4-flash
export CLAUDE_CODE_EFFORT_LEVEL=max
`

describe("DeepSeekCredentialStore", () => {
  test("imports the assignment-only profile without exposing its secret in status", async () => {
    const { root, profilePath } = await fixture()
    await writeFile(profilePath, validProfile, { mode: 0o600 })
    const store = new DeepSeekCredentialStore(root, profilePath)

    const status = await store.getStatus()
    expect(status).toEqual({
      configured: true,
      source: "setdeepseek",
      defaultModel: "deepseek-v4-pro[1m]",
      models: ["deepseek-v4-pro[1m]", "deepseek-v4-flash"],
    })
    expect(JSON.stringify(status)).not.toContain("test-private-key")

    const environment = await store.getEnvironment()
    expect(environment.ANTHROPIC_AUTH_TOKEN).toBe("test-private-key")
    expect(environment.CLAUDE_CODE_SUBAGENT_MODEL).toBe("deepseek-v4-flash")
    expect(environment.CLAUDE_CONFIG_DIR).toBe(path.join(root, "credentials", "claude-deepseek"))
  })

  test("rejects commands, unknown fields, duplicates, and incomplete profiles", () => {
    const invalid = [
      "source ~/.other\n",
      `${validProfile}PATH=/tmp\n`,
      `${validProfile}ANTHROPIC_MODEL=other\n`,
      "ANTHROPIC_AUTH_TOKEN=key\nANTHROPIC_MODEL=model\n",
      "ANTHROPIC_AUTH_TOKEN=$(whoami)\nANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic\nANTHROPIC_MODEL=model\n",
    ]
    for (const profile of invalid) expect(() => parseDeepSeekProfile(profile)).toThrow()
  })

  test("a UI key is stored privately and overrides the imported key", async () => {
    const { root, profilePath } = await fixture()
    await writeFile(profilePath, validProfile, { mode: 0o600 })
    const store = new DeepSeekCredentialStore(root, profilePath)

    await store.setApiKey("replacement-private-key")
    expect((await store.getStatus()).source).toBe("managed")
    expect((await store.getEnvironment()).ANTHROPIC_AUTH_TOKEN).toBe("replacement-private-key")
    expect((await stat(store.filePath)).mode & 0o777).toBe(0o600)
    expect(await readFile(store.filePath, "utf8")).not.toContain("test-private-key")
  })
})
