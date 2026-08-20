import { randomUUID } from "node:crypto"
import { rm } from "node:fs/promises"
import path from "node:path"
import { LOG_PREFIX } from "../shared/branding"
import { readTreeBlobs, resolveWorkingTreeLocation, runGit, type WorkingTreeLocation } from "./diff-store"
import type { TouchedFile } from "./events"

/**
 * Which files a chat actually changed, by snapshotting the working tree at each
 * turn boundary and diffing the two trees.
 *
 * The point is to answer "did this chat touch anything that's still
 * uncommitted?" without a clock. The old answer compared the chat's last turn
 * end against a per-project "when did the tree get dirty" timestamp, which is a
 * proxy for authorship rather than a measurement of it: one file left
 * uncommitted overnight flagged every chat that had run since, and none of them
 * had necessarily touched it.
 *
 * Snapshots measure the filesystem, so they don't care *how* a file changed —
 * an Edit tool, `sed -i` inside a Bash call, a formatter on save, or a subagent
 * all land in the diff identically. Parsing tool calls would see only the first.
 *
 * The trees are written with `git write-tree` against a throwaway index, so the
 * user's index is never touched and nothing is committed, staged, or moved. No
 * refs are created either: we need the tree only long enough to diff it, and
 * loose objects are not pruned anywhere near that fast. (If per-chat diffs or
 * turn-level revert ever want the trees kept, add refs then — the recorded
 * paths don't change.)
 *
 * Each recorded path carries the blob it held in `HEAD` when the turn *started*
 * — the committed content the turn's edit was made on top of. That's what
 * expires a claim: once anyone commits the path, `HEAD` no longer holds that
 * blob and the chat stops being relevant to it, without any clock being
 * involved. Turn *start* rather than turn end, because agents routinely commit
 * their own work mid-turn — a base read afterwards would be the chat's own
 * commit and would keep the chat alive on work it had already landed.
 */

/** A path one turn changed, and the committed content it was based on. */
export type { TouchedFile } from "./events"

/** What `beginTurn` banks: the pre-turn worktree, and the commit it sat on. */
interface PendingTurn {
  /** Tree object of the whole worktree, dirty content included. */
  tree: string | null
  /** `HEAD` at turn start; `null` on an unborn HEAD (nothing committed yet). */
  head: string | null
}

/** Per-chat pre-turn state, plus a location cache so `git rev-parse` runs once per project. */
export class TurnFileTracker {
  private readonly pendingTurns = new Map<string, Promise<PendingTurn>>()
  private readonly locations = new Map<string, Promise<WorkingTreeLocation | null>>()

  constructor(
    private readonly options: {
      /** Absolute project path for a chat, or null when it can't be resolved. */
      resolveChatPath: (chatId: string) => string | null
      /** Persist the files a turn changed. Never called with an empty list. */
      recordFiles: (chatId: string, files: TouchedFile[]) => Promise<void>
    }
  ) {}

  /**
   * Snapshot before the turn runs. Fire-and-forget: the capture races the
   * agent's first tool call, which is fine — a file the agent changes before
   * the snapshot lands simply shows up as pre-existing dirt rather than as this
   * turn's work. Blocking the turn on a `git add -A` would be the worse trade.
   *
   * Returns the capture promise so tests can wait for a deterministic baseline;
   * production callers ignore it.
   */
  beginTurn(chatId: string): Promise<void> {
    const localPath = this.options.resolveChatPath(chatId)
    if (!localPath) return Promise.resolve()
    const capture = this.captureTurn(localPath)
    this.pendingTurns.set(chatId, capture)
    return capture.then(() => undefined, () => undefined)
  }

  /**
   * Snapshot after the turn and record the delta. A missing pre-tree (server
   * restarted mid-turn, project isn't a repo, capture failed) records nothing
   * rather than guessing — a chat with no recorded paths is simply never
   * flagged, which is the safe direction.
   */
  async endTurn(chatId: string): Promise<void> {
    const pending = this.pendingTurns.get(chatId)
    this.pendingTurns.delete(chatId)
    if (!pending) return
    const localPath = this.options.resolveChatPath(chatId)
    if (!localPath) return

    try {
      const [pre, postTree] = await Promise.all([pending, this.captureTree(localPath)])
      if (!pre.tree || !postTree || pre.tree === postTree) return
      const changes = await this.diffTrees(localPath, pre.tree, postTree)
      if (changes.length === 0) return
      await this.options.recordFiles(chatId, await this.toTouchedFiles(localPath, pre.head, changes))
    } catch (error) {
      // Never let file tracking break a turn's completion path.
      console.warn(`${LOG_PREFIX} turn file tracking failed for chat ${chatId}:`, error)
    }
  }

  /** Drops a chat's pending state without recording anything. */
  forget(chatId: string): void {
    this.pendingTurns.delete(chatId)
  }

