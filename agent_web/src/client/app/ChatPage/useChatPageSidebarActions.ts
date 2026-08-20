import { useCallback, useEffect, useRef, useState } from "react"
import type { KannaState } from "../useKannaState"
import type { DiffRenderMode } from "../../components/chat-ui/git/shared"

export const EMPTY_DIFF_SNAPSHOT = {
  projectId: "",
  status: "unknown" as const,
  files: [],
}

/** Read-only local Git diff controls retained for the approved Project. */
export function useChatPageSidebarActions(args: {
  state: KannaState
  projectId: string | null
  showRightSidebar: boolean
}) {
  const { state, projectId, showRightSidebar } = args
  const [diffRenderMode, setDiffRenderMode] = useState<DiffRenderMode>("split")
  const [wrapDiffLines, setWrapDiffLines] = useState(false)
  const refreshTimer = useRef<number | null>(null)

  const refreshDiffs = useCallback(async () => {
    if (!state.activeChatId) return
    await state.socket.command({ type: "chat.refreshDiffs", chatId: state.activeChatId })
  }, [state.activeChatId, state.socket])

  const scheduleTerminalDiffRefresh = useCallback(() => {
    if (!state.activeChatId || !showRightSidebar) return
    if (refreshTimer.current !== null) window.clearTimeout(refreshTimer.current)
    refreshTimer.current = window.setTimeout(() => {
      refreshTimer.current = null
      void refreshDiffs().catch(() => undefined)
    }, 1_000)
  }, [refreshDiffs, showRightSidebar, state.activeChatId])

  useEffect(() => () => {
    if (refreshTimer.current !== null) window.clearTimeout(refreshTimer.current)
  }, [])

  const handleLoadDiffPatch = useCallback(async (filePath: string) => {
    if (!projectId) throw new Error("Project not found")
    const result = await state.socket.command<{ patch: string }>({
      type: "project.readDiffPatch",
      projectId,
      path: filePath,
    })
    return result.patch
  }, [projectId, state.socket])

  return {
    diffRenderMode,
    wrapDiffLines,
    setDiffRenderMode,
    setWrapDiffLines,
    scheduleTerminalDiffRefresh,
    handleLoadDiffPatch,
    refreshDiffs,
  }
}
