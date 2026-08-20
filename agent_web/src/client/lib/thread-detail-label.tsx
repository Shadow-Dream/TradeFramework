import type { ReactNode } from "react"
import { ProjectLabel } from "../components/chat-ui/ProjectLabel"
import { formatSidebarAgeLabel } from "./formatters"
import { getSidebarChatTimestamp } from "./sidebarChats"
import type { SidebarThread } from "./thread-sections"

/**
 * Does this list of chat rows span projects, or is it already inside one?
 * That's the only input the detail slot needs.
 */
export type ThreadDetailScope =
  /** Rows from many projects — the palette's root results, the sidebar's Chats tab. */
  | "cross-project"
  /** Rows under one project header — the palette's project page, the sidebar's Projects tab. */
  | "project-scoped"

/**
 * What goes in a chat row's trailing detail slot. Every surface that renders a
 * chat row asks this one question rather than answering it locally:
 *
 * - **Cross-project** → the project, because that's what tells two rows apart.
 *   Rendered by `ProjectLabel`, so it picks up the branch glyph.
 * - **Project-scoped** → the chat's age, because the project is already on the
 *   header above and would just repeat down the whole list.
 *
 * Call sites pass a *scope*, not a label. That's the point: a new surface can't
 * quietly invent a third policy, and improving either answer — the way
 * repo/branch arrived — reaches every surface at once instead of the one that
 * happened to get edited. `ThreadRowContent` takes `detailLabel` as a required
 * prop for the same reason: there is no implicit default to fall through to.
 *
 * Returns `null` when there's nothing worth showing, which renders as an empty
 * slot rather than a gap in the layout.
 */
export function getThreadDetailLabel(
  thread: SidebarThread,
  scope: ThreadDetailScope,
  nowMs: number
): ReactNode {
  if (scope === "cross-project") return <ProjectLabel label={thread.projectLabel} />

  // Deliberately `getSidebarChatTimestamp` and not `thread.lastActivityAt`: the
  // latter folds in turn-end, so a chat that just finished would read "now"
  // where this reads the age of its last message.
  return formatSidebarAgeLabel(getSidebarChatTimestamp(thread.row), nowMs)
}
