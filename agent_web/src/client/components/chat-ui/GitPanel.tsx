import { Columns2, RefreshCw, Rows3, WrapText, X } from "lucide-react"
import { memo, useCallback, useEffect, useState } from "react"
import type { ChatDiffSnapshot } from "../../../shared/types"
import { Button } from "../ui/button"
import { DiffFileCard } from "./git/DiffFileCard"
import { IconButton, type DiffRenderMode } from "./git/shared"

export { shouldLoadDiffPatchNow } from "./git/DiffFileCard"

interface GitPanelProps {
  projectId: string | null
  diffs: ChatDiffSnapshot
  diffRenderMode: DiffRenderMode
  wrapLines: boolean
  onLoadPatch: (path: string) => Promise<string>
  onRefresh: () => Promise<void>
  onDiffRenderModeChange: (mode: DiffRenderMode) => void
  onWrapLinesChange: (wrap: boolean) => void
  onClose: () => void
}

/** Local, read-only Project diff. Remote Git and branch mutation are intentionally absent. */
function GitPanelImpl({
  diffs,
  diffRenderMode,
  wrapLines,
  onLoadPatch,
  onRefresh,
  onDiffRenderModeChange,
  onWrapLinesChange,
  onClose,
}: GitPanelProps) {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})
  const [patches, setPatches] = useState<Record<string, string>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState<Record<string, boolean>>({})

  useEffect(() => {
    setCollapsed((current) => Object.fromEntries(diffs.files.map((file) => [file.path, current[file.path] ?? true])))
    setPatches({})
    setErrors({})
    setLoading({})
  }, [diffs.files])

  const loadPatch = useCallback(async (filePath: string) => {
    setLoading((current) => ({ ...current, [filePath]: true }))
    try {
      const patch = await onLoadPatch(filePath)
      setPatches((current) => ({ ...current, [filePath]: patch }))
      setErrors((current) => {
        const { [filePath]: _removed, ...rest } = current
        return rest
      })
      return patch
    } catch (error) {
      setErrors((current) => ({ ...current, [filePath]: error instanceof Error ? error.message : String(error) }))
      throw error
    } finally {
      setLoading((current) => ({ ...current, [filePath]: false }))
    }
  }, [onLoadPatch])

  return (
    <aside className="flex h-full min-h-0 flex-col bg-background">
      <header className="flex items-center gap-2 border-b border-border px-3 py-2">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium">Project changes</div>
          <div className="truncate text-xs text-muted-foreground">
            {diffs.status === "no_repo" ? "Not a Git worktree" : `${diffs.files.length} changed file${diffs.files.length === 1 ? "" : "s"}`}
          </div>
        </div>
        <IconButton label="Split diff" active={diffRenderMode === "split"} onClick={() => onDiffRenderModeChange("split")}><Columns2 className="size-3.5" /></IconButton>
        <IconButton label="Unified diff" active={diffRenderMode === "unified"} onClick={() => onDiffRenderModeChange("unified")}><Rows3 className="size-3.5" /></IconButton>
        <IconButton label="Wrap lines" active={wrapLines} onClick={() => onWrapLinesChange(!wrapLines)}><WrapText className="size-3.5" /></IconButton>
        <Button variant="ghost" size="icon-sm" title="Refresh" onClick={() => void onRefresh()}><RefreshCw className="size-3.5" /></Button>
        <Button variant="ghost" size="icon-sm" title="Close" onClick={onClose}><X className="size-3.5" /></Button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {diffs.files.length === 0 ? (
          <div className="px-6 py-12 text-center text-sm text-muted-foreground">No uncommitted file changes.</div>
        ) : diffs.files.map((file) => (
          <DiffFileCard
            key={file.path}
            file={file}
            isCollapsed={collapsed[file.path] ?? true}
            diffRenderMode={diffRenderMode}
            wrapLines={wrapLines}
            onToggleCollapsed={() => setCollapsed((current) => ({ ...current, [file.path]: !(current[file.path] ?? true) }))}
            patch={patches[file.path]}
            patchError={errors[file.path]}
            isPatchLoading={Boolean(loading[file.path])}
            onLoadPatch={loadPatch}
          />
        ))}
      </div>
    </aside>
  )
}

export const GitPanel = memo(GitPanelImpl)
