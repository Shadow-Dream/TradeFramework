import { create } from "zustand"
import { persist } from "zustand/middleware"

export interface ProjectRightSidebarVisibilityState {
  rightPanel: "hidden" | "git"
}

interface RightSidebarState {
  size: number
  projects: Record<string, ProjectRightSidebarVisibilityState>
  togglePanel: (projectId: string, panel: "git") => void
  hidePanel: (projectId: string) => void
  setSize: (size: number) => void
  clearProject: (projectId: string) => void
}

export const DEFAULT_RIGHT_SIDEBAR_SIZE = 420
export const RIGHT_SIDEBAR_MIN_WIDTH_PX = 370

function clampSize(size: number) {
  if (!Number.isFinite(size)) return DEFAULT_RIGHT_SIDEBAR_SIZE
  return Math.max(RIGHT_SIDEBAR_MIN_WIDTH_PX, size)
}

function getProjectVisibilityState(
  projects: Record<string, ProjectRightSidebarVisibilityState>,
  projectId: string,
) {
  return projects[projectId] ?? DEFAULT_RIGHT_SIDEBAR_VISIBILITY_STATE
}

export const useRightSidebarStore = create<RightSidebarState>()(
  persist(
    (set) => ({
      size: DEFAULT_RIGHT_SIDEBAR_SIZE,
      projects: {},
      togglePanel: (projectId, panel) => set((state) => ({
        projects: {
          ...state.projects,
          [projectId]: {
            rightPanel: getProjectVisibilityState(state.projects, projectId).rightPanel === panel ? "hidden" : panel,
          },
        },
      })),
      hidePanel: (projectId) => set((state) => ({
        projects: { ...state.projects, [projectId]: { rightPanel: "hidden" } },
      })),
      setSize: (size) => set({ size: clampSize(size) }),
      clearProject: (projectId) => set((state) => {
        const { [projectId]: _project, ...projects } = state.projects
        return { projects }
      }),
    }),
    {
      name: "trade-agent-right-sidebar-v1",
      version: 1,
      partialize: (state) => ({ size: state.size, projects: state.projects }),
    },
  ),
)

export const DEFAULT_RIGHT_SIDEBAR_VISIBILITY_STATE: ProjectRightSidebarVisibilityState = {
  rightPanel: "hidden",
}

export function getDefaultRightSidebarVisibilityState() {
  return { ...DEFAULT_RIGHT_SIDEBAR_VISIBILITY_STATE }
}
