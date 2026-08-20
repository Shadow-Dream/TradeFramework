import { create } from "zustand"
import type { SidebarData, SidebarProjectGroup } from "../../shared/types"
import { SIDEBAR_FOCUS_MODE_STORAGE_KEY } from "../lib/storageKeys"

/**
 * Focus mode: the sidebar shows one project instead of all of them.
 *
 * The store holds a flag and nothing else. *Which* project is focused is always
 * the current one, read where the sidebar already knows it. So opening a chat in
 * another project re-points focus with no bookkeeping, and there is never a
 * frame where a stored project id and the open chat disagree.
 */

interface FocusModeState {
  enabled: boolean
  setEnabled: (enabled: boolean) => void
  toggle: () => void
}

function readStoredFocusMode() {
  if (typeof window === "undefined") return false
  return window.localStorage.getItem(SIDEBAR_FOCUS_MODE_STORAGE_KEY) === "1"
}

function persistFocusMode(enabled: boolean) {
  if (typeof window === "undefined") return
  window.localStorage.setItem(SIDEBAR_FOCUS_MODE_STORAGE_KEY, enabled ? "1" : "0")
}

export const useFocusModeStore = create<FocusModeState>()((set, get) => ({
  enabled: readStoredFocusMode(),

  setEnabled: (enabled) => {
    if (get().enabled === enabled) return
    persistFocusMode(enabled)
    set({ enabled })
  },

  toggle: () => {
    get().setEnabled(!get().enabled)
  },
}))

export function useFocusModeEnabled(): boolean {
  return useFocusModeStore((state) => state.enabled)
}

/** For key handlers and palette actions, which must not subscribe. */
export function setFocusMode(enabled: boolean) {
  useFocusModeStore.getState().setEnabled(enabled)
}

export function toggleFocusMode() {
  useFocusModeStore.getState().toggle()
}

export function isFocusModeEnabled() {
  return useFocusModeStore.getState().enabled
}

/**
 * The project focus mode is pinned to, or null when it is off. A focused
 * project that has left the snapshot (hidden, deleted) resolves to null, so the
 * sidebar falls back to showing everything rather than going blank.
 */
export function resolveFocusedProjectGroup(
  groups: SidebarProjectGroup[],
  enabled: boolean,
  currentProjectId: string | null
): SidebarProjectGroup | null {
  if (!enabled || !currentProjectId) return null
  return groups.find((group) => group.groupKey === currentProjectId) ?? null
}

/** The snapshot the sidebar renders: every project, or just the focused one. */
export function focusSidebarData(data: SidebarData, focused: SidebarProjectGroup | null): SidebarData {
  if (!focused) return data
  if (data.projectGroups.length === 1 && data.projectGroups[0] === focused) return data
  return { ...data, projectGroups: [focused] }
}
