import { describe, expect, test } from "bun:test"
import { containsForbiddenClientKey } from "./ws-router"

describe("Agent Web ingress contract", () => {
  test("rejects filesystem identity fields anywhere in a browser command", () => {
    for (const key of ["localPath", "absolutePath", "cwd", "workspacePath", "controlPath", "archivePath", "manifestPath"]) {
      expect(containsForbiddenClientKey({
        v: 1,
        type: "command",
        command: { type: "chat.create", projectId: "trade-engine", metadata: { [key]: "/private/path" } },
      })).toBe(true)
    }
  })

  test("allows only logical Project and exact Context identities", () => {
    expect(containsForbiddenClientKey({
      v: 1,
      type: "command",
      command: {
        type: "chat.send",
        projectId: "strategy:momentum-lab",
        provider: "claude-deepseek",
        model: "deepseek-chat",
        context: {
          schemaVersion: "1",
          sourceView: "pipeline",
          capturedAt: "2026-08-16T00:00:00Z",
          references: [{ kind: "pipeline", id: "pipeline-1", version: "3" }],
        },
      },
    })).toBe(false)
  })
})
