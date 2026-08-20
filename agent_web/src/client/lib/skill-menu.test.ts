import { describe, expect, test } from "bun:test"
import type { HarnessSkill } from "../../shared/types"
import { applySkillCompletion, CODEX_SKILL_MENU_TRIGGERS, filterSkillMenuItems, getActiveSlashQuery } from "./skill-menu"

function skill(name: string, description = ""): HarnessSkill {
  return { name, description, source: "skill" }
}

describe("getActiveSlashQuery", () => {
  test("active while the caret is inside the leading /token", () => {
    expect(getActiveSlashQuery("/", 1)).toBe("")
    expect(getActiveSlashQuery("/rev", 4)).toBe("rev")
    expect(getActiveSlashQuery("/rev", 2)).toBe("r")
  })

  test("inactive once the user is typing arguments", () => {
    expect(getActiveSlashQuery("/review args", 8)).toBeNull()
    expect(getActiveSlashQuery("/review ", 8)).toBeNull()
  })

  test("inactive for mid-message slashes and plain text", () => {
    expect(getActiveSlashQuery("see /etc/hosts", 8)).toBeNull()
    expect(getActiveSlashQuery("hello", 3)).toBeNull()
    expect(getActiveSlashQuery("", 0)).toBeNull()
  })

  test("inactive when the caret sits before the slash", () => {
    expect(getActiveSlashQuery("/rev", 0)).toBeNull()
  })

  test("$ triggers only with the codex trigger set", () => {
    expect(getActiveSlashQuery("$dep", 4)).toBeNull()
    expect(getActiveSlashQuery("$dep", 4, CODEX_SKILL_MENU_TRIGGERS)).toBe("dep")
    expect(getActiveSlashQuery("/dep", 4, CODEX_SKILL_MENU_TRIGGERS)).toBe("dep")
    expect(getActiveSlashQuery("$deploy args", 9, CODEX_SKILL_MENU_TRIGGERS)).toBeNull()
    expect(getActiveSlashQuery("see $dep", 8, CODEX_SKILL_MENU_TRIGGERS)).toBeNull()
  })
})

describe("filterSkillMenuItems", () => {
  test("orders ascending so the best match is last (adjacent to the input)", () => {
    const items = filterSkillMenuItems(
      [skill("verify"), skill("code-review"), skill("review")],
      "rev"
    )
    expect(items.map((entry) => entry.name).at(-1)).toBe("review")
    expect(items.map((entry) => entry.name)).toContain("code-review")
  })

  test("matches segment starts in namespaced names", () => {
    const items = filterSkillMenuItems([skill("skill:brave-search"), skill("deploy")], "brave")
    expect(items.map((entry) => entry.name)).toEqual(["skill:brave-search"])
  })

  test("falls back to description matches and drops non-matches", () => {
    const items = filterSkillMenuItems(
      [skill("alpha", "reviews pull requests"), skill("beta", "ships releases")],
      "pull"
    )
    expect(items.map((entry) => entry.name)).toEqual(["alpha"])
  })

  test("empty query keeps every skill", () => {
    expect(filterSkillMenuItems([skill("a"), skill("b")], "")).toHaveLength(2)
  })
})

describe("applySkillCompletion", () => {
  test("replaces the leading token and appends a space", () => {
    expect(applySkillCompletion("/rev", "review")).toBe("/review ")
  })

  test("preserves existing argument text", () => {
    expect(applySkillCompletion("/rev main branch", "review")).toBe("/review main branch")
  })

  test("handles a bare slash", () => {
    expect(applySkillCompletion("/", "skill:brave-search")).toBe("/skill:brave-search ")
  })

  test("normalizes a $ trigger to the canonical / form", () => {
    expect(applySkillCompletion("$dep", "deploy-helper")).toBe("/deploy-helper ")
    expect(applySkillCompletion("$dep to prod", "deploy-helper")).toBe("/deploy-helper to prod")
  })
})
