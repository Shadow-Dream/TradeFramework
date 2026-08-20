import { BarChart3, ExternalLink, FileCheck2, Lightbulb } from "lucide-react"
import type { ReviewArtifactV1 } from "../../../shared/review-artifact"
import type { TradeReferenceV1 } from "../../../shared/trade-context"
import { useOptionalTradeAuth } from "../../app/TradeAuthContext"

function referencePath(reference: TradeReferenceV1) {
  const params = new URLSearchParams()
  if (reference.kind === "pipeline") {
    params.set("pipelineId", reference.id)
    if (reference.version) params.set("version", reference.version)
    return `/pipeline?${params}`
  }
  if (reference.kind === "dataset") {
    params.set("datasetId", reference.id)
    if (reference.version) params.set("version", reference.version)
    return `/data?${params}`
  }
  if (reference.kind === "environment") {
    params.set("environmentId", reference.id)
    if (reference.version) params.set("version", reference.version)
    return `/environment-blueprint?${params}`
  }
  if (reference.kind === "analysis") {
    params.set("analysisId", reference.id)
    if (reference.version) params.set("version", reference.version)
    return `/analysis-blueprint?${params}`
  }
  if (reference.kind.startsWith("module:")) {
    params.set("moduleId", reference.id)
    if (reference.version) params.set("version", reference.version)
    return `/modules?${params}`
  }
  params.set("backtestId", reference.id)
  return `${reference.kind === "result" ? "/result" : "/backtests"}?${params}`
}

function References({ references }: { references: TradeReferenceV1[] }) {
  const auth = useOptionalTradeAuth()
  if (!references.length) return null
  return (
    <div className="flex flex-wrap gap-1.5">
      {references.map((reference) => {
        const label = reference.label || `${reference.kind}:${reference.id}${reference.version ? ` · ${reference.version}` : ""}`
        const href = auth ? new URL(referencePath(reference), auth.tradeEngineUrl).toString() : undefined
        return href ? (
          <a key={`${reference.kind}:${reference.id}:${reference.version ?? ""}`} href={href}
            className="inline-flex items-center gap-1 rounded-full border border-border bg-background/50 px-2 py-1 text-[11px] text-muted-foreground hover:text-foreground">
            <span className="max-w-64 truncate">{label}</span><ExternalLink className="h-3 w-3" />
          </a>
        ) : (
          <span key={`${reference.kind}:${reference.id}:${reference.version ?? ""}`}
            className="rounded-full border border-border bg-background/50 px-2 py-1 text-[11px] text-muted-foreground">{label}</span>
        )
      })}
    </div>
  )
}

export function ReviewArtifactMessage({ artifact }: { artifact: ReviewArtifactV1 }) {
  const brief = artifact.analysisBrief
  const proposal = artifact.proposal
  return (
    <div className="space-y-3 rounded-2xl border border-violet-500/25 bg-violet-500/[0.04] p-4 text-sm">
      <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.16em] text-violet-400">
        <FileCheck2 className="h-4 w-4" /> TradeEngine review artifact
      </div>
      {brief ? (
        <section className="space-y-3">
          <div><h3 className="text-base font-semibold text-foreground">{brief.title}</h3><p className="mt-1 leading-6 text-muted-foreground">{brief.summary}</p></div>
          {brief.confirmedFacts.length ? (
            <div className="space-y-2"><h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-foreground"><FileCheck2 className="h-3.5 w-3.5" /> Confirmed facts</h4>
              {brief.confirmedFacts.map((fact, index) => <div key={index} className="space-y-1.5 rounded-xl bg-background/45 p-3"><p>{fact.claim}</p><References references={fact.references} /></div>)}</div>
          ) : null}
          {brief.calculations.length ? (
            <div className="space-y-2"><h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-foreground"><BarChart3 className="h-3.5 w-3.5" /> Calculations</h4>
              {brief.calculations.map((item, index) => <div key={index} className="space-y-1 rounded-xl bg-background/45 p-3"><p className="font-medium">{item.description}</p><p className="text-xs text-muted-foreground">Method: {item.method}</p><p>{item.result}</p><References references={item.references} /></div>)}</div>
          ) : null}
          <div className="rounded-xl border border-border/60 p-3"><span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Next step</span><p className="mt-1">{brief.nextStep}</p></div>
        </section>
      ) : null}
      {proposal ? (
        <section className="space-y-2 rounded-xl border border-amber-500/20 bg-amber-500/[0.04] p-3">
          <h3 className="flex items-center gap-2 font-semibold"><Lightbulb className="h-4 w-4 text-amber-400" />{proposal.title}</h3>
          <p className="leading-6 text-muted-foreground">{proposal.summary}</p>
          {proposal.suggestedActions.length > 0 ? (
            <div className="space-y-1">
              <p className="text-xs font-medium text-muted-foreground">Suggested next steps</p>
              <ul className="list-disc space-y-1 pl-5 text-sm">
                {proposal.suggestedActions.map((action, index) => <li key={`${index}:${action}`}>{action}</li>)}
              </ul>
            </div>
          ) : null}
          <References references={proposal.references} />
        </section>
      ) : null}
      <p className="text-[11px] text-muted-foreground">Display-only. This artifact cannot apply, publish, run, or execute changes.</p>
    </div>
  )
}
