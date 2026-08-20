import { createHash } from "node:crypto"
import { lstat, readFile } from "node:fs/promises"
import path from "node:path"
import type { ChatDiffFile, ChatDiffSnapshot } from "../shared/types"

const MAX_HEAD_BLOB_PATHS = 1_000
const HEAD_BLOB_PATH_BATCH = 200
const MAX_PATCH_FILE_BYTES = 5 * 1024 * 1024

interface DirtyPathEntry {
  path: string
  previousPath?: string
  changeType: ChatDiffFile["changeType"]
  isUntracked: boolean
}

export async function runGit(
  args: string[],
  cwd: string,
  options?: { stdin?: string; env?: Record<string, string | undefined> },
) {
  const child = Bun.spawn(["git", "-C", cwd, ...args], {
    stdin: options?.stdin === undefined ? undefined : Buffer.from(options.stdin),
    ...(options?.env ? { env: options.env } : {}),
    stdout: "pipe",
    stderr: "pipe",
  })
  const [stdout, stderr, exitCode] = await Promise.all([
    new Response(child.stdout).text(),
    new Response(child.stderr).text(),
    child.exited,
  ])
  return { stdout, stderr, exitCode }
}

function classifyStatus(status: string, untracked: boolean): ChatDiffFile["changeType"] {
  if (untracked || status.includes("A")) return "added"
  if (status.includes("D")) return "deleted"
  if (status.includes("R") || status.includes("C")) return "renamed"
  return "modified"
}

