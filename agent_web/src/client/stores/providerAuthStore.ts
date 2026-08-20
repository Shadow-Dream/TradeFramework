import { useMemo } from "react"
import { create } from "zustand"
import {
  authServiceForProvider,
  type AgentProvider,
  type AppSettingsPatch,
  type AuthServiceId,
  type AuthServiceSnapshot,
  type ProviderAuthSnapshot,
} from "../../shared/types"
import type { KannaSocket } from "../app/socket"

interface ProviderAuthStore {
  snapshot: ProviderAuthSnapshot | null
  /** The app socket, registered by the layout so deep components can send auth commands. */
  socket: KannaSocket | null
  /** Full-screen setup wizard visibility. */
  setupWizardOpen: boolean
  /**
   * The server's app-settings snapshot has arrived, so the three flags below
   * reflect this machine rather than their pre-load defaults. Until then the
   * auto-launch decision has to wait — otherwise a browser that has never
   * connected would read `false` and re-run onboarding on every new browser.
   */
  setupLoaded: boolean
  /** The wizard has been shown at least once (persisted per machine). */
  setupShown: boolean
  /** The wizard's final step was completed (persisted). Hides the Setup cards. */
  setupCompleted: boolean
  /** "Set up later" was chosen (persisted). Suppresses auto-launch only. */
  setupDismissed: boolean
  setSnapshot: (snapshot: ProviderAuthSnapshot | null) => void
  setSocket: (socket: KannaSocket | null) => void
  /** Adopt the machine-wide setup flags pushed on the `app-settings` topic. */
  setSetupFlagsFromServer: (flags: {
    setupShown: boolean
    setupCompleted: boolean
    setupDismissed: boolean
  }) => void
  openSetupWizard: () => void
  /** Close without finishing — persists the dismissal so we never auto-launch again. */
  dismissSetupWizard: () => void
  /** Close from the final step — persists completion so Setup cards disappear. */
  completeSetupWizard: () => void
}

export const useProviderAuthStore = create<ProviderAuthStore>((set, get) => {
  /**
   * Persist an onboarding marker on the machine. Fire-and-forget: the local
   * flag is already set optimistically, and the server echoes the settings
   * snapshot back on the `app-settings` topic to every connected browser.
   */
  const persistSetupFlags = (patch: AppSettingsPatch) => {
    const { socket } = get()
    if (!socket) return
    void socket
      .command({ type: "settings.writeAppSettingsPatch", patch })
      .catch(() => undefined)
  }

  return {
    snapshot: null,
    socket: null,
    setupWizardOpen: false,
    setupLoaded: false,
    setupShown: false,
    setupCompleted: false,
    setupDismissed: false,
    setSnapshot: (snapshot) => set({ snapshot }),
    setSocket: (socket) => set({ socket }),
    setSetupFlagsFromServer: (flags) =>
      set({
        setupLoaded: true,
        // Markers are one-way latches: never let a snapshot un-set a flag this
        // browser just set optimistically while its write is still in flight.
        setupShown: get().setupShown || flags.setupShown,
        setupCompleted: get().setupCompleted || flags.setupCompleted,
        setupDismissed: get().setupDismissed || flags.setupDismissed,
      }),
    openSetupWizard: () => {
      persistSetupFlags({ setupShown: true })
      set({ setupWizardOpen: true, setupShown: true })
    },
    dismissSetupWizard: () => {
      persistSetupFlags({ setupDismissed: true })
      set({ setupWizardOpen: false, setupDismissed: true })
    },
    completeSetupWizard: () => {
      persistSetupFlags({ setupCompleted: true, setupDismissed: true })
      set({ setupWizardOpen: false, setupCompleted: true, setupDismissed: true })
    },
  }
})

export function selectAuthService(
  snapshot: ProviderAuthSnapshot | null,
  service: AuthServiceId
): AuthServiceSnapshot | null {
  return snapshot?.services.find((entry) => entry.service === service) ?? null
}

/**
 * Harnesses whose gating auth service is known to be signed out or missing.
 * "unknown"/"error" states don't gate — never block a switch on a failed probe.
 */
export function getUnauthenticatedHarnesses(snapshot: ProviderAuthSnapshot | null): Set<AgentProvider> {
  const result = new Set<AgentProvider>()
  if (!snapshot) return result
  for (const provider of ["claude-deepseek", "codex-openai"] as const) {
    const serviceId = authServiceForProvider(provider)
    if (!serviceId) continue
    const service = selectAuthService(snapshot, serviceId)
    if (service && (service.authStatus === "signed_out" || service.authStatus === "not_installed")) {
      result.add(provider)
    }
  }
  return result
}

export function useAuthService(service: AuthServiceId): AuthServiceSnapshot | null {
  return useProviderAuthStore((store) => selectAuthService(store.snapshot, service))
}

export function useUnauthenticatedHarnesses(): Set<AgentProvider> {
  const snapshot = useProviderAuthStore((store) => store.snapshot)
  return useMemo(() => getUnauthenticatedHarnesses(snapshot), [snapshot])
}