  /**
   * Pairs each changed path with the blob it held at the turn's starting commit.
   *
   * An unborn HEAD gives every path a `null` base — accurate, since nothing is
   * committed there — while a failed lookup leaves the base *absent*, which
   * reads downstream as "unknown" and keeps the pre-base-blob behaviour rather
   * than inventing a base that would expire the claim early.
   */
  private async toTouchedFiles(
    localPath: string,
    head: string | null,
    changes: TurnFileChange[]
  ): Promise<TouchedFile[]> {
    const paths = changes.map((change) => change.path)
    if (head === null) return changes.map((change) => ({ ...change, baseBlob: null }))
    const location = await this.locationFor(localPath)
    const blobs = location ? await readTreeBlobs(location.repoRoot, head, paths) : null
    if (!blobs) return changes.map((change) => ({ ...change }))
    return changes.map((change) => ({ ...change, baseBlob: blobs.get(change.path) ?? null }))
  }

  /** The pre-turn worktree and the commit it sits on, captured together. */
  private async captureTurn(localPath: string): Promise<PendingTurn> {
    const [tree, head] = await Promise.all([
      this.captureTree(localPath),
      this.readHead(localPath),
    ])
    return { tree, head }
  }

  /** `null` on an unborn HEAD or a project that isn't a repo. */
  private async readHead(localPath: string): Promise<string | null> {
    const location = await this.locationFor(localPath)
    if (!location) return null
    const head = await runGit(["rev-parse", "--verify", "HEAD"], location.repoRoot)
    return head.exitCode === 0 ? head.stdout.trim() || null : null
  }

  private locationFor(localPath: string): Promise<WorkingTreeLocation | null> {
    const cached = this.locations.get(localPath)
    if (cached) return cached
    const resolved = resolveWorkingTreeLocation(localPath)
    this.locations.set(localPath, resolved)
    return resolved
  }

  /**
   * The whole worktree as a tree object, untracked files included and
   * `.gitignore` respected — `git add -A` decides both for us, so the snapshot
   * matches what `git status` reports as dirty.
   */
  private async captureTree(localPath: string): Promise<string | null> {
    const location = await this.locationFor(localPath)
    if (!location) return null

    const indexPath = path.join(location.gitDir, `trade-agent-snapshot-index-${randomUUID()}`)
    const env = { ...process.env, GIT_INDEX_FILE: indexPath }
    try {
      // Seed from HEAD so `add -A` only has to hash what actually differs.
      // Without this every snapshot re-hashes the entire worktree. Failure is
      // fine and expected on an unborn HEAD (a repo with no commits yet).
      await runGit(["read-tree", "HEAD"], location.repoRoot, { env })
      const added = await runGit(["add", "-A", "--", "."], location.repoRoot, { env })
      if (added.exitCode !== 0) return null
      const written = await runGit(["write-tree"], location.repoRoot, { env })
      if (written.exitCode !== 0) return null
      return written.stdout.trim() || null
    } finally {
      await rm(indexPath, { force: true }).catch(() => undefined)
    }
  }

  /**
   * What the turn changed, and by how much.
   *
   * `--no-renames` on purpose: a rename should yield *both* names, since either
   * one can be the path that later shows up dirty. It also keeps `-z` records
   * to a single path each — a rename under `-z` splits its paths across extra
   * NUL fields.
   *
   * `-z` because the default output C-quotes any path with a space or a quote
   * in it, which would then match neither `git status` nor the file on disk.
   */
  private async diffTrees(localPath: string, fromTree: string, toTree: string): Promise<TurnFileChange[]> {
    const location = await this.locationFor(localPath)
    if (!location) return []
    const diff = await runGit(
      ["diff", "--numstat", "-z", "--no-renames", fromTree, toTree],
      location.repoRoot,
    )
    if (diff.exitCode !== 0) return []
    return parseNumstat(diff.stdout)
  }
}

/** One path a turn changed, with the lines it added and removed there. */
export interface TurnFileChange {
  path: string
  /** Absent for binary files, which numstat reports as `-` rather than a count. */
  additions?: number
  deletions?: number
}

/**
 * `--numstat -z` records: `<additions> TAB <deletions> TAB <path> NUL`.
 *
 * Binary files come back as `-` on both counts. Those keep the path (the chat
 * did change the file) but carry no numbers, so nothing downstream has to
 * decide what "+0" means for a PNG.
 */
export function parseNumstat(stdout: string): TurnFileChange[] {
  const changes: TurnFileChange[] = []
  for (const record of stdout.split("\0")) {
    if (!record) continue
    const firstTab = record.indexOf("\t")
    const secondTab = record.indexOf("\t", firstTab + 1)
    if (firstTab === -1 || secondTab === -1) continue
    const filePath = record.slice(secondTab + 1)
    if (!filePath) continue
    const additions = Number.parseInt(record.slice(0, firstTab), 10)
    const deletions = Number.parseInt(record.slice(firstTab + 1, secondTab), 10)
    changes.push({
      path: filePath,
      ...(Number.isNaN(additions) ? {} : { additions }),
      ...(Number.isNaN(deletions) ? {} : { deletions }),
    })
  }
  return changes
}
