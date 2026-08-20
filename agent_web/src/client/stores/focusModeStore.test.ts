import { beforeEach, describe, expect, test } from "bun:test"
import type { SidebarData, SidebarProjectGroup } from "../../shared/types"
import {
  focusSidebarData,
  resolveFocusedProjectGroup,
  useFocusModeStore,
} from "./focusModeStore"

function group(groupKey: string): SidebarProjectGroup {
  return {
    groupKey,
    title: groupKey,
    realTitle: groupKey,
    chats: [],
    previewChats: [],
    olderChats: [],
    defaultCollapsed: false,
  }
}

function data(groups: SidebarProjectGroup[]): SidebarData {
  return { projectGroups: groups }
}

describe("focusModeStore", () => {
  beforeEach(() => {
    useFocusModeStore.setState({ enabled: false })
  })

  test("toggles the flag", () => {
    useFocusModeStore.getState().toggle()
    expect(useFocusModeStore.getState().enabled).toBe(true)

    useFocusModeStore.getState().toggle()
    expect(useFocusModeStore.getState().enabled).toBe(false)
  })

  test("setting the flag to what it already is changes nothing", () => {
    useFocusModeStore.getState().setEnabled(false)
    expect(useFocusModeStore.getState().enabled).toBe(false)
  })
})

describe("resolveFocusedProjectGroup", () => {
  const groups = [group("p1"), group("p2")]

  test("resolves the current project while focus mode is on", () => {
    expect(resolveFocusedProjectGroup(groups, true, "p2")).toBe(groups[1])
  })

  test("resolves nothing while focus mode is off", () => {
    expect(resolveFocusedProjectGroup(groups, false, "p2")).toBeNull()
  })

  test("resolves nothing when no project is current", () => {
    expect(resolveFocusedProjectGroup(groups, true, null)).toBeNull()
  })

  // A hidden or deleted project leaves the snapshot while focus is still on.
  // Falling back to every project beats showing an empty sidebar.
  test("resolves nothing when the focused project has left the snapshot", () => {
    expect(resolveFocusedProjectGroup(groups, true, "gone")).toBeNull()
  })
})

describe("focusSidebarData", () => {
  test("keeps every project when nothing is focused", () => {
    const snapshot = data([group("p1"), group("p2")])
    expect(focusSidebarData(snapshot, null)).toBe(snapshot)
  })

  test("narrows to the focused project", () => {
    const snapshot = data([group("p1"), group("p2")])
    const focused = focusSidebarData(snapshot, snapshot.projectGroups[1])
    expect(focused.projectGroups).toEqual([snapshot.projectGroups[1]])
  })

  // Identity matters: the rows below are memoized on the snapshot.
  test("keeps the snapshot itself when it already holds only the focused project", () => {
    const snapshot = data([group("p1")])
    expect(focusSidebarData(snapshot, snapshot.projectGroups[0])).toBe(snapshot)
  })
})
