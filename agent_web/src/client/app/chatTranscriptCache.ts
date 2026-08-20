import type { ChatSnapshot, TranscriptEntry } from "../../shared/types"

/**
 * Local cache of each chat's loaded transcript window.
 *
 * Transcripts are append-only for the life of a `chatId` — entries are never
 * rewritten and the file is only ever replaced by deleting the chat outright —
 * so a cached window never needs invalidating, only extending. That is what
 * lets a reopened chat resume from its cached position instead of pulling the
 * whole window down again.
 *
 * Only the transcript body is stored. Runtime state (status, queued messages,
 * the read anchor) is small, changes independently, and would be stale on
 * open, so it always comes from the server.
 */

const DATABASE_NAME = "trade-agent:chat-transcripts"
const DATABASE_VERSION = 1
const STORE_NAME = "windows"

/**
 * Bumped when the cached shape changes. Entries written by an older version
 * fail the check and are treated as a cold cache rather than migrated.
 */
const CACHE_SCHEMA_VERSION = 2

/** Windows past this age are dropped on open — stale chats are not worth disk. */
const MAX_ENTRY_AGE_MS = 30 * 24 * 60 * 60 * 1000

/**
 * How long to wait after the last change before writing. Encoding a window is
 * not free, and an active turn changes it many times a second.
 */
const WRITE_DEBOUNCE_MS = 500

/**
 * Ceiling on one chat's cached window. Trimming keeps a whole transcript well
 * under this, so the cap only catches pathological chats — and skipping the
 * write leaves whatever smaller window is already stored, which is still a
 * valid prefix to resume from.
 */
const MAX_CACHED_WINDOW_BYTES = 8 * 1024 * 1024

export interface CachedTranscriptWindow {
  schemaVersion: number
  chatId: string
  /** Absolute index of `entries[0]`. */
  startIndex: number
  entries: TranscriptEntry[]
  updatedAt: number
}

export interface CachedSpan {
  start: number
  end: number
  endEntryId: string
}

function openDatabase(): Promise<IDBDatabase | null> {
  if (typeof indexedDB === "undefined") return Promise.resolve(null)
  return new Promise((resolve) => {
    let request: IDBOpenDBRequest
    try {
      request = indexedDB.open(DATABASE_NAME, DATABASE_VERSION)
    } catch {
      // Private-mode browsers can throw outright rather than fail the request.
      resolve(null)
      return
    }
    request.onupgradeneeded = () => {
      const db = request.result
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME, { keyPath: "chatId" })
      }
    }
    request.onsuccess = () => resolve(request.result)
    // A cache is an optimization; losing it must never surface as an error.
    request.onerror = () => resolve(null)
    request.onblocked = () => resolve(null)
  })
}

let databasePromise: Promise<IDBDatabase | null> | null = null

function getDatabase() {
  databasePromise ??= openDatabase()
  return databasePromise
}

function requestToPromise<T>(request: IDBRequest<T>): Promise<T | null> {
  return new Promise((resolve) => {
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => resolve(null)
  })
}

export async function readCachedWindow(chatId: string): Promise<CachedTranscriptWindow | null> {
  const db = await getDatabase()
  if (!db) return null
  try {
    const store = db.transaction(STORE_NAME, "readonly").objectStore(STORE_NAME)
    const value = await requestToPromise(store.get(chatId) as IDBRequest<CachedTranscriptWindow>)
    if (!value || value.schemaVersion !== CACHE_SCHEMA_VERSION) return null
    if (!Array.isArray(value.entries) || value.entries.length === 0) return null
    if (Date.now() - value.updatedAt > MAX_ENTRY_AGE_MS) {
      void deleteCachedWindow(chatId)
      return null
    }
    return value
  } catch {
    return null
  }
}

export async function deleteCachedWindow(chatId: string): Promise<void> {
  const db = await getDatabase()
  if (!db) return
  try {
    db.transaction(STORE_NAME, "readwrite").objectStore(STORE_NAME).delete(chatId)
  } catch {
    // Best effort.
  }
}

async function writeCachedWindow(window: CachedTranscriptWindow): Promise<void> {
  const db = await getDatabase()
  if (!db) return
  try {
    db.transaction(STORE_NAME, "readwrite").objectStore(STORE_NAME).put(window)
  } catch {
    // Quota or a closing connection — the next open just runs cold.
  }
}

/** The span to resume from, or null when there is nothing usable to resume. */
export function toCachedSpan(window: CachedTranscriptWindow | null): CachedSpan | null {
  if (!window || window.entries.length === 0) return null
  const endEntryId = window.entries[window.entries.length - 1]?._id
  if (!endEntryId) return null
  return {
    start: window.startIndex,
    end: window.startIndex + window.entries.length,
    endEntryId,
  }
}

/**
 * Turn a cached window into the snapshot shape the UI renders.
 *
 * Everything outside the transcript is left empty on purpose: this only exists
 * to paint history immediately, and the server's first push — which carries
 * real runtime state — lands right behind it.
 */
export function cachedWindowToMessages(window: CachedTranscriptWindow) {
  return { messages: window.entries, startIndex: window.startIndex }
}

/**
 * Debounced writer, one per chat.
 *
 * Writes are skipped while a turn is streaming: the window changes many times
 * a second, the server stays the source of truth throughout, and encoding it
 * repeatedly would put cache work on the streaming path for no benefit. The
 * turn settling schedules the write that actually matters.
 */
export function createTranscriptCacheWriter() {
  let timer: ReturnType<typeof setTimeout> | null = null
  let pending: CachedTranscriptWindow | null = null

  function flush() {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
    const value = pending
    pending = null
    if (!value) return
    // Runs once a turn has settled, so a single stringify of a trimmed
    // transcript is cheap next to the write it guards.
    if (JSON.stringify(value.entries).length > MAX_CACHED_WINDOW_BYTES) return
    void writeCachedWindow(value)
  }

  return {
    schedule(chatId: string, snapshot: Pick<ChatSnapshot, "messages" | "startIndex">, isStreaming: boolean) {
      if (snapshot.messages.length === 0) return
      pending = {
        schemaVersion: CACHE_SCHEMA_VERSION,
        chatId,
        startIndex: snapshot.startIndex,
        entries: snapshot.messages,
        updatedAt: Date.now(),
      }
      if (isStreaming) return
      if (timer !== null) return
      timer = setTimeout(flush, WRITE_DEBOUNCE_MS)
    },
    /** Write whatever is pending now — used when a chat closes mid-turn. */
    flush,
  }
}
