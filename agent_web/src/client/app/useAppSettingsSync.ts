import { useCallback, useEffect, useState } from "react"
import type { AppSettingsPatch, AppSettingsSnapshot, KeybindingsSnapshot } from "../../shared/types"
import { useAppSettingsStore } from "../stores/appSettingsStore"
import { useChatPreferencesStore } from "../stores/chatPreferencesStore"
import { useChatSoundPreferencesStore } from "../stores/chatSoundPreferencesStore"
import { useProviderAuthStore } from "../stores/providerAuthStore"
import { useTerminalPreferencesStore } from "../stores/terminalPreferencesStore"
import type { KannaSocket, SocketStatus } from "./socket"

function syncRuntimeStores(snapshot: AppSettingsSnapshot) {
  useAppSettingsStore.getState().setFromServer(snapshot)
  const terminal = useTerminalPreferencesStore.getState()
  terminal.setScrollbackLines(snapshot.terminal.scrollbackLines)
  terminal.setMinColumnWidth(snapshot.terminal.minColumnWidth)
  terminal.setWebglRenderer(snapshot.terminal.webglRenderer)
  const sounds = useChatSoundPreferencesStore.getState()
  sounds.setChatSoundPreference(snapshot.chatSoundPreference)
  sounds.setChatSoundId(snapshot.chatSoundId)
  useChatPreferencesStore.getState().syncProviderDefaults(snapshot.defaultProvider, snapshot.providerDefaults)
  useProviderAuthStore.getState().setSetupFlagsFromServer({
    setupShown: snapshot.setupShown,
    setupCompleted: snapshot.setupCompleted,
    setupDismissed: snapshot.setupDismissed,
  })
}

export function useAppSettingsSync(params: {
  socket: KannaSocket
  connectionStatus: SocketStatus
  setCommandError: (message: string | null) => void
}) {
  const { socket, connectionStatus, setCommandError } = params
  const [keybindings, setKeybindings] = useState<KeybindingsSnapshot | null>(null)
  const [appSettings, setAppSettings] = useState<AppSettingsSnapshot | null>(null)

  useEffect(() => socket.subscribe<KeybindingsSnapshot>({ type: "keybindings" }, (snapshot) => {
    setKeybindings(snapshot)
    setCommandError(null)
  }), [setCommandError, socket])

  useEffect(() => socket.subscribe<AppSettingsSnapshot>({ type: "app-settings" }, (snapshot) => {
    setAppSettings(snapshot)
    syncRuntimeStores(snapshot)
    setCommandError(null)
  }), [setCommandError, socket])

  const handleReadAppSettings = useCallback(async () => {
    try {
      const snapshot = await socket.command<AppSettingsSnapshot>({ type: "settings.readAppSettings" })
      setAppSettings(snapshot)
      syncRuntimeStores(snapshot)
      setCommandError(null)
    } catch (error) {
      setCommandError(error instanceof Error ? error.message : String(error))
    }
  }, [setCommandError, socket])

  const handleWriteAppSettings = useCallback(async (patch: AppSettingsPatch) => {
    try {
      useAppSettingsStore.getState().applyOptimisticPatch(patch)
      const snapshot = await socket.command<AppSettingsSnapshot>({ type: "settings.writeAppSettingsPatch", patch })
      setAppSettings(snapshot)
      syncRuntimeStores(snapshot)
      setCommandError(null)
    } catch (error) {
      setCommandError(error instanceof Error ? error.message : String(error))
      await handleReadAppSettings()
      throw error
    }
  }, [handleReadAppSettings, setCommandError, socket])

  useEffect(() => {
    if (connectionStatus === "connected") void handleReadAppSettings()
  }, [connectionStatus, handleReadAppSettings])

  return { keybindings, appSettings, handleReadAppSettings, handleWriteAppSettings }
}
