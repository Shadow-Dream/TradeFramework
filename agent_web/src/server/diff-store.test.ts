import { afterEach, describe, expect, test } from "bun:test"
import { mkdtemp, rm, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import path from "node:path"
import { DiffStore, probeWorkingTree, readTreeBlobs, resolveWorkingTreeLocation } from "./diff-store"

const tempDirs: string[] = []

afterEach(async () => {
  await Promise.all(tempDirs.splice(0).map((dir) => rm(dir, { recursive: true, force: true })))
})

async function run(command: string[], cwd: string) {
  const process = Bun.spawn(command, { cwd, stdout: "pipe", stderr: "pipe" })
  const [stdout, stderr, exitCode] = await Promise.all([
    new Response(process.stdout).text(),
    new Response(process.stderr).text(),
    process.exited,
  ])
  if (exitCode !== 0) throw new Error(stderr || stdout || `Command failed: ${command.join(" ")}`)
  return stdout
}

async function createRepo() {
  const root = await mkdtemp(path.join(tmpdir(), "trade-agent-diff-"))
  tempDirs.push(root)
  await run(["git", "init", "-b", "main"], root)
  await run(["git", "config", "user.email", "agent@example.invalid"], root)
  await run(["git", "config", "user.name", "TradeEngine Agent"], root)
  await writeFile(path.join(root, "app.txt"), "base\n", "utf8")
  await run(["git", "add", "."], root)
  await run(["git", "commit", "-m", "init"], root)
  return root
}

describe("DiffStore read-only local diff", () => {
  test("reports modified and untracked files and returns their patches", async () => {
    const root = await createRepo()
    await writeFile(path.join(root, "app.txt"), "changed\n", "utf8")
    await writeFile(path.join(root, "notes.txt"), "one\ntwo\n", "utf8")
    const store = new DiffStore(root)
    await store.refreshSnapshot("project-1", root)

    const snapshot = store.getProjectSnapshot("project-1")
    expect(snapshot).toMatchObject({ status: "ready", branchName: "main" })
    expect(snapshot.files.map((file) => file.path).sort()).toEqual(["app.txt", "notes.txt"])
    expect(snapshot.files.find((file) => file.path === "notes.txt")).toMatchObject({
      changeType: "added",
      isUntracked: true,
      additions: 2,
    })
    await expect(store.readPatch({ projectPath: root, path: "app.txt" })).resolves.toMatchObject({
      patch: expect.stringContaining("-base"),
    })
  })

  test("returns no_repo without leaking or inventing repository state", async () => {
    const root = await mkdtemp(path.join(tmpdir(), "trade-agent-no-repo-"))
    tempDirs.push(root)
    const store = new DiffStore(root)
    await store.refreshSnapshot("project-1", root)
    expect(store.getProjectSnapshot("project-1")).toEqual({ status: "no_repo", files: [] })
  })

  test("increments versions only when the snapshot changes", async () => {
    const root = await createRepo()
    const store = new DiffStore(root)
    await store.refreshSnapshot("project-1", root)
    const first = store.getSnapshotVersion("project-1")
    await store.refreshSnapshot("project-1", root)
    expect(store.getSnapshotVersion("project-1")).toBe(first)
    await writeFile(path.join(root, "new.txt"), "new\n", "utf8")
    await store.refreshSnapshot("project-1", root)
    expect(store.getSnapshotVersion("project-1")).toBe(first + 1)
  })

  test("rejects paths outside the approved repository", async () => {
    const root = await createRepo()
    await expect(new DiffStore(root).readPatch({ projectPath: root, path: "../secret" })).rejects.toThrow("Invalid diff path")
  })
})

describe("working tree probes", () => {
  test("reports dirty paths and resolves repository metadata", async () => {
    const root = await createRepo()
    await writeFile(path.join(root, "new.txt"), "new\n", "utf8")
    expect(await probeWorkingTree(root)).toEqual({ dirty: true, paths: ["new.txt"] })
    const location = await resolveWorkingTreeLocation(root)
    expect(location?.repoRoot).toBe(root)
    expect(location?.gitDir).toContain(".git")
  })

  test("reads pinned tree blob identities", async () => {
    const root = await createRepo()
    const blobs = await readTreeBlobs(root, "HEAD", ["app.txt", "missing.txt"])
    expect(blobs?.get("app.txt")).toMatch(/^[0-9a-f]{40}$/)
    expect(blobs?.get("missing.txt")).toBeNull()
  })

  test("fails closed outside a repository", async () => {
    const root = await mkdtemp(path.join(tmpdir(), "trade-agent-probe-"))
    tempDirs.push(root)
    expect(await probeWorkingTree(root)).toEqual({ dirty: false, paths: [] })
    expect(await resolveWorkingTreeLocation(root)).toBeNull()
  })
})
