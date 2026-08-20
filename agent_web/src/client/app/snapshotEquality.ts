import type {
  ChatDiffSnapshot,
  ChatSnapshot,
  ProviderCatalogEntry,
  QueuedChatMessage,
  TranscriptEntry,
} from "../../shared/types"
import { sameAttachmentArray } from "./KannaTranscript"

// Hand-rolled equality helpers for socket snapshots. They let subscription
// handlers keep the previous state object (and thus skip re-renders) when a
// freshly-pushed snapshot is structurally identical to what we already have.

function sameRuntime(left: ChatSnapshot["runtime"] | null | undefined, right: ChatSnapshot["runtime"] | null | undefined) {
  if (left === right) return true
  if (!left || !right) return false
  return left.chatId === right.chatId
    && left.projectId === right.projectId
    && left.title === right.title
    && left.status === right.status
    && left.isDraining === right.isDraining
    && left.provider === right.provider
    && left.planMode === right.planMode
    && left.autoPlan === right.autoPlan
    && left.sessionToken === right.sessionToken
}

function sameTranscriptEntries(left: ChatSnapshot["messages"] | null | undefined, right: ChatSnapshot["messages"] | null | undefined) {
  if (left === right) return true
  if (!left || !right) return false
  if (left.length !== right.length) return false
  return left.every((entry, index) => entry._id === right[index]?._id)
}

function sameProviders(left: ProviderCatalogEntry[] | null | undefined, right: ProviderCatalogEntry[] | null | undefined) {
  if (left === right) return true
  if (!left || !right) return false
  if (left.length !== right.length) return false
  return left.every((provider, index) => {
    const other = right[index]
    return Boolean(other)
      && provider.id === other.id
      && provider.label === other.label
      && provider.defaultModel === other.defaultModel
      && provider.models.length === other.models.length
      && provider.models.every((model, modelIndex) => {
        const otherModel = other.models[modelIndex]
        return Boolean(otherModel)
          && model.id === otherModel.id
          && model.label === otherModel.label
          && model.supportsEffort === otherModel.supportsEffort
      })
  })
}


function sameQueuedMessage(left: QueuedChatMessage, right: QueuedChatMessage) {
  return left.id === right.id
    && left.content === right.content
    && left.createdAt === right.createdAt
    && left.provider === right.provider
    && left.model === right.model
    && left.planMode === right.planMode
    && left.autoPlan === right.autoPlan
    && JSON.stringify(left.modelOptions) === JSON.stringify(right.modelOptions)
    && sameAttachmentArray(left.attachments, right.attachments)
}

function sameQueuedMessages(left: ChatSnapshot["queuedMessages"] | null | undefined, right: ChatSnapshot["queuedMessages"] | null | undefined) {
  if (left === right) return true
  if (!left || !right) return false
  if (left.length !== right.length) return false
  return left.every((message, index) => sameQueuedMessage(message, right[index]!))
}

export function sameDiffs(left: ChatDiffSnapshot | null | undefined, right: ChatDiffSnapshot | null | undefined) {
  if (left === right) return true
  if (!left || !right) return false
  if (left.status !== right.status) return false
  if (left.branchName !== right.branchName) return false
  if (left.files.length !== right.files.length) return false
  return left.files.every((file, index) => {
    const other = right.files[index]
    return Boolean(other)
      && file.path === other.path
      && file.changeType === other.changeType
      && file.isUntracked === other.isUntracked
      && file.additions === other.additions
      && file.deletions === other.deletions
      && file.patchDigest === other.patchDigest
      && file.mimeType === other.mimeType
      && file.size === other.size
  })
}

export function shouldPreserveExistingProjectDiffs(
  current: ChatDiffSnapshot | null | undefined,
  next: ChatDiffSnapshot | null | undefined
) {
  return Boolean(
    current
    && current.status !== "unknown"
    && next
    && next.status === "unknown"
    && next.files.length === 0
  )
}

export function sameChatSnapshotCore(left: ChatSnapshot | null, right: ChatSnapshot | null) {
  if (left === right) return true
  if (!left || !right) return false
  return sameRuntime(left.runtime, right.runtime)
    && sameQueuedMessages(left.queuedMessages, right.queuedMessages)
    && sameTranscriptEntries(left.messages, right.messages)
    && sameProviders(left.availableProviders, right.availableProviders)
}

/**
 * Fold an incremental chat snapshot into the one already held.
 *
 * The server sends only the entries past what this socket last received, so
 * the body has to be spliced back onto the window at its absolute index. A
 * snapshot that is not incremental replaces outright.
 *
 * Returns null when the incoming body cannot be placed contiguously — the
 * server's cursor should make that unreachable, but a caller that gets null
 * must not render a transcript with a hole in it.
 */
export function applyIncrementalChatSnapshot(
  current: Pick<ChatSnapshot, "messages" | "startIndex"> | null,
  incoming: ChatSnapshot | null
): ChatSnapshot | null {
  if (!incoming?.incremental) return incoming
  if (!current) return null

  const offset = incoming.startIndex - current.startIndex
  if (offset < 0 || offset > current.messages.length) return null

  const messages = current.messages.slice(0, offset)
  messages.push(...incoming.messages)
  return {
    ...incoming,
    messages,
    startIndex: current.startIndex,
    incremental: false,
  }
}

/**
 * Fold a pushed snapshot into the one on screen — the whole body of the chat
 * subscription's state updater, extracted so its purity is testable.
 *
 * It must be a pure function of its three arguments, and that is not a style
 * preference. React re-runs state updaters: twice under StrictMode, and again
 * whenever it re-renders from an attempt it discarded. This logic once cleared
 * its cached `base` as it went, and the second run then had nothing to splice
 * the first incremental body onto — it returned null, hit the unplaceable guard,
 * and left the state at null. Reopening a chat with a warm cache painted an
 * empty transcript that only recovered on the next unrelated push.
 *
 * `base` is the window read from the local cache. It seeds the first fold only;
 * `current` takes precedence the moment there is one, so it retires on its own
 * without anyone having to clear it.
 */
export function foldChatSnapshot(
  current: ChatSnapshot | null,
  base: Pick<ChatSnapshot, "messages" | "startIndex"> | null,
  incoming: ChatSnapshot | null
): ChatSnapshot | null {
  const next = applyIncrementalChatSnapshot(current ?? base, incoming)
  if (next === null && incoming?.incremental) {
    // Unplaceable body — keep what is on screen rather than render a transcript
    // with a hole; the next full push repairs it.
    return current
  }
  return sameChatSnapshotCore(current, next) ? current : next
}

export function mergeTranscriptEntries(olderHistoryEntries: TranscriptEntry[], recentEntries: TranscriptEntry[]) {
  const deduped = new Map<string, TranscriptEntry>()
  for (const entry of olderHistoryEntries) {
    deduped.set(entry._id, entry)
  }
  for (const entry of recentEntries) {
    deduped.set(entry._id, entry)
  }
  return [...deduped.values()]
}
