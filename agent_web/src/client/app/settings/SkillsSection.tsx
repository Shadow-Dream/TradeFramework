import { BookOpen, CheckCircle2 } from "lucide-react"
import { AnthropicIcon, OpenAIIcon } from "../../components/provider-icons"

const TASK_SKILLS = [
  { id: "strategy-development", label: "Strategy Development", description: "Develop and verify strategy source within the selected Project." },
  { id: "dataset-preparation", label: "Dataset Preparation", description: "Inspect dataset contracts, causal timing and conformance." },
  { id: "backtest-investigation", label: "Backtest Investigation", description: "Investigate frozen compositions, jobs, failures and results." },
  { id: "research-verification", label: "Research Verification", description: "Separate confirmed facts, calculations and proposals with exact references." },
] as const

export function SkillsSection() {
  return (
    <div className="space-y-5">
      <div className="rounded-2xl border border-border bg-card/40 p-4 text-sm text-muted-foreground">
        These four task Skills come from one TradeEngine source and are installed for both native Agent backends.
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        {TASK_SKILLS.map((skill) => (
          <article key={skill.id} className="rounded-2xl border border-border bg-card/40 p-4">
            <div className="flex items-start gap-3">
              <span className="rounded-xl bg-muted p-2"><BookOpen className="h-4 w-4 text-logo" /></span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <h3 className="text-sm font-semibold text-foreground">{skill.label}</h3>
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
                </div>
                <code className="mt-0.5 block text-xs text-muted-foreground">/{skill.id}</code>
                <p className="mt-2 text-xs leading-5 text-muted-foreground">{skill.description}</p>
                <div className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
                  <AnthropicIcon className="h-3.5 w-3.5" /> Claude Code
                  <span aria-hidden>·</span>
                  <OpenAIIcon className="h-3.5 w-3.5" /> Codex
                </div>
              </div>
            </div>
          </article>
        ))}
      </div>
    </div>
  )
}
