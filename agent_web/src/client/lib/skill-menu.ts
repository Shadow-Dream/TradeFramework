import type { HarnessSkill } from "../../shared/types"

/**
 * Helpers for the composer's "/" skill menu. Pure functions so they can be
 * unit-tested; ChatInput owns the state and rendering.
 *
 * The menu is only offered while the caret sits inside a leading trigger token
 * ("/name", or "$name" on codex) — invocation is start-anchored on every
 * Claude Code harness (which checks trim().startsWith("/")),
 * so a mid-message trigger never opens it.
 */

export const DEFAULT_SKILL_MENU_TRIGGERS: readonly string[] = ["/"]
/** Codex's native skill-mention sigil is "$" — accept it as a menu trigger too. */
export const CODEX_SKILL_MENU_TRIGGERS: readonly string[] = ["/", "$"]

/**
 * Returns the query (text typed after the leading trigger) when the skill menu
 * should be open for the given input value + caret position, else null.
 */
export function getActiveSlashQuery(
  value: string,
  caretPosition: number,
  triggers: readonly string[] = DEFAULT_SKILL_MENU_TRIGGERS
): string | null {
  if (value.length === 0 || !triggers.includes(value[0]!)) return null
  const token = value.slice(1).match(/^[^\s]*/)?.[0] ?? ""
  // Caret must be inside "<trigger>token" (position 1..token end). Once the
  // user moves past the first whitespace they are typing arguments.
  if (caretPosition < 1 || caretPosition > token.length + 1) return null
  return token.slice(0, caretPosition - 1)
}

function scoreSkill(skill: HarnessSkill, query: string): number {
  if (query.length === 0) return 1
  const name = skill.name.toLowerCase()
  const lowered = query.toLowerCase()
  if (name === lowered) return 100
  if (name.startsWith(lowered)) return 80
  // Segment starts: "review" matching "code-review" / "skill:review".
  if (name.split(/[:./_-]/).some((part) => part.startsWith(lowered))) return 60
  if (name.includes(lowered)) return 40
  if (isSubsequence(lowered, name)) return 20
  if (skill.description.toLowerCase().includes(lowered)) return 10
  return 0
}

function isSubsequence(needle: string, haystack: string): boolean {
  let index = 0
  for (const char of haystack) {
    if (char === needle[index]) index += 1
    if (index === needle.length) return true
  }
  return needle.length === 0
}

/**
 * Filter + rank skills for the menu. Rendered top-to-bottom in ASCENDING match
 * quality: the best match sits at the BOTTOM, adjacent to the input bar, so
 * the default selection is the item closest to where the user is typing.
 */
export function filterSkillMenuItems(skills: HarnessSkill[], query: string): HarnessSkill[] {
  return skills
    .map((skill, index) => ({ skill, index, score: scoreSkill(skill, query) }))
    .filter((entry) => entry.score > 0)
    .sort((left, right) =>
      left.score - right.score
      // Stable, deterministic order among equals: reverse-alphabetical so the
      // ascending render puts alphabetically-first names nearest the input.
      || right.skill.name.localeCompare(left.skill.name)
      || right.index - left.index)
    .map((entry) => entry.skill)
}

/**
 * Replace the leading trigger token with the accepted skill, preserving any
 * argument text. Always completes to the "/" form — "$" is only an input
 * convenience; "/" is the canonical invocation the server-side translation
 * understands on every harness.
 */
export function applySkillCompletion(value: string, skillName: string): string {
  if (!value.startsWith("/") && !value.startsWith("$")) return `/${skillName} `
  const token = value.slice(1).match(/^[^\s]*/)?.[0] ?? ""
  const rest = value.slice(1 + token.length)
  return `/${skillName}${rest.length > 0 ? rest : " "}`
}
