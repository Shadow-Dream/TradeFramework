import { ArrowLeft, Bot, Boxes, Loader2, Settings } from "lucide-react"
import { useNavigate, useOutletContext } from "react-router-dom"
import { Button } from "../components/ui/button"
import { useSidebarData } from "../stores/sidebarStore"
import type { KannaState } from "./useKannaState"
import { useTradeAuth } from "./TradeAuthContext"

/** Server-catalogued logical workspaces. Filesystem paths never reach here. */
export function LocalProjectsPage() {
  const state = useOutletContext<KannaState>()
  const navigate = useNavigate()
  const projects = useSidebarData().projectGroups
  const loading = state.connectionStatus === "connecting" || !state.sidebarReady
  const { returnUrl } = useTradeAuth()

  return (
    <main className="flex min-w-0 flex-1 items-center justify-center bg-background px-6 py-10">
      <section className="w-full max-w-2xl rounded-3xl border border-border bg-card/50 p-7 shadow-sm">
        <div className="flex items-start gap-4">
          <div className="rounded-2xl border border-border bg-background p-3">
            <Bot className="h-6 w-6 text-logo" />
          </div>
          <div className="min-w-0 flex-1">
            <h1 className="text-xl font-semibold text-foreground">TradeEngine Agent</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Claude Code + DeepSeek by default, with Codex + GPT available as an isolated backend.
            </p>
          </div>
        </div>

        {state.commandError ? (
          <div className="mt-4 rounded-xl border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {state.commandError}
          </div>
        ) : null}

        <div className="mt-6 grid gap-3 sm:grid-cols-2">
          {projects.map((project) => (
            <button
              key={project.groupKey}
              type="button"
              disabled={loading}
              onClick={() => void state.handleCreateChat(project.groupKey)}
              className="group rounded-2xl border border-border bg-background p-4 text-left transition hover:border-logo/40 hover:bg-muted/30 disabled:opacity-50"
            >
              <div className="flex items-center gap-3">
                <span className="rounded-xl bg-muted p-2 text-muted-foreground group-hover:text-logo">
                  {project.kind === "strategy" ? <Boxes className="h-5 w-5" /> : <Bot className="h-5 w-5" />}
                </span>
                <span>
                  <span className="block text-sm font-medium text-foreground">{project.title}</span>
                  <span className="mt-0.5 block text-xs text-muted-foreground">
                    {project.kind === "strategy"
                      ? "Strategy-only workspace and session history"
                      : "Whole Engine workspace for shared and cross-strategy work"}
                  </span>
                </span>
                {loading ? <Loader2 className="ml-auto h-4 w-4 animate-spin" /> : null}
              </div>
            </button>
          ))}
          {!loading && projects.length === 0 ? (
            <p className="col-span-full rounded-xl border border-dashed border-border px-4 py-6 text-center text-sm text-muted-foreground">
              No approved Agent projects are available.
            </p>
          ) : null}
        </div>

        <div className="mt-5 flex flex-wrap gap-3">
          <Button variant="outline" onClick={() => navigate("/settings/providers")}>
            <Settings className="mr-2 h-4 w-4" />
            Backends & Models
          </Button>
          <Button variant="ghost" onClick={() => window.location.assign(returnUrl)}>
            <ArrowLeft className="mr-2 h-4 w-4" />
            Back to TradeEngine
          </Button>
        </div>
      </section>
    </main>
  )
}
