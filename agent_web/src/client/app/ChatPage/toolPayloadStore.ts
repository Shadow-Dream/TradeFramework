import type { TranscriptEntry } from "../../../shared/types"

/**
 * Cache of tool payloads fetched on demand.
 *
 * Transcripts arrive with tool inputs and results left behind on the server —
 * a collapsed row draws none of them. This holds the ones we have since asked
 * for, so hovering a row can warm it before the click and expanding twice
 * costs one request.
 *
 * Requests made in the same tick are coalesced into a single batch, and an id
 * already cached or already in flight is never asked for twice. A failed batch
 * forgets itself so opening the row can try again.
 */

export interface ToolPayloadStore {
  /** Warm these ids. Safe to call on every hover; duplicates cost nothing. */
  prefetch(entryIds: ReadonlyArray<string | undefined>): void
  get(entryId: string | undefined): TranscriptEntry | undefined
  subscribe(entryId: string | undefined, onChange: () => void): () => void
}

export function createToolPayloadStore(
  fetchEntries: (entryIds: string[]) => Promise<TranscriptEntry[]>
): ToolPayloadStore {
  const cache = new Map<string, TranscriptEntry>()
  const inFlight = new Set<string>()
  const listeners = new Map<string, Set<() => void>>()
  let queued: Set<string> | null = null

  function notify(entryId: string) {
    for (const listener of listeners.get(entryId) ?? []) listener()
  }

  async function flush(batch: Set<string>) {
    const entryIds = [...batch]
    try {
      for (const entry of await fetchEntries(entryIds)) {
        cache.set(entry._id, entry)
      }
    } catch {
      // Swallowed on purpose: a payload that failed to load is a row that
      // stays in its loading state, not an error worth interrupting a
      // transcript for. Clearing in-flight below lets the next open retry.
    } finally {
      for (const entryId of entryIds) {
        inFlight.delete(entryId)
        notify(entryId)
      }
    }
  }

  return {
    prefetch(entryIds) {
      for (const entryId of entryIds) {
        if (!entryId || cache.has(entryId) || inFlight.has(entryId)) continue
        inFlight.add(entryId)
        if (!queued) {
          queued = new Set()
          // One batch per tick, so hovering a group of thirty calls is one
          // request rather than thirty.
          const batch = queued
          queueMicrotask(() => {
            queued = null
            void flush(batch)
          })
        }
        queued.add(entryId)
      }
    },

    get(entryId) {
      return entryId ? cache.get(entryId) : undefined
    },

    subscribe(entryId, onChange) {
      if (!entryId) return () => {}
      let forId = listeners.get(entryId)
      if (!forId) {
        forId = new Set()
        listeners.set(entryId, forId)
      }
      forId.add(onChange)
      return () => {
        forId.delete(onChange)
        if (forId.size === 0) listeners.delete(entryId)
      }
    },
  }
}
