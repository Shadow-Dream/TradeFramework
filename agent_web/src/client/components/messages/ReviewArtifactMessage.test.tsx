import { describe, expect, test } from "bun:test"
import { renderToStaticMarkup } from "react-dom/server"
import type { ReviewArtifactV1 } from "../../../shared/review-artifact"
import { TradeAuthProvider } from "../../app/TradeAuthContext"
import { ReviewArtifactMessage } from "./ReviewArtifactMessage"

const reference = {
  kind: "pipeline",
  id: "pipeline-1",
  version: "version-1",
  digest: "sha256:abc",
  label: "Momentum pipeline",
}

const artifact: ReviewArtifactV1 = {
  schemaVersion: "1",
  analysisBrief: {
    title: "Pipeline review",
    summary: "The exact Pipeline version passed validation.",
    confirmedFacts: [{ claim: "The graph is valid.", references: [reference] }],
    calculations: [{ description: "Score", method: "Versioned result", result: "0.82", references: [reference] }],
    interpretation: [],
    counterEvidence: [],
    falsification: [],
    nextStep: "Review the suggested validation.",
  },
  proposal: {
    title: "Suggested validation",
    summary: "Validate against a longer frozen sample.",
    suggestedActions: ["Create a draft validation request."],
    references: [reference],
  },
}

describe("ReviewArtifactMessage", () => {
  test("renders review facts and exact TradeEngine links without execution controls", () => {
    const html = renderToStaticMarkup(
      <TradeAuthProvider value={{
        account: { userId: "user-1", email: "admin@example.test", role: "admin", expiresAt: "2099-01-01T00:00:00Z" },
        tradeEngineUrl: "http://10.130.130.66:30809/",
        returnUrl: "/pipelines",
        build: "test-build",
      }}>
        <ReviewArtifactMessage artifact={artifact} />
      </TradeAuthProvider>,
    )

    expect(html).toContain("Pipeline review")
    expect(html).toContain("The graph is valid.")
    expect(html).toContain("Suggested validation")
    expect(html).toContain("http://10.130.130.66:30809/pipeline?pipelineId=pipeline-1&amp;version=version-1")
    expect(html).toContain("Display-only")
    expect(html).not.toContain("<button")
    expect(html).not.toContain(">Apply<")
    expect(html).not.toContain(">Run<")
    expect(html).not.toContain(">Execute<")
  })
})
