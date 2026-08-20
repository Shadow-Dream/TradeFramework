import type { ComponentPropsWithRef } from "react"
import { GitBranch } from "lucide-react"
import type { ProjectSidebarLabel } from "../../lib/project-label"
import { cn } from "../../lib/utils"

/**
 * A project's name as the sidebar shows it: the repo, prefixed with a branch
 * glyph when the project is on a branch worth flagging.
 *
 * This used to read `repo/branch`, which spent the widest part of a very narrow
 * slot on the least scannable half — at sidebar width a long branch name pushed
 * the repo into an ellipsis, so the one thing that tells two rows apart was the
 * first to go. The glyph says "not on main" in a fixed 12px; the branch itself,
 * with its owner and everything else about the chat, is in the hover card
 * (`SidebarChatHoverCard`).
 *
 * Takes `ref` and spreads the rest so it can be a Radix `asChild` trigger — the
 * Projects-tab header uses it as one.
 */
export function ProjectLabel({
  label,
  className,
  ...props
}: { label: ProjectSidebarLabel } & ComponentPropsWithRef<"span">) {
  return (
    <span className={cn("flex min-w-0 items-center gap-1", className)} {...props}>
      {/* shrink-0 so a long repo name never squeezes the glyph into a sliver. */}
      {/* Small and heavy: at this size a 2px stroke reads as a grey smudge, so
          the glyph carries its weight by being drawn thicker rather than bigger. */}
      {label.branchName ? <GitBranch className="size-2.5 shrink-0" strokeWidth={2.5} /> : null}
      <span className="min-w-0 truncate">{label.name}</span>
    </span>
  )
}