async function listDirtyPaths(repoRoot: string): Promise<DirtyPathEntry[]> {
  const result = await runGit(
    ["-c", "core.quotepath=false", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
    repoRoot,
  )
  if (result.exitCode !== 0) throw new Error(result.stderr.trim() || "Unable to inspect local changes")

  const records = result.stdout.split("\0")
  const entries: DirtyPathEntry[] = []
  for (let index = 0; index < records.length; index += 1) {
    const record = records[index]
    if (!record || record.length < 4) continue
    const status = record.slice(0, 2)
    const filePath = record.slice(3)
    const isRename = status.includes("R") || status.includes("C")
    const previousPath = isRename ? records[++index] : undefined
    const isUntracked = status === "??"
    entries.push({
      path: filePath,
      ...(previousPath ? { previousPath } : {}),
      changeType: classifyStatus(status, isUntracked),
      isUntracked,
    })
  }
  return entries
}

export interface WorkingTreeScan {
  dirty: boolean
  paths: string[]
}

export interface WorkingTreeProbe {
  dirty: boolean
  paths: ReadonlySet<string>
  headBlobs: ReadonlyMap<string, string | null>
}

export interface WorkingTreeLocation {
  repoRoot: string
  gitDir: string
}

function dirtyPathNames(entries: DirtyPathEntry[]) {
  const names = new Set<string>()
  for (const entry of entries) {
    names.add(entry.path)
    if (entry.previousPath) names.add(entry.previousPath)
  }
  return [...names]
}

export async function resolveWorkingTreeLocation(projectPath: string): Promise<WorkingTreeLocation | null> {
  const [root, gitDir] = await Promise.all([
    runGit(["rev-parse", "--show-toplevel"], projectPath),
    runGit(["rev-parse", "--absolute-git-dir"], projectPath),
  ])
  if (root.exitCode !== 0 || gitDir.exitCode !== 0) return null
  const repoRoot = root.stdout.trim()
  const absoluteGitDir = gitDir.stdout.trim()
  return repoRoot && absoluteGitDir ? { repoRoot, gitDir: absoluteGitDir } : null
}

export async function probeWorkingTree(repoRoot: string): Promise<WorkingTreeScan> {
  try {
    const entries = await listDirtyPaths(repoRoot)
    return { dirty: entries.length > 0, paths: dirtyPathNames(entries) }
  } catch {
    return { dirty: false, paths: [] }
  }
}

export async function readTreeBlobs(
  repoRoot: string,
  treeish: string,
  paths: readonly string[],
): Promise<Map<string, string | null> | null> {
  if (paths.length === 0) return new Map()
  if (paths.length > MAX_HEAD_BLOB_PATHS) return null
  const blobs = new Map<string, string | null>(paths.map((filePath) => [filePath, null]))
  for (let index = 0; index < paths.length; index += HEAD_BLOB_PATH_BATCH) {
    const batch = paths.slice(index, index + HEAD_BLOB_PATH_BATCH)
    const result = await runGit(["ls-tree", "-z", treeish, "--", ...batch], repoRoot)
    if (result.exitCode !== 0) return null
    for (const record of result.stdout.split("\0")) {
      const tab = record.indexOf("\t")
      if (tab === -1) continue
      const sha = record.slice(0, tab).split(" ")[2]
      const filePath = record.slice(tab + 1)
      if (sha && blobs.has(filePath)) blobs.set(filePath, sha)
    }
  }
  return blobs
}

function normalizeRepoPath(value: string) {
  const normalized = path.posix.normalize(value.replaceAll("\\", "/")).replace(/^\.\//u, "")
  if (!normalized || normalized === "." || normalized.startsWith("../") || normalized.includes("/../") || path.posix.isAbsolute(normalized)) {
    throw new Error("Invalid diff path")
  }
  return normalized
}

async function lineCount(absolutePath: string) {
  const info = await lstat(absolutePath).catch(() => null)
  if (!info?.isFile() || info.size > MAX_PATCH_FILE_BYTES) return 0
  const content = await readFile(absolutePath)
  let lines = 0
  for (const byte of content) if (byte === 10) lines += 1
  return content.length > 0 && content.at(-1) !== 10 ? lines + 1 : lines
}

async function diffStats(repoRoot: string, hasHead: boolean) {
  const args = hasHead ? ["diff", "--numstat", "-z", "HEAD"] : ["diff", "--cached", "--numstat", "-z"]
  const result = await runGit(args, repoRoot)
  const stats = new Map<string, { additions: number; deletions: number }>()
  if (result.exitCode !== 0) return stats
  for (const record of result.stdout.split("\0")) {
    if (!record) continue
    const [added, deleted, filePath] = record.split("\t")
    if (!filePath) continue
    stats.set(filePath, {
      additions: Number.parseInt(added ?? "0", 10) || 0,
      deletions: Number.parseInt(deleted ?? "0", 10) || 0,
    })
  }
  return stats
}

async function buildFiles(repoRoot: string, entries: DirtyPathEntry[], hasHead: boolean): Promise<ChatDiffFile[]> {
  const stats = await diffStats(repoRoot, hasHead)
  return Promise.all(entries.map(async (entry) => {
    const measured = stats.get(entry.path)
    const additions = measured?.additions ?? (entry.isUntracked ? await lineCount(path.join(repoRoot, entry.path)) : 0)
    const deletions = measured?.deletions ?? 0
    const info = await lstat(path.join(repoRoot, entry.path)).catch(() => null)
    return {
      path: entry.path,
      changeType: entry.changeType,
      isUntracked: entry.isUntracked,
      additions,
      deletions,
      patchDigest: createHash("sha256")
        .update(JSON.stringify([entry.path, entry.changeType, additions, deletions, info?.size, info?.mtimeMs]))
        .digest("hex"),
      ...(info?.isFile() ? { size: info.size } : {}),
    }
  }))
}

export class DiffStore {
  private readonly snapshots = new Map<string, ChatDiffSnapshot>()
  private readonly versions = new Map<string, number>()
  private readonly activeRefreshes = new Map<string, Promise<boolean>>()
  onWorkingTreeProbe?: (projectId: string, scan: WorkingTreeScan) => void

  constructor(_: string) {}
  async initialize() {}

  getProjectSnapshot(projectId: string): ChatDiffSnapshot {
    const current = this.snapshots.get(projectId)
    return current ? { ...current, files: current.files.map((file) => ({ ...file })) } : { status: "unknown", files: [] }
  }

  getSnapshotVersion(projectId: string) {
    return this.versions.get(projectId) ?? 0
  }

  async refreshSnapshot(projectId: string, projectPath: string): Promise<boolean> {
    const running = this.activeRefreshes.get(projectId)
    if (running) return running
    const refresh = this.performRefresh(projectId, projectPath).finally(() => this.activeRefreshes.delete(projectId))
    this.activeRefreshes.set(projectId, refresh)
    return refresh
  }

  private async performRefresh(projectId: string, projectPath: string) {
    const location = await resolveWorkingTreeLocation(projectPath)
    if (!location) return this.commit(projectId, { status: "no_repo", files: [] })
    const entries = await listDirtyPaths(location.repoRoot)
    const hasHead = (await runGit(["rev-parse", "--verify", "HEAD"], location.repoRoot)).exitCode === 0
    const branch = await runGit(["branch", "--show-current"], location.repoRoot)
    const next: ChatDiffSnapshot = {
      status: "ready",
      ...(branch.exitCode === 0 && branch.stdout.trim() ? { branchName: branch.stdout.trim() } : {}),
      files: await buildFiles(location.repoRoot, entries, hasHead),
    }
    this.onWorkingTreeProbe?.(projectId, { dirty: entries.length > 0, paths: dirtyPathNames(entries) })
    return this.commit(projectId, next)
  }

  private commit(projectId: string, next: ChatDiffSnapshot) {
    const changed = JSON.stringify(this.snapshots.get(projectId) ?? null) !== JSON.stringify(next)
    this.snapshots.set(projectId, next)
    if (changed) this.versions.set(projectId, this.getSnapshotVersion(projectId) + 1)
    return changed
  }

  async readPatch(args: { projectPath: string; path: string }) {
    const relativePath = normalizeRepoPath(args.path)
    const location = await resolveWorkingTreeLocation(args.projectPath)
    if (!location) throw new Error("Project is not in a git repository")
    const entry = (await listDirtyPaths(location.repoRoot)).find((candidate) => candidate.path === relativePath)
    if (!entry) throw new Error("File is no longer changed")
    const info = await lstat(path.join(location.repoRoot, relativePath)).catch(() => null)
    if (info?.isFile() && info.size > MAX_PATCH_FILE_BYTES) throw new Error("This file is too large to preview as a diff.")
    const hasHead = (await runGit(["rev-parse", "--verify", "HEAD"], location.repoRoot)).exitCode === 0
    const command = entry.isUntracked
      ? ["diff", "--no-index", "--no-ext-diff", "--no-color", "--", "/dev/null", relativePath]
      : hasHead
        ? ["diff", "--no-ext-diff", "--no-color", "HEAD", "--", relativePath]
        : ["diff", "--cached", "--no-ext-diff", "--no-color", "--", relativePath]
    const result = await runGit(command, location.repoRoot)
    if (result.exitCode !== 0 && !(entry.isUntracked && result.exitCode === 1)) {
      throw new Error(result.stderr.trim() || "Unable to read local diff")
    }
    return { patch: result.stdout }
  }
}
