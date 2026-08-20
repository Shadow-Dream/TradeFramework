import { create } from "zustand"
import type { UiTurnContextV1 } from "../../shared/ui-sync-protocol"

interface UiContextStoreState {
  snapshot: UiTurnContextV1 | null
  setSnapshot: (snapshot: UiTurnContextV1 | null) => void
}

export const useUiContextStore = create<UiContextStoreState>((set) => ({
  snapshot: null,
  setSnapshot: (snapshot) => set({ snapshot }),
}))
