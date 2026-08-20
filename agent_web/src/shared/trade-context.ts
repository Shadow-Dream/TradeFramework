export const TRADE_CONTEXT_SCHEMA_VERSION = "1" as const
export const MAX_TRADE_CONTEXT_REFERENCES = 32

export interface TradeReferenceV1 {
  kind: string
  id: string
  version?: string
  digest?: string
  label?: string
}

export interface TradeContextV1 {
  schemaVersion: typeof TRADE_CONTEXT_SCHEMA_VERSION
  sourceView: string
  capturedAt: string
  references: TradeReferenceV1[]
}

const ALLOWED_KINDS = new Set(["pipeline", "dataset", "environment", "analysis", "backtest", "result"])
const ID_RE = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,511}$/

function exactKeys(value: Record<string, unknown>, allowed: readonly string[], label: string) {
  const allowedSet = new Set(allowed)
  const unknown = Object.keys(value).filter((key) => !allowedSet.has(key))
  if (unknown.length) throw new Error(`${label} has unknown fields: ${unknown.join(", ")}`)
}

function text(value: unknown, label: string, max: number, pattern?: RegExp) {
  if (typeof value !== "string" || value.length === 0 || value !== value.trim() || value.length > max) {
    throw new Error(`${label} must be a bounded non-empty string.`)
  }
  if (pattern && !pattern.test(value)) throw new Error(`${label} is invalid.`)
  return value
}

export function normalizeTradeContext(value: unknown): TradeContextV1 {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("context must be an object.")
  const input = value as Record<string, unknown>
  exactKeys(input, ["schemaVersion", "sourceView", "capturedAt", "references"], "context")
  if (input.schemaVersion !== "1") throw new Error("context.schemaVersion must be '1'.")
  const capturedAt = text(input.capturedAt, "context.capturedAt", 40)
  if (!Number.isFinite(Date.parse(capturedAt)) || !capturedAt.endsWith("Z")) {
    throw new Error("context.capturedAt must be an RFC 3339 UTC timestamp.")
  }
  if (!Array.isArray(input.references) || input.references.length > MAX_TRADE_CONTEXT_REFERENCES) {
    throw new Error(`context.references must contain at most ${MAX_TRADE_CONTEXT_REFERENCES} entries.`)
  }
  const seen = new Set<string>()
  const references = input.references.map((raw, index): TradeReferenceV1 => {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new Error(`context.references[${index}] must be an object.`)
    const ref = raw as Record<string, unknown>
    exactKeys(ref, ["kind", "id", "version", "digest", "label"], `context.references[${index}]`)
    const kind = text(ref.kind, `context.references[${index}].kind`, 96)
    if (!ALLOWED_KINDS.has(kind) && !kind.startsWith("module:")) {
      throw new Error(`context.references[${index}].kind is unsupported.`)
    }
    const id = text(ref.id, `context.references[${index}].id`, 512, ID_RE)
    const result: TradeReferenceV1 = { kind, id }
    for (const [key, maximum] of [["version", 256], ["digest", 256], ["label", 256]] as const) {
      if (ref[key] !== undefined) result[key] = text(ref[key], `context.references[${index}].${key}`, maximum)
    }
    if (result.version?.toLowerCase() === "latest") throw new Error("Context versions must be exact, not latest.")
    if ((ALLOWED_KINDS.has(kind) && !["backtest", "result"].includes(kind)) || kind.startsWith("module:")) {
      if (!result.version) throw new Error(`context.references[${index}].version is required.`)
    }
    const identity = `${kind}\0${id}\0${result.version ?? ""}\0${result.digest ?? ""}`
    if (seen.has(identity)) throw new Error("context.references contains a duplicate reference.")
    seen.add(identity)
    return result
  })
  const context: TradeContextV1 = {
    schemaVersion: "1",
    sourceView: text(input.sourceView, "context.sourceView", 64, /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$/),
    capturedAt,
    references,
  }
  if (new TextEncoder().encode(JSON.stringify(context)).byteLength > 16 * 1024) {
    throw new Error("context is too large.")
  }
  return context
}

export function emptyTradeContext(now = new Date()): TradeContextV1 {
  return { schemaVersion: "1", sourceView: "agent", capturedAt: now.toISOString(), references: [] }
}
