import { useMemo, useRef } from "react"
import type { SidebarData } from "../../shared/types"
import {
  flattenSidebarThreads,
  stabilizeSidebarThreads,
  type SidebarThread,
} from "../lib/thread-sections"

/**
 * The sidebar's thread list, flattened once and reused where it can be.
 *
 * Both sidebar tabs render the same rows off this list, so it is computed here
 * and handed down rather than flattened again in each of them. The
 * stabilization is what makes the rows memoizable: a push that moved one chat's
 * reply preview yields a list where every other thread object is the one the
 * rows already hold, so React skips them.
 */
export function useStableSidebarThreads(data: SidebarData): SidebarThread[] {
  const previousRef = useRef<SidebarThread[]>([])

  return useMemo(() => {
    const stabilized = stabilizeSidebarThreads(previousRef.current, flattenSidebarThreads(data))
    previousRef.current = stabilized
    return stabilized
  }, [data])
}
