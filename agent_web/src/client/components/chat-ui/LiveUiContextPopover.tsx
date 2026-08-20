import { Check, ChevronDown, Copy, FileText, Monitor, MousePointer2 } from "lucide-react"
import { useEffect, useState } from "react"
import type { UiTabSnapshot, UiTurnContextV1 } from "../../../shared/ui-sync-protocol"
import { copyTextToClipboard } from "../../lib/clipboard"
import { cn } from "../../lib/utils"
import { Popover, PopoverContent, PopoverTrigger } from "../ui/popover"

function clientLabel(tab: UiTabSnapshot) {
  return tab.clientKind === "jupyter" ? "JupyterLab" : "TradeEngine"
}

function tabStatus(tab: UiTabSnapshot) {
  if (!tab.connected) return "disconnected · retained"
  if (tab.focused && tab.visible) return "focused"
  if (tab.visible) return "visible"
  return "background"
}

export function liveUiContextSummary(snapshot: UiTurnContextV1 | null) {
  if (!snapshot) return "Waiting for UI sync"
  const connected = snapshot.tabs.filter((tab) => tab.connected)
  if (!connected.length) return "No connected Engine or Jupyter windows"
  if (!snapshot.activeContext) return `${connected.length} connected window${connected.length === 1 ? "" : "s"}`
  const selection = snapshot.activeContext.selection?.label ?? snapshot.activeContext.selection?.id
  return [
    snapshot.activeContext.view,
    snapshot.activeContext.subview,
    selection,
    `${connected.length} window${connected.length === 1 ? "" : "s"}`,
  ].filter(Boolean).join(" · ")
}

function StatusBadge({ tab, active }: { tab: UiTabSnapshot; active: boolean }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-[10px]">
      {active ? (
        <span className="rounded-full bg-sky-500/15 px-2 py-0.5 font-medium text-sky-600 dark:text-sky-400">active</span>
      ) : null}
      <span className={cn(
        "rounded-full px-2 py-0.5",
        tab.connected
          ? "bg-emerald-500/12 text-emerald-700 dark:text-emerald-400"
          : "bg-muted text-muted-foreground",
      )}>
        {tabStatus(tab)}
      </span>
    </div>
  )
}

