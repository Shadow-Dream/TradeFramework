import type { KeyboardEvent, ReactNode } from "react"
import { cn } from "../../lib/utils"
import type { SettingsRowDef } from "./registry"

/** Shared row layout + tiny helpers for the settings sections. */

export const ENABLED_DISABLED_OPTIONS = [
  { value: "disabled" as const, label: "Off" },
  { value: "enabled" as const, label: "On" },
]

export function getKeybindingsSubtitle() {
  return "Edit Agent Web keyboard shortcuts."
}

export function shouldPreviewChatSoundChange(
  previousValue: string,
  nextValue: string
) {
  return previousValue !== nextValue
}

export function handleSettingsInputKeyDown(event: KeyboardEvent<HTMLInputElement>, commit: () => void) {
  if (event.key !== "Enter") return
  commit()
  event.currentTarget.blur()
}

export function SettingsErrorBanner({ message }: { message: string }) {
  return (
    <div className="mb-4 rounded-lg border border-destructive/20 bg-destructive/5 px-4 py-3 text-sm text-destructive">
      {message}
    </div>
  )
}

type SettingsRowProps = {
  children: ReactNode
  bordered?: boolean
  alignStart?: boolean
  /** Overrides `def.description` when the rendered description is dynamic JSX. */
  description?: ReactNode
} & (
  | {
    /** Registry def: provides the anchor id (palette jump target) + title/description. */
    def: SettingsRowDef
    title?: string
  }
  | {
    def?: undefined
    title: string
    description: ReactNode
  }
)

export function SettingsRow({
  def,
  title,
  description,
  children,
  bordered = true,
  alignStart = false,
}: SettingsRowProps) {
  return (
    <div
      id={def?.id}
      data-settings-row={def ? "" : undefined}
      className={cn("scroll-mt-4", bordered && "border-t border-border")}
    >
      <div
        className={cn(
          "flex flex-col gap-4 py-5 md:flex-row md:justify-between md:gap-8",
          alignStart ? "md:items-start" : "md:items-center"
        )}
      >
        <div className="min-w-0 max-w-xl">
          <div className="text-sm font-medium text-foreground">{title ?? def?.title}</div>
          <div className="mt-1 text-[13px] text-muted-foreground">{description ?? def?.description}</div>
        </div>
        <div className="flex items-center justify-start md:shrink-0 md:justify-end">{children}</div>
      </div>
    </div>
  )
}
