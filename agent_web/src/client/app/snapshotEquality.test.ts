import { describe, expect, test } from "bun:test"
import type { ChatSnapshot, TranscriptEntry } from "../../shared/types"
import { applyIncrementalChatSnapshot, foldChatSnapshot } from "./snapshotEquality"

function entry(id: string): TranscriptEntry {
  return { _id: id, createdAt: 0, kind: "assistant_text", text: id } as TranscriptEntry
}

function snapshot(startIndex: number, ids: string[], incremental?: boolean): ChatSnapshot {
  return {
    runtime: {
      chatId: "chat-1",
      projectId: "project-1",
      title: "t",
      status: "idle",
      isDraining: false,
      provider: "claude-deepseek",
      planMode: false,
      autoPlan: false,
      sessionToken: null,
    },
    queuedMessages: [],
    messages: ids.map(entry),
    startIndex,
    ...(incremental ? { incremental: true } : {}),
    availableProviders: [],
    readAnchor: null,
  }
}

const ids = (value: ChatSnapshot | null) => value?.messages.map((message) => message._id)

describe("applyIncrementalChatSnapshot", () => {
  test("a non-incremental snapshot replaces what is held", () => {
    const current = snapshot(10, ["a", "b"])
    const next = applyIncrementalChatSnapshot(current, snapshot(20, ["c"]))
    expect(ids(next)).toEqual(["c"])
    expect(next?.startIndex).toBe(20)
  })

  test("an incremental snapshot appends at its absolute index", () => {
    const current = snapshot(10, ["a", "b"])
    const next = applyIncrementalChatSnapshot(current, snapshot(12, ["c", "d"], true))

    expect(ids(next)).toEqual(["a", "b", "c", "d"])
    // The merged window keeps the held start, and is no longer a fragment.
    expect(next?.startIndex).toBe(10)
    expect(next?.incremental).toBe(false)
  })

  test("an overlapping incremental body replaces the entries it covers", () => {
    // The server re-sends from a point it already sent when a turn's trailing
    // entry is rewritten; the later copy must win rather than duplicate.
    const current = snapshot(10, ["a", "b", "c"])
    const next = applyIncrementalChatSnapshot(current, snapshot(11, ["b2", "c2"], true))
    expect(ids(next)).toEqual(["a", "b2", "c2"])
  })

  test("a gap ahead of the held window is refused rather than papered over", () => {
    const current = snapshot(10, ["a", "b"])
    // startIndex 13 leaves index 12 missing.
    expect(applyIncrementalChatSnapshot(current, snapshot(13, ["e"], true))).toBeNull()
  })

  test("a body starting before the held window is refused", () => {
    const current = snapshot(10, ["a", "b"])
    expect(applyIncrementalChatSnapshot(current, snapshot(8, ["x"], true))).toBeNull()
  })

  test("an incremental body with nothing held is refused", () => {
    expect(applyIncrementalChatSnapshot(null, snapshot(4, ["a"], true))).toBeNull()
  })

  test("a null snapshot clears, incremental or not", () => {
    expect(applyIncrementalChatSnapshot(snapshot(0, ["a"]), null)).toBeNull()
  })
})

describe("foldChatSnapshot", () => {
  // The chat subscription runs this inside a React state updater, and React
  // re-runs updaters — twice under StrictMode, and again on any render it
  // retries. Every case below is therefore asserted twice against the *same*
  // inputs: a second call must produce the same answer as the first.
  function foldTwice(
    current: ChatSnapshot | null,
    base: Pick<ChatSnapshot, "messages" | "startIndex"> | null,
    incoming: ChatSnapshot | null,
  ) {
    const first = foldChatSnapshot(current, base, incoming)
    const second = foldChatSnapshot(current, base, incoming)
    expect(ids(second)).toEqual(ids(first))
    expect(second?.startIndex).toBe(first?.startIndex)
    return first
  }

  test("seeds the first incremental push from the cached window", () => {
    // The regression: this used to clear `base` as a side effect, so the
    // second run had nothing to splice onto and returned null — a reopened
    // chat with a warm cache painted an empty transcript.
    const base = { messages: [entry("a"), entry("b")], startIndex: 0 }
    const folded = foldTwice(null, base, snapshot(2, ["c"], true))

    expect(ids(folded)).toEqual(["a", "b", "c"])
    expect(folded?.startIndex).toBe(0)
  })

  test("prefers what is held over the cached window once there is any", () => {
    const current = snapshot(0, ["a", "b", "c"])
    const stale = { messages: [entry("a")], startIndex: 0 }
    const folded = foldTwice(current, stale, snapshot(3, ["d"], true))

    expect(ids(folded)).toEqual(["a", "b", "c", "d"])
  })

  test("a full push replaces outright, cache or no cache", () => {
    const base = { messages: [entry("a")], startIndex: 0 }
    expect(ids(foldTwice(null, base, snapshot(0, ["x", "y"])))).toEqual(["x", "y"])
  })

  test("keeps what is on screen when an incremental body cannot be placed", () => {
    const current = snapshot(0, ["a"])
    // startIndex far past the held window — a hole, so the push is refused.
    expect(ids(foldTwice(current, null, snapshot(99, ["z"], true)))).toEqual(["a"])
  })

  test("with no window and no cache, an unplaceable incremental stays empty", () => {
    expect(foldTwice(null, null, snapshot(5, ["z"], true))).toBeNull()
  })

  test("returns the held object unchanged when nothing moved", () => {
    const current = snapshot(0, ["a", "b"])
    // Identity: this is what keeps an unchanged push from re-rendering.
    expect(foldChatSnapshot(current, null, snapshot(0, ["a", "b"]))).toBe(current)
  })
})
