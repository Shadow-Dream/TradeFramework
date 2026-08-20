import { describe, expect, test } from "bun:test"
import { findReviewArtifact, parseReviewArtifact } from "./review-artifact"

const reference = {
  kind: "pipeline",
  id: "pipeline-1",
  version: "version-1",
  digest: "sha256:abc",
  label: "Momentum pipeline",
}

const artifact = {
  schemaVersion: "1",
  analysisBrief: {
    title: "Pipeline review",
    summary: "The exact Pipeline version passed the requested checks.",
    confirmedFacts: [{ claim: "The graph is valid.", references: [reference] }],
    calculations: [{
      description: "Observed score",
      method: "Read the versioned analysis result.",
      result: "0.82",
      references: [reference],
    }],
    interpretation: ["The signal is directionally useful."],
    counterEvidence: ["The sample is short."],
    falsification: ["Recheck on the next frozen Dataset version."],
    nextStep: "Review the proposal before making any change.",
  },
  proposal: {
    title: "Suggested validation",
    summary: "Validate the same graph against a longer frozen sample.",
    suggestedActions: ["Create a draft validation request."],
    references: [reference],
  },
} as const

describe("review artifact contract", () => {
  test("accepts an exact bounded AnalysisBrief and Proposal", () => {
    expect(parseReviewArtifact(artifact)).toEqual(artifact)
    expect(findReviewArtifact({ structuredContent: { artifact } })).toEqual(artifact)
  })

  test("rejects unknown fields, unreferenced facts, and oversized values", () => {
    expect(parseReviewArtifact({ ...artifact, execute: true })).toBeNull()
    expect(parseReviewArtifact({
      ...artifact,
      analysisBrief: { ...artifact.analysisBrief, confirmedFacts: [{ claim: "No evidence", references: [] }] },
    })).toBeNull()
    expect(parseReviewArtifact({
      ...artifact,
      proposal: { ...artifact.proposal, summary: "x".repeat(4_001) },
    })).toBeNull()
  })
})
