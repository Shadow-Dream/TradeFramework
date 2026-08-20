import { beforeEach, describe, expect, test } from "bun:test"
import {
  DEFAULT_RIGHT_SIDEBAR_SIZE,
  getDefaultRightSidebarVisibilityState,
  RIGHT_SIDEBAR_MIN_WIDTH_PX,
  useRightSidebarStore,
} from "./rightSidebarStore"

const PROJECT_ID = "project-1"

describe("rightSidebarStore", () => {
  beforeEach(() => {
    useRightSidebarStore.setState({ size: DEFAULT_RIGHT_SIDEBAR_SIZE, projects: {} })
  })

  test("defaults to a closed local-diff drawer", () => {
    const visibility = useRightSidebarStore.getState().projects[PROJECT_ID] ?? getDefaultRightSidebarVisibilityState()
    expect(visibility.rightPanel).toBe("hidden")
    expect(useRightSidebarStore.getState().size).toBe(DEFAULT_RIGHT_SIDEBAR_SIZE)
  })

  test("keeps visibility isolated per Project while sharing width", () => {
    useRightSidebarStore.getState().togglePanel(PROJECT_ID, "git")
    useRightSidebarStore.getState().setSize(430)
    expect(useRightSidebarStore.getState().projects[PROJECT_ID]?.rightPanel).toBe("git")
    expect(useRightSidebarStore.getState().projects["project-2"]).toBeUndefined()
    expect(useRightSidebarStore.getState().size).toBe(430)
  })

  test("toggles the only supported panel and clamps width", () => {
    useRightSidebarStore.getState().togglePanel(PROJECT_ID, "git")
    useRightSidebarStore.getState().togglePanel(PROJECT_ID, "git")
    expect(useRightSidebarStore.getState().projects[PROJECT_ID]?.rightPanel).toBe("hidden")
    useRightSidebarStore.getState().setSize(100)
    expect(useRightSidebarStore.getState().size).toBe(RIGHT_SIDEBAR_MIN_WIDTH_PX)
  })

  test("clears only the requested Project", () => {
    useRightSidebarStore.getState().togglePanel(PROJECT_ID, "git")
    useRightSidebarStore.getState().togglePanel("project-2", "git")
    useRightSidebarStore.getState().clearProject(PROJECT_ID)
    expect(useRightSidebarStore.getState().projects[PROJECT_ID]).toBeUndefined()
    expect(useRightSidebarStore.getState().projects["project-2"]?.rightPanel).toBe("git")
  })
})
