import { afterEach, describe, expect, test } from "bun:test"
import { mkdtemp, rm, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import path from "node:path"
import { createEmptyState } from "./events"
import { WorktreeProbe } from "./worktree-probe"

const tempDirs: string[] = []

afterEach(async () => {
  await Promise.all(tempDirs.splice(0).map((dir) => rm(dir, { recursive: true, force: true })))
})

async function run(argv: string[], cwd: string) {
  const child = Bun.spawn(argv, { cwd, stdout: "pipe", stderr: "pipe" })
  const [stdout, stderr, code] = await Promise.all([
    new Response(child.stdout).text(),
    new Response(child.stderr).text(),
    child.exited,
  ])
  if (code !== 0) throw new Error(stderr || stdout)
}

async function createRepo() {
  const root = await mkdtemp(path.join(tmpdir(), "trade-agent-probe-"))
  tempDirs.push(root)
  await run(["git", "init", "-b", "main"], root)
  await run(["git", "config", "user.email", "agent@example.invalid"], root)
  await run(["git", "config", "user.name", "Trade Agent"], root)
  await writeFile(path.join(root, "app.txt"), "base\n")
  await run(["git", "add", "."], root)
  await run(["git", "commit", "-m", "init"], root)
  return root
}

function stateWithChat() {
  const state = createEmptyState()
  state.projectsById.set("trade-engine", {
    id: "trade-engine",
    workspaceKey: "trade-engine",
    title: "TradeEngine",
    kind: "trade-engine",
    createdAt: 1,
    updatedAt: 1,
  })
  state.chatsById.set("chat-1", {
    id: "chat-1",
    ownerId: "owner-1",
    createRequestId: "request-1",
    projectId: "trade-engine",
    title: "Chat",
    createdAt: 1,
    updatedAt: 1,
    unread: false,
    provider: "claude-deepseek",
    planMode: false,
    autoPlan: false,
    sessionToken: null,
    lastTurnOutcome: null,
    lastTurnEndedAt: 1,
  })
  return state
}

describe("WorktreeProbe", () => {
  test("derives local dirty paths without persisting a filesystem identity", async () => {
    const repoRoot = await createRepo()
    const state = stateWithChat()
    const probe = new WorktreeProbe(() => state, () => {}, () => repoRoot)

    await probe.refreshForChat("chat-1")
    expect(probe.getStates().get("trade-engine")?.dirty).toBe(false)

    await writeFile(path.join(repoRoot, "app.txt"), "changed\n")
    await probe.refreshForChat("chat-1")
    expect([...(probe.getStates().get("trade-engine")?.paths ?? [])]).toEqual(["app.txt"])
    expect(state.projectsById.get("trade-engine")).not.toHaveProperty("localPath")
  })

  test("publishes only a local repository and branch label", async () => {
    const repoRoot = await createRepo()
    const probe = new WorktreeProbe(() => stateWithChat(), () => {}, () => repoRoot)
    await probe.refreshForChat("chat-1")
    expect(probe.getRepoLabels().get("trade-engine")).toEqual({
      repoName: path.basename(repoRoot),
      branchName: "main",
    })
  })
})
