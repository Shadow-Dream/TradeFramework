import { describe, expect, test } from "bun:test"
import type { TranscriptEntry } from "../../../shared/types"
import { createToolPayloadStore } from "./toolPayloadStore"

function entry(id: string): TranscriptEntry {
  return { _id: id, createdAt: 1, kind: "tool_result", toolId: `t-${id}`, content: `body-${id}` } as unknown as TranscriptEntry
}

/** Lets a test hold a fetch open and settle it on demand. */
function deferredFetcher() {
  const calls: string[][] = []
  let resolveCurrent: ((entries: TranscriptEntry[]) => void) | undefined
  let rejectCurrent: ((error: Error) => void) | undefined
  const fetchEntries = (entryIds: string[]) => {
    calls.push(entryIds)
    return new Promise<TranscriptEntry[]>((resolve, reject) => {
      resolveCurrent = resolve
      rejectCurrent = reject
    })
  }
  return {
    calls,
    fetchEntries,
    resolve: (entries: TranscriptEntry[]) => resolveCurrent?.(entries),
    reject: (error: Error) => rejectCurrent?.(error),
  }
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0))

describe("createToolPayloadStore", () => {
  test("coalesces ids requested in the same tick into one fetch", async () => {
    const fetcher = deferredFetcher()
    const store = createToolPayloadStore(fetcher.fetchEntries)

    store.prefetch(["a", "b"])
    store.prefetch(["c"])
    await tick()

    expect(fetcher.calls).toEqual([["a", "b", "c"]])
  })

  test("serves fetched entries and stops asking for them", async () => {
    const fetcher = deferredFetcher()
    const store = createToolPayloadStore(fetcher.fetchEntries)

    store.prefetch(["a"])
    await tick()
    fetcher.resolve([entry("a")])
    await tick()

    expect(store.get("a")?._id).toBe("a")
    store.prefetch(["a"])
    await tick()
    expect(fetcher.calls).toHaveLength(1)
  })

  test("does not re-request an id already in flight", async () => {
    const fetcher = deferredFetcher()
    const store = createToolPayloadStore(fetcher.fetchEntries)

    store.prefetch(["a"])
    await tick()
    store.prefetch(["a"])
    await tick()

    expect(fetcher.calls).toEqual([["a"]])
  })

  test("notifies subscribers once their entry lands", async () => {
    const fetcher = deferredFetcher()
    const store = createToolPayloadStore(fetcher.fetchEntries)
    let notified = 0
    store.subscribe("a", () => { notified += 1 })

    store.prefetch(["a"])
    await tick()
    expect(notified).toBe(0)

    fetcher.resolve([entry("a")])
    await tick()
    expect(notified).toBe(1)
  })

  test("a failed fetch can be retried rather than wedging the row", async () => {
    const fetcher = deferredFetcher()
    const store = createToolPayloadStore(fetcher.fetchEntries)

    store.prefetch(["a"])
    await tick()
    fetcher.reject(new Error("socket closed"))
    await tick()

    expect(store.get("a")).toBeUndefined()

    store.prefetch(["a"])
    await tick()
    expect(fetcher.calls).toHaveLength(2)
  })

  test("ignores undefined ids, so callers need not filter", async () => {
    const fetcher = deferredFetcher()
    const store = createToolPayloadStore(fetcher.fetchEntries)

    store.prefetch([undefined, "a", undefined])
    await tick()

    expect(fetcher.calls).toEqual([["a"]])
    expect(store.get(undefined)).toBeUndefined()
  })

  test("an unsubscribed listener stops hearing about its entry", async () => {
    const fetcher = deferredFetcher()
    const store = createToolPayloadStore(fetcher.fetchEntries)
    let notified = 0
    const unsubscribe = store.subscribe("a", () => { notified += 1 })

    store.prefetch(["a"])
    await tick()
    unsubscribe()
    fetcher.resolve([entry("a")])
    await tick()

    expect(notified).toBe(0)
  })
})
