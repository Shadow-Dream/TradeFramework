import type { ContextWindowSnapshot } from "../../lib/contextWindow"

export const EMPTY_STATE_TEXT = "What are we building?"
export const EMPTY_STATE_TYPING_INTERVAL_MS = 19
export const CHAT_NAVBAR_OFFSET_PX = 72

export function getIgnoreFolderEntryFromDiffPath(filePath: string) {
  const normalized = filePath.replaceAll("\\", "/").replace(/\/+/g, "/").replace(/\/$/u, "")
  const lastSlashIndex = normalized.lastIndexOf("/")
  if (lastSlashIndex <= 0) {
    return null
  }
  return `${normalized.slice(0, lastSlashIndex)}/`
}

export function shouldAutoFollowTranscriptResize(
  showScrollButton: boolean,
  selectionAutoFollowUntil: number,
  now = Date.now()
) {
  return !showScrollButton || now < selectionAutoFollowUntil
}

export function sameContextWindowSnapshot(left: ContextWindowSnapshot | null, right: ContextWindowSnapshot | null) {
  if (left === right) return true
  if (!left || !right) return false
  return left.usedTokens === right.usedTokens
    && left.maxTokens === right.maxTokens
    && left.remainingTokens === right.remainingTokens
    && left.usedPercentage === right.usedPercentage
    && left.remainingPercentage === right.remainingPercentage
    && left.compactsAutomatically === right.compactsAutomatically
    && left.updatedAt === right.updatedAt
}

export function hasFileDragTypes(types: Iterable<string>) {
  return Array.from(types).includes("Files")
}
