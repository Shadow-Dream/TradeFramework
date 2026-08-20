import { PatchDiff } from "@pierre/diffs/react"
import { ChevronDown, ChevronUp, LoaderCircle } from "lucide-react"
import { useEffect, useRef } from "react"
import { Button } from "../../ui/button"
import { CopyButton } from "../../ui/copy-button"
import { DiffFileStat, type DiffFile, type DiffRenderMode } from "./shared"

export function shouldLoadDiffPatchNow(args: {
  isCollapsed: boolean
  patch?: string
  patchError?: string
  isPatchLoading: boolean
}) {
  return !args.isCollapsed && args.patch === undefined && args.patchError === undefined && !args.isPatchLoading
}

/** Read-only local diff row. No discard, ignore, editor, branch or remote action. */
export function DiffFileCard({
  file,
  isCollapsed,
  diffRenderMode,
  wrapLines,
  onToggleCollapsed,
  patch,
  patchError,
  isPatchLoading,
  onLoadPatch,
}: {
  file: DiffFile
  isCollapsed: boolean
  diffRenderMode: DiffRenderMode
  wrapLines: boolean
  onToggleCollapsed: () => void
  patch?: string
  patchError?: string
  isPatchLoading: boolean
  onLoadPatch: (path: string) => Promise<string>
}) {
  const requestedKey = useRef<string | null>(null)
  useEffect(() => {
    if (!shouldLoadDiffPatchNow({ isCollapsed, patch, patchError, isPatchLoading })) return
    const key = `${file.path}\0${file.patchDigest}`
    if (requestedKey.current === key) return
    requestedKey.current = key
    void onLoadPatch(file.path).catch(() => undefined)
  }, [file.patchDigest, file.path, isCollapsed, isPatchLoading, onLoadPatch, patch, patchError])

  return (
    <section className="border-b border-border last:border-b-0">
      <div className="flex min-w-0 items-center gap-2 px-3 py-2">
        <Button variant="ghost" size="icon-sm" onClick={onToggleCollapsed} aria-label={isCollapsed ? "Show diff" : "Hide diff"}>
          {isCollapsed ? <ChevronDown className="size-3.5" /> : <ChevronUp className="size-3.5" />}
        </Button>
        <button type="button" className="min-w-0 flex-1 truncate text-left font-mono text-xs" onClick={onToggleCollapsed}>
          {file.path}
        </button>
        <DiffFileStat additions={file.additions} deletions={file.deletions} />
        <CopyButton text={file.path} title="Copy relative path" copyClassName="size-3.5" />
      </div>
      {!isCollapsed ? (
        <div className="overflow-hidden border-t border-border/70">
          {isPatchLoading ? (
            <div className="flex items-center gap-2 px-3 py-4 text-sm text-muted-foreground"><LoaderCircle className="size-4 animate-spin" />Loading diff…</div>
          ) : patchError ? (
            <div className="px-3 py-4 text-sm text-destructive">{patchError}</div>
          ) : patch !== undefined ? (
            <PatchDiff patch={patch} options={{ diffStyle: diffRenderMode, disableFileHeader: true, overflow: wrapLines ? "wrap" : "scroll", lineDiffType: "word", diffIndicators: "classic" }} />
          ) : (
            <div className="px-3 py-4 text-sm text-muted-foreground">Diff unavailable.</div>
          )}
        </div>
      ) : null}
    </section>
  )
}
