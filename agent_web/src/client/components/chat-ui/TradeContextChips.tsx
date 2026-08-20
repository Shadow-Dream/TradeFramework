import { Database, X } from "lucide-react"
import type { TradeContextV1, TradeReferenceV1 } from "../../../shared/trade-context"
import { Button } from "../ui/button"

function referenceLabel(reference: TradeReferenceV1) {
  const identity = reference.version ? `${reference.id}@${reference.version}` : reference.id
  return `${reference.label ?? reference.kind}: ${identity}`
}

export function TradeContextChips({
  context,
  onClear,
  compact = false,
}: {
  context?: TradeContextV1 | null
  onClear?: () => void
  compact?: boolean
}) {
  if (!context || context.references.length === 0) return null
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-1.5" aria-label="TradeEngine context">
      {context.references.map((reference) => (
        <span
          key={`${reference.kind}:${reference.id}:${reference.version ?? ""}:${reference.digest ?? ""}`}
          className={compact
            ? "inline-flex max-w-full items-center gap-1 rounded-full border border-border bg-muted/40 px-2 py-0.5 text-[11px] text-muted-foreground"
            : "inline-flex max-w-full items-center gap-1.5 rounded-full border border-sky-500/25 bg-sky-500/10 px-2.5 py-1 text-xs text-foreground"}
          title={referenceLabel(reference)}
        >
          <Database className="size-3 shrink-0 text-sky-500" />
          <span className="truncate">{referenceLabel(reference)}</span>
        </span>
      ))}
      {onClear ? (
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="size-7 rounded-full text-muted-foreground"
          onClick={onClear}
          aria-label="Clear TradeEngine context"
          title="Clear context"
        >
          <X className="size-3.5" />
        </Button>
      ) : null}
    </div>
  )
}