function UiTabDetails({ tab, active }: { tab: UiTabSnapshot; active: boolean }) {
  return (
    <section className="p-4">
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-medium text-foreground">
            <Monitor className="size-4 shrink-0 text-muted-foreground" />
            <span>{clientLabel(tab)}</span>
          </div>
          <div className="mt-1 break-all font-mono text-[10px] text-muted-foreground">{tab.tabId}</div>
        </div>
        <StatusBadge tab={tab} active={active} />
      </div>

      {tab.context ? (
        <div className="mt-4 space-y-3 text-[11px]">
          <div className="grid grid-cols-[72px_minmax(0,1fr)] gap-x-2 gap-y-1.5 rounded-xl border border-border/60 bg-muted/15 p-3">
            <span className="text-muted-foreground">View</span>
            <span className="min-w-0 break-words text-foreground/90">
              {tab.context.view}{tab.context.subview ? ` / ${tab.context.subview}` : ""}
            </span>
            <span className="text-muted-foreground">Route</span>
            <span className="min-w-0 break-all font-mono text-[10px] text-foreground/80">{tab.context.route}</span>
            {tab.context.projectId ? (
              <>
                <span className="text-muted-foreground">Project</span>
                <span className="min-w-0 break-all font-mono text-[10px] text-foreground/80">{tab.context.projectId}</span>
              </>
            ) : null}
          </div>

          {tab.context.selection ? (
            <div className="flex min-w-0 items-start gap-2 rounded-lg bg-violet-500/8 px-2.5 py-2">
              <MousePointer2 className="mt-0.5 size-3.5 shrink-0 text-violet-500" />
              <div className="min-w-0">
                <div className="font-medium text-foreground/90">
                  {tab.context.selection.label ?? tab.context.selection.id}
                </div>
                <div className="break-all font-mono text-[10px] text-muted-foreground">
                  {tab.context.selection.kind} · {tab.context.selection.id}
                </div>
              </div>
            </div>
          ) : null}

          {tab.context.resourceRefs.length ? (
            <div>
              <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">Resources</div>
              <div className="flex flex-wrap gap-1">
                {tab.context.resourceRefs.map((reference, index) => (
                  <span
                    key={`${reference.kind}:${reference.id}:${reference.version ?? ""}:${index}`}
                    className="max-w-full rounded-md border border-border/70 bg-background/70 px-2 py-1 font-mono text-[10px] text-foreground/80"
                  >
                    {reference.kind}: {reference.label ?? reference.id}{reference.version ? ` @ ${reference.version}` : ""}
                  </span>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      ) : (
        <p className="mt-4 text-[11px] text-muted-foreground">No semantic page context published.</p>
      )}

      {tab.documents.length ? (
        <div className="mt-4 space-y-1.5">
          <div className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">Open documents</div>
          {tab.documents.map((document) => (
            <div key={document.documentId} className="flex min-w-0 items-start gap-2 rounded-lg border border-border/60 bg-background/55 px-2.5 py-2 text-[10px]">
              <FileText className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
              <div className="min-w-0 flex-1">
                <div className="truncate text-[11px] font-medium text-foreground/90" title={document.label}>{document.label}</div>
                <div className="mt-0.5 flex flex-wrap gap-x-2 gap-y-0.5 font-mono text-muted-foreground">
                  <span>{document.kind}</span>
                  <span>rev {document.revision}</span>
                  <span>saved {document.savedRevision}</span>
                  <span>{document.readOnly ? "read only" : document.dirty ? "dirty" : "saved"}</span>
                </div>
                {document.relativePath ? <div className="mt-0.5 truncate font-mono text-muted-foreground">{document.relativePath}</div> : null}
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  )
}

export function LiveUiContextDetails({ snapshot }: { snapshot: UiTurnContextV1 | null }) {
  const connectedTabs = snapshot?.tabs.filter((tab) => tab.connected) ?? []
  const preferredTabId = connectedTabs.some((tab) => tab.tabId === snapshot?.activeTabId)
    ? snapshot?.activeTabId ?? null
    : connectedTabs[0]?.tabId ?? null
  const [selectedTabId, setSelectedTabId] = useState<string | null>(preferredTabId)

  useEffect(() => {
    if (!connectedTabs.some((tab) => tab.tabId === selectedTabId)) {
      setSelectedTabId(preferredTabId)
    }
  }, [connectedTabs, preferredTabId, selectedTabId])

  if (!snapshot) {
    return <div className="px-4 py-8 text-center text-xs text-muted-foreground">Waiting for the live UI subscription.</div>
  }

  const selectedTab = connectedTabs.find((tab) => tab.tabId === selectedTabId)
    ?? connectedTabs.find((tab) => tab.tabId === preferredTabId)
    ?? null

  return (
    <div>
      <div className="border-b border-border/70 px-4 py-3 text-[11px] text-muted-foreground">
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          <span>server seq <span className="font-mono text-foreground/80">{snapshot.serverSeq}</span></span>
          <span>{connectedTabs.length} connected window{connectedTabs.length === 1 ? "" : "s"}</span>
          <span>{new Date(snapshot.capturedAt).toLocaleTimeString()}</span>
        </div>
        {snapshot.activeContextAmbiguous ? (
          <p className="mt-2 rounded-lg bg-amber-500/10 px-2.5 py-1.5 text-amber-700 dark:text-amber-400">
            Active context is ambiguous: multiple windows were interacted with at nearly the same time.
          </p>
        ) : null}
      </div>

      {connectedTabs.length ? (
        <div className="flex min-h-0 max-h-[min(68vh,680px)] flex-col sm:flex-row">
          <nav className="flex max-h-32 shrink-0 gap-1 overflow-auto border-b border-border/70 bg-muted/15 p-2 sm:max-h-none sm:w-48 sm:flex-col sm:border-b-0 sm:border-r" aria-label="Connected UI windows">
            {connectedTabs.map((tab, index) => {
              const selected = tab.tabId === selectedTab?.tabId
              const active = tab.tabId === snapshot.activeTabId
              return (
                <button
                  key={tab.tabId}
                  type="button"
                  onClick={() => setSelectedTabId(tab.tabId)}
                  className={cn(
                    "min-w-40 rounded-lg border px-2.5 py-2 text-left transition-colors sm:min-w-0",
                    selected
                      ? "border-sky-500/35 bg-sky-500/10"
                      : "border-transparent hover:border-border/70 hover:bg-muted/50",
                  )}
                  aria-pressed={selected}
                >
                  <div className="flex items-center gap-1.5 text-[11px] font-medium text-foreground/90">
                    <span className="flex size-4 shrink-0 items-center justify-center rounded bg-muted font-mono text-[9px]">{index + 1}</span>
                    <span className="truncate">{clientLabel(tab)}</span>
                    <span className={cn("ml-auto size-1.5 shrink-0 rounded-full", active ? "bg-sky-500" : "bg-emerald-500")} />
                  </div>
                  <div className="mt-1 truncate text-[10px] text-muted-foreground">
                    {tab.context?.view ?? "No page context"}{tab.context?.subview ? ` / ${tab.context.subview}` : ""}
                  </div>
                  {tab.context?.selection ? (
                    <div className="mt-0.5 truncate text-[10px] text-violet-600 dark:text-violet-400">
                      {tab.context.selection.label ?? tab.context.selection.id}
                    </div>
                  ) : null}
                </button>
              )
            })}
          </nav>
          <div className="min-h-0 min-w-0 flex-1 overflow-y-auto">
            {selectedTab ? <UiTabDetails tab={selectedTab} active={selectedTab.tabId === snapshot.activeTabId} /> : null}
          </div>
        </div>
      ) : (
        <div className="px-4 py-8 text-center text-xs text-muted-foreground">
          No TradeEngine or JupyterLab window has published context yet.
        </div>
      )}

      <details className="border-t border-border/70 px-4 py-3">
        <summary className="cursor-pointer select-none text-[11px] font-medium text-muted-foreground">Raw snapshot</summary>
        <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-muted/45 p-2.5 font-mono text-[10px] leading-relaxed text-foreground/80">
          {JSON.stringify(snapshot, null, 2)}
        </pre>
      </details>
    </div>
  )
}

export function LiveUiContextPopover({ snapshot }: { snapshot: UiTurnContextV1 | null }) {
  const [copied, setCopied] = useState(false)
  const connectedTabs = snapshot?.tabs.filter((tab) => tab.connected) ?? []
  const connected = connectedTabs.length > 0

  async function copySnapshot() {
    if (!snapshot || !await copyTextToClipboard(JSON.stringify(snapshot, null, 2))) return
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1_500)
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="mx-2 mb-2 block w-[calc(100%-1rem)] min-w-0 rounded-xl border border-border/70 bg-muted/35 px-3 py-2 text-left text-[11px] text-muted-foreground transition-colors hover:border-border hover:bg-muted/55"
          title="Inspect the live server-captured Engine and Jupyter context"
          aria-label="Inspect live UI context"
        >
          <span className="flex min-w-0 items-center gap-2">
            <span className={cn(
              "size-1.5 shrink-0 rounded-full",
              connected ? "bg-emerald-500" : snapshot ? "bg-amber-500" : "bg-muted-foreground/45",
            )} aria-hidden="true" />
            <span className="shrink-0 font-medium text-foreground/80">Live UI context</span>
            <span className="min-w-0 flex-1 text-right text-[10px]">
              {connectedTabs.length} connected window{connectedTabs.length === 1 ? "" : "s"}
            </span>
            {snapshot?.activeContextAmbiguous ? <span className="shrink-0 text-amber-600">ambiguous</span> : null}
            <ChevronDown className="size-3.5 shrink-0" />
          </span>

          {connectedTabs.length ? (
            <span className="mt-2 grid gap-1" aria-label="Connected UI window summary">
              {connectedTabs.map((tab, index) => {
                const active = tab.tabId === snapshot?.activeTabId
                return (
                  <span
                    key={tab.tabId}
                    className={cn(
                      "flex min-w-0 flex-wrap items-baseline gap-x-1.5 gap-y-0.5 rounded-lg border px-2 py-1",
                      active
                        ? "border-sky-500/30 bg-sky-500/8 text-foreground/90"
                        : "border-border/50 bg-background/35 text-foreground/75",
                    )}
                  >
                    <span className="flex size-4 shrink-0 items-center justify-center rounded bg-muted font-mono text-[9px]">{index + 1}</span>
                    <span className="shrink-0 font-medium">{clientLabel(tab)}</span>
                    <span aria-hidden="true">·</span>
                    <span className="break-words">{tab.context?.view ?? "No page context"}{tab.context?.subview ? ` / ${tab.context.subview}` : ""}</span>
                    {tab.context?.selection ? (
                      <>
                        <span aria-hidden="true">·</span>
                        <span className="break-words text-violet-600 dark:text-violet-400">
                          {tab.context.selection.label ?? tab.context.selection.id}
                        </span>
                      </>
                    ) : null}
                    {active ? <span className="ml-auto shrink-0 text-[9px] font-medium text-sky-600 dark:text-sky-400">active</span> : null}
                  </span>
                )
              })}
            </span>
          ) : (
            <span className="mt-1.5 block text-[10px]">No connected Engine or Jupyter windows</span>
          )}
        </button>
      </PopoverTrigger>
      <PopoverContent side="top" align="center" sideOffset={8} className="w-[min(calc(100vw-24px),680px)] overflow-hidden p-0">
        <div className="flex items-center justify-between gap-3 border-b border-border/70 px-4 py-3">
          <div>
            <div className="text-sm font-semibold text-foreground">Live UI Context</div>
            <div className="mt-0.5 text-[10px] text-muted-foreground">The next Turn freezes a server-side snapshot of this state.</div>
          </div>
          <button
            type="button"
            disabled={!snapshot}
            onClick={() => void copySnapshot()}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-border px-2 py-1 text-[10px] text-muted-foreground transition-colors hover:bg-muted disabled:opacity-40"
          >
            {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
            {copied ? "Copied" : "Copy JSON"}
          </button>
        </div>
        <LiveUiContextDetails snapshot={snapshot} />
      </PopoverContent>
    </Popover>
  )
}
