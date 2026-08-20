import { existsSync, readFileSync, readdirSync } from "node:fs"
import path from "node:path"
import type { HarnessSkill } from "../shared/types"

export interface ResolvedHarnessSkill extends HarnessSkill {
  /** Server-only native discovery path. Never include this in a browser snapshot. */
  path: string
}

export const TRADE_TASK_SKILL_NAMES = new Set([
  "strategy-development",
  "dataset-preparation",
  "backtest-investigation",
  "research-verification",
])

const SKILL_INVOCATION_PATTERN = /^\/([\w:.-]+)(?:\s+([\s\S]*))?$/

export interface SkillInvocation { name: string; args: string }

export function parseSkillInvocation(content: string): SkillInvocation | null {
  const match = content.trim().match(SKILL_INVOCATION_PATTERN)
  if (!match?.[1] || !TRADE_TASK_SKILL_NAMES.has(match[1])) return null
  return { name: match[1], args: match[2]?.trim() ?? "" }
}

export function buildSkillSystemMessage(skillPath: string): string {
  return `<system-message>Use the TradeEngine task skill at ${skillPath} for this turn.</system-message>`
}

export function appendSystemMessageBlock(content: string, block: string): string {
  const trimmed = content.trim()
  return trimmed.length > 0 ? `${trimmed}\n\n${block}` : block
}

export function parseFrontmatter(markdown: string): Record<string, string> {
  const match = markdown.match(/^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/)
  if (!match?.[1]) return {}
  const fields: Record<string, string> = {}
  for (const line of match[1].split(/\r?\n/)) {
    const separator = line.indexOf(":")
    if (separator <= 0) continue
    const key = line.slice(0, separator).trim().toLowerCase()
    let value = line.slice(separator + 1).trim()
    if ((value.startsWith("\"") && value.endsWith("\"")) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1)
    }
    if (key && value) fields[key] = value
  }
  return fields
}

export function scanSkillsRoot(root: string): ResolvedHarnessSkill[] {
  let entries: string[]
  try { entries = readdirSync(root) } catch { return [] }
  const skills: ResolvedHarnessSkill[] = []
  for (const name of entries.sort()) {
    if (!TRADE_TASK_SKILL_NAMES.has(name)) continue
    const skillFile = path.join(root, name, "SKILL.md")
    if (!existsSync(skillFile)) continue
    let markdown: string
    try { markdown = readFileSync(skillFile, "utf8") } catch { continue }
    const frontmatter = parseFrontmatter(markdown)
    skills.push({
      name,
      description: frontmatter.description ?? "TradeEngine task workflow",
      source: "skill",
      path: skillFile,
    })
  }
  return skills
}

function repositoryRoot(cwd: string) {
  let current = path.resolve(cwd)
  while (true) {
    if (existsSync(path.join(current, ".git"))) return current
    const parent = path.dirname(current)
    if (parent === current) return path.resolve(cwd)
    current = parent
  }
}

export interface ScanArgs { cwd: string }

/** Only the repository's four canonical TradeEngine Skills are public. */
export function scanClaudeSkills(args: ScanArgs): ResolvedHarnessSkill[] {
  return scanSkillsRoot(path.join(repositoryRoot(args.cwd), ".claude", "skills"))
}

/** Same canonical source, exposed through Codex's repository skill root. */
export function scanCodexSkills(args: ScanArgs): ResolvedHarnessSkill[] {
  return scanSkillsRoot(path.join(repositoryRoot(args.cwd), ".agents", "skills"))
}

export function findSkillByName(skills: ResolvedHarnessSkill[], name: string): ResolvedHarnessSkill | null {
  if (!TRADE_TASK_SKILL_NAMES.has(name)) return null
  return skills.find((skill) => skill.name === name) ?? null
}
