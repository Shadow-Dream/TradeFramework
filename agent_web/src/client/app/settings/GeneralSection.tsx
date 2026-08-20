import { useEffect, useState } from "react"
import { Monitor, Moon, Sun } from "lucide-react"
import { Input } from "../../components/ui/input"
import { SegmentedControl } from "../../components/ui/segmented-control"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select"
import { useTheme, type ThemePreference } from "../../hooks/useTheme"
import { playChatNotificationSound } from "../../lib/chatSounds"
import {
  DEFAULT_TERMINAL_MIN_COLUMN_WIDTH,
  DEFAULT_TERMINAL_SCROLLBACK,
  MAX_TERMINAL_MIN_COLUMN_WIDTH,
  MAX_TERMINAL_SCROLLBACK,
  MIN_TERMINAL_MIN_COLUMN_WIDTH,
  MIN_TERMINAL_SCROLLBACK,
  useTerminalPreferencesStore,
} from "../../stores/terminalPreferencesStore"
import { CHAT_SOUND_OPTIONS, useChatSoundPreferencesStore, type ChatSoundId, type ChatSoundPreference } from "../../stores/chatSoundPreferencesStore"
import type { KannaState } from "../useKannaState"
import {
  handleSettingsInputKeyDown,
  SettingsErrorBanner,
  SettingsRow,
  shouldPreviewChatSoundChange,
} from "./shared"
import { SETTINGS_ROWS } from "./registry"

const themeOptions = [
  { value: "light" as ThemePreference, label: "Light", icon: Sun },
  { value: "dark" as ThemePreference, label: "Dark", icon: Moon },
  { value: "system" as ThemePreference, label: "System", icon: Monitor },
]

const chatSoundPreferenceOptions: { value: ChatSoundPreference; label: string }[] = [
  { value: "never", label: "Never" },
  { value: "unfocused", label: "When Unfocused" },
  { value: "always", label: "Always" },
]

