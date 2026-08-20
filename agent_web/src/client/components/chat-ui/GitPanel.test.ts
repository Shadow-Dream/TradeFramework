import { describe, expect, test } from "bun:test"
import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { GitPanel, shouldLoadDiffPatchNow } from "./GitPanel"
import { TooltipProvider } from "../ui/tooltip"

describe("GitPanel", () => {
  test("loads only an expanded diff whose patch is still missing", () => {
    expect(shouldLoadDiffPatchNow({
      isCollapsed: false,
      hasPreviewAttachment: false,
      patch: undefined,
      patchError: undefined,
      isPatchLoading: false,
    })).toBe(true)
    expect(shouldLoadDiffPatchNow({
      isCollapsed: true,
      hasPreviewAttachment: false,
      patch: undefined,
      patchError: undefined,
      isPatchLoading: false,
    })).toBe(false)
    expect(shouldLoadDiffPatchNow({
      isCollapsed: false,
      hasPreviewAttachment: false,
      patch: "diff --git a/app.ts b/app.ts",
      patchError: undefined,
      isPatchLoading: false,
    })).toBe(false)
  })

  test("renders a local, read-only Project diff", () => {
    const markup = renderToStaticMarkup(createElement(
      TooltipProvider,
      null,
      createElement(GitPanel, {
        projectId: "project-1",
        diffs: {
          status: "ready",
          branchName: "main",
          files: [{
            path: "src/app.ts",
            changeType: "modified",
            isUntracked: false,
            additions: 1,
            deletions: 1,
            patchDigest: "digest-1",
          }],
        },
        diffRenderMode: "unified",
        wrapLines: false,
        onLoadPatch: async () => "",
        onRefresh: async () => {},
        onDiffRenderModeChange: () => {},
        onWrapLinesChange: () => {},
        onClose: () => {},
      }),
    ))

    expect(markup).toContain("Project changes")
    expect(markup).toContain("src/app.ts")
    expect(markup).not.toContain("Commit")
    expect(markup).not.toContain("Push")
    expect(markup).not.toContain("Branch switcher")
    expect(markup).not.toContain("GitHub")
  })

  test("describes a non-Git Project without offering setup", () => {
    const markup = renderToStaticMarkup(createElement(
      TooltipProvider,
      null,
      createElement(GitPanel, {
        projectId: "project-1",
        diffs: { status: "no_repo", files: [] },
        diffRenderMode: "split",
        wrapLines: true,
        onLoadPatch: async () => "",
        onRefresh: async () => {},
        onDiffRenderModeChange: () => {},
        onWrapLinesChange: () => {},
        onClose: () => {},
      }),
    ))
    expect(markup).toContain("Not a Git worktree")
    expect(markup).not.toContain("Setup Git")
  })
})
