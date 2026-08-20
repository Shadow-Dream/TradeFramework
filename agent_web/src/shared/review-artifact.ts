import type { TradeReferenceV1 } from "./trade-context"

export interface AnalysisFactV1 {
  claim: string
  references: TradeReferenceV1[]
}

export interface AnalysisCalculationV1 {
  description: string
  method: string
  result: string
  references: TradeReferenceV1[]
}

export interface AnalysisBriefV1 {
  title: string
  summary: string
  confirmedFacts: AnalysisFactV1[]
  calculations: AnalysisCalculationV1[]
  interpretation: string[]
  counterEvidence: string[]
  falsification: string[]
  nextStep: string
}

export interface ProposalV1 {
  title: string
  summary: string
  suggestedActions: string[]
  references: TradeReferenceV1[]
}

export interface ReviewArtifactV1 {
  schemaVersion: "1"
  analysisBrief?: AnalysisBriefV1
  proposal?: ProposalV1
}

function object(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null
}

function hasExactKeys(value: Record<string, unknown>, allowed: readonly string[], required: readonly string[]) {
  return Object.keys(value).every((key) => allowed.includes(key)) && required.every((key) => key in value)
}

function text(value: unknown, maximum: number) {
  return typeof value === "string" && value.length > 0 && value === value.trim() && value.length <= maximum
}

function references(value: unknown): value is TradeReferenceV1[] {
  return Array.isArray(value) && value.length >= 1 && value.length <= 32 && value.every((item) => {
    const record = object(item)
    return Boolean(record && hasExactKeys(record, ["kind", "id", "version", "digest", "label"], ["kind", "id"])
      && text(record.kind, 64) && text(record.id, 512)
      && (record.version === undefined || text(record.version, 256))
      && (record.digest === undefined || text(record.digest, 256))
      && (record.label === undefined || text(record.label, 256)))
  })
}

function textList(value: unknown) {
  return Array.isArray(value) && value.length <= 16 && value.every((item) => text(item, 2_000))
}

export function parseReviewArtifact(value: unknown): ReviewArtifactV1 | null {
  const envelope = object(value)
  if (!envelope || !hasExactKeys(envelope, ["schemaVersion", "analysisBrief", "proposal"], ["schemaVersion"])
    || envelope.schemaVersion !== "1" || (!envelope.analysisBrief && !envelope.proposal)) return null
  if (envelope.analysisBrief !== undefined) {
    const brief = object(envelope.analysisBrief)
    const keys = ["title", "summary", "confirmedFacts", "calculations", "interpretation", "counterEvidence", "falsification", "nextStep"]
    if (!brief || !hasExactKeys(brief, keys, keys) || !text(brief.title, 200) || !text(brief.summary, 4_000)
      || !text(brief.nextStep, 4_000) || !textList(brief.interpretation) || !textList(brief.counterEvidence)
      || !textList(brief.falsification) || !Array.isArray(brief.confirmedFacts) || brief.confirmedFacts.length > 32
      || !Array.isArray(brief.calculations) || brief.calculations.length > 32) return null
    if (!brief.confirmedFacts.every((item) => {
      const fact = object(item)
      return Boolean(fact && hasExactKeys(fact, ["claim", "references"], ["claim", "references"])
        && text(fact.claim, 2_000) && references(fact.references))
    })) return null
    if (!brief.calculations.every((item) => {
      const calculation = object(item)
      const calculationKeys = ["description", "method", "result", "references"]
      return Boolean(calculation && hasExactKeys(calculation, calculationKeys, calculationKeys)
        && text(calculation.description, 2_000) && text(calculation.method, 2_000)
        && text(calculation.result, 2_000) && references(calculation.references))
    })) return null
  }
  if (envelope.proposal !== undefined) {
    const proposal = object(envelope.proposal)
    const keys = ["title", "summary", "suggestedActions", "references"]
    if (!proposal || !hasExactKeys(proposal, keys, keys)
      || !text(proposal.title, 200) || !text(proposal.summary, 4_000)
      || !Array.isArray(proposal.suggestedActions) || proposal.suggestedActions.length > 16
      || !proposal.suggestedActions.every((item) => text(item, 1_000))
      || !references(proposal.references)) return null
  }
  if (new TextEncoder().encode(JSON.stringify(envelope)).byteLength > 32 * 1024) return null
  return envelope as unknown as ReviewArtifactV1
}

export function findReviewArtifact(value: unknown, depth = 0): ReviewArtifactV1 | null {
  if (depth > 5) return null
  if (typeof value === "string" && value.length <= 32 * 1024 && value.trimStart().startsWith("{")) {
    try {
      return findReviewArtifact(JSON.parse(value), depth + 1)
    } catch {
      return null
    }
  }
  const direct = parseReviewArtifact(value)
  if (direct) return direct
  const record = object(value)
  if (record) {
    if ("artifact" in record) {
      const artifact = parseReviewArtifact(record.artifact)
      if (artifact) return artifact
    }
    for (const child of Object.values(record).slice(0, 24)) {
      const found = findReviewArtifact(child, depth + 1)
      if (found) return found
    }
  }
  if (Array.isArray(value)) {
    for (const child of value.slice(0, 24)) {
      const found = findReviewArtifact(child, depth + 1)
      if (found) return found
    }
  }
  return null
}
