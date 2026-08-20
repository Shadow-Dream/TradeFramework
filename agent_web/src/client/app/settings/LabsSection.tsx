import { useState } from "react"
import { SegmentedControl } from "../../components/ui/segmented-control"
import type { KannaState } from "../useKannaState"
import { SETTINGS_ROWS } from "./registry"
import { ENABLED_DISABLED_OPTIONS, SettingsErrorBanner, SettingsRow } from "./shared"

export function LabsSection({
  state,
}: {
  state: Pick<KannaState, "appSettings" | "handleWriteAppSettings">
}) {
  const { appSettings, handleWriteAppSettings } = state
  const [error, setError] = useState<string | null>(null)

  async function handleWebglRendererChange(nextValue: "enabled" | "disabled") {
    try {
      setError(null)
      await handleWriteAppSettings({ terminal: { webglRenderer: nextValue === "enabled" } })
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to save Labs settings.")
    }
  }

  const webglRendererValue = appSettings?.terminal.webglRenderer === true ? "enabled" : "disabled"

  return (
    <>
      {error ? <SettingsErrorBanner message={error} /> : null}
      <div className="border-b border-border">
        <SettingsRow def={SETTINGS_ROWS.terminalWebglRenderer} bordered={false}>
          <SegmentedControl
            value={webglRendererValue}
            onValueChange={(value) => {
              void handleWebglRendererChange(value)
            }}
            options={ENABLED_DISABLED_OPTIONS}
            size="sm"
          />
        </SettingsRow>
      </div>
    </>
  )
}
