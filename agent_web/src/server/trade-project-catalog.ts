import { lstat, realpath } from "node:fs/promises"
import path from "node:path"

export type AgentProjectKind = "trade-engine" | "strategy"

export interface AgentProject {
  projectId: string
  label: string
  kind: AgentProjectKind
}

interface CatalogEntry extends AgentProject {
  workspaceKey: string
  absolutePath: string
}

export interface ExternalAgentProject {
  projectId: string
  label: string
  path: string
}

const STRATEGY_PROJECT_ID_RE = /^strategy:[a-z0-9][a-z0-9._-]{0,127}$/

function catalogError(message: string): Error {
  const error = new Error(message)
  error.name = "ProjectCatalogError"
  return error
}

async function requirePlainDirectory(candidate: string, label: string) {
  const metadata = await lstat(candidate).catch(() => null)
  if (!metadata?.isDirectory() || metadata.isSymbolicLink()) {
    throw catalogError(`${label} is not an approved project directory.`)
  }
  return await realpath(candidate)
}

function isWithinRoot(root: string, candidate: string) {
  const relative = path.relative(root, candidate)
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative))
}

export function parseExternalAgentProjects(raw: string | undefined): ExternalAgentProject[] {
  if (!raw?.trim()) return []
  let payload: unknown
  try {
    payload = JSON.parse(raw)
  } catch {
    throw catalogError("TRADE_AGENT_PROJECTS_JSON must be valid JSON.")
  }
  if (!Array.isArray(payload)) {
    throw catalogError("TRADE_AGENT_PROJECTS_JSON must be an array.")
  }
  const seen = new Set<string>()
  return payload.map((value) => {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw catalogError("Each external Agent project must be an object.")
    }
    const record = value as Record<string, unknown>
    if (
      Object.keys(record).sort().join(",") !== "label,path,projectId"
      || typeof record.projectId !== "string"
      || !STRATEGY_PROJECT_ID_RE.test(record.projectId)
      || typeof record.label !== "string"
      || record.label.trim() !== record.label
      || record.label.length < 1
      || record.label.length > 128
      || /[\u0000-\u001f\u007f]/.test(record.label)
      || typeof record.path !== "string"
      || !path.isAbsolute(record.path)
    ) {
      throw catalogError("External Agent project fields are invalid.")
    }
    if (seen.has(record.projectId)) {
      throw catalogError("External Agent project ids must be unique.")
    }
    seen.add(record.projectId)
    return {
      projectId: record.projectId,
      label: record.label,
      path: path.normalize(record.path),
    }
  })
}

/**
 * Server-owned mapping between public project ids and Agent working
 * directories. The browser never supplies or receives a filesystem path.
 */
export class TradeProjectCatalog {
  readonly rootPath: string
  readonly externalProjects: readonly ExternalAgentProject[]
  private resolvedRoot: string | null = null
  private entries = new Map<string, CatalogEntry>()

  constructor(rootPath: string, externalProjects: readonly ExternalAgentProject[] = []) {
    const trimmed = rootPath.trim()
    if (!trimmed || !path.isAbsolute(trimmed)) {
      throw catalogError("TRADE_ENGINE_ROOT must be an absolute directory.")
    }
    this.rootPath = path.normalize(trimmed)
    this.externalProjects = [...externalProjects]
  }

  async refresh(): Promise<AgentProject[]> {
    const resolvedRoot = await requirePlainDirectory(this.rootPath, "TradeEngine root")
    const next = new Map<string, CatalogEntry>()
    next.set("trade-engine", {
      projectId: "trade-engine",
      workspaceKey: "trade-engine",
      label: "TradeEngine",
      kind: "trade-engine",
      absolutePath: resolvedRoot,
    })

    const paths = new Set([resolvedRoot])
    for (const project of this.externalProjects) {
      if (!STRATEGY_PROJECT_ID_RE.test(project.projectId) || next.has(project.projectId)) {
        throw catalogError("External Agent project id is invalid or duplicated.")
      }
      const resolved = await requirePlainDirectory(project.path, "External Agent project")
      if (
        isWithinRoot(resolvedRoot, resolved)
        || isWithinRoot(resolved, resolvedRoot)
        || paths.has(resolved)
      ) {
        throw catalogError("External Agent project roots must be distinct and outside TradeEngine.")
      }
      paths.add(resolved)
      next.set(project.projectId, {
        projectId: project.projectId,
        workspaceKey: project.projectId,
        label: project.label,
        kind: "strategy",
        absolutePath: resolved,
      })
    }

    this.resolvedRoot = resolvedRoot
    this.entries = next
    return this.list()
  }

  list(): AgentProject[] {
    return [...this.entries.values()].map(({ projectId, label, kind }) => ({ projectId, label, kind }))
  }

  listInternal(): ReadonlyArray<CatalogEntry> {
    return [...this.entries.values()]
  }

  has(projectId: string): boolean {
    return this.entries.has(projectId)
  }

  hasPath(candidate: string): boolean {
    const normalized = path.normalize(candidate)
    return [...this.entries.values()].some((entry) => entry.absolutePath === normalized)
  }

  getWorkspaceRecord(projectId: string) {
    const entry = this.entries.get(projectId)
    if (!entry) throw catalogError("Project is not in the TradeEngine catalog.")
    return {
      projectId: entry.projectId,
      workspaceKey: entry.workspaceKey,
      title: entry.label,
      kind: entry.kind,
    }
  }

  /**
   * Revalidates the directory on every turn/tool boundary. A removed,
   * replaced or symlinked strategy cannot keep using a stale cwd.
   */
  async resolve(projectId: string): Promise<string> {
    const entry = this.entries.get(projectId)
    const root = this.resolvedRoot
    if (!entry || !root) throw catalogError("Project is not in the TradeEngine catalog.")
    const resolved = await requirePlainDirectory(entry.absolutePath, "Agent project")
    if ((entry.kind === "trade-engine" && !isWithinRoot(root, resolved)) || resolved !== entry.absolutePath) {
      throw catalogError("Agent project is no longer an approved directory.")
    }
    return resolved
  }

  async resolvePath(candidate: string): Promise<string> {
    const normalized = path.normalize(candidate)
    const entry = [...this.entries.values()].find((value) => value.absolutePath === normalized)
    if (!entry) throw catalogError("Project is not in the TradeEngine catalog.")
    return await this.resolve(entry.projectId)
  }

  resolveKnown(projectId: string): string {
    const entry = this.entries.get(projectId)
    if (!entry) throw catalogError("Project is not in the TradeEngine catalog.")
    return entry.absolutePath
  }
}