export function GeneralSection({
  state,
}: {
  state: Pick<KannaState, "handleWriteAppSettings">
}) {
  const { theme, setTheme } = useTheme()
  const handleWriteAppSettings = state.handleWriteAppSettings

  const scrollbackLines = useTerminalPreferencesStore((store) => store.scrollbackLines)
  const minColumnWidth = useTerminalPreferencesStore((store) => store.minColumnWidth)
  const setScrollbackLines = useTerminalPreferencesStore((store) => store.setScrollbackLines)
  const setMinColumnWidth = useTerminalPreferencesStore((store) => store.setMinColumnWidth)
  const chatSoundPreference = useChatSoundPreferencesStore((store) => store.chatSoundPreference)
  const chatSoundId = useChatSoundPreferencesStore((store) => store.chatSoundId)
  const setChatSoundPreference = useChatSoundPreferencesStore((store) => store.setChatSoundPreference)
  const setChatSoundId = useChatSoundPreferencesStore((store) => store.setChatSoundId)

  const [scrollbackDraft, setScrollbackDraft] = useState(String(scrollbackLines))
  const [minColumnWidthDraft, setMinColumnWidthDraft] = useState(String(minColumnWidth))
  const [appSettingsError, setAppSettingsError] = useState<string | null>(null)

  useEffect(() => {
    setScrollbackDraft(String(scrollbackLines))
  }, [scrollbackLines])

  useEffect(() => {
    setMinColumnWidthDraft(String(minColumnWidth))
  }, [minColumnWidth])

  function commitScrollback() {
    const nextValue = Number(scrollbackDraft)
    if (!Number.isFinite(nextValue)) {
      setScrollbackDraft(String(scrollbackLines))
      return
    }
    setScrollbackLines(nextValue)
    void handleWriteAppSettings({ terminal: { scrollbackLines: nextValue } }).catch((error) => {
      setAppSettingsError(error instanceof Error ? error.message : "Unable to save terminal settings.")
    })
  }

  function commitMinColumnWidth() {
    const nextValue = Number(minColumnWidthDraft)
    if (!Number.isFinite(nextValue)) {
      setMinColumnWidthDraft(String(minColumnWidth))
      return
    }
    setMinColumnWidth(nextValue)
    void handleWriteAppSettings({ terminal: { minColumnWidth: nextValue } }).catch((error) => {
      setAppSettingsError(error instanceof Error ? error.message : "Unable to save terminal settings.")
    })
  }

  function handleThemeChange(nextTheme: typeof theme) {
    setTheme(nextTheme)
    void handleWriteAppSettings({ theme: nextTheme }).catch((error) => {
      setAppSettingsError(error instanceof Error ? error.message : "Unable to save theme settings.")
    })
  }

  function handleChatSoundPreferenceChange(nextValue: ChatSoundPreference) {
    if (!shouldPreviewChatSoundChange(chatSoundPreference, nextValue)) {
      return
    }

    setChatSoundPreference(nextValue)
    void handleWriteAppSettings({ chatSoundPreference: nextValue }).catch((error) => {
      setAppSettingsError(error instanceof Error ? error.message : "Unable to save chat sound settings.")
    })
    void playChatNotificationSound(chatSoundId, 1).catch(() => undefined)
  }

  function handleChatSoundIdChange(nextValue: ChatSoundId) {
    if (!shouldPreviewChatSoundChange(chatSoundId, nextValue)) {
      return
    }

    setChatSoundId(nextValue)
    void handleWriteAppSettings({ chatSoundId: nextValue }).catch((error) => {
      setAppSettingsError(error instanceof Error ? error.message : "Unable to save chat sound settings.")
    })
    void playChatNotificationSound(nextValue, 1).catch(() => undefined)
  }

  return (
    <>
      {appSettingsError ? <SettingsErrorBanner message={appSettingsError} /> : null}
      <div className="border-b border-border">
        <SettingsRow def={SETTINGS_ROWS.theme} bordered={false}>
          <SegmentedControl
            value={theme}
            onValueChange={handleThemeChange}
            options={themeOptions}
            size="sm"
          />
        </SettingsRow>

        <SettingsRow def={SETTINGS_ROWS.chatSounds}>
          <Select
            value={chatSoundPreference}
            onValueChange={(value) => handleChatSoundPreferenceChange(value as ChatSoundPreference)}
          >
            <SelectTrigger className="min-w-[180px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                {chatSoundPreferenceOptions.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
        </SettingsRow>

        <SettingsRow def={SETTINGS_ROWS.chatSound}>
          <Select
            value={chatSoundId}
            onValueChange={(value) => handleChatSoundIdChange(value as ChatSoundId)}
          >
            <SelectTrigger className="min-w-[180px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                {CHAT_SOUND_OPTIONS.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
        </SettingsRow>

        <SettingsRow def={SETTINGS_ROWS.terminalScrollback}>
          <div className="flex w-full min-w-0 flex-col items-stretch gap-2 md:w-auto md:items-end">
            <Input
              type="number"
              min={MIN_TERMINAL_SCROLLBACK}
              max={MAX_TERMINAL_SCROLLBACK}
              step={100}
              value={scrollbackDraft}
              onChange={(event) => setScrollbackDraft(event.target.value)}
              onBlur={commitScrollback}
              onKeyDown={(event) => handleSettingsInputKeyDown(event, commitScrollback)}
              className="hide-number-steppers w-full text-left font-mono md:w-28 md:text-right"
            />
            <div className="text-left text-xs text-muted-foreground md:text-right">
              {MIN_TERMINAL_SCROLLBACK}-{MAX_TERMINAL_SCROLLBACK} lines
              {scrollbackLines === DEFAULT_TERMINAL_SCROLLBACK ? " (default)" : ""}
            </div>
          </div>
        </SettingsRow>

        <SettingsRow def={SETTINGS_ROWS.terminalMinColumnWidth}>
          <div className="flex w-full min-w-0 flex-col items-stretch gap-2 md:w-auto md:items-end">
            <Input
              type="number"
              min={MIN_TERMINAL_MIN_COLUMN_WIDTH}
              max={MAX_TERMINAL_MIN_COLUMN_WIDTH}
              step={10}
              value={minColumnWidthDraft}
              onChange={(event) => setMinColumnWidthDraft(event.target.value)}
              onBlur={commitMinColumnWidth}
              onKeyDown={(event) => handleSettingsInputKeyDown(event, commitMinColumnWidth)}
              className="hide-number-steppers w-full text-left font-mono md:w-28 md:text-right"
            />
            <div className="text-left text-xs text-muted-foreground md:text-right">
              {MIN_TERMINAL_MIN_COLUMN_WIDTH}-{MAX_TERMINAL_MIN_COLUMN_WIDTH} px
              {minColumnWidth === DEFAULT_TERMINAL_MIN_COLUMN_WIDTH ? " (default)" : ""}
            </div>
          </div>
        </SettingsRow>

      </div>
    </>
  )
}
