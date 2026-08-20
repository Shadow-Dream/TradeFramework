import { describe, expect, test } from "bun:test"
import { renderToStaticMarkup } from "react-dom/server"
import type { UiTurnContextV1 } from "../../../shared/ui-sync-protocol"
import { LiveUiContextDetails, LiveUiContextPopover, liveUiContextSummary } from "./LiveUiContextPopover"

const snapshot: UiTurnContextV1 = {
  schemaVersion: "1",
  capturedAt: "2026-08-17T20:00:00.000Z",
  serverSeq: 42,
  activeTabId: "engine-tab-0001",
  activeContextAmbiguous: false,
  activeContext: {
    route: "/pipeline?pipelineId=momentum-lab",
    view: "Pipeline Builder",
    subview: "Signal",
    projectId: "momentum-lab",
    resourceRefs: [{ kind: "pipeline", id: "momentum-lab", version: "3", label: "Momentum Lab" }],
    selection: { kind: "graph-node", id: "score", label: "Score node" },
    documentId: "pipeline:momentum-lab",
    documentRevision: 7,
  },
  tabs: [{
    tabId: "engine-tab-0001",
    clientKind: "engine-spa",
    capabilities: ["presence", "context", "document-read", "document-write"],
    connected: true,
    visible: true,
    focused: true,
    openedAt: "2026-08-17T19:59:00.000Z",
    lastSeenAt: "2026-08-17T20:00:00.000Z",
    lastInteractionAt: "2026-08-17T20:00:00.000Z",
    context: {
      route: "/pipeline?pipelineId=momentum-lab",
      view: "Pipeline Builder",
      subview: "Signal",
      projectId: "momentum-lab",
      resourceRefs: [{ kind: "pipeline", id: "momentum-lab", version: "3", label: "Momentum Lab" }],
      selection: { kind: "graph-node", id: "score", label: "Score node" },
      documentId: "pipeline:momentum-lab",
      documentRevision: 7,
    },
    documents: [{
      documentId: "pipeline:momentum-lab",
      kind: "pipeline-draft",
      label: "Momentum Lab Pipeline",
      projectId: "momentum-lab",
      revision: 7,
      savedRevision: 6,
      contentDigest: "digest-7",
      dirty: true,
      readOnly: false,
    }],
  }, {
    tabId: "jupyter-tab-0001",
    clientKind: "jupyter",
    capabilities: ["presence", "context", "document-read", "document-write"],
    connected: true,
    visible: true,
    focused: false,
    openedAt: "2026-08-17T19:58:00.000Z",
    lastSeenAt: "2026-08-17T19:59:30.000Z",
    lastInteractionAt: "2026-08-17T19:59:20.000Z",
    context: {
      route: "/jupyter/w/momentum-lab/lab/tree/strategy.py",
      view: "jupyter",
      subview: "strategy.py",
      resourceRefs: [],
    },
    documents: [],
  }, {
    tabId: "engine-tab-retained",
    clientKind: "engine-spa",
    capabilities: ["presence", "context"],
    connected: false,
    visible: false,
    focused: false,
    openedAt: "2026-08-17T19:57:00.000Z",
    lastSeenAt: "2026-08-17T19:58:00.000Z",
    lastInteractionAt: "2026-08-17T19:58:00.000Z",
    context: null,
    documents: [],
  }],
}

describe("LiveUiContextPopover", () => {
  test("summarizes the active view, selection, and connected window count", () => {
    expect(liveUiContextSummary(snapshot)).toBe("Pipeline Builder · Signal · Score node · 2 windows")
  })

  test("renders connected windows and hides the reconnect cache", () => {
    const html = renderToStaticMarkup(<LiveUiContextDetails snapshot={snapshot} />)
    expect(html).toContain("TradeEngine")
    expect(html).toContain("JupyterLab")
    expect(html).toContain("2 connected windows")
    expect(html).toContain("Pipeline Builder")
    expect(html).toContain("jupyter")
    expect(html).toContain("strategy.py")
    expect(html).not.toContain("disconnected · retained")
    expect(html).toContain("Score node")
    expect(html).toContain("graph-node · score")
    expect(html).toContain("Momentum Lab Pipeline")
    expect(html).toContain("rev 7")
    expect(html).toContain("dirty")
  })

  test("shows every connected window directly in the persistent summary", () => {
    const html = renderToStaticMarkup(<LiveUiContextPopover snapshot={snapshot} />)
    expect(html).toContain("2 connected windows")
    expect(html).toContain("TradeEngine")
    expect(html).toContain("Pipeline Builder")
    expect(html).toContain("Signal")
    expect(html).toContain("Score node")
    expect(html).toContain("JupyterLab")
    expect(html).toContain("strategy.py")
    expect(html).not.toContain("engine-tab-retained")
  })

  test("does not produce a zero-over-total summary for retained tabs", () => {
    const retainedOnly = {
      ...snapshot,
      activeTabId: null,
      activeContext: null,
      tabs: snapshot.tabs.map((tab) => ({ ...tab, connected: false, focused: false, visible: false })),
    }
    expect(liveUiContextSummary(retainedOnly)).toBe("No connected Engine or Jupyter windows")
  })
})
