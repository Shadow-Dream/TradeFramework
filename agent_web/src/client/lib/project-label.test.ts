import { describe, expect, test } from "bun:test"
import { formatProjectRepoBranch, formatProjectSidebarLabel, getProjectSidebarLabel } from "./project-label"

describe("formatProjectRepoBranch", () => {
  test("names the branch even when the sidebar would hide it", () => {
    expect(formatProjectRepoBranch({ repoName: "kanna", branchName: "main" })).toBe("kanna/main")
  })

  test("falls back to the bare repo on a detached HEAD", () => {
    expect(formatProjectRepoBranch({ repoName: "kanna" })).toBe("kanna")
  })

  test("has nothing to say outside a repo, leaving the caller its own fallback", () => {
    expect(formatProjectRepoBranch({})).toBeNull()
    expect(formatProjectRepoBranch({ branchName: "feat/x" })).toBeNull()
  })
})

describe("formatProjectSidebarLabel", () => {
  test("uses the folder name when the project is not in a repo", () => {
    expect(formatProjectSidebarLabel({ title: "notes" })).toBe("notes")
  })

  test("uses repo/branch when the project is on a branch worth naming", () => {
    expect(formatProjectSidebarLabel({ title: "kanna", repoName: "kanna", branchName: "feat/x" }))
      .toBe("kanna/feat/x")
  })

  test("drops the branch on main and master", () => {
    expect(formatProjectSidebarLabel({ title: "kanna", repoName: "kanna", branchName: "main" }))
      .toBe("kanna")
    expect(formatProjectSidebarLabel({ title: "kanna", repoName: "kanna", branchName: "master" }))
      .toBe("kanna")
  })

  test("only drops an exact match, not a branch that merely starts with it", () => {
    expect(formatProjectSidebarLabel({ title: "kanna", repoName: "kanna", branchName: "maintenance" }))
      .toBe("kanna/maintenance")
  })

  test("prefers the repo root over the project's own folder name", () => {
    // A project opened at <repo>/packages/ui: the repo is what identifies it.
    expect(formatProjectSidebarLabel({ title: "ui", repoName: "kanna", branchName: "feat/x" }))
      .toBe("kanna/feat/x")
  })

  test("a rename wins over the repo", () => {
    expect(formatProjectSidebarLabel({
      title: "Work",
      sidebarTitle: "Work",
      repoName: "kanna",
      branchName: "feat/x",
    })).toBe("Work")
  })

  test("falls back to the repo alone on a detached HEAD", () => {
    expect(formatProjectSidebarLabel({ title: "kanna", repoName: "kanna" })).toBe("kanna")
  })

  test("never renders empty while the repo is still being resolved", () => {
    expect(formatProjectSidebarLabel({ title: "kanna", branchName: "feat/x" })).toBe("kanna")
  })
})

describe("getProjectSidebarLabel", () => {
  test("shows the repo inline and keeps the branch for the tooltip", () => {
    // The branch is what the glyph and the tooltip are for — it must never be
    // folded into the inline name, which is the part the sidebar truncates.
    expect(getProjectSidebarLabel({
      title: "kanna",
      repoName: "kanna",
      branchName: "feat/x",
    })).toEqual({
      name: "kanna",
      branchName: "feat/x",
      currentBranch: "feat/x",
      repoPath: "kanna",
      text: "kanna/feat/x",
    })
  })

  test("an unremarkable branch leaves nothing to flag", () => {
    // No branchName → no glyph, so the row reads as a plain project name.
    expect(getProjectSidebarLabel({ title: "kanna", repoName: "kanna", branchName: "main" }).branchName)
      .toBeUndefined()
  })

  test("falls back to the bare repo when the origin owner is unknown", () => {
    expect(getProjectSidebarLabel({ title: "kanna", repoName: "kanna", branchName: "feat/x" }).repoPath)
      .toBe("kanna")
  })

  test("a project outside a repo has no tooltip content at all", () => {
    const label = getProjectSidebarLabel({ title: "notes" })

    expect(label.branchName).toBeUndefined()
    expect(label.repoPath).toBeUndefined()
  })

  test("a rename suppresses the branch flag along with the repo", () => {
    // You named it; that name is the whole label, glyph included.
    const label = getProjectSidebarLabel({
      title: "Work",
      sidebarTitle: "Work",
      repoName: "kanna",
      branchName: "feat/x",
    })

    // The checkout survives the rename even though the name doesn't — the card
    // still has to say which branch you're on.
    expect(label).toEqual({ name: "Work", currentBranch: "feat/x", text: "Work" })
  })

})
