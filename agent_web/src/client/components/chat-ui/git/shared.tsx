import type { ReactNode } from "react"
import type { ChatDiffSnapshot } from "../../../../shared/types"
import { cn } from "../../../lib/utils"
import { Tooltip, TooltipContent, TooltipTrigger } from "../../ui/tooltip"

export type DiffRenderMode = "unified" | "split"
export type DiffFile = ChatDiffSnapshot["files"][number]

export function IconButton(props: {
  label: string
  active?: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <Tooltip delayDuration={0}>
      <TooltipTrigger asChild>
        <button
          type="button"
          aria-label={props.label}
          title={props.label}
          onClick={props.onClick}
          className={cn(
            "flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground",
            props.active && "bg-accent text-foreground"
          )}
        >
          {props.children}
        </button>
      </TooltipTrigger>
      <TooltipContent>{props.label}</TooltipContent>
    </Tooltip>
  )
}

/**
 * A file's line changes — `+6`, `-2`, or both — in the git panel's colours.
 *
 * Shared with the sidebar hover card's list of what a chat changed, so the two
 * places you read "how big was this edit" read identically. Each side keeps its
 * own meaning for the numbers (the panel's are the file's current diff against
 * HEAD; the card's are what one chat wrote there), but a count is a count and
 * two typographies for it would only invite comparing them wrongly.
 *
 * Renders nothing when both counts are zero or unknown, so a binary file — or
 * anything recorded before counts existed — leaves an empty slot rather than
 * claiming `+0`.
 */
export function DiffFileStat({
  additions,
  deletions,
  className,
}: {
  additions?: number
  deletions?: number
  className?: string
}) {
  const hasAdditions = (additions ?? 0) > 0
  const hasDeletions = (deletions ?? 0) > 0
  if (!hasAdditions && !hasDeletions) return null

  return (
    <span className={cn("whitespace-nowrap text-xs font-mono", className)}>
      {hasAdditions ? <span className="text-emerald-600 dark:text-emerald-400">+{additions}</span> : null}
      {hasDeletions ? (
        <span className={cn("text-red-600 dark:text-red-400", hasAdditions && "ml-2")}>-{deletions}</span>
      ) : null}
    </span>
  )
}
